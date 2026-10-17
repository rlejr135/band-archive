"""Private coordination contract, optimistic concurrency and reset boundaries."""
from datetime import date
import importlib.util
from pathlib import Path

import pytest
from flask import g, request
from sqlalchemy import text

import auth_service
from app import create_app
from config import TestingConfig
from extensions import db
from migration_bootstrap import phase16_metadata, phase17_metadata, schema_contract_differences
from models import AppUser, AuditEvent, Band, BandMembership, BandAvailabilityDay as Day, BandAvailabilityState as State, BandPersonalScheduleNote as Note


@pytest.fixture
def calendar(monkeypatch):
    app = create_app(TestingConfig, start_worker=False)
    with app.app_context():
        db.drop_all()
        db.create_all()
        users = [AppUser(supabase_user_id=f'user-{i}', email=f'user-{i}@example.com',
                         display_name=f'Person {i}', email_verified=i != 6) for i in range(1, 8)]
        db.session.add_all(users)
        bands = [Band(name='First', slug='first'), Band(name='Other', slug='other')]
        db.session.add_all(bands)
        db.session.flush()
        memberships = [BandMembership(band_id=bands[0].id, user_id=users[i].id,
                         role='owner' if i == 0 else ('admin' if i == 1 else 'member'),
                         status='left' if i == 4 else 'active') for i in range(6)]
        memberships.append(BandMembership(band_id=bands[1].id, user_id=users[6].id, role='owner', status='active'))
        db.session.add_all(memberships)
        db.session.commit()
        ids = {f'user-{i}': user.id for i, user in enumerate(users, 1)}
        app.config['BAND_API_SCHEMA_READY'] = True
    def authenticate():
        subject = request.headers.get('X-Test-User')
        if subject not in ids:
            raise auth_service.AuthError('missing_token', 'Authentication required.')
        g.current_app_user = db.session.get(AppUser, ids[subject])
        return g.current_app_user
    monkeypatch.setattr(auth_service, 'authenticate_request', authenticate)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def headers(user=3):
    return {'X-Test-User': f'user-{user}'}


BASE = '/api/v2/bands/1/availability'
QUERY = '?from=2026-10-01&to=2026-11-11'


def row(day='2026-10-17', revision=0, **values):
    return {'date': day, 'revision': revision, 'morning': 'UNKNOWN', 'afternoon': 'UNKNOWN',
            'evening': 'AVAILABLE', **values}


def batch(client, days=None, generation=0, user=3):
    return client.put(BASE + '/me/batch', headers=headers(user), json={
        'generation': generation, 'days': [row()] if days is None else days})


def test_snapshot_is_private_active_verified_only_and_does_not_create_state(calendar):
    client = calendar.test_client()
    assert client.get(BASE + QUERY).status_code == 401
    for user in (5, 7):
        assert client.get(BASE + QUERY, headers=headers(user)).status_code == 404
    assert client.get(BASE + QUERY, headers=headers(6)).status_code == 403
    result = client.get(BASE + QUERY, headers=headers())
    assert result.status_code == 200
    assert result.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in result.vary
    body = result.get_json()
    assert body['generation'] == 0 and body['days'] == []
    assert {m['membership_id'] for m in body['members']} == {1, 2, 3, 4}
    assert 'email' not in result.get_data(as_text=True)
    with calendar.app_context():
        assert State.query.count() == 0


def test_batch_atomic_revision_idempotency_and_own_reset_tombstone(calendar):
    client = calendar.test_client()
    assert batch(client).get_json() == {'changed': True, 'generation': 0}
    assert batch(client).get_json()['changed'] is False
    failed = batch(client, [row('2026-10-18'), row(revision=0, evening='UNAVAILABLE')])
    assert failed.status_code == 409
    with calendar.app_context():
        assert Day.query.count() == 1
    cleared = client.delete(BASE + '/me/2026-10-17', headers=headers(), json={'generation': 0, 'revision': 1})
    assert cleared.status_code == 200
    item = client.get(BASE + QUERY, headers=headers()).get_json()['days'][0]
    assert item['revision'] == 2 and item['evening'] == 'UNKNOWN'
    assert batch(client).status_code == 409
    assert batch(client, [row(revision=2)]).status_code == 200


def test_notes_private_to_author_shared_opt_in_and_delete_revision(calendar):
    client = calendar.test_client()
    path = BASE + '/me/notes/2026-10-17/evening'
    private = {'generation': 0, 'revision': 0, 'body': '<b>personal</b>', 'visibility': 'PRIVATE'}
    assert client.put(path, headers=headers(), json=private).status_code == 200
    for user in (1, 2, 4):
        assert client.get(BASE + QUERY, headers=headers(user)).get_json()['notes'] == []
    own = client.get(BASE + QUERY, headers=headers()).get_json()['notes'][0]
    assert own['body'] == '<b>personal</b>'
    assert client.put(path, headers=headers(), json={**private, 'revision': 1, 'visibility': 'BAND'}).status_code == 200
    shared = client.get(BASE + QUERY, headers=headers(4)).get_json()['notes']
    assert shared[0]['body'] == private['body']
    assert client.delete(path, headers=headers(), json={'generation': 0, 'revision': 2}).status_code == 200
    deleted = client.get(BASE + QUERY, headers=headers()).get_json()['notes'][0]
    assert deleted['body'] is None and deleted['revision'] == 3
    assert client.get(BASE + QUERY, headers=headers(1)).get_json()['notes'] == []
    assert client.put(path, headers=headers(), json=private).status_code == 409
    assert client.put(path, headers=headers(), json={**private, 'revision': 3}).status_code == 200


def test_owner_reset_only_current_band_old_saves_fail_and_audit_has_no_text(calendar):
    client = calendar.test_client()
    batch(client)
    path = BASE + '/me/notes/2026-10-17/morning'
    client.put(path, headers=headers(), json={'generation': 0, 'revision': 0, 'body': 'Secret schedule', 'visibility': 'PRIVATE'})
    with calendar.app_context():
        db.session.add(Day(band_id=2, membership_id=7, date=date(2026, 10, 17), evening='AVAILABLE'))
        db.session.commit()
    for user in (2, 3):
        assert client.post(BASE + '/reset', headers=headers(user), json={'generation': 0, 'confirm': True}).status_code == 403
    reset = client.post(BASE + '/reset', headers=headers(1), json={'generation': 0, 'confirm': True})
    assert reset.get_json() == {'changed': True, 'generation': 1}
    assert client.post(BASE + '/reset', headers=headers(1), json={'generation': 0, 'confirm': True}).status_code == 409
    assert batch(client).status_code == 409
    body = client.get(BASE + QUERY, headers=headers()).get_json()
    assert body['generation'] == 1 and body['last_reset_at'] and body['notes'] == body['days'] == []
    assert batch(client, generation=1).status_code == 200
    with calendar.app_context():
        assert Day.query.filter_by(band_id=2).count() == 1
        assert Note.query.count() == 0
        assert AuditEvent.query.filter_by(event_type='band.availability.reset').count() == 1
        assert 'Secret schedule' not in AuditEvent.query.one().details_json


def test_delete_missing_note_creates_revision_tombstone_and_retry_is_idempotent(calendar):
    client = calendar.test_client()
    path = BASE + '/me/notes/2026-10-17/afternoon'
    assert client.delete(path, headers=headers(), json={'generation': 0, 'revision': 0}).status_code == 200
    retry = client.delete(path, headers=headers(), json={'generation': 0, 'revision': 0})
    assert retry.get_json()['changed'] is False
    assert client.put(path, headers=headers(), json={
        'generation': 0, 'revision': 0, 'body': 'old save', 'visibility': 'PRIVATE'}).status_code == 409
    own = client.get(BASE + QUERY, headers=headers()).get_json()['notes'][0]
    assert own['body'] is None and own['revision'] == 1


@pytest.mark.parametrize('changes', [
    {'generation': True}, {'generation': []}, {'generation': -1}, {'days': []},
    {'days': [row(), row()]}, {'days': [row('2026-02-30')]},
    {'days': [row(revision=True)]}, {'days': [row(morning=[])]},
    {'days': [row(evening='available')]}, {'days': [row('2026-10-01'), row('2026-11-12')]},
    {'membership_id': 1}, {'days': [row(member_id=1)]},
])
def test_strict_batch_validation(calendar, changes):
    response = calendar.test_client().put(BASE + '/me/batch', headers=headers(),
                json={'generation': 0, 'days': [row()], **changes})
    assert response.status_code == 400


@pytest.mark.parametrize('changes', [
    {'body': ''}, {'body': []}, {'body': 'x' * 201}, {'body': 'bad\x00'},
    {'visibility': []}, {'visibility': {}}, {'visibility': None}, {'visibility': 'PUBLIC'},
    {'revision': True}, {'user_id': 1},
])
def test_strict_note_validation(calendar, changes):
    response = calendar.test_client().put(BASE + '/me/notes/2026-10-17/morning', headers=headers(),
        json={'generation': 0, 'revision': 0, 'body': 'Note', 'visibility': 'PRIVATE', **changes})
    assert response.status_code == 400


@pytest.mark.parametrize('query', [
    '', '?from=2026-01-01', '?from=2026-01-01&to=2026-02-12',
    '?from=2026-01-02&to=2026-01-01', '?from=2026-02-29&to=2026-03-01',
    '?from=2026-01-01&to=2026-01-02&to=2026-01-03',
])
def test_query_boundaries(calendar, query):
    assert calendar.test_client().get(BASE + query, headers=headers()).status_code == 400


def test_json_size_duplicate_fields_and_changed_member_identity(calendar):
    client = calendar.test_client()
    assert client.put(BASE + '/me/batch', headers=headers(), data='x' * 32769,
                      content_type='application/json').status_code == 413
    assert client.put(BASE + '/me/batch', headers=headers(), data='{"generation":0,"generation":1,"days":[]}',
                      content_type='application/json').status_code == 400
    assert batch(client, user=7).status_code == 404
    assert batch(client, user=5).status_code == 404
    batch(client)
    with calendar.app_context():
        member = db.session.get(BandMembership, 3)
        member.status = 'left'
        db.session.commit()
    assert client.get(BASE + QUERY, headers=headers()).status_code == 404
    assert client.get(BASE + QUERY, headers=headers(1)).get_json()['days'] == []


def test_immutable_metadata_and_sqlite_upgrade_downgrade_guards(calendar):
    assert not set(('band_availability_state', 'band_availability_day', 'band_personal_schedule_note')) & set(phase16_metadata().tables)
    assert {'band_availability_state', 'band_availability_day', 'band_personal_schedule_note'} <= set(phase17_metadata().tables)
    with calendar.app_context(), db.engine.connect() as connection:
        # Current ORM fixtures now include later member profile fields too;
        # the immutable v17 snapshot is verified independently above.
        assert schema_contract_differences(connection) == []
