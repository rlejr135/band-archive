"""HTTP/JWT vote atomicity, identity and savepoint reconciliation contracts."""

import hashlib
import json

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Query, Session

from extensions import db
from models import (AuditEvent, BandMembership, Media, MediaVote, Song,
                    SongSuggestion, SongSuggestionVote)
import routes.content_writes as writes
from tests.test_band_access_phase6 import access_app, signer  # noqa: F401
from tests.test_v2_content_writes import _band_id, principals, write_client  # noqa: F401


EVENTS = ('content.media_vote.changed', 'content.suggestion_vote.changed')


def _seed(app, kind, *, account_vote=False):
    band_id = _band_id(app)
    with app.app_context():
        membership = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        if kind == 'media':
            song = Song(band_id=band_id, title='Vote contract', artist='Artist', visibility='PUBLIC')
            db.session.add(song); db.session.flush()
            item = Media(band_id=band_id, song_id=song.id, filename='owned.mp3',
                         file_type='audio', visibility='PUBLIC', upvote_count=1, downvote_count=0, vote_score=1)
            db.session.add(item); db.session.flush()
            legacy = MediaVote(band_id=band_id, media_id=item.id, voter_hash='d' * 64, is_legacy=True, value=1)
            vote_model = MediaVote
            account = MediaVote(band_id=band_id, media_id=item.id, created_by_membership_id=membership.id,
                                voter_hash=hashlib.sha256(f'v2-membership:{membership.id}'.encode()).hexdigest(),
                                is_legacy=False, value=1)
        else:
            item = SongSuggestion(band_id=band_id, title='Vote contract', artist='Artist',
                                  link='https://example.invalid/owned', visibility='PUBLIC', thumbs_up=1, thumbs_down=0)
            db.session.add(item); db.session.flush()
            legacy = SongSuggestionVote(band_id=band_id, suggestion_id=item.id, is_legacy=True, value=1)
            vote_model = SongSuggestionVote
            account = SongSuggestionVote(band_id=band_id, suggestion_id=item.id,
                                         voter_user_id=membership.user_id, created_by_membership_id=membership.id,
                                         is_legacy=False, value=1)
        db.session.add(legacy)
        if account_vote:
            db.session.add(account)
            if kind == 'media':
                item.upvote_count = item.vote_score = 2
            else:
                item.thumbs_up = 2
        db.session.commit()
        return {'band': band_id, 'item': item.id, 'legacy': legacy.id,
                'membership': membership.id, 'user': membership.user_id, 'vote_model': vote_model}


def _snapshot(app):
    with app.app_context():
        db.session.remove()
        result = {}
        for model in (Media, MediaVote, SongSuggestion, SongSuggestionVote):
            result[model.__name__] = [tuple(getattr(row, column.name) for column in model.__table__.columns)
                                      for row in model.query.order_by(model.id)]
        result['audits'] = [(row.event_type, row.band_id, row.actor_user_id, row.target_type,
                             row.target_id, row.details_json) for row in AuditEvent.query.filter(
                                 AuditEvent.event_type.in_(EVENTS)).order_by(AuditEvent.id)]
        return result


def _request(client, headers, graph, kind, value):
    segment = 'media' if kind == 'media' else 'suggestions'
    return client.put(f"/api/v2/bands/{graph['band']}/{segment}/{graph['item']}/vote",
                      headers=headers, json={'value': value})


@pytest.mark.parametrize('kind', ['media', 'suggestion'])
@pytest.mark.parametrize('failure', ['audit_flush', 'commit', 'serializer', 'jsonify'])
def test_vote_failure_rolls_back_vote_row_aggregates_and_audit(
        access_app, write_client, principals, monkeypatch, kind, failure):
    graph = _seed(access_app, kind)
    before, observed = _snapshot(access_app), []

    def pending(session):
        return any(isinstance(row, AuditEvent) and row.event_type in EVENTS for row in session.new)

    def before_flush(session, context, instances):
        if pending(session):
            session.info['vote_failure_contract'] = True
            if failure == 'audit_flush':
                observed.append(failure)
                raise RuntimeError('injected vote audit failure')

    def before_commit(session):
        if failure == 'commit' and (pending(session) or session.info.get('vote_failure_contract')):
            observed.append(failure)
            raise RuntimeError('injected vote commit failure')

    def fail(*args, **kwargs):
        observed.append(failure)
        raise RuntimeError('injected vote response failure')

    if failure in {'serializer', 'jsonify'}:
        name = ('serialize_media' if kind == 'media' else '_suggestion_response') if failure == 'serializer' else 'jsonify'
        monkeypatch.setattr(writes, name, fail)
    event.listen(Session, 'before_flush', before_flush)
    event.listen(Session, 'before_commit', before_commit)
    try:
        with pytest.raises(RuntimeError, match='injected vote'):
            _request(write_client, principals['member'], graph, kind, -1)
    finally:
        event.remove(Session, 'before_flush', before_flush)
        event.remove(Session, 'before_commit', before_commit)
    assert observed == [failure]
    assert _snapshot(access_app) == before


@pytest.mark.parametrize('kind', ['media', 'suggestion'])
def test_vote_response_precedes_commit_and_repeats_audit_without_double_counting(
        access_app, write_client, principals, monkeypatch, kind):
    graph = _seed(access_app, kind)
    phases, original = [], writes.jsonify

    def jsonify(*args, **kwargs):
        phases.append('response')
        db.session.info['vote_response_contract'] = True
        return original(*args, **kwargs)

    def before_commit(session):
        if session.info.pop('vote_response_contract', False):
            phases.append('commit')

    monkeypatch.setattr(writes, 'jsonify', jsonify)
    event.listen(Session, 'before_commit', before_commit)
    try:
        for value in (1, 1, 0):
            response = _request(write_client, principals['member'], graph, kind, value)
            assert response.status_code == 200
            assert response.headers['Cache-Control'] == 'private, no-store'
            assert 'Authorization' in response.headers['Vary']
            assert set(response.json) == {kind, 'viewer_vote'}
            assert response.json['viewer_vote'] == value
            assert response.json[kind]['vote_score'] == (2 if value == 1 else 1)
    finally:
        event.remove(Session, 'before_commit', before_commit)
    assert phases == ['response', 'commit'] * 3
    after = _snapshot(access_app)
    assert [json.loads(row[-1]) for row in after['audits']] == [
        {'value': 1, 'changed': True}, {'value': 1, 'changed': False}, {'value': 0, 'changed': True}]
    with access_app.app_context():
        votes = graph['vote_model'].query.all()
        assert len(votes) == 1 and votes[0].id == graph['legacy'] and votes[0].value == 1


@pytest.mark.parametrize('kind', ['media', 'suggestion'])
def test_vote_real_unique_violation_reconciles_existing_row_without_double_counting(
        access_app, write_client, principals, monkeypatch, kind):
    graph = _seed(access_app, kind, account_vote=True)
    original, hidden = Query.one_or_none, []

    def stale_once(query):
        result = original(query)
        # Model only the stale first lookup. The attempted insert, unique
        # constraint, savepoint rollback and authoritative second read are real.
        if query.column_descriptions[0].get('entity') is graph['vote_model'] and not hidden:
            assert result is not None and result.is_legacy is False
            hidden.append(result.id)
            return None
        return result

    monkeypatch.setattr(Query, 'one_or_none', stale_once)
    response = _request(write_client, principals['member'], graph, kind, -1)
    assert response.status_code == 200 and response.json['viewer_vote'] == -1
    assert response.json[kind]['vote_score'] == 0
    with access_app.app_context():
        db.session.remove()
        account = graph['vote_model'].query.filter_by(is_legacy=False).one()
        assert hidden == [account.id] and account.value == -1
        assert account.created_by_membership_id == graph['membership']
        assert graph['vote_model'].query.count() == 2
        audit = AuditEvent.query.filter(AuditEvent.event_type.in_(EVENTS)).one()
        assert json.loads(audit.details_json) == {'value': -1, 'changed': True}


def test_suggestion_vote_identity_survives_inactive_membership_and_refreshes_audit_context(
        access_app, write_client, principals):
    graph = _seed(access_app, 'suggestion', account_vote=True)
    with access_app.app_context():
        account = SongSuggestionVote.query.filter_by(voter_user_id=graph['user']).one()
        vote_id = account.id
        # Model an existing account vote whose historical membership context
        # is absent. Reactivation must update that context, not insert a vote.
        account.created_by_membership_id = None
        db.session.get(BandMembership, graph['membership']).status = 'left'
        db.session.commit()
    before = _snapshot(access_app)
    assert _request(write_client, principals['member'], graph, 'suggestion', 1).status_code == 403
    assert _snapshot(access_app) == before
    with access_app.app_context():
        db.session.get(BandMembership, graph['membership']).status = 'active'
        db.session.commit()
    response = _request(write_client, principals['member'], graph, 'suggestion', 1)
    assert response.status_code == 200 and response.json['suggestion']['vote_score'] == 2
    with access_app.app_context():
        db.session.remove()
        row = SongSuggestionVote.query.filter_by(voter_user_id=graph['user']).one()
        assert row.id == vote_id and row.created_by_membership_id == graph['membership'] and row.value == 1
        assert SongSuggestionVote.query.count() == 2
        audit = AuditEvent.query.filter(AuditEvent.event_type.in_(EVENTS)).one()
        assert json.loads(audit.details_json) == {'value': 1, 'changed': False}
