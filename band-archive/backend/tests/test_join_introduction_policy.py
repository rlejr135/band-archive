from datetime import datetime, timedelta, timezone
import pytest
from flask import g, request

import auth_service
from app import create_app
from config import TestingConfig
from extensions import db
from models import AppUser, AuditEvent, Band, BandMembership, PolicyAcceptanceReceipt
from migration_bootstrap import phase15_metadata, phase16_metadata


@pytest.fixture
def band_app(monkeypatch):
    app = create_app(TestingConfig, start_worker=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
        app.config['BAND_API_SCHEMA_READY'] = True
        users = [
            AppUser(supabase_user_id='admin-id', email='admin@example.com', is_platform_admin=True,
                    email_verified=True),
            AppUser(supabase_user_id='owner-id', email='owner@example.com', email_verified=True),
            AppUser(supabase_user_id='member-id', email='member@example.com', email_verified=True),
            AppUser(supabase_user_id='other-id', email='other@example.com', email_verified=True),
            AppUser(supabase_user_id='second-manager-id', email='second-manager@example.com', email_verified=True),
        ]
        db.session.add_all(users)
        db.session.commit()
        identities = {user.supabase_user_id: user.id for user in users}

    def authenticate_for_test():
        subject = request.headers.get('X-Test-User')
        if subject not in identities:
            raise auth_service.AuthError('missing_token', 'Bearer authentication is required.')
        g.current_app_user = db.session.get(AppUser, identities[subject])
        return g.current_app_user

    monkeypatch.setattr(auth_service, 'authenticate_request', authenticate_for_test)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _headers(subject):
    return {'X-Test-User': subject}


def test_revision_15_snapshot_excludes_policy_additions_and_revision_16_includes_them():
    old = phase15_metadata()
    new = phase16_metadata()
    assert 'join_introduction' not in old.tables['band_membership'].c
    assert 'policy_acceptance_receipt' not in old.tables
    assert 'join_introduction' in new.tables['band_membership'].c
    assert new.tables['band_membership'].c.join_introduction.type.length == 300
    assert 'policy_acceptance_receipt' in new.tables


def _create_band(client):
    pending = client.post('/api/v2/band-creation-requests', headers=_headers('owner-id'), json={
        'name': 'Intro Band', 'slug': 'intro-band',
    })
    assert pending.status_code == 201
    approved = client.post('/api/v2/platform/band-creation-requests/1/approve', headers=_headers('admin-id'))
    assert approved.status_code == 200
    return approved.get_json()['request']['band_id']


def test_join_introduction_is_manager_pending_only_and_duplicate_preserves_first(band_app):
    client = band_app.test_client()
    band_id = _create_band(client)
    intro = '  Hello, we play jazz.  '
    response = client.post(f'/api/v2/bands/{band_id}/join-requests', headers=_headers('member-id'),
                           json={'name': 'New Member', 'introduction': intro})
    assert response.status_code == 201
    membership_id = response.get_json()['membership']['id']
    duplicate = client.post(f'/api/v2/bands/{band_id}/join-requests', headers=_headers('member-id'),
                            json={'name': 'Do not overwrite', 'introduction': 'Overwrite attempt'})
    assert duplicate.status_code == 200
    assert duplicate.get_json()['created'] is False

    manager = client.get(f'/api/v2/bands/{band_id}/memberships', headers=_headers('owner-id')).get_json()
    requested = next(item for item in manager['memberships'] if item['id'] == membership_id)
    assert requested['introduction'] == 'Hello, we play jazz.'
    with band_app.app_context():
        other = AppUser.query.filter_by(supabase_user_id='other-id').one()
        db.session.add(BandMembership(band_id=band_id, user_id=other.id, status='active'))
        db.session.commit()
    member_view = client.get(f'/api/v2/bands/{band_id}/memberships', headers=_headers('member-id')).get_json()
    assert member_view == {'error': 'Band not found'}
    roster = client.get(f'/api/v2/bands/{band_id}/memberships', headers=_headers('other-id')).get_json()
    assert all('introduction' not in item for item in roster['memberships'])
    pending_self = client.get('/api/v2/bands', headers=_headers('member-id')).get_json()['pending_memberships']
    assert all('introduction' not in item for item in pending_self)
    with band_app.app_context():
        membership = db.session.get(BandMembership, membership_id)
        assert membership.join_introduction == 'Hello, we play jazz.'
        assert AuditEvent.query.filter(AuditEvent.details_json.contains('Hello, we play jazz.')).count() == 0

    rejected = client.patch(f'/api/v2/memberships/{membership_id}', headers=_headers('owner-id'), json={'action': 'reject'})
    assert rejected.status_code == 200
    with band_app.app_context():
        assert db.session.get(BandMembership, membership_id).join_introduction is None
    rejoin = client.post(f'/api/v2/bands/{band_id}/join-requests', headers=_headers('member-id'),
                         json={'name': 'Returning Member', 'introduction': 'New request'})
    assert rejoin.status_code == 201
    manager = client.get(f'/api/v2/bands/{band_id}/memberships', headers=_headers('owner-id')).get_json()
    assert next(item for item in manager['memberships'] if item['id'] == membership_id)['introduction'] == 'New request'
    retired = client.post(f'/api/v2/bands/{band_id}/invite-codes', headers=_headers('owner-id'))
    assert retired.status_code == 410
    with band_app.app_context():
        membership = db.session.get(BandMembership, membership_id)
        assert membership.status == 'pending'
        assert membership.join_introduction == 'New request'


def test_join_introduction_manager_audience_is_band_scoped_and_approval_clears(band_app):
    client = band_app.test_client()
    band_a = _create_band(client)
    request_a = client.post(f'/api/v2/bands/{band_a}/join-requests', headers=_headers('member-id'),
                            json={'name': 'Applicant A', 'introduction': 'Private request A'}).get_json()['membership']
    with band_app.app_context():
        other = AppUser.query.filter_by(supabase_user_id='other-id').one()
        membership = db.session.get(BandMembership, request_a['id'])
        membership.status = 'pending'
        membership.join_introduction = 'Private request A'
        db.session.add(BandMembership(band_id=band_a, user_id=other.id, role='admin', status='active'))
        db.session.commit()
    admin_view = client.get(f'/api/v2/bands/{band_a}/memberships', headers=_headers('other-id')).get_json()
    assert next(item for item in admin_view['memberships'] if item['id'] == request_a['id'])['introduction'] == 'Private request A'

    band_b = client.post('/api/v2/band-creation-requests', headers=_headers('second-manager-id'), json={
        'name': 'Second Band', 'slug': 'second-band',
    })
    assert band_b.status_code == 201
    band_b_id = client.post('/api/v2/platform/band-creation-requests/2/approve',
                            headers=_headers('admin-id')).get_json()['request']['band_id']
    second_band_manager = client.get(f'/api/v2/bands/{band_b_id}/memberships',
                                     headers=_headers('second-manager-id'))
    assert second_band_manager.status_code == 200
    assert client.get(f'/api/v2/bands/{band_a}/memberships', headers=_headers('second-manager-id')).status_code == 404
    # The manager in another band and the platform admin without membership
    # cannot inspect this band's pending introduction.
    outsider = client.get(f'/api/v2/bands/{band_a}/memberships', headers=_headers('admin-id'))
    assert outsider.status_code == 404

    approved = client.patch(f'/api/v2/memberships/{request_a["id"]}', headers=_headers('owner-id'),
                            json={'action': 'approve'})
    assert approved.status_code == 200
    with band_app.app_context():
        assert db.session.get(BandMembership, request_a['id']).join_introduction is None


def test_join_introduction_validation_is_bounded_and_backward_compatible(band_app):
    client = band_app.test_client()
    band_id = _create_band(client)
    assert client.post(f'/api/v2/bands/{band_id}/join-requests', headers=_headers('member-id'), json={'name': 'Applicant'}).status_code == 201
    other_band = Band(name='Other', slug='other-band')
    with band_app.app_context():
        db.session.add(other_band)
        db.session.commit()
        other_id = other_band.id
    for body in ({'name': 'Applicant', 'introduction': 12}, {'name': 'Applicant', 'introduction': 'x' * 301}, {'name': 'Applicant', 'unexpected': 'no'}):
        response = client.post(f'/api/v2/bands/{other_id}/join-requests', headers=_headers('other-id'), json=body)
        assert response.status_code == 400
    assert client.post(f'/api/v2/bands/{other_id}/join-requests', headers=_headers('other-id'),
                       json={'name': 'Applicant', 'introduction': 'bad\x00text'}).status_code == 400
    oversized = client.post(f'/api/v2/bands/{other_id}/join-requests', headers=_headers('other-id'),
                            data=b' ' * 4097, content_type='application/json')
    assert oversized.status_code == 413


def _enable_policy_gate(app, cutoff=None):
    app.config.update({
        'POLICY_ACCEPTANCE_REQUIRED': True,
        'POLICY_NEW_ACCOUNT_CUTOFF': cutoff or (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        'POLICY_TERMS_VERSION': 'terms-1',
        'POLICY_PRIVACY_VERSION': 'privacy-1',
        'POLICY_TERMS_URL': 'https://policy.test/terms',
        'POLICY_PRIVACY_URL': 'https://policy.test/privacy',
        'POLICY_RETENTION_NOTICE': 'Retention details approved for test.',
        'POLICY_PROVIDERS_NOTICE': 'Provider details approved for test.',
        'OPERATOR_NAME': 'Test Operator',
        'CONTACT_EMAIL': 'contact@example.test',
        'SERVICE_NAME': 'Band Archive Test',
    })


def test_policy_config_is_public_but_readiness_and_gate_default_off(band_app):
    response = band_app.test_client().get('/api/v2/policy')
    assert response.status_code == 200
    body = response.get_json()
    assert body['policies']['ready'] is False
    assert body['policies']['policy_acceptance_enabled'] is False
    assert body['policies']['retention_notice'] == ''
    assert body['service']['contact_email'] == 'rlejr135@gmail.com'
    assert body['service']['sender_email'] == 'rlejr135@gmail.com'
    assert 'secret' not in str(body).lower()


def test_policy_acceptance_receipt_is_server_owned_and_gates_only_new_local_users(band_app):
    client = band_app.test_client()
    _enable_policy_gate(band_app)
    contact_override = band_app.test_client().get('/api/v2/policy').get_json()
    assert contact_override['service']['contact_email'] == 'contact@example.test'
    assert contact_override['service']['sender_email'] == 'contact@example.test'
    band_app.config['SENDER_EMAIL'] = 'notifications@example.test'
    explicit_sender = band_app.test_client().get('/api/v2/policy').get_json()
    assert explicit_sender['service']['sender_email'] == 'notifications@example.test'
    assert client.post('/api/v2/bands/999/join-requests', headers=_headers('member-id'), json={}).status_code == 403
    blocked = client.post('/api/v2/bands/999/join-requests', headers=_headers('member-id'), json={})
    assert blocked.get_json()['code'] == 'policy_acceptance_required'
    assert client.get('/api/v2/policy').get_json()['policies']['policy_acceptance_enabled'] is True

    accepted = client.post('/api/v2/me/policy-acceptance', headers=_headers('member-id'), json={
        'terms_version': 'terms-1', 'privacy_version': 'privacy-1',
        'age_14_plus': True, 'terms_accepted': True,
    })
    assert accepted.status_code == 201
    assert accepted.get_json()['accepted'] is True
    assert client.get('/api/v2/me', headers=_headers('member-id')).get_json()['user']['policy_acceptance_required'] is False
    duplicate = client.post('/api/v2/me/policy-acceptance', headers=_headers('member-id'), json={
        'terms_version': 'terms-1', 'privacy_version': 'privacy-1',
        'age_14_plus': True, 'terms_accepted': True,
    })
    assert duplicate.status_code == 200 and duplicate.get_json()['created'] is False
    assert duplicate.get_json()['accepted_at'] == accepted.get_json()['accepted_at']
    with band_app.app_context():
        receipt = db.session.get(PolicyAcceptanceReceipt, AppUser.query.filter_by(supabase_user_id='member-id').one().id)
        assert receipt.terms_accepted is True and receipt.age_14_plus is True
        assert receipt.accepted_at is not None


def test_policy_acceptance_rejects_mismatch_and_does_not_force_existing_users(band_app):
    client = band_app.test_client()
    _enable_policy_gate(band_app)
    existing = client.post('/api/v2/me/policy-acceptance', headers=_headers('member-id'), json={
        'terms_version': 'terms-1', 'privacy_version': 'wrong',
        'age_14_plus': True, 'terms_accepted': True,
    })
    assert existing.status_code == 409
    assert existing.get_json()['code'] == 'policy_version_mismatch'
    assert client.post('/api/v2/me/policy-acceptance', headers=_headers('member-id'), json={
        'terms_version': 'terms-1', 'privacy_version': 'privacy-1',
        'age_14_plus': False, 'terms_accepted': True,
    }).status_code == 400
    for body in (
        {'terms_version': ['terms-1'], 'privacy_version': 'privacy-1', 'age_14_plus': True, 'terms_accepted': True},
        {'terms_version': 'terms-1', 'privacy_version': 'privacy-1', 'age_14_plus': True,
         'terms_accepted': True, 'extra': 'ignored? no'},
        {'terms_version': 'terms-1', 'privacy_version': 'privacy-1', 'age_14_plus': True},
    ):
        assert client.post('/api/v2/me/policy-acceptance', headers=_headers('member-id'), json=body).status_code == 400
    with band_app.app_context():
        user = AppUser.query.filter_by(supabase_user_id='other-id').one()
        user.created_at = datetime.now(timezone.utc) - timedelta(days=30)
        db.session.commit()
    # The old local account remains able to use writes without being asked for
    # a receipt; the invalid band still returns its ordinary not-found status.
    response = client.post('/api/v2/bands/999/join-requests', headers=_headers('other-id'), json={'name': 'Applicant'})
    assert response.status_code == 404


def test_policy_configuration_fails_closed_when_enabled_without_public_facts_or_captcha_key():
    class MissingPolicyFacts(TestingConfig):
        POLICY_ACCEPTANCE_REQUIRED = True

    with pytest.raises(RuntimeError, match='Policy acceptance is enabled'):
        create_app(MissingPolicyFacts, start_worker=False)

    class MissingCaptchaKey(TestingConfig):
        CAPTCHA_ENABLED = True
        CAPTCHA_SITE_KEY = ''

    with pytest.raises(RuntimeError, match='CAPTCHA is enabled'):
        create_app(MissingCaptchaKey, start_worker=False)


def test_enabled_policy_gate_exempts_only_existing_upload_session_operations(band_app):
    client = band_app.test_client()
    _enable_policy_gate(band_app)
    headers = _headers('member-id')
    initiate_paths = (
        '/api/v2/bands/999/uploads/direct/initiate',
        '/api/v2/bands/999/gallery/direct/initiate',
        '/api/v2/bands/999/uploads/multipart/initiate',
    )
    for path in initiate_paths:
        response = client.post(path, headers=headers, json={})
        assert response.status_code == 403
        assert response.get_json()['code'] == 'policy_acceptance_required'

    completion_paths = (
        '/api/v2/bands/999/uploads/direct/missing/complete',
        '/api/v2/bands/999/gallery/direct/missing/complete',
        '/api/v2/bands/999/uploads/multipart/missing/parts',
        '/api/v2/bands/999/uploads/multipart/missing/parts/1/ack',
        '/api/v2/bands/999/uploads/multipart/missing/complete',
        '/api/v2/bands/999/uploads/multipart/missing/abort',
    )
    for path in completion_paths:
        response = client.post(path, headers=headers, json={})
        assert response.status_code != 403 or response.get_json().get('code') != 'policy_acceptance_required'
    endpoint_names = {rule.endpoint for rule in band_app.url_map.iter_rules()}
    assert {
        'uploads.v2_complete_direct_upload', 'uploads.v2_complete_direct_gallery_upload',
        'uploads.v2_presign_multipart_part', 'uploads.v2_acknowledge_multipart_part',
        'uploads.v2_complete_multipart_upload', 'uploads.v2_abort_multipart_upload',
    }.issubset(endpoint_names)

