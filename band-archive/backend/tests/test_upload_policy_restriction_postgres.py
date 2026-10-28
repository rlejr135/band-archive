"""Rights restrictions applied during object I/O must defeat cached access."""
import os
import threading
import uuid
from datetime import datetime, timezone

import pytest

from content_restrictions import set_restriction
from direct_upload_service import complete_direct_session
from errors import NotFoundError
from extensions import db
from models import (AppUser, BandMembership, DirectUploadSession, Media,
                    MultipartUploadPart, MultipartUploadSession, Song)
from multipart_upload_service import complete_session
import routes.uploads as uploads
import upload_authorization_service as upload_authorization
from storage import storage
from tests.test_invite_link_concurrency import invite_pg
from tests.test_data_api_security_migration import private_pg17


pytestmark = [pytest.mark.postgres_integration, pytest.mark.skipif(
    os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1', reason='owned localhost PG17 only')]


@pytest.mark.parametrize('kind', ['direct', 'multipart'])
def test_new_restriction_during_r2_window_invalidates_request_negative_cache(invite_pg, monkeypatch, kind):
    app, _engine, ids = invite_pg
    with app.app_context():
        actor = db.session.get(AppUser, ids['owner'])
        actor.is_platform_admin = True
        membership = BandMembership.query.filter_by(band_id=ids['band'], user_id=actor.id).one()
        song = Song(band_id=ids['band'], created_by_membership_id=membership.id,
                    title='Rights review race', artist='Fixture')
        db.session.add(song)
        db.session.flush()
        common = dict(band_id=ids['band'], created_by_membership_id=membership.id,
                      song_id=song.id, object_key=f'bands/{ids["band"]}/race.mp3',
                      original_filename='race.mp3', content_type='audio/mpeg',
                      declared_bytes=1, capability_token_hash='synthetic-only')
        session = (DirectUploadSession(kind='media', **common) if kind == 'direct'
                   else MultipartUploadSession(r2_upload_id='synthetic-only', **common))
        db.session.add(session)
        db.session.flush()
        if kind == 'multipart':
            db.session.add(MultipartUploadPart(session_id=session.id, part_number=1, etag='fixture',
                                               uploaded_bytes=1, acknowledged_at=datetime.now(timezone.utc)))
        db.session.commit()
        song_id, session_id = song.id, session.id
        db.session.remove()

    at_storage, resume = threading.Event(), threading.Event()
    outcome, materialized, signed, deleted = [], [], [], []
    monkeypatch.setattr(upload_authorization, 'current_app_user', lambda required=True: db.session.get(AppUser, ids['owner']))
    monkeypatch.setattr(storage, 'generate_url', lambda *args, **kwargs: signed.append(args) or 'unexpected')
    monkeypatch.setattr(storage, 'delete', lambda *args: deleted.append(args))

    def external_wait():
        at_storage.set()
        assert resume.wait(15), 'The independent restriction transaction did not finish.'

    if kind == 'multipart':
        monkeypatch.setattr(storage, 'complete_multipart_upload', lambda *args: external_wait())

    def complete_in_http_request():
        # The request persists over both completion transactions. An app-only
        # context would bypass the HTTP negative cache and miss this regression.
        with app.test_request_context():
            try:
                loader = uploads._v2_direct_completion_session if kind == 'direct' else uploads._v2_multipart_completion_session
                session = loader(session_id, ids['band'])

                def object_size(_key):
                    if kind == 'direct':
                        external_wait()
                    return 1

                def create_item(session, size):
                    materialized.append(session.id)
                    item = Media(band_id=ids['band'], created_by_membership_id=membership_id,
                                 song_id=song_id, filename='race.mp3', file_type='audio', file_size=size)
                    db.session.add(item)
                    db.session.flush()
                    return item

                operation = complete_direct_session if kind == 'direct' else complete_session
                operation(session, object_size, create_item,
                          before_materialize=lambda item: upload_authorization.reauthorize_upload_session_owner(ids['band'], item))
                outcome.append('unexpected success')
            except NotFoundError as error:
                outcome.append(error.status_code)
            except Exception as error:
                outcome.append(type(error).__name__)
            finally:
                db.session.remove()

    with app.app_context():
        membership_id = BandMembership.query.filter_by(band_id=ids['band'], user_id=ids['owner']).one().id
        db.session.remove()
    thread = threading.Thread(target=complete_in_http_request, daemon=True)
    thread.start()
    try:
        assert at_storage.wait(15), 'Completion did not release its claim transaction.'
        with app.app_context():
            report = set_restriction(band_id=ids['band'], resource_type='song', resource_id=song_id,
                                     case_ref=str(uuid.uuid4()), actor_user_id=ids['owner'], active=True,
                                     reason_code='copyright', apply=True)
            assert report['applied'] is True
            db.session.commit()
            db.session.remove()
    finally:
        resume.set()
    thread.join(20)
    assert not thread.is_alive()
    assert outcome == [404]
    assert materialized == [] and signed == [] and deleted == []
    with app.app_context():
        assert Media.query.filter_by(band_id=ids['band']).count() == 0
        model = DirectUploadSession if kind == 'direct' else MultipartUploadSession
        assert db.session.get(model, session_id).status == 'initiated'
