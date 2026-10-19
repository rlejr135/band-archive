"""Security and persistence contracts for reusable invite links."""
import hashlib
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import create_app
from extensions import db
from invite_link_service import canonical_token
from migration_bootstrap import (metadata_for_recorded_revision, phase18_metadata,
                                 phase19_metadata, phase20_metadata)
from models import (Announcement, AppUser, Band, BandInviteLink, GalleryImage,
                    BandMembership, InviteResumeContext, Member, Song)
from tests.test_auth import AuthTestBootstrapConfig, _install_jwks, _jwk, _token


@pytest.fixture(autouse=True)
def _isolated_process_limits():
    import invite_link_service
    with invite_link_service._lock:
        invite_link_service._windows.clear()
        invite_link_service._global_context_issuance.clear()
    yield
    with invite_link_service._lock:
        invite_link_service._windows.clear()
        invite_link_service._global_context_issuance.clear()


@pytest.fixture
def invite_signer():
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def invite_auth(invite_signer):
    app = create_app(AuthTestBootstrapConfig, start_worker=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
        owner = AppUser(supabase_user_id='11111111-1111-1111-1111-111111111111',
                        email='user@example.com', email_verified=True)
        outsider = AppUser(supabase_user_id='77777777-7777-7777-7777-777777777777',
                           email='outsider@example.com', email_verified=True)
        band = Band(name='Private Band', slug='band-a', default_visibility='MEMBERS')
        db.session.add_all((owner, outsider, band))
        db.session.flush()
        from models import BandMembership
        membership = BandMembership(band_id=band.id, user_id=owner.id, role='owner', status='active')
        db.session.add(membership)
        db.session.flush()
        db.session.add_all((
            Song(band_id=band.id, created_by_membership_id=membership.id, visibility='PUBLIC',
                 title='Must stay hidden by private parent', artist='A'),
            Announcement(band_id=band.id, created_by_membership_id=membership.id,
                         visibility='PUBLIC', content='Must stay hidden by private parent'),
            GalleryImage(band_id=band.id, created_by_membership_id=membership.id,
                         visibility='PUBLIC', filename='public.jpg', object_key='private/test.jpg',
                         original_filename='public.jpg', is_featured=True),
        ))
        db.session.commit()
        app.config['BAND_API_SCHEMA_READY'] = True
    app.config['SUPABASE_AUTH_ENABLED'] = True
    _install_jwks(app, [[_jwk(invite_signer, 'invite-key')]])
    yield app, invite_signer
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _auth(signer, subject='11111111-1111-1111-1111-111111111111', email='user@example.com'):
    return {'Authorization': f'Bearer {_token(signer, sub=subject, email=email, kid="invite-key")}'}


def test_invite_metadata_snapshots_are_exact_and_legacy_refresh_guards_new_records():
    before_logo = phase18_metadata()
    with_logo = phase19_metadata()
    with_invites = phase20_metadata()
    assert 'band_logo' not in before_logo.tables
    assert 'band_invite_link' not in before_logo.tables
    assert 'invite_resume_context' not in before_logo.tables
    assert set(with_logo.tables) == set(before_logo.tables) | {'band_logo'}
    assert set(with_invites.tables) == set(with_logo.tables) | {'band_invite_link', 'invite_resume_context'}
    historical = (
        '20260914_01', '20260914_02', '20260915_03', '20260916_04', '20260916_05',
        '20260920_06', '20260927_07', '20261002_08', '20261002_09', '20261003_10',
        '20261004_11', '20261006_12', '20261010_13', '20261014_14', '20261016_15',
        '20261017_16', '20261017_17', '20261017_18', '20261018_19',
    )
    for revision in historical:
        snapshot = metadata_for_recorded_revision(revision)
        assert 'band_invite_link' not in snapshot.tables
        assert 'invite_resume_context' not in snapshot.tables
    from tools.legacy_rehearsal.preserve_identity_refresh import MUST_BE_EMPTY_TABLES
    assert {'band_invite_link', 'invite_resume_context'} <= MUST_BE_EMPTY_TABLES


def test_canonical_token_rejects_noncanonical_last_bits_and_unicode():
    import base64
    token = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
    assert canonical_token(token)
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-'
    assert not canonical_token(token[:-1] + alphabet[(alphabet.index(token[-1]) + 1) % 64])
    assert not canonical_token('é' * 43)
    assert not canonical_token(token + '=')


def test_private_invite_preview_is_public_only_no_store_and_rejects_bad_auth(invite_auth, monkeypatch):
    app, signer = invite_auth
    client = app.test_client()
    with app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        token = 'A' * 43  # the token is intentionally canonicalized below
        import secrets
        token = secrets.token_urlsafe(32)
        row = BandInviteLink(band_id=band.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                             created_by_user_id=AppUser.query.filter_by(email='user@example.com').one().id,
                             created_at=datetime.now(timezone.utc),
                             expires_at=datetime.now(timezone.utc).replace(microsecond=0))
        # Ensure exactly 24h of lifetime, independent of database precision.
        from datetime import timedelta
        row.expires_at = row.created_at + timedelta(hours=24)
        db.session.add(row)
        db.session.commit()
    response = client.post('/api/v2/invite-links/preview', json={'token': token})
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    payload = response.get_json()
    assert payload['invite']['band']['slug'] == 'band-a'
    assert payload['invite']['expires_at'].endswith('+00:00')
    assert payload['invite']['song_count_scope'] == 'PUBLIC'
    assert payload['invite']['stats']['total_songs'] == 0
    assert payload['invite']['featured_gallery'] is None
    assert payload['invite']['announcement'] is None
    assert payload['viewer']['membership_status'] is None
    denied = client.post('/api/v2/invite-links/preview', json={'token': token},
                         headers={'Authorization': 'Bearer not-a-jwt'})
    assert denied.status_code == 401
    malformed = client.post('/api/v2/invite-links/preview', json={'token': token, 'email': 'extra@example.com'})
    assert malformed.status_code == 400


def test_context_binding_and_accept_requires_authenticated_resume(invite_auth):
    app, signer = invite_auth
    client = app.test_client()
    owner_headers = _auth(signer)
    with app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
    created = client.post(f'/api/v2/bands/{band.id}/invite-links', json={}, headers=owner_headers)
    assert created.status_code == 201
    token = created.get_json()['token']
    assert len(token) == 43
    with app.app_context():
        link = BandInviteLink.query.one()
        assert link.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert link.expires_at - link.created_at == __import__('datetime').timedelta(hours=24)
    context_response = client.post('/api/v2/invite-links/resume-context',
                                   json={'token': token, 'email': 'USER@example.com'})
    assert context_response.status_code == 201
    context = context_response.get_json()['context']
    assert context_response.get_json()['expires_at'].endswith('+00:00')
    assert context_response.headers['Cache-Control'] == 'private, no-store'
    with app.app_context():
        saved = InviteResumeContext.query.one()
        assert saved.context_hash == hashlib.sha256(context.encode()).hexdigest()
        assert saved.expected_email_hmac != 'user@example.com'
        assert token not in saved.context_hash
    premature = client.post('/api/v2/invite-links/accept', json={'context': context}, headers=owner_headers)
    assert premature.status_code == 404
    wrong_actor = _auth(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com')
    wrong = client.post('/api/v2/invite-links/resume', json={'context': context}, headers=wrong_actor)
    assert wrong.status_code == 404
    resumed = client.post('/api/v2/invite-links/resume', json={'context': context}, headers=owner_headers)
    assert resumed.status_code == 200
    assert resumed.get_json()['expires_at'].endswith('+00:00')
    assert resumed.get_json()['invite']['expires_at'].endswith('+00:00')
    assert 'viewer' in resumed.get_json() and 'viewer' not in resumed.get_json()['invite']
    accepted = client.post('/api/v2/invite-links/accept', json={'context': context}, headers=owner_headers)
    assert accepted.status_code == 200
    assert accepted.get_json()['membership'] == {'status': 'active', 'role': 'owner'}


def test_private_band_invite_does_not_sign_private_gallery(invite_auth, monkeypatch):
    app, signer = invite_auth
    client = app.test_client()
    with app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        band.default_visibility = 'MEMBERS'
        # Public child rows under a private parent must still be hidden.
        db.session.commit()
    calls = []
    from storage import storage
    monkeypatch.setattr(storage, 'generate_url', lambda *args, **kwargs: calls.append(args) or 'https://should-not-sign.invalid')
    # Random canonical token does not resolve; create exact valid row.
    import secrets
    token = secrets.token_urlsafe(32)
    with app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        db.session.add(BandInviteLink(band_id=band.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            created_by_user_id=AppUser.query.filter_by(email='user@example.com').one().id,
            created_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc)+__import__('datetime').timedelta(hours=24)))
        db.session.commit()
    response = client.post('/api/v2/invite-links/preview', json={'token': token})
    assert response.status_code == 200
    assert response.get_json()['invite']['band']['slug'] == 'band-a'
    assert response.get_json()['invite']['stats']['total_songs'] == 0
    assert response.get_json()['invite']['featured_gallery'] is None
    assert response.get_json()['invite']['announcement'] is None
    assert calls == []


def test_invite_revision20_sqlite_upgrade_and_populated_downgrade_guard(tmp_path):
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / 'migrations/versions/20261019_20_reusable_invite_links.py'
    spec = importlib.util.spec_from_file_location('invite_revision20_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f'sqlite:///{(tmp_path / "invite.db").as_posix()}')
    metadata = sa.MetaData()
    sa.Table('band', metadata, sa.Column('id', sa.Integer, primary_key=True))
    sa.Table('app_user', metadata, sa.Column('id', sa.Integer, primary_key=True))
    with engine.begin() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        metadata.create_all(connection)
        connection.execute(sa.text('INSERT INTO band(id) VALUES(1)'))
        connection.execute(sa.text('INSERT INTO app_user(id) VALUES(1)'))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        connection.execute(sa.text("""INSERT INTO band_invite_link
            (id,band_id,token_hash,created_by_user_id,created_at,expires_at)
            VALUES(1,1,:digest,1,'2026-10-19','2026-10-20')"""), {'digest': 'a' * 64})
        connection.execute(sa.text("""INSERT INTO invite_resume_context
            (id,invite_link_id,context_hash,expected_email_hmac,created_at,expires_at)
            VALUES(1,1,:context,:email,'2026-10-19','2026-10-19 01:00:00')"""),
                           {'context': 'b' * 64, 'email': 'c' * 64})
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match='Refusing to discard'):
                migration.downgrade()
        connection.execute(sa.text('DELETE FROM band_invite_link WHERE id=1'))
        assert connection.execute(sa.text('SELECT count(*) FROM invite_resume_context')).scalar_one() == 0
        assert connection.execute(sa.text('SELECT count(*) FROM app_user')).scalar_one() == 1
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert 'band_invite_link' not in sa.inspect(connection).get_table_names()
        assert 'invite_resume_context' not in sa.inspect(connection).get_table_names()
    engine.dispose()


def test_context_creation_and_manager_rate_limit_prevent_mutations(invite_auth):
    app, signer = invite_auth
    client = app.test_client()
    headers = _auth(signer)
    created = client.post('/api/v2/bands/1/invite-links', json={}, headers=headers)
    assert created.status_code == 201
    for _ in range(9):
        assert client.post('/api/v2/bands/1/invite-links', json={}, headers=headers).status_code == 201
    before = None
    with app.app_context():
        before = BandInviteLink.query.count()
    limited = client.post('/api/v2/bands/1/invite-links', json={}, headers=headers)
    assert limited.status_code == 429
    assert limited.headers.get('Retry-After')
    with app.app_context():
        assert BandInviteLink.query.count() == before


def test_reusable_link_joins_two_accounts_and_completed_context_cannot_rejoin_after_leave(invite_auth):
    app, signer = invite_auth
    client = app.test_client()
    owner_headers = _auth(signer)
    issued = client.post('/api/v2/bands/1/invite-links', json={}, headers=owner_headers)
    token = issued.get_json()['token']
    owner_context = client.post('/api/v2/invite-links/resume-context',
                                json={'token': token, 'email': 'user@example.com'}).get_json()['context']
    assert client.post('/api/v2/invite-links/resume', json={'context': owner_context},
                       headers=owner_headers).status_code == 200
    owner_result = client.post('/api/v2/invite-links/accept', json={'context': owner_context},
                               headers=owner_headers).get_json()
    assert owner_result['already_member'] is True and owner_result['joined'] is False

    outsider_headers = _auth(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com')
    outsider_context = client.post('/api/v2/invite-links/resume-context',
                                   json={'token': token, 'email': 'outsider@example.com'}).get_json()['context']
    assert client.post('/api/v2/invite-links/resume', json={'context': outsider_context},
                       headers=outsider_headers).status_code == 200
    # A bound invitation is not sufficient to create an unnamed profile.
    for invalid in ({'context': outsider_context}, {'context': outsider_context, 'name': '  '},
                    {'context': outsider_context, 'name': 'bad\u200bname'}):
        assert client.post('/api/v2/invite-links/accept', json=invalid,
                           headers=outsider_headers).status_code == 400
    with app.app_context():
        outsider = AppUser.query.filter_by(email='outsider@example.com').one()
        assert BandMembership.query.filter_by(band_id=1, user_id=outsider.id).count() == 0
        assert InviteResumeContext.query.filter_by(context_hash=hashlib.sha256(
            outsider_context.encode('ascii')).hexdigest()).one().completed_at is None
    joined = client.post('/api/v2/invite-links/accept', json={'context': outsider_context, 'name': 'Invited Member'},
                         headers=outsider_headers)
    assert joined.status_code == 200
    assert joined.get_json()['joined'] is True
    assert joined.get_json()['membership'] == {'status': 'active', 'role': 'member'}
    with app.app_context():
        member = AppUser.query.filter_by(email='outsider@example.com').one()
        assert BandMembership.query.filter_by(band_id=1, user_id=member.id, status='active').one().role == 'member'
        assert Member.query.filter_by(band_id=1, linked_user_id=member.id).count() == 1
        assert Member.query.filter_by(band_id=1, linked_user_id=member.id).one().name == 'Invited Member'
        assert BandMembership.query.filter_by(band_id=1, user_id=member.id).one().requested_name is None
    # Completed retries may omit or change name but never rename the profile.
    assert client.post('/api/v2/invite-links/accept', json={'context': outsider_context},
                       headers=outsider_headers).status_code == 200
    assert client.post('/api/v2/invite-links/accept', json={'context': outsider_context, 'name': 'Rename attempt'},
                       headers=outsider_headers).status_code == 200
    with app.app_context():
        assert Member.query.filter_by(band_id=1, linked_user_id=member.id).one().name == 'Invited Member'
    assert client.post('/api/v2/bands/1/leave', headers=outsider_headers).status_code == 200
    replay = client.post('/api/v2/invite-links/accept', json={'context': outsider_context},
                         headers=outsider_headers)
    assert replay.status_code == 404
    with app.app_context():
        member = AppUser.query.filter_by(email='outsider@example.com').one()
        assert BandMembership.query.filter_by(band_id=1, user_id=member.id).one().status == 'left'


@pytest.mark.parametrize('status', ['pending', 'left', 'rejected'])
def test_accept_reactivates_inactive_membership_as_plain_member(invite_auth, status):
    app, signer = invite_auth
    with app.app_context():
        outsider = AppUser.query.filter_by(email='outsider@example.com').one()
        db.session.add(BandMembership(band_id=1, user_id=outsider.id, role='admin', status=status,
                                      requested_name='Old application name'))
        outsider_id = outsider.id
        db.session.commit()
    client = app.test_client()
    owner_headers = _auth(signer)
    token = client.post('/api/v2/bands/1/invite-links', json={}, headers=owner_headers).get_json()['token']
    context = client.post('/api/v2/invite-links/resume-context', json={
        'token': token, 'email': 'outsider@example.com',
    }).get_json()['context']
    headers = _auth(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com')
    assert client.post('/api/v2/invite-links/resume', json={'context': context}, headers=headers).status_code == 200
    accepted = client.post('/api/v2/invite-links/accept', json={'context': context, 'name': 'Returning Member'}, headers=headers)
    assert accepted.status_code == 200
    assert accepted.get_json()['membership'] == {'status': 'active', 'role': 'member'}
    with app.app_context():
        assert Member.query.filter_by(band_id=1, linked_user_id=outsider_id).one().name == 'Returning Member'
        assert BandMembership.query.filter_by(band_id=1, user_id=outsider_id).one().requested_name is None


def test_revoke_after_resume_and_expired_context_cannot_accept(invite_auth):
    app, signer = invite_auth
    client = app.test_client()
    owner_headers = _auth(signer)
    token = client.post('/api/v2/bands/1/invite-links', json={}, headers=owner_headers).get_json()['token']
    context = client.post('/api/v2/invite-links/resume-context', json={
        'token': token, 'email': 'outsider@example.com',
    }).get_json()['context']
    outsider_headers = _auth(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com')
    assert client.post('/api/v2/invite-links/resume', json={'context': context}, headers=outsider_headers).status_code == 200
    with app.app_context():
        link = BandInviteLink.query.one()
        link_id = link.id
    assert client.post(f'/api/v2/bands/1/invite-links/{link_id}/revoke',
                       json={}, headers=owner_headers).status_code == 200
    assert client.post('/api/v2/invite-links/accept', json={'context': context},
                       headers=outsider_headers).status_code == 404


@pytest.mark.parametrize('expired_target', ['context', 'link'])
def test_expired_context_and_link_are_generic_not_found(invite_auth, expired_target):
    from datetime import timedelta
    app, signer = invite_auth
    client = app.test_client()
    owner_headers = _auth(signer)
    token = client.post('/api/v2/bands/1/invite-links', json={}, headers=owner_headers).get_json()['token']
    context = client.post('/api/v2/invite-links/resume-context', json={
        'token': token, 'email': 'outsider@example.com',
    }).get_json()['context']
    outsider_headers = _auth(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com')
    assert client.post('/api/v2/invite-links/resume', json={'context': context}, headers=outsider_headers).status_code == 200
    with app.app_context():
        saved = InviteResumeContext.query.one()
        link = BandInviteLink.query.one()
        if expired_target == 'context':
            saved.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
            saved.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        else:
            link.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
            link.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()
    assert client.post('/api/v2/invite-links/accept', json={'context': context},
                       headers=outsider_headers).status_code == 404
    preview = client.post('/api/v2/invite-links/preview', json={'token': token})
    assert preview.status_code == (200 if expired_target == 'context' else 404)


def test_policy_gate_exempts_only_anonymous_preview_and_context_start(invite_auth):
    app, signer = invite_auth
    client = app.test_client()
    headers = _auth(signer)
    token = client.post('/api/v2/bands/1/invite-links', json={}, headers=headers).get_json()['token']
    app.config.update({
        'POLICY_ACCEPTANCE_REQUIRED': True,
        'POLICY_NEW_ACCOUNT_CUTOFF': '2000-01-01T00:00:00+00:00',
        'SERVICE_NAME': 'Test', 'CONTACT_EMAIL': 'contact@example.test', 'OPERATOR_NAME': 'Test operator',
        'POLICY_TERMS_VERSION': 'terms-1', 'POLICY_PRIVACY_VERSION': 'privacy-1',
        'POLICY_TERMS_URL': 'https://example.test/terms', 'POLICY_PRIVACY_URL': 'https://example.test/privacy',
        'POLICY_RETENTION_NOTICE': 'Test retention', 'POLICY_PROVIDERS_NOTICE': 'Test providers',
    })
    assert client.post('/api/v2/invite-links/preview', json={'token': token}).status_code == 200
    started = client.post('/api/v2/invite-links/resume-context',
                          json={'token': token, 'email': 'user@example.com'})
    assert started.status_code == 201
    blocked = client.post('/api/v2/invite-links/resume', json={'context': started.get_json()['context']},
                          headers=headers)
    assert blocked.status_code == 403
    assert blocked.get_json()['code'] == 'policy_acceptance_required'


def _add_actor(app, subject, email, *, platform_admin=False):
    with app.app_context():
        actor = AppUser(supabase_user_id=subject, email=email, email_verified=True,
                        is_platform_admin=platform_admin)
        db.session.add(actor)
        db.session.flush()
        actor_id = actor.id
        db.session.commit()
        return actor_id


def test_owner_and_same_band_admin_can_issue_list_and_revoke(invite_auth):
    app, signer = invite_auth
    admin_id = _add_actor(app, 'admin-subject', 'admin@example.com')
    with app.app_context():
        db.session.add(BandMembership(band_id=1, user_id=admin_id, role='admin', status='active'))
        db.session.commit()
    client = app.test_client()
    owner = _auth(signer)
    admin = _auth(signer, 'admin-subject', 'admin@example.com')

    owner_created = client.post('/api/v2/bands/1/invite-links', json={}, headers=owner)
    assert owner_created.status_code == 201
    owner_link_id = owner_created.get_json()['invite_link']['id']
    admin_created = client.post('/api/v2/bands/1/invite-links', json={}, headers=admin)
    assert admin_created.status_code == 201
    admin_link_id = admin_created.get_json()['invite_link']['id']
    listed = client.get('/api/v2/bands/1/invite-links', headers=admin)
    assert listed.status_code == 200
    assert {item['id'] for item in listed.get_json()['invite_links']} == {owner_link_id, admin_link_id}
    assert all(item['created_at'].endswith('+00:00') and item['expires_at'].endswith('+00:00')
               for item in listed.get_json()['invite_links'])
    revoked = client.post(f'/api/v2/bands/1/invite-links/{admin_link_id}/revoke',
                          json={}, headers=admin)
    assert revoked.status_code == 200
    assert revoked.get_json()['invite_link']['revoked_at'] is not None


@pytest.mark.parametrize('actor_kind', ['same_band_member', 'foreign_band_owner', 'nonmember', 'platform_only_admin'])
def test_invite_management_rejects_nonmanagers_without_side_effects(invite_auth, actor_kind):
    app, signer = invite_auth
    subject = f'{actor_kind}-subject'
    email = f'{actor_kind}@example.com'
    actor_id = _add_actor(app, subject, email, platform_admin=actor_kind == 'platform_only_admin')
    with app.app_context():
        if actor_kind == 'same_band_member':
            db.session.add(BandMembership(band_id=1, user_id=actor_id, role='member', status='active'))
        elif actor_kind == 'foreign_band_owner':
            foreign = Band(name='Other Band', slug='other-band')
            db.session.add(foreign)
            db.session.flush()
            db.session.add(BandMembership(band_id=foreign.id, user_id=actor_id, role='owner', status='active'))
        db.session.commit()
        before = BandInviteLink.query.filter_by(band_id=1).count()
    headers = _auth(signer, subject, email)
    client = app.test_client()
    issue = client.post('/api/v2/bands/1/invite-links', json={}, headers=headers)
    listing = client.get('/api/v2/bands/1/invite-links', headers=headers)
    assert issue.status_code in (403, 404)
    assert listing.status_code in (403, 404)
    if actor_kind == 'same_band_member':
        owned_link = client.post('/api/v2/bands/1/invite-links', json={}, headers=_auth(signer))
        assert owned_link.status_code == 201
        link_id = owned_link.get_json()['invite_link']['id']
        before += 1
        existing = client.post(f'/api/v2/bands/1/invite-links/{link_id}/revoke', json={}, headers=headers)
        missing = client.post(f'/api/v2/bands/1/invite-links/{link_id + 1000}/revoke', json={}, headers=headers)
        assert existing.status_code == missing.status_code == 403
        assert existing.get_json() == missing.get_json()
    with app.app_context():
        assert BandInviteLink.query.filter_by(band_id=1).count() == before


def test_invalid_authorization_context_start_and_bad_bodies_do_not_create_rows(invite_auth):
    app, signer = invite_auth
    client = app.test_client()
    token = client.post('/api/v2/bands/1/invite-links', json={}, headers=_auth(signer)).get_json()['token']
    bad_auth = client.post('/api/v2/invite-links/resume-context',
                           json={'token': token, 'email': 'user@example.com'},
                           headers={'Authorization': 'Bearer not-a-jwt'})
    assert bad_auth.status_code == 401
    malformed = client.post('/api/v2/invite-links/resume-context', data='{"token":',
                            content_type='application/json')
    oversized = client.post('/api/v2/invite-links/resume-context',
                            data=' ' * 8193, content_type='application/json')
    invalid_context = client.post('/api/v2/invite-links/resume', json={'context': 'not-canonical'},
                                  headers=_auth(signer))
    assert malformed.status_code == 400
    assert oversized.status_code == 413
    assert invalid_context.status_code == 404
    with app.app_context():
        assert InviteResumeContext.query.count() == 0


def test_deleted_band_makes_preview_resume_and_accept_generic_not_found(invite_auth):
    app, signer = invite_auth
    client = app.test_client()
    owner = _auth(signer)
    outsider = _auth(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com')
    token = client.post('/api/v2/bands/1/invite-links', json={}, headers=owner).get_json()['token']
    context_response = client.post('/api/v2/invite-links/resume-context',
                                   json={'token': token, 'email': 'outsider@example.com'})
    assert context_response.status_code == 201
    context = context_response.get_json()['context']
    assert client.post('/api/v2/invite-links/resume', json={'context': context},
                       headers=outsider).status_code == 200
    with app.app_context():
        band = db.session.get(Band, 1)
        band.deleted_at = datetime.now(timezone.utc)
        db.session.commit()
        before_memberships = BandMembership.query.filter_by(band_id=1).count()
    assert client.post('/api/v2/invite-links/preview', json={'token': token}).status_code == 404
    assert client.post('/api/v2/invite-links/resume', json={'context': context},
                       headers=outsider).status_code == 404
    assert client.post('/api/v2/invite-links/accept', json={'context': context},
                       headers=outsider).status_code == 404
    with app.app_context():
        assert BandMembership.query.filter_by(band_id=1).count() == before_memberships
        assert BandMembership.query.filter_by(band_id=1, user_id=2).one_or_none() is None


def test_revoke_hides_band_and_link_existence_from_nonmembers(invite_auth):
    app, signer = invite_auth
    owner = _auth(signer)
    outsider = _auth(signer, '77777777-7777-7777-7777-777777777777', 'outsider@example.com')
    client = app.test_client()
    issued = client.post('/api/v2/bands/1/invite-links', json={}, headers=owner)
    assert issued.status_code == 201
    existing_link_id = issued.get_json()['invite_link']['id']
    with app.app_context():
        private_band = Band(name='Another Private Band', slug='band-private-b')
        db.session.add(private_band)
        db.session.flush()
        private_band_id = private_band.id
        private_link = BandInviteLink(
            band_id=private_band.id,
            token_hash=hashlib.sha256(b'not-a-secret-token').hexdigest(),
            created_by_user_id=1,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + __import__('datetime').timedelta(hours=24),
        )
        db.session.add(private_link)
        db.session.commit()
        private_link_id = private_link.id

    attempts = [
        (1, existing_link_id),
        (1, existing_link_id + 1000),
        (private_band_id, private_link_id),
        (private_band_id, private_link_id + 1000),
        (999_999, existing_link_id),
        (999_999, existing_link_id + 1000),
    ]
    responses = [client.post(f'/api/v2/bands/{band_id}/invite-links/{link_id}/revoke',
                             json={}, headers=outsider)
                 for band_id, link_id in attempts]
    assert all(response.status_code == 404 for response in responses)
    assert all(response.get_json() == responses[0].get_json() for response in responses[1:])
