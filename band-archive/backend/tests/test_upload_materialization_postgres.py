"""Owned-local PG authority changes during the verified-object I/O window.

This covers completion reauthorization with two proven distinct PostgreSQL
connections. R2 I/O and materialization are fakes; membership/account updates,
completion claims, recovery states and authorization queries are real.
"""

import os
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from direct_upload_service import complete_direct_session
from errors import ValidationError
from extensions import db
from models import (AppUser, BandMembership, DirectUploadSession, Media,
                    MultipartUploadPart, MultipartUploadSession, Song)
from multipart_upload_service import complete_session
import routes.uploads as uploads
import upload_authorization_service as upload_authorization
from storage import storage
from tests.test_invite_link_concurrency import invite_pg
from tests.test_data_api_security_migration import private_pg17  # noqa: F401


pytestmark = [pytest.mark.postgres_integration, pytest.mark.skipif(
    os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
    reason='explicitly provisioned owned localhost PG17 only')]


@pytest.mark.parametrize('kind', ['direct', 'multipart'])
@pytest.mark.parametrize('revocation', ['membership_left', 'actor_inactive'])
def test_authority_revoked_during_head_prevents_materialization(
        invite_pg, monkeypatch, kind, revocation):
    app, engine, ids = invite_pg
    model = DirectUploadSession if kind == 'direct' else MultipartUploadSession
    with app.app_context():
        membership = BandMembership.query.filter_by(
            band_id=ids['band'], user_id=ids['owner']).one()
        song = Song(band_id=ids['band'], created_by_membership_id=membership.id,
                    title='Completion authority window', artist='Fixture')
        db.session.add(song)
        db.session.flush()
        common = dict(
            band_id=ids['band'], created_by_membership_id=membership.id,
            song_id=song.id, object_key=f'bands/{ids["band"]}/authority.mp4',
            original_filename='authority.mp4', content_type='video/mp4',
            declared_bytes=1, capability_token_hash='synthetic-only')
        session = (model(kind='media', **common) if kind == 'direct'
                   else model(r2_upload_id='synthetic-only', is_legacy=False, **common))
        db.session.add(session)
        db.session.flush()
        if kind == 'multipart':
            db.session.add(MultipartUploadPart(
                session_id=session.id, part_number=1, etag='fixture',
                uploaded_bytes=1, acknowledged_at=datetime.now(timezone.utc)))
        db.session.commit()
        membership_id, session_id = membership.id, session.id
        db.session.remove()

    at_head, resume = threading.Event(), threading.Event()
    pids, outcome, materialized = {}, [], []
    monkeypatch.setattr(upload_authorization, 'current_app_user',
                        lambda required=True: db.session.get(AppUser, ids['owner']))
    monkeypatch.setattr(storage, 'complete_multipart_upload', lambda *args: None)

    def complete_in_request():
        with app.test_request_context():
            try:
                loader = (uploads._v2_direct_completion_session if kind == 'direct'
                          else uploads._v2_multipart_completion_session)
                session = loader(session_id, ids['band'])

                def object_size(_key):
                    # Retain the post-claim connection while the independent
                    # mutation commits, proving this is not a same-session edit.
                    pids['completion'] = db.session.execute(
                        text('SELECT pg_backend_pid()')).scalar_one()
                    at_head.set()
                    assert resume.wait(15), 'authority update did not finish'
                    return 1

                def create_item(_session, _size):
                    materialized.append('unexpected materialization')
                    raise AssertionError('revoked authority reached materialization')

                operation = complete_direct_session if kind == 'direct' else complete_session
                operation(
                    session, object_size, create_item,
                    before_materialize=lambda completing:
                        upload_authorization.reauthorize_upload_session_owner(ids['band'], completing))
                outcome.append('unexpected success')
            except ValidationError as error:
                outcome.append(error.status_code)
            except Exception as error:
                outcome.append(type(error).__name__)
            finally:
                db.session.remove()

    thread = threading.Thread(target=complete_in_request, daemon=True)
    thread.start()
    try:
        assert at_head.wait(15), 'completion did not enter the object-verification window'
        with engine.begin() as connection:
            pids['revocation'] = connection.execute(text('SELECT pg_backend_pid()')).scalar_one()
            assert pids['completion'] != pids['revocation']
            if revocation == 'membership_left':
                connection.execute(text("UPDATE band_membership SET status='left' WHERE id=:id"),
                                   {'id': membership_id})
            else:
                connection.execute(text("UPDATE app_user SET status='inactive' WHERE id=:id"),
                                   {'id': ids['owner']})
    finally:
        resume.set()
        thread.join(20)
    assert not thread.is_alive()
    assert outcome == [403]
    assert materialized == []
    with app.app_context():
        assert Media.query.filter_by(band_id=ids['band']).count() == 0
        assert db.session.get(model, session_id).status == (
            'initiated' if kind == 'direct' else 'failed')
        if revocation == 'membership_left':
            assert db.session.get(BandMembership, membership_id).status == 'left'
        else:
            assert db.session.get(AppUser, ids['owner']).status == 'inactive'
