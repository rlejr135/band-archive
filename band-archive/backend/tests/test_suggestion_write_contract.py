"""Focused v08 suggestion-write regression coverage.

These tests deliberately exercise the authenticated v2 boundary separately
from the password-based compatibility route tests in ``test_suggestions``.
"""

import pytest

from extensions import db
from models import BandMembership, Song, SongSuggestion, SongSuggestionVote
from tests.test_band_access_phase6 import access_app, signer
from tests.test_v2_content_writes import _band_id, principals, write_client


@pytest.mark.parametrize('link', (
    'javascript:alert(1)', '//example.test/path', '/relative/path',
    'https://user:password@example.test/path', 'https://example.test/has space',
    'https://example.test/%0Aheader', 'https://example.test/%250Aheader',
    'https://example.test/%1f', 'https://example.test/%7f', 'https://example.test/%C2%85',
    'https://example.test/%', 'https://example.test/%2', 'https://example.test/zero\u200bwidth',
))
def test_v2_suggestion_create_and_update_share_the_strict_link_contract(
        access_app, write_client, principals, link):
    band_id = _band_id(access_app)
    create = write_client.post(f'/api/v2/bands/{band_id}/suggestions', headers=principals['member'], json={
        'title': 'candidate', 'artist': 'artist', 'link': link,
    })
    assert create.status_code == 400

    valid = write_client.post(f'/api/v2/bands/{band_id}/suggestions', headers=principals['member'], json={
        'title': 'candidate', 'artist': 'artist', 'link': 'https://예시.테스트/곡?name=한글',
    })
    assert valid.status_code == 201
    suggestion_id = valid.get_json()['suggestion']['id']
    update = write_client.patch(f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}',
                                headers=principals['member'], json={'link': link})
    assert update.status_code == 400
    with access_app.app_context():
        assert db.session.get(SongSuggestion, suggestion_id).link == 'https://예시.테스트/곡?name=한글'


def test_v2_proceed_revalidates_corrupt_legacy_link_before_mutating_queue(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    created = write_client.post(f'/api/v2/bands/{band_id}/suggestions', headers=principals['member'], json={
        'title': 'candidate', 'artist': 'artist', 'link': 'https://example.test/valid',
    })
    suggestion_id = created.get_json()['suggestion']['id']
    with access_app.app_context():
        db.session.get(SongSuggestion, suggestion_id).link = 'https://example.test/%250Aheader'
        db.session.commit()

    response = write_client.post(f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}/proceed',
                                 headers=principals['owner'], json={})
    assert response.status_code == 400
    with access_app.app_context():
        assert db.session.get(SongSuggestion, suggestion_id) is not None
        assert Song.query.filter_by(band_id=band_id, title='candidate').count() == 0


def test_v2_suggestion_vote_is_account_owned_across_leave_and_rejoin(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    created = write_client.post(f'/api/v2/bands/{band_id}/suggestions', headers=principals['member'], json={
        'title': 'candidate', 'artist': 'artist', 'link': 'https://example.test/valid',
    })
    suggestion_id = created.get_json()['suggestion']['id']
    url = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}/vote'
    assert write_client.put(url, headers=principals['member'], json={'value': 1}).status_code == 200

    with access_app.app_context():
        old = BandMembership.query.filter_by(band_id=band_id, role='member', status='active').one()
        old_id = old.id
        old.status = 'left'
        db.session.commit()

    # Membership is intentionally one durable row per (band, account), so a
    # leave/rejoin reactivates it rather than making a second identity.  The
    # vote nevertheless remains keyed by account, not by that audit row.
    assert write_client.put(url, headers=principals['member'], json={'value': -1}).status_code == 403
    with access_app.app_context():
        old = db.session.get(BandMembership, old_id)
        old.status = 'active'
        db.session.commit()

    changed = write_client.put(url, headers=principals['member'], json={'value': -1})
    assert changed.status_code == 200
    assert changed.get_json()['viewer_vote'] == -1
    with access_app.app_context():
        rows = SongSuggestionVote.query.filter_by(suggestion_id=suggestion_id, is_legacy=False).all()
        assert len(rows) == 1
        assert rows[0].voter_user_id is not None and rows[0].value == -1
        assert rows[0].created_by_membership_id == old_id
        suggestion = db.session.get(SongSuggestion, suggestion_id)
        assert (suggestion.thumbs_up, suggestion.thumbs_down) == (0, 1)


def test_v2_suggestion_vote_isolated_by_account_and_read_back_to_the_viewer(
        access_app, write_client, principals):
    band_id = _band_id(access_app)
    created = write_client.post(f'/api/v2/bands/{band_id}/suggestions', headers=principals['member'], json={
        'title': 'candidate', 'artist': 'artist', 'link': 'https://example.test/valid',
    })
    suggestion_id = created.get_json()['suggestion']['id']
    url = f'/api/v2/bands/{band_id}/suggestions/{suggestion_id}/vote'
    assert write_client.put(url, headers=principals['member'], json={'value': 1}).status_code == 200
    assert write_client.put(url, headers=principals['owner'], json={'value': -1}).status_code == 200
    member_view = write_client.get(f'/api/v2/bands/{band_id}/suggestions', headers=principals['member'])
    owner_view = write_client.get(f'/api/v2/bands/{band_id}/suggestions', headers=principals['owner'])
    assert member_view.get_json()['suggestions'][0]['viewer_vote'] == 1
    assert owner_view.get_json()['suggestions'][0]['viewer_vote'] == -1
