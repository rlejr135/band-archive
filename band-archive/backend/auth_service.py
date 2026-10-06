"""Supabase bearer-token verification and local user synchronization."""

import threading
import time
from dataclasses import dataclass

import jwt
import requests
from flask import current_app, g, request
from sqlalchemy.exc import IntegrityError
from sqlalchemy import inspect, text

from extensions import db
from models import AppUser, AuthBootstrapState


class AuthError(Exception):
    def __init__(self, code, message, status_code=401):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def normalize_email(value):
    if not isinstance(value, str):
        raise AuthError('invalid_token', 'Token email claim is invalid.')
    email = value.strip().casefold()
    if not email or email.count('@') != 1 or email.startswith('@') or email.endswith('@'):
        raise AuthError('invalid_token', 'Token email claim is invalid.')
    return email


def assert_auth_schema_ready():
    """Require isolated Alembic upgrade before enabling protected endpoints."""
    from migration_bootstrap import ALEMBIC_VERSION_TABLE, migration_head_revision

    tables = set(inspect(db.engine).get_table_names())
    required = {'app_user', 'auth_bootstrap_state', ALEMBIC_VERSION_TABLE}
    if not required.issubset(tables):
        raise RuntimeError(
            'Supabase authentication requires an isolated Alembic upgrade before app startup. '
            'Run `python migration_runner.py --database-url ... upgrade` while the app is stopped.'
        )
    with db.engine.connect() as connection:
        revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one_or_none()
    if revision != migration_head_revision():
        raise RuntimeError(
            'Supabase authentication requires the database at Alembic head before app startup. '
            'Run `python migration_runner.py --database-url ... upgrade` while the app is stopped.'
        )


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    email: str
    email_verified: bool
    display_name: str | None


class JWKSCache:
    def __init__(self, url, ttl_seconds, timeout_seconds, clock=time.monotonic, fetch=requests.get):
        self.url = url
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.fetch = fetch
        self._expires_at = 0
        self._keys = {}
        self._lock = threading.Lock()
        self._generation = 0
        self._rotation_cooldown_until = 0
        self._negative_kids = {}

    def _refresh_locked(self, rotation=False):
        try:
            response = self.fetch(self.url, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            keys = payload.get('keys') if isinstance(payload, dict) else None
            if not isinstance(keys, list):
                raise ValueError('JWKS keys missing')
            parsed = {key.get('kid'): jwt.PyJWK.from_dict(key) for key in keys if key.get('kid')}
            if not parsed:
                raise ValueError('JWKS has no keyed public keys')
        except Exception as error:
            raise AuthError('auth_unavailable', 'Authentication key service is unavailable.', 503) from error
        self._keys = parsed
        now = self.clock()
        self._expires_at = now + self.ttl_seconds
        self._generation += 1
        if rotation:
            self._rotation_cooldown_until = self._expires_at
        self._negative_kids.clear()

    def _ensure_fresh_locked(self):
        if self.clock() >= self._expires_at:
            self._refresh_locked()
            return True
        return False

    def _unknown_key(self, kid):
        self._negative_kids[kid] = self.clock() + self.ttl_seconds
        raise AuthError('invalid_token', 'Token signing key is unknown.')

    def key_for(self, kid):
        """Return a key and generation, with one bounded rotation fetch.

        The lock spans network refreshes deliberately: it makes the refresh a
        single-flight operation. Negative kids and the generation cooldown
        ensure a flood of forged or unknown-kid tokens cannot fetch JWKS once
        per request.
        """
        with self._lock:
            freshly_loaded = self._ensure_fresh_locked()
            key = self._keys.get(kid)
            if key is not None:
                return key, self._generation, not freshly_loaded

            if self._negative_kids.get(kid, 0) > self.clock():
                self._unknown_key(kid)
            # The initial/TTL fetch was already a current-key lookup. Avoid a
            # second request for the same token, but allow one recovery fetch
            # for a new kid seen while an otherwise fresh cache is in use.
            if not freshly_loaded and self.clock() >= self._rotation_cooldown_until:
                self._refresh_locked(rotation=True)
                key = self._keys.get(kid)
                if key is not None:
                    return key, self._generation, False
            self._unknown_key(kid)

    def key_after_signature_failure(self, kid, observed_generation, allow_recovery):
        """Recover a genuine same-kid rotation at most once per cache TTL."""
        with self._lock:
            self._ensure_fresh_locked()
            # Another request already refreshed after this token selected its
            # key. Re-use that result instead of amplifying the remote fetch.
            if observed_generation != self._generation:
                key = self._keys.get(kid)
                if key is None:
                    self._unknown_key(kid)
                return key, self._generation
            if allow_recovery and self.clock() >= self._rotation_cooldown_until:
                self._refresh_locked(rotation=True)
            key = self._keys.get(kid)
            if key is None:
                self._unknown_key(kid)
            return key, self._generation


def _cache():
    cache = current_app.extensions.get('supabase_jwks_cache')
    if cache is None:
        cache = JWKSCache(
            current_app.config['SUPABASE_JWKS_URL'],
            current_app.config['SUPABASE_JWKS_CACHE_SECONDS'],
            current_app.config['SUPABASE_HTTP_TIMEOUT_SECONDS'],
        )
        current_app.extensions['supabase_jwks_cache'] = cache
    return cache


def _bearer_token(header):
    if not header:
        raise AuthError('missing_token', 'Bearer authentication is required.')
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1]:
        raise AuthError('malformed_token', 'Authorization must use Bearer <token>.')
    return parts[1]


def _decode(token, key):
    return jwt.decode(
        token,
        key.key,
        algorithms=[key.algorithm_name],
        audience=current_app.config['SUPABASE_JWT_AUDIENCE'],
        issuer=current_app.config['SUPABASE_JWT_ISSUER'],
        leeway=current_app.config['SUPABASE_JWT_LEEWAY_SECONDS'],
        # PyJWT validates nbf when present. Supabase access tokens need not
        # include it, so requiring it would reject otherwise valid sessions.
        options={'require': ['exp', 'iss', 'aud', 'sub', 'email']},
    )


def _email_is_confirmed(token, subject, email, claims):
    """Confirm email when the signed access token has no confirmation claim.

    Supabase's standard access-token contract includes the signed subject and
    email but does not include the User object's ``email_confirmed_at`` field.
    An explicit false claim remains an immediate rejection.  For a token that
    has no confirmation claim at all, ask only the configured, validated
    Supabase Auth origin for its matching User record.
    """
    if 'email_verified' in claims:
        # Explicit denial always wins, even if a malformed token also carries
        # a timestamp-like field.
        return claims['email_verified'] is True
    if 'email_confirmed_at' in claims:
        confirmed_at = claims['email_confirmed_at']
        return isinstance(confirmed_at, str) and bool(confirmed_at.strip())
    try:
        response = requests.get(
            f"{current_app.config['SUPABASE_URL']}/auth/v1/user",
            headers={
                'apikey': current_app.config['SUPABASE_PUBLISHABLE_KEY'],
                'Authorization': f'Bearer {token}',
            },
            timeout=current_app.config['SUPABASE_HTTP_TIMEOUT_SECONDS'],
            # Never forward the bearer token to a redirect destination.
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise AuthError('auth_unavailable', 'Authentication user service is unavailable.', 503)
        response.raise_for_status()
        user = response.json()
        if not isinstance(user, dict):
            raise AuthError('auth_unavailable', 'Authentication user service is unavailable.', 503)
        returned_subject = user.get('id')
        returned_email = user.get('email')
        if not isinstance(returned_subject, str) or returned_subject.strip() != subject:
            raise AuthError('invalid_token', 'Token subject does not match the authentication user.', 401)
        if normalize_email(returned_email) != email:
            raise AuthError('invalid_token', 'Token email does not match the authentication user.', 401)
        confirmed_at = user.get('email_confirmed_at')
        return isinstance(confirmed_at, str) and bool(confirmed_at.strip())
    except AuthError:
        raise
    except requests.HTTPError as error:
        response = error.response
        if response is not None and response.status_code in {401, 403}:
            return False
        raise AuthError('auth_unavailable', 'Authentication user service is unavailable.', 503) from error
    except Exception as error:
        raise AuthError('auth_unavailable', 'Authentication user service is unavailable.', 503) from error


def verify_bearer_token(header=None):
    if not current_app.config.get('SUPABASE_AUTH_ENABLED'):
        raise AuthError('auth_unavailable', 'Authentication is not enabled for this deployment.', 503)
    token = _bearer_token(header if header is not None else request.headers.get('Authorization'))
    try:
        unverified = jwt.get_unverified_header(token)
        kid = unverified.get('kid')
        if not isinstance(kid, str) or not kid:
            raise AuthError('invalid_token', 'Token signing key is missing.')
        key, generation, allow_recovery = _cache().key_for(kid)
        try:
            claims = _decode(token, key)
        except jwt.InvalidSignatureError:
            replacement, _ = _cache().key_after_signature_failure(kid, generation, allow_recovery)
            claims = _decode(token, replacement)
    except AuthError:
        raise
    except jwt.PyJWTError as error:
        raise AuthError('invalid_token', 'Bearer token is invalid or expired.') from error

    subject = claims.get('sub')
    if not isinstance(subject, str) or not subject.strip():
        raise AuthError('invalid_token', 'Token subject claim is invalid.')
    metadata = claims.get('user_metadata') if isinstance(claims.get('user_metadata'), dict) else {}
    display_name = metadata.get('full_name') or metadata.get('name')
    if not isinstance(display_name, str):
        display_name = None
    normalized_subject = subject.strip()
    email = normalize_email(claims.get('email'))
    if not _email_is_confirmed(token, normalized_subject, email, claims):
        raise AuthError('email_not_verified', 'Verified email is required.')
    return VerifiedIdentity(normalized_subject, email, True, display_name)


def _claim_initial_admin(user):
    configured = current_app.config.get('BOOTSTRAP_PLATFORM_ADMIN_EMAIL', '')
    if not configured or normalize_email(configured) != user.email:
        return False
    try:
        with db.session.begin_nested():
            db.session.add(AuthBootstrapState(key='platform_admin', claimed_by_user_id=user.id))
            db.session.flush()
        user.is_platform_admin = True
        return True
    except IntegrityError:
        return False


def synchronize_user(identity):
    user = AppUser.query.filter_by(supabase_user_id=identity.subject).one_or_none()
    email_owner = AppUser.query.filter_by(email=identity.email).one_or_none()
    if email_owner is not None and email_owner is not user:
        raise AuthError('identity_conflict', 'Email is already linked to another account.', 409)
    if user is None:
        user = AppUser(
            supabase_user_id=identity.subject,
            email=identity.email,
            display_name=identity.display_name,
            email_verified=True,
        )
        db.session.add(user)
        try:
            db.session.flush()
        except IntegrityError as error:
            db.session.rollback()
            existing = AppUser.query.filter_by(supabase_user_id=identity.subject).one_or_none()
            if existing is None or existing.email != identity.email:
                raise AuthError('identity_conflict', 'Email is already linked to another account.', 409) from error
            user = existing
        else:
            _claim_initial_admin(user)
    else:
        if user.status != 'active':
            raise AuthError('account_disabled', 'This account is disabled.', 403)
        user.email = identity.email
        user.email_verified = True
        if identity.display_name:
            user.display_name = identity.display_name
    db.session.commit()
    return user


def authenticate_request():
    identity = verify_bearer_token()
    user = synchronize_user(identity)
    g.current_app_user = user
    return user


def current_app_user(required=False):
    user = getattr(g, 'current_app_user', None)
    if user is None and required:
        user = authenticate_request()
    return user


def require_auth(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        authenticate_request()
        return view(*args, **kwargs)
    return wrapped
