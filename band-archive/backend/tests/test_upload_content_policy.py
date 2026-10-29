"""Policy receipts bind new uploads while preserving issued capabilities."""
import json
import uuid
from datetime import date

import pytest

from extensions import db
from models import (AuditEvent, Band, BandMembership, ContentRestriction, DirectUploadSession,
                    Media, Member, PolicyAcceptanceReceipt, Rehearsal, Song)
from policy_service import validate_policy_configuration
from errors import ValidationError
from policy_service import upload_session_visibility
from storage import storage
from tests.test_band_access_phase6 import access_app, signer
from tests.test_v2_content_writes import _band_id, principals, write_client
from tests.test_band_upload_phase8 import _song


def _enable(app):
    app.config.update(UPLOAD_POLICY_REQUIRED=True, UPLOAD_POLICY_VERSION='upload-v1',
                      POLICY_TERMS_VERSION='terms-v1', POLICY_PRIVACY_VERSION='privacy-v1',
                      POLICY_TERMS_URL='/terms', POLICY_PRIVACY_URL='/privacy',
                      POLICY_RETENTION_NOTICE='Test retention', POLICY_PROVIDERS_NOTICE='Test providers')


def _accept(client, headers):
    return client.post('/api/v2/me/upload-policy-acceptance', headers=headers, json={
        'terms_version': 'terms-v1', 'privacy_version': 'privacy-v1',
        'terms_accepted': True, 'age_14_plus': True,
    })


def _assertion(**changes):
    return {'version': 'upload-v1', 'batch_id': str(uuid.uuid4()),
            'rights_confirmed': True, 'visibility': 'MEMBERS', **changes}


@pytest.mark.parametrize('kind', ['direct', 'multipart', 'personal-direct', 'personal-multipart', 'gallery'])
def test_all_upload_starts_require_current_receipt_and_scope_without_storage_side_effects(
        access_app, write_client, principals, monkeypatch, kind):
    band_id = _band_id(access_app)
    song_id, _ = _song(access_app, band_id)
    with access_app.app_context():
        member = Member(band_id=band_id, name='Policy test member', instrument='bass')
        db.session.add(member)
        db.session.commit()
        member_id = member.id
    _enable(access_app)
    calls = []
    monkeypatch.setattr(storage, 'create_multipart_upload', lambda *args: calls.append('multipart') or 'upload-id')
    monkeypatch.setattr(storage, 'generate_upload_url', lambda *args, **kwargs: calls.append('url') or 'https://example.invalid/put')
    route = 'gallery/direct' if kind == 'gallery' else 'uploads/' + ('multipart' if 'multipart' in kind else 'direct')
    data = {'filename': 'photo.jpg' if kind == 'gallery' else 'video.mp4', 'declared_bytes': 10}
    if kind != 'gallery':
        data.update({'member_id': member_id} if kind.startswith('personal') else {'song_id': song_id})
    url = f'/api/v2/bands/{band_id}/{route}/initiate'
    headers = principals['member']
    assert write_client.post(url, headers=headers, json=data).status_code == 403
    assert _accept(write_client, headers).status_code == 201
    assert write_client.post(url, headers=headers, json=data).status_code == 400
    data['upload_policy'] = _assertion(version='stale')
    assert write_client.post(url, headers=headers, json=data).status_code == 409
    assert calls == []
    data['upload_policy'] = _assertion()
    started = write_client.post(url, headers=headers, json=data)
    assert started.status_code == 201, started.get_json()
    with access_app.app_context():
        rows = AuditEvent.query.filter_by(event_type='upload_policy_confirmed',
                                          target_id=started.json['session_id']).all()
        assert len(rows) == 1
        assert rows[0].band_id == band_id
        assert json.loads(rows[0].details_json) == data['upload_policy']


def test_existing_account_latest_receipt_is_idempotent_and_updates_versions(access_app, write_client, principals):
    _enable(access_app)
    headers = principals['member']
    assert write_client.get('/api/v2/me', headers=headers).json['user']['upload_policy_acceptance_required'] is True
    assert _accept(write_client, headers).status_code == 201
    assert _accept(write_client, headers).status_code == 200
    access_app.config['POLICY_TERMS_VERSION'] = 'terms-v2'
    assert write_client.get('/api/v2/me', headers=headers).json['user']['upload_policy_acceptance_required'] is True
    response = write_client.post('/api/v2/me/upload-policy-acceptance', headers=headers, json={
        'terms_version': 'terms-v2', 'privacy_version': 'privacy-v1',
        'terms_accepted': True, 'age_14_plus': True})
    assert response.status_code == 201
    with access_app.app_context():
        assert AuditEvent.query.filter_by(event_type='account_policy_accepted').count() == 2
        assert PolicyAcceptanceReceipt.query.count() == 1
        last = AuditEvent.query.filter_by(event_type='account_policy_accepted').order_by(AuditEvent.id.desc()).first()
        previous = json.loads(last.details_json)['previous']
        assert previous['terms_version'] == 'terms-v1'
        assert previous['privacy_version'] == 'privacy-v1'
        assert previous['accepted_at']


def test_direct_completion_uses_original_visibility_and_receipt_is_not_duplicated(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, _ = _song(access_app, band_id)
    _enable(access_app)
    headers = principals['member']
    _accept(write_client, headers)
    monkeypatch.setattr(storage, 'generate_upload_url', lambda *args, **kwargs: 'https://example.invalid/put')
    monkeypatch.setattr(storage, 'head', lambda *args: {'ContentLength': 10})
    # Audio avoids starting an extraction worker in a policy contract test.
    started = write_client.post(f'/api/v2/bands/{band_id}/uploads/direct/initiate', headers=headers, json={
        'filename': 'clip.m4a', 'declared_bytes': 10, 'song_id': song_id, 'upload_policy': _assertion()})
    assert started.status_code == 201
    body = started.json
    capability = {body['capability_header']: body['upload_capability_token']}
    with access_app.app_context():
        db.session.get(Band, band_id).default_visibility = 'PUBLIC'
        db.session.commit()
    access_app.config['POLICY_TERMS_VERSION'] = 'terms-v2'
    url = f'/api/v2/bands/{band_id}/uploads/direct/{body["session_id"]}/complete'
    assert write_client.post(url, headers={**headers, **capability}).status_code == 201
    assert write_client.post(url, headers={**headers, **capability}).status_code == 200
    with access_app.app_context():
        assert Media.query.filter_by(band_id=band_id).one().visibility == 'MEMBERS'
        assert AuditEvent.query.filter_by(event_type='upload_policy_confirmed').count() == 1


def test_legacy_mutations_cannot_bypass_enabled_policy(access_app, write_client):
    _enable(access_app)
    access_app.config['LEGACY_V1_WRITES_ENABLED'] = True
    assert write_client.post('/uploads/presign', json={}).status_code == 410
    assert write_client.post('/gallery', json={}).status_code == 410


@pytest.mark.parametrize('invalid', [[], {}, 1, True, ''])
def test_invalid_visibility_rejected_before_storage_or_reservation(
        access_app, write_client, principals, monkeypatch, invalid):
    band_id = _band_id(access_app)
    song_id, _ = _song(access_app, band_id)
    _enable(access_app)
    _accept(write_client, principals['member'])
    calls = []
    monkeypatch.setattr(storage, 'create_multipart_upload', lambda *args: calls.append('create'))
    monkeypatch.setattr(storage, 'generate_upload_url', lambda *args, **kwargs: calls.append('url'))
    for kind in ['direct', 'multipart']:
        response = write_client.post(f'/api/v2/bands/{band_id}/uploads/{kind}/initiate',
                                     headers=principals['member'], json={
            'filename': 'video.mp4', 'declared_bytes': 10, 'song_id': song_id,
            'upload_policy': _assertion(visibility=invalid)})
        assert response.status_code == 400
    assert calls == []
    with access_app.app_context():
        assert DirectUploadSession.query.count() == 0


def test_public_parent_expansion_requires_current_confirmation(access_app, write_client, principals):
    band_id = _band_id(access_app)
    song_id, _ = _song(access_app, band_id)
    with access_app.app_context():
        db.session.get(Band, band_id).default_visibility = 'PUBLIC'
        db.session.commit()
    _enable(access_app)
    headers = principals['member']
    _accept(write_client, headers)
    endpoint = f'/api/v2/bands/{band_id}/songs/{song_id}'
    assert write_client.patch(endpoint, headers=headers, json={'visibility': 'PUBLIC'}).status_code == 409
    with access_app.app_context():
        assert db.session.get(Song, song_id).visibility == 'MEMBERS'
    response = write_client.patch(endpoint, headers=headers, json={
        'visibility': 'PUBLIC', 'visibility_confirm': {'version': 'upload-v1', 'confirmed': True}})
    assert response.status_code == 200


def test_policy_startup_and_runtime_fail_closed_when_facts_are_missing(access_app, write_client, principals):
    band_id = _band_id(access_app)
    song_id, _ = _song(access_app, band_id)
    _enable(access_app)
    access_app.config['POLICY_PROVIDERS_NOTICE'] = ''
    with pytest.raises(RuntimeError, match='Upload policy'):
        validate_policy_configuration(access_app.config)
    response = write_client.post(f'/api/v2/bands/{band_id}/uploads/direct/initiate',
                                 headers=principals['member'], json={
        'filename': 'clip.mp3', 'declared_bytes': 10, 'song_id': song_id, 'upload_policy': _assertion()})
    assert response.status_code == 503


def test_feature_disabled_session_can_complete_after_enabling_without_new_acceptance(
        access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, _ = _song(access_app, band_id)
    monkeypatch.setattr(storage, 'generate_upload_url', lambda *args, **kwargs: 'https://example.invalid/put')
    monkeypatch.setattr(storage, 'head', lambda *args: {'ContentLength': 10})
    started = write_client.post(f'/api/v2/bands/{band_id}/uploads/direct/initiate',
                                 headers=principals['member'], json={
        'filename': 'clip.mp3', 'declared_bytes': 10, 'song_id': song_id})
    assert started.status_code == 201
    _enable(access_app)
    body = started.json
    response = write_client.post(f'/api/v2/bands/{band_id}/uploads/direct/{body["session_id"]}/complete',
                                  headers={**principals['member'], body['capability_header']: body['upload_capability_token']})
    assert response.status_code == 201
    with access_app.app_context():
        assert Media.query.filter_by(band_id=band_id).one().visibility == db.session.get(Band, band_id).default_visibility


def test_receipt_failure_rolls_back_new_direct_reservation(access_app, write_client, principals, monkeypatch):
    band_id = _band_id(access_app)
    song_id, _ = _song(access_app, band_id)
    _enable(access_app)
    _accept(write_client, principals['member'])
    def fail_receipt(*args):
        raise RuntimeError('Simulated receipt persistence failure')
    monkeypatch.setattr('upload_initiation_service.record_upload_assertion', fail_receipt)
    with pytest.raises(RuntimeError, match='receipt persistence'):
        write_client.post(f'/api/v2/bands/{band_id}/uploads/direct/initiate',
                          headers=principals['member'], json={
            'filename': 'clip.mp3', 'declared_bytes': 10, 'song_id': song_id, 'upload_policy': _assertion()})
    with access_app.app_context():
        assert DirectUploadSession.query.count() == 0


@pytest.mark.parametrize('corruption', ['wrong-band', 'wrong-actor', 'duplicate'])
def test_upload_evidence_cannot_be_reused_across_scope_or_duplicated(
        access_app, write_client, principals, monkeypatch, corruption):
    band_id = _band_id(access_app)
    song_id, _ = _song(access_app, band_id)
    _enable(access_app)
    _accept(write_client, principals['member'])
    monkeypatch.setattr(storage, 'generate_upload_url', lambda *args, **kwargs: 'https://example.invalid/put')
    started = write_client.post(f'/api/v2/bands/{band_id}/uploads/direct/initiate',
                                 headers=principals['member'], json={
        'filename': 'clip.mp3', 'declared_bytes': 10, 'song_id': song_id, 'upload_policy': _assertion()})
    assert started.status_code == 201
    with access_app.app_context():
        session = db.session.get(DirectUploadSession, started.json['session_id'])
        original = AuditEvent.query.filter_by(event_type='upload_policy_confirmed', target_id=session.id).one()
        if corruption == 'duplicate':
            db.session.add(AuditEvent(band_id=band_id, actor_user_id=original.actor_user_id,
                                     event_type=original.event_type, target_type=original.target_type,
                                     target_id=session.id, details_json=original.details_json))
            db.session.flush()
        else:
            # Simulate a corrupt read without modifying the append-only audit row.
            db.session.expunge(original)
            if corruption == 'wrong-band':
                session.band_id = Band.query.filter(Band.id != band_id).first().id
            else:
                session.created_by_membership_id = None
        with db.session.no_autoflush:
            with pytest.raises(ValidationError, match='evidence is inconsistent'):
                upload_session_visibility(session, 'PUBLIC')
        db.session.rollback()


def test_detaching_private_rehearsal_requires_public_expansion_confirmation(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    song_id, creator_id = _song(access_app, band_id)
    with access_app.app_context():
        db.session.get(Band, band_id).default_visibility = 'PUBLIC'
        song = db.session.get(Song, song_id)
        song.visibility = 'PUBLIC'
        rehearsal = Rehearsal(band_id=band_id, title='Private rehearsal', date=date(2026, 10, 21),
                              visibility='MEMBERS', created_by_membership_id=creator_id)
        rehearsal.songs.append(song)
        db.session.add(rehearsal)
        db.session.add(Media(band_id=band_id, song_id=song_id, filename='clip.mp3', file_type='audio',
                             visibility='PUBLIC', created_by_membership_id=creator_id))
        db.session.commit()
        rehearsal_id = rehearsal.id
    _enable(access_app)
    _accept(write_client, principals['member'])
    endpoint = f'/api/v2/bands/{band_id}/rehearsals/{rehearsal_id}'
    response = write_client.patch(endpoint, headers=principals['member'], json={'song_ids': []})
    assert response.status_code == 409, response.json
    with access_app.app_context():
        assert [item.id for item in db.session.get(Rehearsal, rehearsal_id).songs] == [song_id]
    assert write_client.patch(endpoint, headers=principals['member'], json={
        'song_ids': [], 'visibility_confirm': {'version': 'upload-v1', 'confirmed': True}}).status_code == 200


@pytest.mark.parametrize('kind', ['direct', 'multipart'])
def test_restricted_parent_blocks_new_upload_and_issued_session_completion_before_r2(
        access_app, write_client, principals, monkeypatch, kind):
    band_id = _band_id(access_app)
    song_id, creator_id = _song(access_app, band_id)
    calls = []
    monkeypatch.setattr(storage, 'create_multipart_upload', lambda *args: 'issued-before-restriction')
    monkeypatch.setattr(storage, 'generate_upload_url', lambda *args, **kwargs: 'https://example.invalid/put')
    monkeypatch.setattr(storage, 'head', lambda *args: calls.append('head') or {'ContentLength': 10})
    monkeypatch.setattr(storage, 'complete_multipart_upload', lambda *args: calls.append('complete'))
    url = f'/api/v2/bands/{band_id}/uploads/{kind}'
    data = {'filename': 'clip.mp4', 'declared_bytes': 10, 'song_id': song_id}
    started = write_client.post(f'{url}/initiate', headers=principals['member'], json=data)
    assert started.status_code == 201
    body = started.json
    with access_app.app_context():
        actor = db.session.get(BandMembership, creator_id)
        db.session.add(ContentRestriction(band_id=band_id, resource_type='song', resource_id=song_id,
                                         case_ref=str(uuid.uuid4()), reason_code='copyright',
                                         created_by_user_id=actor.user_id))
        db.session.commit()
    assert write_client.post(f'{url}/initiate', headers=principals['member'], json=data).status_code == 404
    capability = {body['capability_header']: body['upload_capability_token']}
    assert write_client.post(f'{url}/{body["session_id"]}/complete',
                             headers={**principals['member'], **capability}).status_code == 404
    assert calls == []
