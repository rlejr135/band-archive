"""Owned-localhost PostgreSQL races for leave and ownership operations.

Set RUN_PRIVATE_PG_INTEGRATION=1 to opt in. The fixture provisions its own
PostgreSQL cluster and never accepts an operator database URL.
"""

import os
import threading

import pytest

from app import create_app
from band_service import leave_band, set_role, transfer_ownership
from config import TestingConfig
from errors import NotFoundError, ValidationError
from extensions import db
from models import AppUser, Band, BandMembership, DirectUploadSession, Media, Song
from direct_upload_service import complete_direct_session
import routes.uploads as upload_routes

from tests.test_preserve_identity_refresh_postgres import private_pg17, _upgrade_postgres

pytestmark = pytest.mark.skipif(
    os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
    reason='explicitly opt into the owned localhost PostgreSQL 17 fixture',
)


def test_postgres_transfer_role_and_leave_serialize_band_first(private_pg17, monkeypatch):
    print('private PG17 migration: start', flush=True)
    _upgrade_postgres(private_pg17)
    print('private PG17 migration: complete', flush=True)
    config = type('PrivateLeaveConfig', (TestingConfig,), {
        'SQLALCHEMY_DATABASE_URI': private_pg17,
        'SQLALCHEMY_ENGINE_OPTIONS': {
            'connect_args': {'options': '-c lock_timeout=5s -c statement_timeout=20s'},
        },
    })
    app = create_app(config, start_worker=False)
    app.config['BAND_API_SCHEMA_READY'] = True

    with app.app_context():
        owner_user = AppUser(supabase_user_id='leave-race-owner', email='owner@example.test',
                             status='active', email_verified=True)
        target_user = AppUser(supabase_user_id='leave-race-target', email='target@example.test',
                              status='active', email_verified=True)
        other_user = AppUser(supabase_user_id='leave-race-other', email='other@example.test',
                             status='active', email_verified=True)
        db.session.add_all((owner_user, target_user, other_user))
        db.session.flush()
        band = Band(name='Leave Race', slug='leave-race', created_by_user_id=owner_user.id)
        db.session.add(band)
        db.session.flush()
        owner = BandMembership(band_id=band.id, user_id=owner_user.id, role='owner', status='active')
        target = BandMembership(band_id=band.id, user_id=target_user.id, role='member', status='active')
        other = BandMembership(band_id=band.id, user_id=other_user.id, role='member', status='active')
        db.session.add_all((owner, target, other))
        db.session.commit()
        band_id = band.id
        ids = (owner_user.id, target_user.id, other_user.id)
        membership_ids = (owner.id, target.id, other.id)
        db.session.remove()

    def compete(actions):
        start = threading.Barrier(len(actions))
        results = []
        result_lock = threading.Lock()

        def run(action):
            with app.app_context():
                actor_id, operation, argument = action
                actor = db.session.get(AppUser, actor_id)
                start.wait(timeout=10)
                try:
                    result = operation(actor, band_id, argument)
                    outcome = ('ok', getattr(result, 'status', None), getattr(result, 'role', None))
                except ValidationError as error:
                    db.session.rollback()
                    outcome = ('rejected', error.status_code)
                except NotFoundError as error:
                    db.session.rollback()
                    outcome = ('not_found', error.status_code)
                except Exception as error:  # surfaced to the main test thread
                    db.session.rollback()
                    outcome = ('unexpected', type(error).__name__)
                finally:
                    db.session.remove()
                with result_lock:
                    results.append(outcome)

        threads = [threading.Thread(target=run, args=(action,), daemon=True) for action in actions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        alive = [thread for thread in threads if thread.is_alive()]
        if alive:
            # Do not strand a waiting peer when reporting a broken race.
            start.abort()
            for thread in alive:
                thread.join(timeout=2)
        assert not alive, 'PostgreSQL band lock race exceeded 15 seconds'
        assert all(result[0] != 'unexpected' for result in results), results
        assert len(results) == len(actions)
        return results

    # If leave wins, the transfer sees an inactive target; if transfer wins,
    # leave sees that the target is now owner. Either serial order is valid.
    transfer_leave = compete([
        (ids[0], transfer_ownership, membership_ids[1]),
        (ids[1], lambda actor, b_id, _unused: leave_band(actor, b_id), None),
    ])
    assert len(transfer_leave) == 2
    with app.app_context():
        owners = BandMembership.query.filter_by(band_id=band_id, role='owner', status='active').all()
        assert len(owners) == 1

    # Role mutation and leave of the same target must also serialize. If role
    # change wins, leaving as admin is allowed; if leave wins, role change is
    # rejected because the target is no longer active.
    with app.app_context():
        current_owner_id = BandMembership.query.filter_by(
            band_id=band_id, role='owner', status='active').one().user_id
    role_leave = compete([
        (current_owner_id, lambda actor, b_id, membership_id: set_role(actor, membership_id, 'admin'),
         membership_ids[2]),
        (ids[2], lambda actor, b_id, _unused: leave_band(actor, b_id), None),
    ])
    assert len(role_leave) == 2
    with app.app_context():
        owners = BandMembership.query.filter_by(band_id=band_id, role='owner', status='active').all()
        assert len(owners) == 1
        assert BandMembership.query.filter_by(band_id=band_id, user_id=ids[2]).one().status in ('active', 'left')

        upload_user = AppUser(supabase_user_id='leave-race-uploader', email='uploader@example.test',
                              status='active', email_verified=True)
        db.session.add(upload_user)
        db.session.flush()
        upload_membership = BandMembership(band_id=band_id, user_id=upload_user.id,
                                           role='member', status='active')
        db.session.add(upload_membership)
        db.session.flush()
        song = Song(band_id=band_id, created_by_membership_id=upload_membership.id,
                    title='Race song', artist='Test')
        db.session.add(song)
        db.session.flush()
        upload_session = DirectUploadSession(
            band_id=band_id, created_by_membership_id=upload_membership.id, kind='media',
            object_key=f'bands/{band_id}/race.mp3', original_filename='race.mp3',
            content_type='audio/mpeg', declared_bytes=1, song_id=song.id,
            capability_token_hash='not-a-real-capability',
        )
        db.session.add(upload_session)
        db.session.commit()
        upload_user_id, upload_membership_id, upload_session_id, song_id = (
            upload_user.id, upload_membership.id, upload_session.id, song.id)
        db.session.remove()

    head_verified = threading.Event()
    allow_materialize = threading.Event()
    completion_result = []
    monkeypatch.setattr(
        upload_routes, 'current_app_user',
        lambda required=True: db.session.get(AppUser, upload_user_id),
    )

    def finish_upload():
        with app.app_context():
            try:
                session = db.session.get(DirectUploadSession, upload_session_id)

                def verify_size(_key):
                    head_verified.set()
                    if not allow_materialize.wait(timeout=10):
                        raise TimeoutError('completion test barrier timed out')
                    return 1

                def create_media(completing, size):
                    item = Media(
                        band_id=band_id, created_by_membership_id=upload_membership_id,
                        visibility='PUBLIC', song_id=song_id,
                        filename=completing.object_key.rsplit('/', 1)[1],
                        object_key=completing.object_key, original_filename='race.mp3',
                        file_size=size,
                    )
                    db.session.add(item)
                    db.session.flush()
                    return item

                item, status = complete_direct_session(
                    session, verify_size, create_media,
                    before_materialize=lambda completing: upload_routes._v2_reauthorize_upload_session_owner(
                        band_id, completing),
                )
                completion_result.append(('ok', item.id, status))
            except Exception as error:
                db.session.rollback()
                completion_result.append(('error', type(error).__name__))
            finally:
                db.session.remove()

    completion_thread = threading.Thread(target=finish_upload, daemon=True)
    completion_thread.start()
    try:
        assert head_verified.wait(timeout=10), 'upload did not reach post-claim verification'
        with app.app_context():
            with pytest.raises(ValidationError) as blocked:
                leave_band(db.session.get(AppUser, upload_user_id), band_id)
            assert blocked.value.status_code == 409
            assert blocked.value.message == 'upload_in_progress'
            db.session.rollback()
    finally:
        allow_materialize.set()
    completion_thread.join(timeout=15)
    assert not completion_thread.is_alive(), 'upload materialization exceeded 15 seconds'
    assert completion_result and completion_result[0][0] == 'ok', completion_result
    with app.app_context():
        assert DirectUploadSession.query.filter_by(id=upload_session_id, status='completed').count() == 1
        assert Media.query.filter_by(band_id=band_id, created_by_membership_id=upload_membership_id).count() == 1
        db.session.remove()
