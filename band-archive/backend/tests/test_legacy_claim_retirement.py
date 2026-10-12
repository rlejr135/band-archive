"""Retired web claims cannot change the already-claimed ownership or history."""
import pytest
from sqlalchemy import text

import auth_service
from band_service import issue_legacy_claim, redeem_legacy_claim
from extensions import db
from models import AppUser, Band, Media, Member, Song


def _snapshot():
    return {table:tuple(tuple(row) for row in db.session.execute(text('SELECT * FROM '+table+' ORDER BY id')).all())
        for table in ('band','app_user','band_membership','legacy_band_claim_token','audit_event',
                      'song','media','member','personal_log','comment','gallery_image','rehearsal',
                      'announcement','media_vote','song_vote','song_suggestion')}


@pytest.mark.parametrize('authenticated',[False,True])
def test_four_retired_claim_routes_are_404_without_auth_or_ownership_mutation(app,monkeypatch,authenticated):
    # The current-schema fixture supports imported content as well as claims;
    # the historical Phase 3 fixture intentionally leaves v1 content unscoped.
    with app.app_context():
        band_id=Band.query.filter(Band.is_legacy_import.is_(True)).one().id
        admin=AppUser(supabase_user_id='admin-id',email='admin@example.test',is_platform_admin=True,email_verified=True)
        owner=AppUser(supabase_user_id='owner-id',email='owner@example.test',email_verified=True)
        db.session.add_all((admin,owner)); db.session.commit()
        _,raw=issue_legacy_claim(admin,band_id)
        redeem_legacy_claim(owner,band_id,raw)
        song=Song(band_id=band_id,title='preserved archive song',artist='fixture')
        db.session.add(song)
        db.session.flush()
        db.session.add_all((Member(band_id=band_id,name='preserved member',instrument='bass'),
            Media(band_id=band_id,song_id=song.id,filename='preserved.mp3',file_type='audio',transcoding_status='not_required')))
        db.session.commit()
        before=_snapshot()
        assert before['song'] and before['media'] and before['member'] and before['audit_event']
        assert db.session.get(Band,band_id).created_by_user_id==owner.id
    monkeypatch.setattr(auth_service,'authenticate_request',lambda:pytest.fail('retired route must not authenticate'))
    client=app.test_client()
    headers={'Authorization':'Bearer synthetic-retired-route'} if authenticated else {}
    paths=[('POST',f'/api/v2/platform/bands/{band_id}/legacy-ownership-claim'),
        ('GET','/api/v2/platform/legacy-ownership-claims/eligible-bands'),
        ('POST',f'/api/v2/platform/bands/{band_id}/legacy-ownership-claim/invalidate'),
        ('POST',f'/api/v2/bands/{band_id}/legacy-ownership-claim/redeem')]
    for method,path in paths:
        assert client.open(path,method=method,headers=headers,json={'token':'discarded'}).status_code==404
    assert not any('legacy-ownership-claim' in rule.rule for rule in app.url_map.iter_rules())
    with app.app_context(): assert _snapshot()==before
