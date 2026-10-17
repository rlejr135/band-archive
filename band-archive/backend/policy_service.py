"""Public service identity and server-owned policy acceptance decisions."""

from datetime import datetime, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from errors import ValidationError
from extensions import db
from models import AppUser, PolicyAcceptanceReceipt


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
