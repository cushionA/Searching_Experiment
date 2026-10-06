import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {completedDocument,waitForDocument}=createRequire(import.meta.url)('../fourget-selfhost/fourplay/navigation-gate.cjs');
const url='http://127.0.0.1/document';
const makeSession=()=>({tab:{id:2},container:{id:'container'},responses:[],errors:[]});
const tab=(href=url,status='complete')=>({id:2,container:'container',url:href,status});
const response=(href=url,status=200)=>({id:2,container:'container',type:'main_frame',url:href,status});
test('initial blank and unrelated response cannot complete navigation',()=>{
 const s=makeSession();s.responses=[response()];
 assert.equal(completedDocument(tab('about:blank'),s),false);
 for(const mismatch of [{id:3},{container:'other'},{type:'image'},{url:'http://127.0.0.1/other'}]) {
  s.responses=[{...response(),...mismatch}];assert.equal(completedDocument(tab(),s),false);
 }
 s.responses=[response()];assert.equal(completedDocument(tab(),s),true);
 assert.equal(completedDocument(tab(url,'loading'),s),false);
});
test('redirect requires matching final document and preserves error response completion',()=>{
 const s=makeSession(),final=url+'/final';s.responses=[response(url,302)];
 assert.equal(completedDocument(tab(final),s),false);
 s.responses.push(response(final,503));assert.equal(completedDocument(tab(final+'#anchor'),s),true);
});
test('HTTP 503 and favicon errors do not end the wait before document completion',async()=>{
 const s=makeSession();let time=0,polls=0;
 s.errors=[{id:2,container:'container',url,error:'HTTP/1.1 503 Service Unavailable'},
  {id:2,container:'container',url:url+'/favicon.ico',error:'NS_BINDING_ABORTED'}];
 const outcome=await waitForDocument(s,{requestedURL:url,timeoutMs:500,pollMs:100,now:()=>time,sleep:async ms=>{time+=ms;},getTabs:async()=>{
  if(++polls===1) return [tab('about:blank')];if(polls===2) return [tab(url,'loading')];s.responses=[response(url,503)];return [tab()];
 }});
 assert.equal(outcome.outcome,'complete');assert.equal(outcome.polls,3);assert.equal(outcome.elapsed_ms,200);
});
test('persistent blank or incomplete resource has a finite deadline',async()=>{
 for(const stuckTab of [tab('about:blank'),tab(url,'loading')]) {
  const s=makeSession();s.responses=[response()];let time=0;
  const r=await waitForDocument(s,{requestedURL:url,timeoutMs:300,pollMs:100,now:()=>time,sleep:async ms=>{time+=ms;},getTabs:async()=>[stuckTab]});
  assert.equal(r.outcome,'timeout');assert.equal(r.elapsed_ms,300);assert.equal(r.polls,3);
 }
});
test('stalled tab-list RPC is bounded, and scoped network failure can end early',async()=>{
 const s=makeSession();const start=performance.now();
 const r=await waitForDocument(s,{requestedURL:url,timeoutMs:20,getTabs:()=>new Promise(()=>{})});
 assert.equal(r.outcome,'timeout');assert.ok(performance.now()-start<1000);
 s.errors=[{id:2,container:'container',url,error:'NS_ERROR_CONNECTION_REFUSED'}];
 assert.equal((await waitForDocument(s,{requestedURL:url,getTabs:async()=>[tab('about:blank')]})).outcome,'navigation_failed');
});
