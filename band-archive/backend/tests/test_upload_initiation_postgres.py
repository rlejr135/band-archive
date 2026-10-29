"""Owned-local PG reservation contention across gallery and personal logs.

The shared quota helper proves distinct PostgreSQL backends and an observed
blocking relationship. Both participants below are real HTTP initiates; only
actor resolution and R2 are doubled. Existing content uses the other ledger
models rather than Media so their contribution to the same quota is exercised.
"""

import os
import threading

import pytest

from extensions import db
from member_profile_service import ensure_member_profile
from models import (Band, BandMembership, DirectUploadSession, GalleryImage, Media,
                    PersonalLog, PersonalLogMultipartUploadSession)
import upload_initiation_service as initiation_service
from tests.test_upload_quota_postgres import (
    _contend, _fixture, _reservations, invite_pg, private_pg17,  # noqa: F401
)


pytestmark = [pytest.mark.postgres_integration, pytest.mark.skipif(
    os.environ.get('RUN_PRIVATE_PG_INTEGRATION') != '1',
    reason='explicitly provisioned owned localhost PG17 only')]


def _targets(app, band_id, used_kind):
    with app.app_context():
        Band.query.filter_by(id=band_id).with_for_update().one()
        membership = BandMembership.query.filter_by(
            band_id=band_id, role='owner', status='active').with_for_update().one()
        member = ensure_member_profile(membership)
        # Keep the shared fixture's existing Media identity, but put its five
        # bytes in the specific ledger model tested by this case.
        Media.query.filter_by(band_id=band_id).one().file_size = 0
        fields = dict(band_id=band_id, created_by_membership_id=membership.id,
                      filename='used.mp3' if used_kind == 'personal_log' else 'used.jpg',
                      file_size=5)
        if used_kind == 'personal_log':
            db.session.add(PersonalLog(member_id=member.id, title='Existing practice',
                original_filename='used.mp3', file_type='audio',
                object_key=f'bands/{band_id}/personal_logs/used.mp3', **fields))
        else:
            db.session.add(GalleryImage(original_filename='used.jpg',
                object_key=f'bands/{band_id}/gallery/used.jpg', **fields))
        db.session.commit()
        member_id = member.id
        db.session.remove()
        return member_id


def _post(app, band_id, member_id, kind):
    if kind == 'gallery':
        path = f'/api/v2/bands/{band_id}/gallery/direct/initiate'
        payload = {'filename': 'new.jpg', 'content_type': 'image/jpeg', 'declared_bytes': 10}
    else:
        path = f'/api/v2/bands/{band_id}/uploads/multipart/initiate'
        payload = {'filename': 'new.mp4', 'content_type': 'video/mp4',
                   'declared_bytes': 10, 'member_id': member_id, 'title': 'New practice'}
    with app.test_client() as client:
        response = client.post(path, json=payload)
        return response.status_code, response.get_json()


@pytest.mark.parametrize('first_kind,second_kind,used_kind', [
    ('gallery', 'personal_log', 'personal_log'),
    ('personal_log', 'gallery', 'gallery'),
], ids=['gallery-before-personal-log', 'personal-log-before-gallery'])
def test_gallery_and_personal_log_share_locked_reservation_capacity(
        invite_pg, monkeypatch, first_kind, second_kind, used_kind):
    app, engine, band_id, _song_id, _expired_id, calls = _fixture(invite_pg, monkeypatch)
    member_id = _targets(app, band_id, used_kind)
    ready, release = threading.Event(), threading.Event()
    factory_name = 'create_direct_session' if first_kind == 'gallery' else 'create_session'
    original_factory = getattr(initiation_service, factory_name)
    gate_lock = threading.Lock()
    gated = False

    def create(*args, **kwargs):
        nonlocal gated
        with gate_lock:
            first_call = not gated
            gated = True
        if first_call:
            # Accounting has completed, but no new reservation exists yet.
            # Releasing the band lock early must fail the shared blocking proof.
            ready.set()
            assert release.wait(30), 'coordinator did not release reservation creation'
        return original_factory(*args, **kwargs)

    monkeypatch.setattr(initiation_service, factory_name, create)
    first, second = _contend(
        app, engine,
        lambda: _post(app, band_id, member_id, first_kind),
        lambda: _post(app, band_id, member_id, second_kind), ready, release)

    assert first[0] == 201
    assert first[1]['capability_header'] == 'X-Upload-Capability'
    assert first[1]['upload_capability_token']
    assert second == (409, {'error': 'Band storage quota exceeded.'})
    with app.app_context():
        db.session.remove()
        reservations = _reservations(band_id)
        assert len(reservations) == 1
        reservation = reservations[0]
        assert reservation.declared_bytes == 10
        assert sum(item.file_size for item in Media.query.filter_by(band_id=band_id)) == 0
        personal_bytes = sum(item.file_size for item in PersonalLog.query.filter_by(band_id=band_id))
        gallery_bytes = sum(item.file_size for item in GalleryImage.query.filter_by(band_id=band_id))
        assert (personal_bytes, gallery_bytes) == ((5, 0) if used_kind == 'personal_log' else (0, 5))
        assert personal_bytes + gallery_bytes + reservation.declared_bytes == 15
        if first_kind == 'gallery':
            assert isinstance(reservation, DirectUploadSession) and reservation.kind == 'gallery'
            assert reservation.object_key.startswith(f'bands/{band_id}/gallery/')
            assert first[1]['upload_url'] == 'https://storage.invalid/upload'
        else:
            assert isinstance(reservation, PersonalLogMultipartUploadSession)
            assert reservation.member_id == member_id
            assert reservation.object_key.startswith(f'bands/{band_id}/personal_logs/')
        assert reservation.id == first[1]['session_id']
    assert len(calls['multipart']) == (1 if first_kind == 'personal_log' else 0)
    assert len(calls['presign']) == (1 if first_kind == 'gallery' else 0)
    assert calls['delete'] == []
