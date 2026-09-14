"""Supabase bearer-token verification and local user synchronization."""

import threading
import time
from dataclasses import dataclass

import jwt
import requests
from flask import current_app, g, request
from sqlalchemy.exc import IntegrityError

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

    def _refresh(self):
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
        self._expires_at = self.clock() + self.ttl_seconds

    def key_for(self, kid, force_refresh=False):
        with self._lock:
            if force_refresh or self.clock() >= self._expires_at:
                self._refresh()
            key = self._keys.get(kid)
            if key is None and not force_refresh:
                self._refresh()  # Unknown kid can be a normal key rotation.
                key = self._keys.get(kid)
            if key is None:
                raise AuthError('invalid_token', 'Token signing key is unknown.')
            return key


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


def verify_bearer_token(header=None):
    if not current_app.config.get('SUPABASE_AUTH_ENABLED'):
        raise AuthError('auth_unavailable', 'Authentication is not enabled for this deployment.', 503)
    token = _bearer_token(header if header is not None else request.headers.get('Authorization'))
    try:
        unverified = jwt.get_unverified_header(token)
        kid = unverified.get('kid')
        if not isinstance(kid, str) or not kid:
            raise AuthError('invalid_token', 'Token signing key is missing.')
        key = _cache().key_for(kid)
        try:
            claims = _decode(token, key)
        except jwt.InvalidSignatureError:
            claims = _decode(token, _cache().key_for(kid, force_refresh=True))
    except AuthError:
        raise
    except jwt.PyJWTError as error:
        raise AuthError('invalid_token', 'Bearer token is invalid or expired.') from error

    subject = claims.get('sub')
    if not isinstance(subject, str) or not subject.strip():
        raise AuthError('invalid_token', 'Token subject claim is invalid.')
    verified = claims.get('email_verified') is True or bool(claims.get('email_confirmed_at'))
    if not verified:
        raise AuthError('email_not_verified', 'Verified email is required.')
    metadata = claims.get('user_metadata') if isinstance(claims.get('user_metadata'), dict) else {}
    display_name = metadata.get('full_name') or metadata.get('name')
    if not isinstance(display_name, str):
        display_name = None
    return VerifiedIdentity(subject.strip(), normalize_email(claims.get('email')), verified, display_name)


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
