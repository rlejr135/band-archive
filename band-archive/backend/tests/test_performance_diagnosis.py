"""Local query-count evidence; candidate batching is monkeypatched, not shipped."""
from datetime import date
import json
import os
from pathlib import Path
import time

import pytest
from sqlalchemy import event, text

import legacy_scope
from errors import NotFoundError
from extensions import db
from models import AppUser, Band, BandMembership, Media, Rehearsal, Song
from tests.test_auth import auth_app, signer, _install_jwks, _jwk, _token, _UserResponse


def _candidate_single_invocation_batch(records,message='Resource not found'):
    """Proposal ONLY: no cache persists beyond one existing validation call."""
    try:
        band=legacy_scope.legacy_band_id()
        seen=set()
        for item in records: legacy_scope._validate_graph(item,band,seen)
    except NotFoundError: legacy_scope._not_found(message)
    return records


def _measure(app,request,phases=None):
    counts={'select':0,'update':0,'insert':0,'delete':0,'commit':0}
    if phases is not None: phases['sql_counts']=counts
    with app.app_context():
        engine=db.engine
        db.session.remove()
    def statement(connection,cursor,sql,parameters,context,executemany):
        verb=sql.lstrip().split(None,1)[0].lower()
        if verb in counts: counts[verb]+=1
    def commit(connection): counts['commit']+=1
    event.listen(engine,'before_cursor_execute',statement)
    event.listen(engine,'commit',commit)
    start=time.perf_counter()
    try: response=request()
    finally:
        elapsed=time.perf_counter()-start
        event.remove(engine,'before_cursor_execute',statement)
        event.remove(engine,'commit',commit)
    return response,{**counts,'local_elapsed_seconds':round(elapsed,6)}


@pytest.fixture
def connected_archive(app):
    with app.app_context():
        band=Band.query.filter(Band.is_legacy_import.is_(True)).one()
        band.slug='perf-archive'
        songs=[Song(band_id=band.id,title=f'song-{i}',artist='test') for i in range(13)]
        rehearsals=[Rehearsal(band_id=band.id,title=f'rehearsal-{i}',date=date(2026,1,1)) for i in range(11)]
        db.session.add_all(songs+rehearsals); db.session.flush()
        for i in range(42): rehearsals[i%11].songs.append(songs[i%13])
        for i in range(83):
            db.session.add(Media(band_id=band.id,song_id=songs[i%13].id,rehearsal_id=rehearsals[i%11].id,
                filename=f'fixture-{i}.mp3',file_type='audio',transcoding_status='not_required'))
        db.session.commit()
        app.config['BAND_API_SCHEMA_READY']=True
        return {'band':band.id,'song':songs[0].id}


def test_connected_archive_query_counts_baseline_vs_batch_proposal(app,client,connected_archive,monkeypatch):
    import routes.songs
    original=routes.songs.validate_legacy_graphs
    phase={}
    def phase_validator(function):
        def validate(records,message):
            start=phase['sql_counts']['select']
            result=function(records,message)
            phase['validation_select']=phase['sql_counts']['select']-start
            return result
        return validate
    monkeypatch.setattr(routes.songs,'validate_legacy_graphs',phase_validator(original))
    baseline,before=_measure(app,lambda:client.get('/songs'),phase)
    before['validation_select']=phase['validation_select']
    before['root_and_serialization_select']=before['select']-before['validation_select']
    monkeypatch.setattr(routes.songs,'validate_legacy_graphs',phase_validator(_candidate_single_invocation_batch))
    candidate,after=_measure(app,lambda:client.get('/songs'),phase)
    after['validation_select']=phase['validation_select']
    after['root_and_serialization_select']=after['select']-after['validation_select']
    public,public_counts=_measure(app,lambda:client.get(f"/api/v2/bands/{connected_archive['band']}/songs?limit=100&offset=0"))
    assert baseline.status_code==candidate.status_code==public.status_code==200
    assert baseline.get_json()==candidate.get_json()
    assert len(candidate.get_json())==13
    assert sum(len(song['media']) for song in candidate.get_json())==83
    assert after['select']<before['select']/5
    evidence={'fixture':{'songs':13,'media':83,'rehearsals':11,'associations':42},'legacy_baseline':before,'legacy_batch_proposal':after,'v2_public_song_list':public_counts,
        'response_contract_identical':True,'proposal_only_monkeypatch':True,'production_requests':0}
    print('LOCAL_QUERY_EVIDENCE '+json.dumps(evidence,sort_keys=True))
    target=os.environ.get('PERF_DIAGNOSTIC_OUTPUT')
    if target:
        with Path(target).open('x',encoding='utf-8') as stream: json.dump(evidence,stream,sort_keys=True,indent=2)


def test_batch_proposal_does_not_cache_across_calls_and_rejects_foreign_child(app,client,connected_archive,monkeypatch):
    import routes.songs
    from storage import storage
    monkeypatch.setattr(routes.songs,'validate_legacy_graphs',_candidate_single_invocation_batch)
    assert client.get('/songs').status_code==200
    with app.app_context():
        foreign=Band(name='foreign',slug='foreign-perf',default_visibility='PUBLIC',storage_quota_bytes=1)
        db.session.add(foreign); db.session.flush()
        db.session.execute(text("INSERT INTO media (band_id,song_id,filename,file_type,visibility,transcoding_status,processing_attempts,upvote_count,downvote_count,vote_score) VALUES (:band,:song,'foreign.mp3','audio','PUBLIC','not_required',0,0,0,0)"),{'band':foreign.id,'song':connected_archive['song']})
        db.session.commit()
    signed=[]
    monkeypatch.setattr(storage,'generate_url',lambda *a,**k:signed.append(a) or 'should-not-sign')
    assert client.get('/songs').status_code==404
    assert signed==[]


def test_standard_token_confirmation_remote_call_and_sync_counts(auth_app,signer,monkeypatch):
    import auth_service
    _install_jwks(auth_app,[[_jwk(signer,'key-1')]])
    calls=[]
    def fetch(url,headers,timeout,allow_redirects):
        calls.append(1)
        assert allow_redirects is False
        return _UserResponse({'id':'11111111-1111-1111-1111-111111111111','email':'user@example.com','email_confirmed_at':'2026-10-06T00:00:00Z'})
    monkeypatch.setattr(auth_service.requests,'get',fetch)
    with auth_app.app_context():
        user=AppUser(supabase_user_id='11111111-1111-1111-1111-111111111111',email='user@example.com',email_verified=True)
        band=Band(name='workspace',slug='perf-workspace',default_visibility='PUBLIC',storage_quota_bytes=1)
        db.session.add_all((user,band)); db.session.flush()
        db.session.add(BandMembership(band_id=band.id,user_id=user.id,role='owner',status='active'))
        db.session.commit()
        auth_app.config['BAND_API_SCHEMA_READY']=True
    headers={'Authorization':'Bearer '+_token(signer,_without_email_verified=True)}
    evidence=[]
    for path in ('/api/v2/me','/api/v2/bands','/api/v2/bands/by-slug/perf-workspace'):
        previous=len(calls)
        response,counts=_measure(auth_app,lambda:auth_app.test_client().get(path,headers=headers))
        assert response.status_code==200
        assert len(calls)-previous==1
        evidence.append({'request_index':len(evidence)+1,'confirmation_remote_calls':len(calls)-previous,'database':counts})
    assert len(calls)==3
    result={'requests':evidence,'total_confirmation_remote_calls':3,'production_requests':0}
    print('MOCKED_AUTH_QUERY_EVIDENCE '+json.dumps(result,sort_keys=True))
    target=os.environ.get('PERF_DIAGNOSTIC_OUTPUT')
    if target:
        with Path(target).with_name('auth-query-evidence.json').open('x',encoding='utf-8') as stream: json.dump(result,stream,sort_keys=True,indent=2)
