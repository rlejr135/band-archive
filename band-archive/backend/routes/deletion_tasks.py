"""Owner/platform deletion API with durable, idempotent task status."""
from functools import wraps
import json

from flask import Blueprint, current_app, jsonify, request, url_for
from sqlalchemy import text

from auth_service import current_app_user
from band_access import MembershipAudience, RequestAudience, BandReadScope, content_capabilities
from band_service import ACTIVE, audit
from deletion_service import (FILE_RESOURCES, RESOURCE_MODELS, actor_is_eligible,
                              block_for_processing, cleanup_terminal_parent_upload_sessions,
                              create_or_get_task, lock_band_and_membership,
                              lock_target, load_current_actor, task_public_dict)
from errors import NotFoundError, ValidationError
from extensions import db
from models import (Announcement, AppUser, BandMembership, Comment, DirectUploadSession,
                    GalleryImage, Media, Member, MultipartUploadSession, PersonalLog,
                    PersonalLogMultipartUploadSession, Rehearsal, R2DeletionTask, Song,
                    SongSuggestion)
from v2_content_serializers import scope_with_band_visibility
from sqlalchemy.exc import IntegrityError


deletion_tasks_bp = Blueprint('deletion_tasks', __name__)


class DeletionConflict(ValidationError):
    def __init__(self, code, message):
        super().__init__(message, 409)
        self.code = code


def _private_response():
    from flask import after_this_request

    @after_this_request
    def no_store(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Authorization')
        return response


def _require_schema():
    if not current_app.config.get('BAND_API_SCHEMA_READY', False):
        return jsonify(error='band_schema_unavailable'), 503
    return None


def _require_worker():
    if not current_app.config.get('PG_R2_DELETION_WORKER_ENABLED', False):
        return jsonify(error='deletion_unavailable'), 503
    return None


def _error(status, code, message):
    return jsonify(error=code, message=message), status


def _authority(band_id, *, operator=False):
    cached = current_app_user(required=True)
    if db.engine.dialect.name == 'sqlite':
        db.session.execute(text('BEGIN IMMEDIATE'))
    band, membership = lock_band_and_membership(band_id, cached.id, require_member=not operator)
    if band is None:
        raise NotFoundError('Band not found')
    actor = load_current_actor(cached.id, lock=True)
    if not actor_is_eligible(actor):
        raise ValidationError('An active verified account is required.', 403)
    if operator:
        if actor.is_platform_admin is not True:
            raise ValidationError('Platform administrator access is required.', 403)
        return actor, band, membership, None
    if membership is None:
        raise ValidationError('An active band membership is required.', 403)
    member_audience = MembershipAudience(
        id=membership.id, band_id=membership.band_id, role=membership.role,
        status=membership.status, user_subject=actor.supabase_user_id,
        user_display_name=actor.display_name, user_email=actor.email,
    )
    audience = RequestAudience(
        user_id=actor.id, subject=actor.supabase_user_id,
        account_active=True, email_verified=True,
        is_platform_admin=actor.is_platform_admin is True,
    )
    scope = scope_with_band_visibility(BandReadScope(
        band_id=band.id, band_slug=band.slug, audience=audience, membership=member_audience,
    ), band)
    return actor, band, membership, scope


def _platform_operator_precheck():
    """Reject non-operators before looking up any band, target, or task.

    This cheap read is repeated under the locked authority check before writes;
    it only prevents resource-existence probes and is not the final authority.
    """
    cached = current_app_user(required=True)
    actor = load_current_actor(cached.id, lock=False)
    if not actor_is_eligible(actor):
        raise ValidationError('An active verified account is required.', 403)
    if actor.is_platform_admin is not True:
        raise ValidationError('Platform administrator access is required.', 403)
    return actor


def _target_preconditions(resource_type, target):
    if block_for_processing(resource_type, target):
        raise DeletionConflict('content_processing', 'Media processing must finish before deletion.')
    if resource_type == 'song' and (target.media_files or target.rehearsals or target.sheet_music or target.sheet_music_object_key):
        raise DeletionConflict('children_exist', 'Song with attached media or file references cannot be deleted.')
    if resource_type == 'rehearsal' and target.media_files:
        raise DeletionConflict('children_exist', 'Rehearsal with attached media cannot be deleted.')
    if resource_type == 'member' and target.personal_logs:
        raise DeletionConflict('children_exist', 'Member with personal logs cannot be deleted.')
    if resource_type == 'song':
        pending = MultipartUploadSession.query.filter(
            MultipartUploadSession.song_id == target.id,
            MultipartUploadSession.status.in_(('initiated', 'completing', 'expiring', 'aborting')),
        ).first() or DirectUploadSession.query.filter(
            DirectUploadSession.song_id == target.id,
            DirectUploadSession.status.in_(('initiated', 'completing', 'expiring')),
        ).first()
        if pending:
            raise DeletionConflict('children_exist', 'Song has an upload that must finish cleanup first.')
    if resource_type == 'rehearsal':
        pending = MultipartUploadSession.query.filter(
            MultipartUploadSession.rehearsal_id == target.id,
            MultipartUploadSession.status.in_(('initiated', 'completing', 'expiring', 'aborting')),
        ).first() or DirectUploadSession.query.filter(
            DirectUploadSession.rehearsal_id == target.id,
            DirectUploadSession.status.in_(('initiated', 'completing', 'expiring')),
        ).first()
        if pending:
            raise DeletionConflict('children_exist', 'Rehearsal has an upload that must finish cleanup first.')
    if resource_type == 'member':
        pending = DirectUploadSession.query.filter(
            DirectUploadSession.member_id == target.id,
            DirectUploadSession.status.in_(('initiated', 'completing', 'expiring')),
        ).first() or PersonalLogMultipartUploadSession.query.filter(
            PersonalLogMultipartUploadSession.member_id == target.id,
            PersonalLogMultipartUploadSession.status.in_(('initiated', 'completing', 'expiring', 'aborting')),
        ).first()
        if pending:
            raise DeletionConflict('children_exist', 'Member has an upload that must finish cleanup first.')
    if resource_type in {'song', 'rehearsal', 'member'}:
        if not cleanup_terminal_parent_upload_sessions(resource_type, target):
            raise DeletionConflict('children_exist', 'Upload history is not in a safe terminal state.')


def _enqueue(band_id, resource_type, resource_id, *, operator=False, reason=None):
    if resource_type not in FILE_RESOURCES or resource_id <= 0:
        raise NotFoundError('Resource not found')
    actor, band, membership, scope = _authority(band_id, operator=operator)
    if not operator and membership.role != 'owner' and actor.is_platform_admin is not True:
        db.session.rollback()
        raise ValidationError('Only an active verified owner or platform administrator may delete content.', 403)
    existing = R2DeletionTask.query.filter_by(
        band_id=band_id, resource_type=resource_type, resource_id=resource_id,
    ).with_for_update().one_or_none() if db.engine.dialect.name == 'postgresql' else R2DeletionTask.query.filter_by(
        band_id=band_id, resource_type=resource_type, resource_id=resource_id,
    ).one_or_none()
    if existing:
        db.session.commit()
        return existing, False
    target = lock_target(resource_type, band_id, resource_id)
    if target is None:
        db.session.rollback()
        raise NotFoundError('Resource not found')
    if not operator and not content_capabilities(scope, target)['can_delete']:
        db.session.rollback()
        raise ValidationError('Only an active verified owner or platform administrator may delete content.', 403)
    try:
        _target_preconditions(resource_type, target)
        task, created = create_or_get_task(band, actor, resource_type, target)
        audit('platform.content.deletion.queued' if operator else 'content.deletion.queued', actor, band, target, {
            'resource_type': resource_type, 'task_id': task.id,
            **({'reason': reason.strip()[:500]} if operator and reason else {}),
        })
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        task = R2DeletionTask.query.filter_by(
            band_id=band_id, resource_type=resource_type, resource_id=resource_id,
        ).one()
        created = False
    except ValueError:
        db.session.rollback()
        raise DeletionConflict('key_conflict', 'Deletion keys are invalid or shared; no storage change was made.') from None
    except ValidationError:
        db.session.rollback()
        raise
    worker = current_app.extensions.get('r2_deletion_worker')
    if worker:
        worker.wake()
    return task, created


def _task_response(task, status_code=202):
    value = task_public_dict(task)
    value['poll_url'] = url_for('deletion_tasks.band_task_status', band_id=task.band_id, task_id=task.id)
    value['retry_after_seconds'] = value['retry_after_seconds'] or 5
    response = jsonify(value)
    response.status_code = 200 if task.status == 'completed' else status_code
    response.headers['Retry-After'] = str(value['retry_after_seconds'])
    return response


@deletion_tasks_bp.delete('/api/v2/bands/<int:band_id>/media/<int:resource_id>')
@deletion_tasks_bp.delete('/api/v2/bands/<int:band_id>/personal-logs/<int:resource_id>')
@deletion_tasks_bp.delete('/api/v2/bands/<int:band_id>/gallery/<int:resource_id>')
def delete_member_file_resource(band_id, resource_id):
    _private_response()
    unavailable = _require_schema()
    if unavailable:
        return unavailable
    unavailable = _require_worker()
    if unavailable:
        return unavailable
    resource_type = {'/media/': 'media', '/personal-logs/': 'personal_log', '/gallery/': 'gallery'}[
        next(token for token in ('/media/', '/personal-logs/', '/gallery/') if token in request.path)
    ]
    try:
        task, _ = _enqueue(band_id, resource_type, resource_id)
    except NotFoundError:
        db.session.rollback()
        return _error(404, 'not_found', 'Resource not found.')
    except DeletionConflict as exc:
        db.session.rollback()
        return _error(409, exc.code, exc.message)
    except ValidationError as exc:
        db.session.rollback()
        return _error(exc.status_code, 'deletion_denied', str(exc))
    return _task_response(task)


@deletion_tasks_bp.delete('/api/v2/bands/<int:band_id>/members/<int:resource_id>')
def delete_member_synchronously(band_id, resource_id):
    """Member records are text-only and remain a synchronous owner operation."""
    _private_response()
    unavailable = _require_schema()
    if unavailable:
        return unavailable
    try:
        actor, band, membership, scope = _authority(band_id)
        if membership.role != 'owner' and actor.is_platform_admin is not True:
            raise ValidationError('Only an active verified owner may delete a member.', 403)
        member = lock_target('member', band_id, resource_id)
        if member is None:
            raise NotFoundError('Resource not found')
        _target_preconditions('member', member)
        audit('content.member.deleted', actor, band, member)
        db.session.delete(member)
        db.session.commit()
        return jsonify({'deleted': True, 'id': resource_id})
    except NotFoundError:
        db.session.rollback()
        return _error(404, 'not_found', 'Resource not found.')
    except DeletionConflict as exc:
        db.session.rollback()
        return _error(409, exc.code, exc.message)
    except ValidationError as exc:
        db.session.rollback()
        return _error(exc.status_code, 'deletion_denied', str(exc))


def _platform_delete_text(band_id, resource_type, resource_id, reason):
    actor, band, _membership, _scope = _authority(band_id, operator=True)
    target = lock_target(resource_type, band_id, resource_id)
    if target is None:
        raise NotFoundError('Resource not found')
    _target_preconditions(resource_type, target)
    if resource_type == 'comment':
        from routes.content_writes import _assert_comment_tree_safe
        _assert_comment_tree_safe(target)
    if resource_type == 'announcement':
        target.content = ''
    else:
        db.session.delete(target)
    audit('platform.content.deletion.completed', actor, band, target, {
        'resource_type': resource_type,
        'reason': reason,
    })
    db.session.commit()
    return {'status': 'completed', 'resource_type': resource_type, 'resource_id': resource_id}


@deletion_tasks_bp.post('/api/v2/platform/bands/<int:band_id>/deletions')
def platform_delete_resource(band_id):
    _private_response()
    try:
        _platform_operator_precheck()
    except ValidationError as exc:
        return _error(exc.status_code, 'deletion_denied', str(exc))
    unavailable = _require_schema()
    if unavailable:
        return unavailable
    if request.content_length is not None and request.content_length > 8192:
        return _error(413, 'request_too_large', 'The deletion request is too large.')
    if not request.is_json:
        return _error(400, 'invalid_request', 'A JSON request body is required.')
    try:
        raw_body = request.stream.read(8193)
        if len(raw_body) > 8192:
            return _error(413, 'request_too_large', 'The deletion request is too large.')
        value = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return _error(400, 'invalid_request', 'A valid JSON request body is required.')
    if not isinstance(value, dict) or set(value) != {'resource_type', 'resource_id', 'reason', 'confirm'}:
        return _error(400, 'invalid_request', 'resource_type, resource_id, reason and confirm are required.')
    resource_type, resource_id, reason = value['resource_type'], value['resource_id'], value['reason']
    if (not isinstance(resource_type, str) or resource_type not in RESOURCE_MODELS
            or type(resource_id) is not int or resource_id <= 0
            or value['confirm'] is not True or not isinstance(reason, str) or not reason.strip()
            or len(reason.strip()) > 500):
        return _error(400, 'invalid_request', 'A valid target, reason and confirm=true are required.')
    if resource_type not in FILE_RESOURCES:
        try:
            data = _platform_delete_text(band_id, resource_type, resource_id, reason.strip())
            return jsonify(data), 200
        except NotFoundError:
            db.session.rollback()
            return _error(404, 'not_found', 'Resource not found.')
        except DeletionConflict as exc:
            db.session.rollback()
            return _error(409, exc.code, exc.message)
        except ValidationError as exc:
            db.session.rollback()
            return _error(exc.status_code, 'deletion_denied', str(exc))
    unavailable = _require_worker()
    if unavailable:
        return unavailable
    try:
        task, _ = _enqueue(band_id, resource_type, resource_id, operator=True,
                           reason=reason.strip())
    except NotFoundError:
        db.session.rollback()
        return _error(404, 'not_found', 'Resource not found.')
    except DeletionConflict as exc:
        db.session.rollback()
        return _error(409, exc.code, exc.message)
    except ValidationError as exc:
        db.session.rollback()
        return _error(exc.status_code, 'deletion_denied', str(exc))
    data = task_public_dict(task)
    data['status_url'] = url_for('deletion_tasks.platform_task_status', task_id=task.id)
    response = jsonify(data)
    response.status_code = 200 if task.status == 'completed' else 202
    return response


def _read_task(task_id, *, band_id=None, operator=False):
    _private_response()
    if operator:
        _platform_operator_precheck()
    cached = current_app_user(required=True)
    actor = load_current_actor(cached.id, lock=False)
    if not actor_is_eligible(actor):
        raise ValidationError('An active verified account is required.', 403)
    task = R2DeletionTask.query.filter_by(id=task_id).one_or_none()
    if task is None or (band_id is not None and task.band_id != band_id):
        raise NotFoundError('Task not found')
    if operator:
        if actor.is_platform_admin is not True:
            raise ValidationError('Platform administrator access is required.', 403)
    else:
        member = BandMembership.query.filter_by(band_id=task.band_id, user_id=actor.id, status=ACTIVE).one_or_none()
        if member is None or (member.role != 'owner' and actor.is_platform_admin is not True):
            raise NotFoundError('Task not found')
    return jsonify(task_public_dict(task))


@deletion_tasks_bp.get('/api/v2/bands/<int:band_id>/deletion-tasks/<task_id>')
def band_task_status(band_id, task_id):
    try:
        return _read_task(task_id, band_id=band_id)
    except NotFoundError:
        return _error(404, 'not_found', 'Task not found.')
    except ValidationError as exc:
        return _error(exc.status_code, 'deletion_status_denied', str(exc))


@deletion_tasks_bp.get('/api/v2/platform/deletion-tasks/<task_id>')
def platform_task_status(task_id):
    try:
        return _read_task(task_id, operator=True)
    except NotFoundError:
        return _error(404, 'not_found', 'Task not found.')
    except ValidationError as exc:
        return _error(exc.status_code, 'deletion_status_denied', str(exc))
