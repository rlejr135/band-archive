import assert from 'node:assert/strict';
import test from 'node:test';
import { captureNativeUploadScope, claimNativeProcessingRecovery, consumeNativeUpload, filterNativeUploads, hydrateNativeUploadQueue, isNativeUploadScopeCurrent, mergeNativeUploadState, nativeTargetMatches, releaseNativeProcessingRecovery, resetNativeUploadQueueForTest, setNativeUploadAccountScope, subscribeNativeUploadQueue, syncNativeProcessingStatus, updateNativeUpload } from './nativeUploadQueue.js';

test('native queue hydrates once and fans out one listener snapshot', async () => {
  resetNativeUploadQueueForTest(); let list = 0; let nativeListener; const acked = [];
  const transport = { kind: 'native', listPending: async () => { list += 1; return { items: [{ id: 'done', state: 'completed', target: { song_id: 3 } }] }; }, addListener: async (_name, fn) => { nativeListener = fn; return () => {}; }, acknowledge: async ({ id }) => acked.push(id) };
  const received = []; const unsubscribe = subscribeNativeUploadQueue((value) => received.push(value));
  await hydrateNativeUploadQueue(transport); await hydrateNativeUploadQueue(transport);
  assert.equal(list, 1); assert.equal(received.at(-1)[0].target.song_id, 3); nativeListener({ id: 'live', state: 'uploading', progress: 10, target: { song_id: 3 } }); assert.equal(received.at(-1).find((item) => item.id === 'live').progress, 10);
  await consumeNativeUpload(transport,'done',{ songId:3 },async () => {}); await consumeNativeUpload(transport,'done',{ songId:3 },async () => {}); assert.deepEqual(acked,['done']); unsubscribe(); resetNativeUploadQueueForTest();
});

test('started native queue explicitly refreshes recovered snapshots without adding a listener', async () => {
  resetNativeUploadQueueForTest(); let list=0; let listenersAdded=0;
  const transport={ kind:'native', listPending:async()=>({ items:++list === 1 ? [] : [{ id:'unlocked',state:'queued',target:{song_id:7} }] }), addListener:async()=>{listenersAdded+=1;return()=>{};} };
  await hydrateNativeUploadQueue(transport);
  await hydrateNativeUploadQueue(transport,{ refresh:true });
  assert.equal(list,2); assert.equal(listenersAdded,1);
  assert.equal(filterNativeUploads({songId:7})[0]?.id,'unlocked');
  resetNativeUploadQueueForTest();
});

test('an account-scoped refresh replaces retained native snapshots from the prior account', async () => {
  resetNativeUploadQueueForTest(); let list=0;
  const transport={ kind:'native', listPending:async()=>({ items:++list === 1 ? [{ id:'account-a',state:'queued',target:{song_id:1} }] : [{ id:'account-b',state:'queued',target:{song_id:2} }] }), addListener:async()=>()=>{} };
  await hydrateNativeUploadQueue(transport);
  const refreshed=await hydrateNativeUploadQueue(transport,{ refresh:true });
  assert.deepEqual(refreshed.map((item)=>item.id),['account-b']);
  resetNativeUploadQueueForTest();
});

test('a retained v2 event from account B is dropped after switching the JS scope to account A', async () => {
  resetNativeUploadQueueForTest(); let retained;
  const transport={ kind:'native', listPending:async()=>({ items:[] }), addListener:async(_name,listener)=>{retained=listener;return()=>{};} };
  setNativeUploadAccountScope('account-b'); await hydrateNativeUploadQueue(transport);
  retained({id:'b-event',protocol:'v2',accountId:'account-b',state:'queued',target:{song_id:1}});
  setNativeUploadAccountScope('account-a');
  retained({id:'late-b-event',protocol:'v2',accountId:'account-b',state:'queued',target:{song_id:1}});
  const snapshots=[]; const stop=subscribeNativeUploadQueue((items)=>snapshots.push(items.map((item)=>item.id)));
  assert.deepEqual(snapshots.at(-1),[]);
  stop(); resetNativeUploadQueueForTest();
});

test('delayed B processing recovery is fenced after switching to A and cannot sync or recreate a row', async () => {
  resetNativeUploadQueueForTest(); let resolve; const delayed=new Promise((done)=>{resolve=done;}); const synced=[];
  const transport={kind:'native',listPending:async()=>({items:[{id:'b-processing',protocol:'v2',accountId:'account-b',state:'processing',target:{song_id:1}}]}),addListener:async()=>()=>{},syncStatus:async(value)=>synced.push(value)};
  setNativeUploadAccountScope('account-b'); await hydrateNativeUploadQueue(transport); const scope=captureNativeUploadScope('b-processing');
  const recovery=delayed.then(async()=>{if(!isNativeUploadScopeCurrent(scope))return; updateNativeUpload({id:'b-processing',state:'completed',result:'{}'}); if(isNativeUploadScopeCurrent(scope))await syncNativeProcessingStatus(transport,'b-processing','completed',{id:1,transcoding_status:'completed'});});
  setNativeUploadAccountScope('account-a'); resolve(); await recovery;
  assert.deepEqual(filterNativeUploads({songId:1}),[]); assert.deepEqual(synced,[]); resetNativeUploadQueueForTest();
});

test('partial callbacks cannot create unknown rows but full legacy and active v2 events can', async () => {
  resetNativeUploadQueueForTest(); setNativeUploadAccountScope('account-a');
  assert.equal(updateNativeUpload({id:'partial',state:'completed'}),false);
  assert.equal(updateNativeUpload({id:'wrong-v2',protocol:'v2',accountId:'account-b',state:'queued',target:{song_id:1}}),false);
  assert.equal(updateNativeUpload({id:'legacy',protocol:'v1',state:'queued',target:{song_id:2}}),true);
  assert.equal(updateNativeUpload({id:'active-v2',protocol:'v2',accountId:'account-a',state:'queued',target:{song_id:3}}),true);
  assert.deepEqual(filterNativeUploads({songId:2}).map((item)=>item.id),['legacy']); assert.deepEqual(filterNativeUploads({songId:3}).map((item)=>item.id),['active-v2']);
  resetNativeUploadQueueForTest();
});

test('B to A to B gives the new B generation its own recovery claim and old release cannot clear it', async () => {
  resetNativeUploadQueueForTest(); const synced=[]; let resolveOld; let resolveNew;
  const oldWait=new Promise((done)=>{resolveOld=done;}); const newWait=new Promise((done)=>{resolveNew=done;});
  const transport={kind:'native',listPending:async()=>({items:[{id:'same-id',protocol:'v2',accountId:'account-b',state:'processing',target:{song_id:1}}]}),addListener:async()=>()=>{},syncStatus:async(value)=>synced.push(value)};
  setNativeUploadAccountScope('account-b'); await hydrateNativeUploadQueue(transport); const oldScope=captureNativeUploadScope('same-id'); const oldClaim=claimNativeProcessingRecovery(oldScope);
  const oldRecovery=oldWait.then(async()=>{if(!isNativeUploadScopeCurrent(oldScope))return;updateNativeUpload({id:'same-id',state:'completed',result:'{}'});await syncNativeProcessingStatus(transport,'same-id','completed',{id:1,transcoding_status:'completed'});}).finally(()=>releaseNativeProcessingRecovery(oldClaim));
  setNativeUploadAccountScope('account-a'); await hydrateNativeUploadQueue(transport,{refresh:true});
  setNativeUploadAccountScope('account-b'); await hydrateNativeUploadQueue(transport,{refresh:true}); const newScope=captureNativeUploadScope('same-id'); const newClaim=claimNativeProcessingRecovery(newScope);
  assert.ok(oldClaim); assert.ok(newClaim); assert.notEqual(oldClaim.key,newClaim.key); assert.equal(claimNativeProcessingRecovery(newScope),null);
  const newRecovery=newWait.then(async()=>{if(!isNativeUploadScopeCurrent(newScope))return;updateNativeUpload({id:'same-id',state:'completed',result:'{}'});await syncNativeProcessingStatus(transport,'same-id','completed',{id:1,transcoding_status:'completed'});}).finally(()=>releaseNativeProcessingRecovery(newClaim));
  resolveOld(); await oldRecovery; assert.equal(claimNativeProcessingRecovery(newScope),null);
  assert.equal(isNativeUploadScopeCurrent(oldScope),false); assert.equal(isNativeUploadScopeCurrent(newScope),true);
  resolveNew(); await newRecovery;
  assert.equal(filterNativeUploads({songId:1})[0]?.state,'completed'); assert.equal(synced.length,1);
  resetNativeUploadQueueForTest();
});

test('target filters keep song, rehearsal, and member uploads isolated', async () => {
  resetNativeUploadQueueForTest();
  const transport={ kind:'native', listPending:async()=>({ items:[
    { id:'song',state:'uploading',target:{song_id:4,rehearsal_id:null} },
    { id:'rehearsal',state:'processing',target:{song_id:4,rehearsal_id:9} },
    { id:'member',state:'retry_wait',target:{member_id:2} },
  ]}), addListener:async()=>()=>{} };
  await hydrateNativeUploadQueue(transport);
  assert.deepEqual(filterNativeUploads({ songId:4,rehearsalId:null }).map((item)=>item.id),['song']);
  assert.deepEqual(filterNativeUploads({ rehearsalId:9 }).map((item)=>item.id),['rehearsal']);
  assert.deepEqual(filterNativeUploads({ memberId:2 }).map((item)=>item.id),['member']);
  assert.equal(nativeTargetMatches({ target:{song_id:4,rehearsal_id:9} },{ songId:4,rehearsalId:null }),false);
  assert.equal(nativeTargetMatches({ target:{member_id:2,rehearsal_id:9} },{ rehearsalId:9 }),false);
  assert.equal(nativeTargetMatches({ bandId:4,target:{song_id:4,band_id:4} },{ songId:4,bandId:4 }),true);
  assert.equal(nativeTargetMatches({ bandId:4,target:{song_id:4,band_id:4} },{ songId:4,bandId:5 }),false);
  assert.equal(nativeTargetMatches({ bandId:4,target:{song_id:4,band_id:4} },{ songId:4 }),false);
  resetNativeUploadQueueForTest();
});

test('processing recovery transitions update the common snapshot before terminal acknowledgement', async () => {
  resetNativeUploadQueueForTest(); const synced=[]; const acked=[];
  const transport={ kind:'native', listPending:async()=>({ items:[{ id:'processing',state:'processing',target:{rehearsal_id:9,song_id:4},result:'{"id":12,"transcoding_status":"processing"}' }] }), addListener:async()=>()=>{}, syncStatus:async(value)=>synced.push(value), acknowledge:async(value)=>acked.push(value.id) };
  await hydrateNativeUploadQueue(transport);
  await syncNativeProcessingStatus(transport,'processing','completed',{ id:12,transcoding_status:'completed',audio_url:'https://audio.example/a.m4a' });
  assert.equal(filterNativeUploads({ rehearsalId:9 })[0].state,'completed');
  assert.equal(synced[0].state,'completed');
  await consumeNativeUpload(transport,'processing',{ rehearsalId:9 },async(item)=>assert.equal(item.state,'completed'));
  assert.deepEqual(acked,['processing']); resetNativeUploadQueueForTest();
});

test('unmounted or unrelated screens do not acknowledge terminal snapshots', async () => {
  resetNativeUploadQueueForTest(); const acked=[];
  const transport={ kind:'native', listPending:async()=>({ items:[{ id:'other',state:'failed',target:{member_id:8},error:'failed' }] }), addListener:async()=>()=>{}, acknowledge:async({id})=>acked.push(id) };
  const unsubscribe=subscribeNativeUploadQueue(()=>{}); unsubscribe(); await hydrateNativeUploadQueue(transport);
  assert.equal(await consumeNativeUpload(transport,'other',{ songId:1 },async()=>{}),false);
  assert.deepEqual(acked,[]); resetNativeUploadQueueForTest();
});

test('duplicate snapshots do not notify subscribers or update a target state', async () => {
  resetNativeUploadQueueForTest(); let nativeListener; const item={ id:'song', state:'uploading', progress:10, target:{ song_id:4,rehearsal_id:null } };
  const transport={ kind:'native', listPending:async()=>({ items:[item] }), addListener:async(_name,listener)=>{nativeListener=listener;return()=>{};} };
  let notifications=0; const unsubscribe=subscribeNativeUploadQueue(()=>{notifications+=1;});
  await hydrateNativeUploadQueue(transport); const afterInitial=notifications;
  nativeListener({ ...item, target:{ ...item.target } });
  assert.equal(notifications,afterInitial);
  const previous=mergeNativeUploadState({},filterNativeUploads({ songId:4,rehearsalId:null }));
  assert.strictEqual(mergeNativeUploadState(previous,filterNativeUploads({ songId:4,rehearsalId:null })),previous);
  nativeListener({ id:'other',state:'uploading',progress:30,target:{ member_id:9 } });
  assert.strictEqual(mergeNativeUploadState(previous,filterNativeUploads({ songId:4,rehearsalId:null })),previous);
  unsubscribe(); resetNativeUploadQueueForTest();
});
