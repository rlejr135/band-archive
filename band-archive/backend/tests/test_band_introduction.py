from datetime import datetime, timedelta, timezone

from extensions import db
from models import Announcement, Band, BandMembership, GalleryImage, Song
from storage import storage
from tests.test_auth import _install_jwks, _jwk, _token
from tests.test_band_access_phase6 import _headers, access_app, signer


def _introduction(client, slug, headers=None):
    return client.get(f'/api/v2/bands/by-slug/{slug}/introduction', headers=headers or {})


def test_introduction_is_bounded_audience_scoped_and_carries_only_viewer_state(
        access_app, signer):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        public = Band.query.filter_by(slug='band-b').one()
        owner = BandMembership.query.filter_by(band_id=public.id, role='owner').one()
        db.session.add_all((
            Song(band_id=public.id, created_by_membership_id=owner.id,
                 visibility='MEMBERS', title='Hidden song', artist='Hidden'),
            GalleryImage(band_id=public.id, created_by_membership_id=owner.id,
                         visibility='PUBLIC', filename='featured.jpg',
                         object_key='bands/b/featured.jpg', is_featured=True),
            Announcement(band_id=public.id, created_by_membership_id=owner.id,
                         visibility='PUBLIC', content='Public announcement'),
        ))
        deleted = Band(name='Gone', slug='gone', deleted_at=datetime.now(timezone.utc))
        db.session.add(deleted)
        db.session.commit()
        owner_token = _token(signer, sub='77777777-7777-7777-7777-777777777777',
                             email='outsider@example.com')
        pending_token = _token(signer, sub='44444444-4444-4444-4444-444444444444',
                               email='pending@example.com')

    client = access_app.test_client()
    visitor = _introduction(client, 'band-b')
    assert visitor.status_code == 200
    data = visitor.get_json()['introduction']
    assert data['band'] == {
        'id': data['band']['id'], 'name': 'Band B', 'slug': 'band-b', 'description': None,
    }
    assert data['stats'] == {'total_songs': 1}
    assert data['song_count_scope'] == 'PUBLIC'
    assert data['featured_gallery']['url'] == 'https://storage.test/bands/b/featured.jpg'
    assert data['announcement']['content'] == 'Public announcement'
    assert data['viewer'] == {'membership_status': None, 'can_open_workspace': False}
    assert set(data) == {'band', 'featured_gallery', 'stats', 'song_count_scope', 'announcement', 'viewer'}
    assert visitor.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in visitor.headers['Vary']

    access_app.config['BAND_API_SCHEMA_READY'] = False
    schema_unavailable = _introduction(client, 'band-b')
    access_app.config['BAND_API_SCHEMA_READY'] = True
    assert schema_unavailable.status_code == 503
    assert schema_unavailable.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in schema_unavailable.headers['Vary']

    with access_app.app_context():
        public = Band.query.filter_by(slug='band-b').one()
        GalleryImage.query.filter_by(band_id=public.id, is_featured=True).one().visibility = 'MEMBERS'
        Announcement.query.filter_by(band_id=public.id).one().visibility = 'MEMBERS'
        db.session.commit()
    concealed_content = _introduction(client, 'band-b').get_json()['introduction']
    assert concealed_content['featured_gallery'] is None
    assert concealed_content['announcement'] is None
    assert concealed_content['stats'] == {'total_songs': 1}

    owner_response = _introduction(client, 'band-b', _headers(owner_token))
    owner_data = owner_response.get_json()['introduction']
    assert owner_data['stats'] == {'total_songs': 2}
    assert owner_data['song_count_scope'] == 'MEMBERS'
    assert owner_data['featured_gallery']['url'] == 'https://storage.test/bands/b/featured.jpg'
    assert owner_data['announcement']['content'] == 'Public announcement'
    assert owner_data['viewer'] == {'membership_status': 'active', 'can_open_workspace': True}
    assert not ({'members', 'songs', 'calendar', 'gallery', 'media'} & set(owner_data))

    pending_response = _introduction(client, 'band-b', _headers(pending_token))
    assert pending_response.get_json()['introduction']['viewer'] == {
        'membership_status': 'pending', 'can_open_workspace': False,
    }
    assert pending_response.get_json()['introduction']['song_count_scope'] == 'PUBLIC'


def test_introduction_conceals_private_deleted_and_missing_bands_and_rejects_invalid_auth(
        access_app, signer, monkeypatch):
    _install_jwks(access_app, [[_jwk(signer, 'key-1')]])
    with access_app.app_context():
        db.session.add(Band(name='Gone', slug='gone', deleted_at=datetime.now(timezone.utc)))
        db.session.commit()

    client = access_app.test_client()
    for slug in ('band-a', 'gone', 'missing'):
        response = _introduction(client, slug)
        assert response.status_code == 404
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'Authorization' in response.headers['Vary']

    private_owner = _token(signer, sub='11111111-1111-1111-1111-111111111111',
                           email='user@example.com')
    private_member = _introduction(client, 'band-a', _headers(private_owner))
    assert private_member.status_code == 200
    assert private_member.get_json()['introduction']['band']['slug'] == 'band-a'
    assert private_member.get_json()['introduction']['song_count_scope'] == 'MEMBERS'
    assert private_member.get_json()['introduction']['stats']['total_songs'] == 3

    invalid = _introduction(client, 'band-b', _headers(_token(
        signer, exp=datetime.now(timezone.utc) - timedelta(seconds=1),
    )))
    assert invalid.status_code == 401
    assert invalid.headers['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in invalid.headers['Vary']

    monkeypatch.setattr(storage, 'generate_url', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))
    with access_app.app_context():
        public = Band.query.filter_by(slug='band-b').one()
        db.session.add(GalleryImage(band_id=public.id, visibility='PUBLIC', filename='bad.jpg',
                                    object_key='bands/b/bad.jpg', is_featured=True))
        db.session.commit()
    image_failure = _introduction(client, 'band-b').get_json()['introduction']['featured_gallery']
    assert image_failure is None
