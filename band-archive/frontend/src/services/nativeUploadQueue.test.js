import assert from 'node:assert/strict';
import test from 'node:test';
import { consumeNativeUpload, filterNativeUploads, hydrateNativeUploadQueue, mergeNativeUploadState, nativeTargetMatches, resetNativeUploadQueueForTest, subscribeNativeUploadQueue, syncNativeProcessingStatus } from './nativeUploadQueue.js';

test('native queue hydrates once and fans out one listener snapshot', async () => {
  resetNativeUploadQueueForTest(); let list = 0; let nativeListener; const acked = [];
  const transport = { kind: 'native', listPending: async () => { list += 1; return { items: [{ id: 'done', state: 'completed', target: { song_id: 3 } }] }; }, addListener: async (_name, fn) => { nativeListener = fn; return () => {}; }, acknowledge: async ({ id }) => acked.push(id) };
  const received = []; const unsubscribe = subscribeNativeUploadQueue((value) => received.push(value));
  await hydrateNativeUploadQueue(transport); await hydrateNativeUploadQueue(transport);
  assert.equal(list, 1); assert.equal(received.at(-1)[0].target.song_id, 3); nativeListener({ id: 'live', state: 'uploading', progress: 10, target: { song_id: 3 } }); assert.equal(received.at(-1).find((item) => item.id === 'live').progress, 10);
  await consumeNativeUpload(transport,'done',{ songId:3 },async () => {}); await consumeNativeUpload(transport,'done',{ songId:3 },async () => {}); assert.deepEqual(acked,['done']); unsubscribe(); resetNativeUploadQueueForTest();
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
