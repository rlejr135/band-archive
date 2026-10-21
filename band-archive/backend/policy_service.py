"""Public service identity and server-owned policy acceptance decisions."""

from datetime import datetime, timezone
import json
import uuid

from flask import current_app
from sqlalchemy.exc import IntegrityError

from errors import ValidationError
from extensions import db
from models import AppUser, AuditEvent, PolicyAcceptanceReceipt


def _cutoff():
    raw = current_app.config.get('POLICY_NEW_ACCOUNT_CUTOFF', '')
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = datetime.fromisoformat(raw.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def policy_is_ready():
    return policy_is_ready_config(current_app.config)


def policy_acceptance_enabled():
    return bool(current_app.config.get('POLICY_ACCEPTANCE_REQUIRED')) and policy_is_ready() and _cutoff() is not None


def validate_policy_configuration(config):
    """Fail closed if the feature switch is enabled without complete facts."""
    if config.get('POLICY_ACCEPTANCE_REQUIRED'):
        # This validator runs outside an application context at startup.
        try:
            cutoff = datetime.fromisoformat(config.get('POLICY_NEW_ACCOUNT_CUTOFF', '').strip().replace('Z', '+00:00'))
        except ValueError:
            cutoff = None
        if not policy_is_ready_config(config) or cutoff is None or cutoff.tzinfo is None:
            raise RuntimeError('Policy acceptance is enabled without complete, versioned public policy configuration.')
    for key in ('POLICY_TERMS_VERSION', 'POLICY_PRIVACY_VERSION'):
        if len(str(config.get(key, ''))) > 100:
            raise RuntimeError(f'{key} must be at most 100 characters.')
    if config.get('UPLOAD_POLICY_REQUIRED'):
        version = config.get('UPLOAD_POLICY_VERSION', '')
        if not policy_is_ready_config(config) or not isinstance(version, str) or not version.strip() or len(version) > 100:
            raise RuntimeError('Upload policy is enabled without complete, versioned public policy configuration.')
    if config.get('CAPTCHA_ENABLED') and not str(config.get('CAPTCHA_SITE_KEY', '')).strip():
        raise RuntimeError('CAPTCHA is enabled without a public site key.')


def policy_is_ready_config(config):
    required = (
        'SERVICE_NAME', 'CONTACT_EMAIL', 'OPERATOR_NAME', 'POLICY_TERMS_VERSION',
        'POLICY_PRIVACY_VERSION', 'POLICY_TERMS_URL', 'POLICY_PRIVACY_URL',
        'POLICY_RETENTION_NOTICE', 'POLICY_PROVIDERS_NOTICE',
    )
    return all(isinstance(config.get(key), str) and bool(config[key].strip()) for key in required)


def public_policy_config():
    config = current_app.config
    ready = policy_is_ready()
    return {
        'service': {
            'name': config.get('SERVICE_NAME', 'Band Archive'),
            'contact_email': config.get('CONTACT_EMAIL', ''),
            'sender_email': config.get('SENDER_EMAIL') or config.get('CONTACT_EMAIL', ''),
            'operator_name': config.get('OPERATOR_NAME', ''),
        },
        'policies': {
            'ready': ready,
            'policy_acceptance_enabled': policy_acceptance_enabled(),
            'terms_version': config.get('POLICY_TERMS_VERSION', ''),
            'privacy_version': config.get('POLICY_PRIVACY_VERSION', ''),
            'terms_url': config.get('POLICY_TERMS_URL', ''),
            'privacy_url': config.get('POLICY_PRIVACY_URL', ''),
            'retention_notice': config.get('POLICY_RETENTION_NOTICE', ''),
            'providers_notice': config.get('POLICY_PROVIDERS_NOTICE', ''),
            'minimum_age': 14,
            'upload': {'enabled': upload_policy_enabled(),
                       'version': config.get('UPLOAD_POLICY_VERSION', ''),
                       'default_visibility': 'MEMBERS', 'notice_url': '/upload-policy'},
        },
        'captcha': {
            'enabled': bool(config.get('CAPTCHA_ENABLED')),
            'site_key': config.get('CAPTCHA_SITE_KEY', ''),
        },
    }


def receipt_required(user):
    if not policy_acceptance_enabled():
        return False
    cutoff = _cutoff()
    created_at = user.created_at
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at.astimezone(timezone.utc) < cutoff:
        return False
    return db.session.get(PolicyAcceptanceReceipt, user.id) is None


def accept_policy(user, body):
    if not policy_acceptance_enabled():
        raise ValidationError('Policy acceptance is not enabled.', 409, code='policy_not_ready')
    expected = {'terms_version', 'privacy_version', 'age_14_plus', 'terms_accepted'}
    if set(body) != expected:
        raise ValidationError('Request must contain exactly the required policy assertions.')
    if body['age_14_plus'] is not True or body['terms_accepted'] is not True:
        raise ValidationError('The required age and terms assertions must be accepted.')
    if not isinstance(body['terms_version'], str) or not isinstance(body['privacy_version'], str):
        raise ValidationError('Policy versions must be text.')
    if body['terms_version'] != current_app.config['POLICY_TERMS_VERSION']:
        raise ValidationError('The terms version is no longer current.', 409, code='policy_version_mismatch')
    if body['privacy_version'] != current_app.config['POLICY_PRIVACY_VERSION']:
        raise ValidationError('The privacy notice version is no longer current.', 409, code='policy_version_mismatch')
    if not receipt_required(user):
        return db.session.get(PolicyAcceptanceReceipt, user.id), False
    locked_user = AppUser.query.filter_by(id=user.id).with_for_update().populate_existing().one_or_none()
    if locked_user is None or not receipt_required(locked_user):
        return db.session.get(PolicyAcceptanceReceipt, user.id), False
    receipt = PolicyAcceptanceReceipt(
        user_id=locked_user.id,
        terms_version=current_app.config['POLICY_TERMS_VERSION'],
        privacy_version=current_app.config['POLICY_PRIVACY_VERSION'],
        terms_accepted=True,
        age_14_plus=True,
        accepted_at=datetime.now(timezone.utc),
    )
    db.session.add(receipt)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        receipt = db.session.get(PolicyAcceptanceReceipt, user.id)
        if receipt is None:
            raise
        return receipt, False
    return receipt, True


def upload_policy_enabled():
    return bool(current_app.config.get('UPLOAD_POLICY_REQUIRED'))


def _upload_policy_ready():
    version = current_app.config.get('UPLOAD_POLICY_VERSION')
    return policy_is_ready() and isinstance(version, str) and bool(version.strip()) and len(version) <= 100


def upload_policy_receipt_required(user):
    if not upload_policy_enabled():
        return False
    receipt = db.session.get(PolicyAcceptanceReceipt, user.id)
    return (receipt is None or receipt.terms_version != current_app.config['POLICY_TERMS_VERSION']
            or receipt.privacy_version != current_app.config['POLICY_PRIVACY_VERSION']
            or receipt.terms_accepted is not True or receipt.age_14_plus is not True)


def accept_upload_policy(user, body):
    if not upload_policy_enabled() or not _upload_policy_ready():
        raise ValidationError('Upload policy acceptance is not enabled.', 409, code='policy_not_ready')
    expected = {'terms_version', 'privacy_version', 'age_14_plus', 'terms_accepted'}
    if set(body) != expected or body.get('age_14_plus') is not True or body.get('terms_accepted') is not True:
        raise ValidationError('The current terms and age assertions are required.')
    if (body.get('terms_version') != current_app.config['POLICY_TERMS_VERSION']
            or body.get('privacy_version') != current_app.config['POLICY_PRIVACY_VERSION']):
        raise ValidationError('Policy versions are no longer current.', 409, code='policy_version_mismatch')
    locked = AppUser.query.filter_by(id=user.id).with_for_update().populate_existing().one_or_none()
    if locked is None or locked.status != 'active' or locked.email_verified is not True:
        raise ValidationError('An active verified account is required.', 403)
    receipt = db.session.get(PolicyAcceptanceReceipt, locked.id)
    if not upload_policy_receipt_required(locked):
        return receipt, False
    now = datetime.now(timezone.utc)
    if receipt is None:
        receipt = PolicyAcceptanceReceipt(user_id=locked.id)
        db.session.add(receipt)
    previous = ({'terms_version': receipt.terms_version, 'privacy_version': receipt.privacy_version,
                 'accepted_at': receipt.accepted_at.isoformat() if receipt.accepted_at else None}
                if receipt.accepted_at else None)
    receipt.terms_version = body['terms_version']
    receipt.privacy_version = body['privacy_version']
    receipt.terms_accepted = True
    receipt.age_14_plus = True
    receipt.accepted_at = now
    db.session.add(AuditEvent(actor_user_id=locked.id, event_type='account_policy_accepted',
                             target_type='AppUser', target_id=str(locked.id),
                             details_json=json.dumps({'terms_version': receipt.terms_version,
                                                      'privacy_version': receipt.privacy_version,
                                                      'previous': previous})))
    db.session.commit()
    return receipt, True


def validate_upload_assertion(data, user):
    """Validate before any storage URL, reservation, or multipart initiation."""
    if not upload_policy_enabled():
        return None
    if not _upload_policy_ready():
        raise ValidationError('Upload policy configuration is unavailable.', 503, code='policy_not_ready')
    if upload_policy_receipt_required(user):
        raise ValidationError('Review the upload terms before uploading.', 403,
                              code='upload_policy_acceptance_required')
    value = data.get('upload_policy')
    if not isinstance(value, dict) or set(value) != {'version', 'batch_id', 'rights_confirmed', 'visibility'}:
        raise ValidationError('Upload rights and visibility confirmation is required.', 400,
                              code='upload_policy_confirmation_required')
    if not isinstance(value.get('version'), str) or value['version'] != current_app.config['UPLOAD_POLICY_VERSION']:
        raise ValidationError('The upload policy version is no longer current.', 409,
                              code='upload_policy_version_mismatch')
    if (value.get('rights_confirmed') is not True or not isinstance(value.get('visibility'), str)
            or value['visibility'] not in {'PUBLIC', 'MEMBERS'}):
        raise ValidationError('Confirm upload rights and select a valid visibility.')
    try:
        batch_id = str(uuid.UUID(value.get('batch_id')))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('batch_id must be a UUID.')
    return {'version': value['version'], 'batch_id': batch_id,
            'visibility': value['visibility'], 'rights_confirmed': True}


def record_upload_assertion(session, membership, assertion):
    if assertion is None:
        return
    if session.id is None:
        db.session.flush()
    db.session.add(AuditEvent(band_id=session.band_id, actor_user_id=membership.user_id,
                             event_type='upload_policy_confirmed', target_type=type(session).__name__,
                             target_id=session.id, details_json=json.dumps(assertion, sort_keys=True)))
    db.session.flush()


def upload_session_visibility(session, fallback):
    """Use the original decision; old/feature-disabled sessions keep their contract."""
    rows = AuditEvent.query.filter_by(event_type='upload_policy_confirmed',
                                    target_type=type(session).__name__, target_id=session.id).limit(2).all()
    if not rows:
        return fallback
    from models import BandMembership
    creator = (db.session.get(BandMembership, session.created_by_membership_id)
               if session.created_by_membership_id is not None else None)
    row = rows[0]
    if (len(rows) != 1 or creator is None or creator.band_id != session.band_id
            or row.band_id != session.band_id or row.actor_user_id != creator.user_id):
        raise ValidationError('Upload policy evidence is inconsistent.', 409)
    try:
        value = json.loads(row.details_json)
        if (value['rights_confirmed'] is not True or not isinstance(value['visibility'], str)
                or value['visibility'] not in {'PUBLIC', 'MEMBERS'}
                or not isinstance(value['version'], str) or not value['version'].strip()
                or len(value['version']) > 100):
            raise ValueError()
        uuid.UUID(value['batch_id'])
        return value['visibility']
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValidationError('Upload policy evidence is invalid.', 409)


def confirm_public_expansion(data, user, band, target):
    if not upload_policy_enabled():
        return
    if not _upload_policy_ready():
        raise ValidationError('Upload policy configuration is unavailable.', 503, code='policy_not_ready')
    if upload_policy_receipt_required(user):
        raise ValidationError('Review the upload terms before sharing publicly.', 403,
                              code='upload_policy_acceptance_required')
    value = data.get('visibility_confirm')
    if (not isinstance(value, dict) or set(value) != {'version', 'confirmed'}
            or value.get('confirmed') is not True
            or value.get('version') != current_app.config['UPLOAD_POLICY_VERSION']):
        raise ValidationError('Confirm that this content may become publicly accessible.', 409,
                              code='public_visibility_confirmation_required')
    from band_service import audit
    audit('content.public_visibility_confirmed', user, band, target,
          {'version': value['version'], 'confirmed': True})
