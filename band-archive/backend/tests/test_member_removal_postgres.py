"""Real band-lock ordering and completion-window removal on owned localhost PG."""

import os
import threading

import pytest

from band_service import remove_membership, transfer_ownership
from direct_upload_service import complete_direct_session
from errors import NotFoundError, ValidationError
from extensions import db
from models import AppUser, AuditEvent, BandMembership, DirectUploadSession, Media, Song
import routes.uploads as upload_routes
from tests.test_invite_link_concurrency import invite_pg, _ordered_contention
from tests.test_data_api_security_migration import private_pg17


pytestmark = [pytest.mark.postgres_integration, pytest.mark.skipif(
    os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1', reason='owned localhost PG17 only')]


@pytest.mark.parametrize('transfer_first', [True, False])
@pytest.mark.parametrize('platform_actor', [True, False])
def test_removal_and_target_owner_promotion_serialize(invite_pg, transfer_first, platform_actor):
    app, engine, ids = invite_pg
    with app.app_context():
        target = BandMembership(band_id=ids['band'], user_id=ids['entrant'], role='member', status='active')
        db.session.add(target)
        platform = AppUser(supabase_user_id='remove-platform', email='remove-platform@example.invalid',
                           is_platform_admin=True, email_verified=True)
        db.session.add(platform)
        db.session.commit()
        target_id, remover_id = target.id, platform.id if platform_actor else ids['owner']

    def operation(transfer):
        try:
            actor = db.session.get(AppUser, ids['owner'] if transfer else remover_id)
            if transfer:
                transfer_ownership(actor, ids['band'], target_id)
            else:
                remove_membership(actor, ids['band'], target_id)
            return {'ok': True}
        except (ValidationError, NotFoundError) as error:
            db.session.rollback()
            return {'error': error.status_code}

    first, second = _ordered_contention(app, engine, lambda: operation(transfer_first), lambda: operation(not transfer_first))
    assert first == {'ok': True}
    assert second['error'] in {403, 404, 409}
    with app.app_context():
        target = db.session.get(BandMembership, target_id)
        owners = BandMembership.query.filter_by(band_id=ids['band'], role='owner', status='active').all()
        assert len(owners) == 1
        assert (target.role, target.status) == (('owner', 'active') if transfer_first else ('member', 'left'))
        assert AuditEvent.query.filter_by(event_type='membership_removed', target_id=str(target_id)).count() == int(not transfer_first)


def test_removal_during_external_upload_verification_prevents_materialization(invite_pg, monkeypatch):
    app, _engine, ids = invite_pg
    with app.app_context():
        target = BandMembership(band_id=ids['band'], user_id=ids['entrant'], role='member', status='active')
        db.session.add(target)
        db.session.flush()
        song = Song(band_id=ids['band'], created_by_membership_id=target.id, title='Retained', artist='Test')
        db.session.add(song)
        db.session.flush()
        session = DirectUploadSession(band_id=ids['band'], created_by_membership_id=target.id,
            kind='media', song_id=song.id, object_key=f'bands/{ids["band"]}/race.mp3',
            original_filename='race.mp3', content_type='audio/mpeg', declared_bytes=1,
            capability_token_hash='synthetic-only')
        db.session.add(session)
        db.session.commit()
        target_id, song_id, session_id = target.id, song.id, session.id
        db.session.remove()
    verified, release = threading.Event(), threading.Event()
    results = []
    monkeypatch.setattr(upload_routes, 'current_app_user', lambda required=True: db.session.get(AppUser, ids['entrant']))

    def complete():
        with app.app_context():
            try:
                def object_size(_key):
                    verified.set()
                    assert release.wait(10)
                    return 1

                def create_item(session, size):
                    item = Media(band_id=ids['band'], created_by_membership_id=target_id, song_id=song_id,
                                 filename='race.mp3', file_size=size, object_key=session.object_key)
                    db.session.add(item)
                    db.session.flush()
                    return item

                complete_direct_session(db.session.get(DirectUploadSession, session_id), object_size, create_item,
                    before_materialize=lambda session: upload_routes._v2_reauthorize_upload_session_owner(ids['band'], session))
                results.append('unexpected success')
            except ValidationError as error:
                results.append(error.status_code)
            finally:
                db.session.remove()

    worker = threading.Thread(target=complete, daemon=True)
    worker.start()
    try:
        assert verified.wait(10)
        with app.app_context():
            item, changed = remove_membership(db.session.get(AppUser, ids['owner']), ids['band'], target_id)
            assert changed is True and item.status == 'left'
    finally:
        release.set()
    worker.join(15)
    assert not worker.is_alive()
    assert results == [403]
    with app.app_context():
        assert db.session.get(DirectUploadSession, session_id).status == 'initiated'
        assert Media.query.filter_by(band_id=ids['band']).count() == 0
        assert db.session.get(Song, song_id).title == 'Retained'


def test_completion_lock_wins_then_removal_retains_completed_media(invite_pg, monkeypatch):
    app, engine, ids = invite_pg
    with app.app_context():
        target = BandMembership(band_id=ids['band'], user_id=ids['entrant'], role='member', status='active')
        db.session.add(target)
        db.session.flush()
        song = Song(band_id=ids['band'], created_by_membership_id=target.id, title='Already complete', artist='Test')
        db.session.add(song)
        db.session.flush()
        session = DirectUploadSession(band_id=ids['band'], created_by_membership_id=target.id,
            kind='media', song_id=song.id, object_key=f'bands/{ids["band"]}/complete.mp3',
            original_filename='complete.mp3', content_type='audio/mpeg', declared_bytes=1,
            capability_token_hash='synthetic-only')
        db.session.add(session)
        db.session.commit()
        target_id, song_id, session_id = target.id, song.id, session.id
        db.session.remove()
    monkeypatch.setattr(upload_routes, 'current_app_user', lambda required=True: db.session.get(AppUser, ids['entrant']))

    def complete():
        def create_item(session, size):
            item = Media(band_id=ids['band'], created_by_membership_id=target_id, song_id=song_id,
                         filename='complete.mp3', file_size=size, object_key=session.object_key)
            db.session.add(item)
            db.session.flush()
            return item
        item, status = complete_direct_session(db.session.get(DirectUploadSession, session_id), lambda _key: 1,
            create_item, before_materialize=lambda session: upload_routes._v2_reauthorize_upload_session_owner(ids['band'], session))
        return {'media_id': item.id, 'status': status}

    def remove():
        row, changed = remove_membership(db.session.get(AppUser, ids['owner']), ids['band'], target_id)
        return {'status': row.status, 'changed': changed}

    first, second = _ordered_contention(app, engine, complete, remove)
    assert first['status'] == 201
    assert second == {'status': 'left', 'changed': True}
    with app.app_context():
        assert db.session.get(DirectUploadSession, session_id).status == 'completed'
        assert db.session.get(Media, first['media_id']).created_by_membership_id == target_id
        assert db.session.get(Song, song_id).title == 'Already complete'
        assert db.session.get(BandMembership, target_id).status == 'left'
