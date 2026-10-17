"""Legacy dashboard aggregate visibility and operator deletion guards."""

import json

import pytest

from extensions import db
from models import AppUser, Band, BandMembership, Song
from tests.test_auth import _install_jwks, _jwk
from tests.test_deletion_retry_contracts import OPERATOR_SUB, headers
from tests.test_durable_deletion_tasks import OWNER_SUB, deletion_app, signer


@pytest.fixture
def authenticated_client(deletion_app, signer):
    _install_jwks(deletion_app, [[_jwk(signer, 'key-1')]])
    return deletion_app.test_client()


@pytest.fixture
def dashboard_band(deletion_app):
    with deletion_app.app_context():
        band = Band.query.filter_by(slug='band-a').one()
        band.is_legacy_import = True
        band.default_visibility = 'PUBLIC'
        public_song = Song.query.filter_by(band_id=band.id).one()
        public_song.visibility = 'PUBLIC'
        public_song.status = 'Practice'
        private_song = Song(
            band_id=band.id, title='Private', artist='Band',
            visibility='MEMBERS', status='Completed',
        )
        db.session.add(private_song)
        db.session.commit()
        return band.id


def test_legacy_dashboard_counts_only_public_songs_for_visitors_and_left_members(
        deletion_app, authenticated_client, signer, dashboard_band):
    client = authenticated_client
    anonymous = client.get('/dashboard/stats')
    assert anonymous.status_code == 200
    assert anonymous.get_json() == {
        'total_songs': 1,
        'status_counts': {'Practice': 1},
    }

    with deletion_app.app_context():
        user = AppUser.query.filter_by(supabase_user_id=OWNER_SUB).one()
        BandMembership.query.filter_by(band_id=dashboard_band, user_id=user.id).one().status = 'left'
        db.session.commit()
    left = client.get('/dashboard/stats', headers=headers(signer, OWNER_SUB))
    assert left.status_code == 200
    assert left.get_json() == anonymous.get_json()


def test_legacy_dashboard_active_member_sees_all_songs_but_private_band_is_empty_for_visitors(
        deletion_app, authenticated_client, signer, dashboard_band):
    client = authenticated_client
    member = client.get('/dashboard/stats', headers=headers(signer, OWNER_SUB))
    assert member.status_code == 200
    assert member.get_json() == {
        'total_songs': 2,
        'status_counts': {'Completed': 1, 'Practice': 1},
    }

    with deletion_app.app_context():
        db.session.get(Band, dashboard_band).default_visibility = 'MEMBERS'
        db.session.commit()
    visitor = client.get('/dashboard/stats')
    assert visitor.status_code == 200
    assert visitor.get_json() == {'total_songs': 0, 'status_counts': {}}
    assert visitor.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in visitor.headers['Vary']


@pytest.mark.parametrize('resource_type', ([], {}))
def test_platform_deletion_rejects_non_string_resource_type_without_server_error(
        deletion_app, authenticated_client, signer, resource_type):
    with deletion_app.app_context():
        operator = AppUser.query.filter_by(supabase_user_id=OPERATOR_SUB).one()
        operator.is_platform_admin = True
        db.session.commit()
    response = authenticated_client.post(
        '/api/v2/platform/bands/999999/deletions',
        headers={**headers(signer, OPERATOR_SUB, 'operator@example.com'),
                 'Content-Type': 'application/json'},
        data=json.dumps({
            'resource_type': resource_type, 'resource_id': 1,
            'reason': 'test-only', 'confirm': True,
        }),
    )
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['error'] == 'invalid_request'


def test_platform_deletion_authenticates_before_parsing_malformed_body(
        authenticated_client):
    response = authenticated_client.post(
        '/api/v2/platform/bands/999999/deletions',
        data=b'{not-json', content_type='application/json',
    )
    assert response.status_code == 401


def test_platform_deletion_denies_regular_user_before_band_lookup(
        authenticated_client, signer):
    response = authenticated_client.post(
        '/api/v2/platform/bands/999999/deletions',
        headers=headers(signer, OWNER_SUB),
        json={'resource_type': 'song', 'resource_id': 1,
              'reason': 'test-only', 'confirm': True},
    )
    assert response.status_code == 403
