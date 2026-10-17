"""Small, bounded worker for user-requested R2 deletion tasks only."""
from datetime import timedelta
import logging
import threading
import uuid

from botocore.exceptions import ClientError
from sqlalchemy import or_

from deletion_service import (ERROR_CODES, FILE_RESOURCES, RESOURCE_MODELS,
                              _object_keys, as_utc, validate_unshared_manifest, utcnow)
from extensions import db
from models import (Band, DirectUploadSession, GalleryImage, Media, MultipartUploadSession,
                    PersonalLog, PersonalLogMultipartUploadSession, R2DeletionTask)
from startup_guard import postgres_automation_allowed
from storage import storage

logger = logging.getLogger(__name__)


def _claim_one(lease_seconds):
    now = utcnow()
    query = R2DeletionTask.query.filter(
        or_(
            (R2DeletionTask.status.in_(('queued', 'retry_wait')) & (R2DeletionTask.available_at <= now)),
            (R2DeletionTask.status == 'processing') & (R2DeletionTask.lease_expires_at <= now),
        )
    ).order_by(R2DeletionTask.available_at, R2DeletionTask.created_at)
    if db.engine.dialect.name == 'postgresql':
        query = query.with_for_update(skip_locked=True)
    task = query.first()
    if task is None:
        return None
    max_attempts = max(1, min(20, int(__import__('flask').current_app.config.get('R2_DELETION_MAX_ATTEMPTS', 8))))
    if task.attempt_count >= max_attempts:
        # Expired leases are bounded too: a crashed worker must not cause
        # unlimited repeat storage calls after its retry budget is exhausted.
        task.status = 'blocked'
        task.error_code = 'storage_unavailable'
        task.lease_token = task.lease_expires_at = None
        task.updated_at = now
        db.session.commit()
        return None
    task.status = 'processing'
    task.lease_token = str(uuid.uuid4())
    task.lease_expires_at = now + timedelta(seconds=lease_seconds)
    task.attempt_count += 1
    task.updated_at = now
    db.session.commit()
    return task.id, task.lease_token


def _target_safe_to_finalize(task, target):
    return target is not None and task.resource_type in FILE_RESOURCES


def _valid_completed_result_references(task, target):
    """Check nullable upload-result FKs before storage I/O and before commit."""
    if task.resource_type == 'media':
        queries = (
            DirectUploadSession.query.filter_by(media_id=target.id),
            MultipartUploadSession.query.filter_by(media_id=target.id),
        )
    elif task.resource_type == 'personal_log':
        queries = (
            DirectUploadSession.query.filter_by(personal_log_id=target.id),
            PersonalLogMultipartUploadSession.query.filter_by(personal_log_id=target.id),
        )
    else:
        queries = (DirectUploadSession.query.filter_by(result_gallery_image_id=target.id),)
    result = []
    for query in queries:
        rows = query.limit(1001).all()
        if len(rows) > 1000 or any(row.band_id != task.band_id or row.status != 'completed' for row in rows):
            return None
        result.extend(rows)
    return result


def process_task(task_id, lease_token):
    task = R2DeletionTask.query.filter_by(id=task_id, status='processing', lease_token=lease_token).with_for_update().one_or_none()
    if task is None:
        db.session.rollback()
        return False
    model = RESOURCE_MODELS[task.resource_type]
    target = model.query.filter_by(id=task.resource_id, band_id=task.band_id).with_for_update().one_or_none()
    if target is None or not _target_safe_to_finalize(task, target):
        task.status = 'blocked'
        task.error_code = 'key_conflict'
        task.lease_token = task.lease_expires_at = None
        task.updated_at = utcnow()
        db.session.commit()
        return False
    if task.resource_type in FILE_RESOURCES:
        band = db.session.get(Band, task.band_id)
        try:
            fresh_keys = _object_keys(band, task.resource_type, target)
            if fresh_keys != list(task.object_keys or []):
                raise ValueError('stale manifest')
            validate_unshared_manifest(task.band_id, target, fresh_keys, task.resource_type)
        except Exception:
            task.status = 'blocked'
            task.error_code = 'key_conflict'
            task.lease_token = task.lease_expires_at = None
            task.updated_at = utcnow()
            db.session.commit()
            return False
        if _valid_completed_result_references(task, target) is None:
            task.status = 'blocked'
            task.error_code = 'key_conflict'
            task.lease_token = task.lease_expires_at = None
            task.updated_at = utcnow()
            db.session.commit()
            return False
    object_keys = list(task.object_keys or [])
    db.session.commit()  # release row locks during bounded external storage I/O
    try:
        for key in object_keys:
            current = R2DeletionTask.query.filter_by(
                id=task_id, status='processing', lease_token=lease_token,
            ).one_or_none()
            if current is None or current.lease_expires_at is None or as_utc(current.lease_expires_at) <= utcnow():
                db.session.rollback()
                return False
            live_target = model.query.filter_by(id=current.resource_id, band_id=current.band_id).one_or_none()
            if live_target is None:
                db.session.rollback()
                return _block_task(task_id, lease_token, 'key_conflict')
            live_band = db.session.get(Band, current.band_id)
            try:
                if _object_keys(live_band, current.resource_type, live_target) != list(current.object_keys or []):
                    db.session.rollback()
                    return _block_task(task_id, lease_token, 'key_conflict')
                validate_unshared_manifest(current.band_id, live_target, list(current.object_keys or []), current.resource_type)
            except Exception:
                db.session.rollback()
                return _block_task(task_id, lease_token, 'key_conflict')
            db.session.rollback()
            try:
                storage.delete_bounded(key)
            except ClientError as exc:
                code = str((getattr(exc, 'response', {}) or {}).get('Error', {}).get('Code', ''))
                status = (getattr(exc, 'response', {}) or {}).get('ResponseMetadata', {}).get('HTTPStatusCode')
                if code not in {'NoSuchKey', 'NotFound', '404'} and status != 404:
                    raise
    except Exception as exc:
        db.session.rollback()
        logger.warning('R2 deletion storage call failed (%s)', type(exc).__name__)
        return _retry_task(task_id, lease_token, 'storage_unavailable')

    task = R2DeletionTask.query.filter_by(id=task_id, status='processing', lease_token=lease_token).with_for_update().one_or_none()
    if task is None:
        db.session.rollback()
        return False
    model = RESOURCE_MODELS[task.resource_type]
    target = model.query.filter_by(id=task.resource_id, band_id=task.band_id).with_for_update().one_or_none()
    lease_expiry = as_utc(task.lease_expires_at) if task.lease_expires_at else None
    if (target is None or not _target_safe_to_finalize(task, target)
            or lease_expiry is None or lease_expiry <= utcnow()):
        task.status = 'blocked'
        task.error_code = 'key_conflict'
        task.lease_token = task.lease_expires_at = None
        task.updated_at = utcnow()
        db.session.commit()
        return False
    # Re-derive and recheck the complete manifest in the final transaction.
    # No task is completed if a reference appeared while storage I/O ran.
    band = db.session.get(Band, task.band_id)
    try:
        fresh_keys = _object_keys(band, task.resource_type, target)
        if fresh_keys != list(task.object_keys or []):
            raise ValueError('stale manifest')
        validate_unshared_manifest(task.band_id, target, fresh_keys, task.resource_type)
    except Exception:
        task.status = 'blocked'
        task.error_code = 'key_conflict'
        task.lease_token = task.lease_expires_at = None
        task.updated_at = utcnow()
        db.session.commit()
        return False
    # Successful upload-capability rows are retained for idempotency/audit, but
    # their nullable result pointers must not prevent deletion of the resource.
    # Repeat the pre-I/O fence and refuse to mutate cross-band/stale rows.
    result_rows = _valid_completed_result_references(task, target)
    if result_rows is None:
        task.status = 'blocked'
        task.error_code = 'key_conflict'
        task.lease_token = task.lease_expires_at = None
        task.updated_at = utcnow()
        db.session.commit()
        return False
    for row in result_rows:
        if isinstance(row, DirectUploadSession):
            if task.resource_type == 'media':
                row.media_id = None
            elif task.resource_type == 'personal_log':
                row.personal_log_id = None
            else:
                row.result_gallery_image_id = None
        elif isinstance(row, MultipartUploadSession):
            row.media_id = None
        else:
            row.personal_log_id = None
    db.session.delete(target)
    task.status = 'completed'
    task.object_keys = None
    task.actor_user_id = None
    task.error_code = 'none'
    task.lease_token = task.lease_expires_at = None
    task.completed_at = task.updated_at = utcnow()
    db.session.commit()
    return True


def _retry_task(task_id, lease_token, code):
    task = R2DeletionTask.query.filter_by(id=task_id, status='processing', lease_token=lease_token).with_for_update().one_or_none()
    if task is None:
        db.session.rollback()
        return False
    max_attempts = max(1, min(20, int(__import__('flask').current_app.config.get('R2_DELETION_MAX_ATTEMPTS', 8))))
    task.status = 'blocked' if task.attempt_count >= max_attempts else 'retry_wait'
    task.error_code = code if code in ERROR_CODES else 'unknown'
    task.lease_token = task.lease_expires_at = None
    delay = min(3600, 5 * (2 ** min(10, max(0, task.attempt_count - 1))))
    task.available_at = utcnow() + timedelta(seconds=delay)
    task.updated_at = utcnow()
    db.session.commit()
    return False


def _block_task(task_id, lease_token, code):
    task = R2DeletionTask.query.filter_by(id=task_id, status='processing', lease_token=lease_token).with_for_update().one_or_none()
    if task is None:
        db.session.rollback()
        return False
    task.status = 'blocked'
    task.error_code = code if code in ERROR_CODES else 'unknown'
    task.lease_token = task.lease_expires_at = None
    task.updated_at = utcnow()
    db.session.commit()
    return False


class DeletionWorker:
    def __init__(self, app):
        self.app = app
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name='r2-deletion-worker', daemon=True)
        self.thread.start()

    def wake(self):
        self.wake_event.set()

    def stop(self, timeout=5):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout)

    def _run(self):
        poll = max(.5, min(60, float(self.app.config.get('R2_DELETION_WORKER_POLL_SECONDS', 5))))
        lease = max(30, min(600, int(self.app.config.get('R2_DELETION_WORKER_LEASE_SECONDS', 120))))
        batch = max(1, min(10, int(self.app.config.get('R2_DELETION_WORKER_BATCH_SIZE', 2))))
        while not self.stop_event.is_set():
            try:
                with self.app.app_context():
                    if not postgres_automation_allowed(self.app, db.engine, 'PG_R2_DELETION_WORKER_ENABLED'):
                        self.wake_event.wait(poll)
                        self.wake_event.clear()
                        continue
                    progressed = False
                    for _ in range(batch):
                        claimed = _claim_one(lease)
                        if not claimed:
                            break
                        process_task(*claimed)
                        progressed = True
                    if progressed:
                        continue
            except Exception as exc:
                with self.app.app_context():
                    db.session.rollback()
                logger.warning('R2 deletion worker iteration failed (%s)', type(exc).__name__)
            self.wake_event.wait(poll)
            self.wake_event.clear()


def start_worker(app):
    worker = DeletionWorker(app)
    worker.start()
    app.extensions['r2_deletion_worker'] = worker
    return worker
