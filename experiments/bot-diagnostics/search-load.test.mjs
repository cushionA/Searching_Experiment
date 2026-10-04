import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Evidence,verify} from './evidence.mjs';
import {DEFAULT_MAX_EVIDENCE_BYTES,loadPlan,runSearchLoad,siteIDs,stages,validateLoadManifest} from './search-load.mjs';

function manifest({max_evidence_bytes=DEFAULT_MAX_EVIDENCE_BYTES,queries=['猫','日本語検索'],stageLimits,observe_ms}={}) {
  return {schema:1,queries,max_evidence_bytes,...(stageLimits?{stages:stageLimits}:{}),...(observe_ms!==undefined?{observe_ms}:{}),
    sites:siteIDs.map(id=>({id,tool:'patchright',origin:'http://127.0.0.1:39123',
    search_url:'http://127.0.0.1:39123/search?q={query}'}))};
}
function tempRun() {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'search-load-test-'));
  return {root,directory:path.join(root,'run'),close:()=>fs.rmSync(root,{recursive:true,force:true})};
}
function fakeClock() {
  let value=1_800_000_000_000;
  return {now:()=>value,sleep:async ms=>{value+=ms;},advance:ms=>{value+=ms;}};
}
function factory({clock,onGoto=()=>({outcome:'content_observed',http_status:200}),bodyBytes=0,instances=[],callLog=[]}={}) {
  return async({site,evidence})=>{
    const adapter={site,urls:[],opened:false,async open(){this.opened=true;},async goto(url){
      this.urls.push(url);callLog.push({site:site.id,url});const index=this.urls.length;clock.advance(50);
      const payload=onGoto({site,index,url});
      let main_record=null;
      if(bodyBytes){
        const record=evidence.reserve(`${site.tool}/${site.id}`,url,'main_document');
        evidence.finish(record,payload.http_status||200,{'content-type':'text/html'},Buffer.alloc(bodyBytes,65));
        main_record=record.index;
      }
      const result={...payload,main_record};evidence.result({client:site.tool,target:site.id,...result});return result;
    },async close(){this.opened=false;}};
    instances.push(adapter);return adapter;
  };
}

test('manifest validation fixes the ten-site set and browser-only site tool mapping',()=>{
  const valid=validateLoadManifest(manifest(),{fixture:true});
  assert.equal(valid.sites.length,10);
  assert.deepEqual(stages.map(stage=>stage.max_searches),[100,300,600]);
  assert.equal(loadPlan(valid).queries_per_site,1000);
  assert.equal(loadPlan(valid).max_total_searches,10000);
  const bounded=validateLoadManifest(manifest({stageLimits:[{id:'qps-0.2',qps:0.2,max_searches:10},
    {id:'qps-0.5',qps:0.5,max_searches:20},{id:'qps-1',qps:1,max_searches:70}],observe_ms:0}),{fixture:true});
  assert.equal(loadPlan(bounded).queries_per_site,100);
  assert.equal(loadPlan(bounded).max_total_searches,1000);
  assert.throws(()=>validateLoadManifest({...manifest(),sites:manifest().sites.slice(1)},{fixture:true}),/invalid_search_load_manifest/);
  assert.throws(()=>validateLoadManifest({...manifest(),sites:manifest().sites.map((site,index)=>index?site:{...site,tool:'impit'})},{fixture:true}),/invalid_site_config/);
  assert.throws(()=>validateLoadManifest({...manifest(),max_evidence_bytes:DEFAULT_MAX_EVIDENCE_BYTES+1},{fixture:true}),/invalid_evidence_limit/);
  assert.throws(()=>validateLoadManifest({...manifest(),max_duration_ms:6_600_001},{fixture:true}),/invalid_duration_limit/);
  assert.throws(()=>validateLoadManifest(manifest({stageLimits:[{id:'qps-0.2',qps:0.3,max_searches:10},
    {id:'qps-0.5',qps:0.5,max_searches:20},{id:'qps-1',qps:1,max_searches:70}]}),{fixture:true}),/invalid_stage_limits/);
  assert.throws(()=>validateLoadManifest(manifest({stageLimits:[{id:'qps-0.2',qps:0.2,max_searches:101},
    {id:'qps-0.5',qps:0.5,max_searches:20},{id:'qps-1',qps:1,max_searches:70}]}),{fixture:true}),/invalid_stage_limits/);
  assert.throws(()=>validateLoadManifest(manifest({observe_ms:6001}),{fixture:true}),/invalid_observe_ms/);
});

test('search caps, rotating queries, rate upper bounds, and one session per site',async()=>{
  const temp=tempRun(),clock=fakeClock(),instances=[],callLog=[];
  const stagePlan=[{id:'slow',qps:0.2,max_searches:2},{id:'fast',qps:1,max_searches:3}];
  try {
    await runSearchLoad({manifest:manifest(),directory:temp.directory,fixture:true,stagePlan,clock,
      adapterFactory:factory({clock,instances,callLog})});
    assert.equal(instances.length,10);
    assert.deepEqual(callLog.slice(0,10).map(call=>call.site),siteIDs);
    for(const adapter of instances) {
      assert.equal(adapter.urls.length,5);
      assert.deepEqual(adapter.urls.map(url=>new URL(url).searchParams.get('q')),['猫','日本語検索','猫','日本語検索','猫']);
    }
    const state=JSON.parse(fs.readFileSync(path.join(temp.directory,'search-load-state.json'),'utf8'));
    for(const site of Object.values(state.sites)) {
      assert.equal(site.measurements.length,5);
      assert.ok(site.measurements[1].start_interval_ms>=5000);
      assert.ok(site.measurements[2].start_interval_ms>=1000);
      assert.ok(site.measurements.every(row=>row.latency_ms===50));
    }
  } finally {temp.close();}
});

test('403, 429, challenge, auth-required, and timeout halt that site at first observation',async()=>{
  const cases=[
    ['google',{http_status:403,outcome:'access_denied_observed'},'http_403'],
    ['google',{http_status:429,outcome:'rate_limited_observed'},'http_429'],
    ['google',{http_status:200,outcome:'challenge_observed'},'challenge'],
    ['google',{http_status:200,outcome:'content_observed',visible_text_prefix:'AUTH_REQUIRED'},'auth_required'],
    ['google',{http_status:null,outcome:'navigation_unverified',navigation_error:{message:'Timeout 30000ms exceeded'}},'timeout'],
    ['google',{http_status:200,outcome:'content_observed',visible_text_prefix:'Anubis: Verifying your request...'},'verification_gate'],
    ['qwant',{http_status:200,outcome:'content_observed',visible_text_prefix:'This service is not yet available in your country'},'country_unavailable'],
  ];
  for(const [siteID,response,reason] of cases) {
    const temp=tempRun(),clock=fakeClock();
    try {
      await runSearchLoad({manifest:manifest(),directory:temp.directory,fixture:true,
        stagePlan:[{id:'probe',qps:1,max_searches:3}],clock,
        adapterFactory:factory({clock,onGoto:({site})=>site.id===siteID?response:{outcome:'content_observed',http_status:200}})});
      const state=JSON.parse(fs.readFileSync(path.join(temp.directory,'search-load-state.json'),'utf8'));
      assert.equal(state.sites[siteID].query_count,1);
      assert.equal(state.sites[siteID].stopped.reason,reason);
    } finally {temp.close();}
  }
});

test('response bytes are preserved and the next navigation stops after a single-navigation overshoot',async()=>{
  const temp=tempRun(),clock=fakeClock(),small=1024*1024,instances=[];
  try {
    await runSearchLoad({manifest:manifest({max_evidence_bytes:small}),directory:temp.directory,fixture:true,
      stagePlan:[{id:'probe',qps:1,max_searches:3}],clock,adapterFactory:factory({clock,bodyBytes:small+17,instances})});
    const state=JSON.parse(fs.readFileSync(path.join(temp.directory,'search-load-state.json'),'utf8'));
    assert.equal(state.observed_response_body_bytes,small+17);
    assert.ok(state.saved_evidence_bytes>small);
    assert.equal(state.sites.google.query_count,1);
    assert.equal(state.sites.google.stopped.reason,'saved_evidence_limit_before_navigation');
    const evidence=new Evidence(temp.directory,true);
    assert.equal(evidence.records[0].bytes_charged,small+17);
    assert.equal(fs.statSync(path.join(temp.directory,'blobs',evidence.records[0].body_sha256)).size,small+17);
  } finally {temp.close();}
});

test('global duration deadline stops before another navigation and interrupts a slow one',async()=>{
  const temp=tempRun(),clock=fakeClock(),instances=[];
  try {
    await runSearchLoad({manifest:{...manifest(),max_duration_ms:25},directory:temp.directory,fixture:true,
      stagePlan:[{id:'probe',qps:1,max_searches:3}],clock,
      adapterFactory:async({site,evidence})=>{
        const adapter={site,opened:false,async open(){this.opened=true;},async goto(url){
          if(site.id==='google') return new Promise(()=>{});
          evidence.result({client:site.tool,target:site.id,outcome:'content_observed',http_status:200});
          return {outcome:'content_observed',http_status:200};
        },async close(){this.opened=false;}};
        instances.push(adapter);return adapter;
      }});
    const state=JSON.parse(fs.readFileSync(path.join(temp.directory,'search-load-state.json'),'utf8'));
    assert.equal(state.global_stop.reason,'max_duration_during_navigation');
    assert.equal(state.sites.google.stopped.reason,'max_duration');
    assert.equal(state.sites.google.query_count,1);
    assert.equal(instances.length,1);
  } finally {temp.close();}
});

test('resume requires the identical conditions and a stopped run remains stopped',async()=>{
  const temp=tempRun(),clock=fakeClock(),instances=[];
  const current=manifest();
  try {
    await runSearchLoad({manifest:current,directory:temp.directory,fixture:true,
      stagePlan:[{id:'probe',qps:1,max_searches:1}],clock,adapterFactory:factory({clock,instances,
        onGoto:({site})=>site.id==='google'?{outcome:'access_denied_observed',http_status:403}:{outcome:'content_observed',http_status:200}})});
    const priorCalls=instances.reduce((sum,adapter)=>sum+adapter.urls.length,0);
    assert.equal(instances.find(adapter=>adapter.site.id==='google').urls.length,1);
    await assert.rejects(runSearchLoad({manifest:manifest({queries:['別の条件']}),directory:temp.directory,resume:true,fixture:true,
      stagePlan:[{id:'probe',qps:1,max_searches:1}],clock}),/resume_conditions_mismatch/);
    await runSearchLoad({manifest:current,directory:temp.directory,resume:true,fixture:true,
      stagePlan:[{id:'probe',qps:1,max_searches:1}],clock,adapterFactory:factory({clock,instances})});
    assert.equal(instances.reduce((sum,adapter)=>sum+adapter.urls.length,0),priorCalls);
  } finally {temp.close();}
});

test('write-ahead query reservation prevents an interrupted attempt from being resent',async()=>{
  const temp=tempRun(),clock=fakeClock(),instances=[],manifestInput=manifest();
  const stagePlan=[{id:'probe',qps:1,max_searches:2}];
  try {
    await assert.rejects(runSearchLoad({manifest:manifestInput,directory:temp.directory,fixture:true,stagePlan,clock,
      adapterFactory:factory({clock,instances}),beforeNavigation:()=>{throw new Error('simulated_process_interruption');}}),
    /simulated_process_interruption/);
    const interrupted=JSON.parse(fs.readFileSync(path.join(temp.directory,'search-load-state.json'),'utf8'));
    assert.equal(interrupted.sites.google.inflight.query_index,0);
    assert.equal(interrupted.sites.google.query_count,1);
    await runSearchLoad({manifest:manifestInput,directory:temp.directory,resume:true,fixture:true,stagePlan,clock,
      adapterFactory:factory({clock,instances})});
    const resumed=JSON.parse(fs.readFileSync(path.join(temp.directory,'search-load-state.json'),'utf8'));
    assert.equal(resumed.sites.google.query_count,1);
    assert.equal(resumed.sites.google.stopped.reason,'interrupted_attempt_no_retry');
    assert.equal(resumed.sites.google.measurements[0].outcome,'interrupted_attempt_no_retry');
    assert.equal(instances.find(adapter=>adapter.site.id==='google').urls.length,0);
  } finally {temp.close();}
});

test('an adapter execution error stops that site without further navigation',async()=>{
  const temp=tempRun(),clock=fakeClock(),instances=[];
  try {
    await runSearchLoad({manifest:manifest(),directory:temp.directory,fixture:true,
      stagePlan:[{id:'probe',qps:1,max_searches:3}],clock,
      adapterFactory:async options=>{
        const create=factory({clock,instances,onGoto:({site})=>{
          if(site.id==='google') throw new Error('adapter_navigation_failed');
          return {outcome:'content_observed',http_status:200};
        }});
        return create(options);
      }});
    const state=JSON.parse(fs.readFileSync(path.join(temp.directory,'search-load-state.json'),'utf8'));
    assert.equal(state.sites.google.query_count,1);
    assert.equal(state.sites.google.stopped.reason,'execution_error');
    assert.equal(instances.find(adapter=>adapter.site.id==='google').urls.length,1);
  } finally {temp.close();}
});

test('requested timezone must be observed before any site navigation',async()=>{
  const temp=tempRun(),clock=fakeClock(),instances=[],configured=manifest();
  configured.sites[0].timezoneId='Asia/Tokyo';
  try {
    await runSearchLoad({manifest:configured,directory:temp.directory,fixture:true,
      stagePlan:[{id:'probe',qps:1,max_searches:2}],clock,
      adapterFactory:async options=>{const adapter=await factory({clock,instances})(options);adapter.capabilities={timezoneId:false};return adapter;}});
    const state=JSON.parse(fs.readFileSync(path.join(temp.directory,'search-load-state.json'),'utf8'));
    assert.equal(instances[0].urls.length,0);
    assert.equal(state.sites.google.stopped.reason,'unsupported_timezone');
  } finally {temp.close();}
});

test('navigation checkpoint persists its main record before the measurement is written',async()=>{
  const temp=tempRun(),clock=fakeClock(),instances=[];let checkpointCount=0;
  try {
    await runSearchLoad({manifest:manifest(),directory:temp.directory,fixture:true,clock,
      stagePlan:[{id:'probe',qps:1,max_searches:1}],
      evidenceFactory:(directory,resume)=>{
        const evidence=new Evidence(directory,resume,{policy:'browser_observation',
          authorization:'offline search-load checkpoint test',flush_interval_ms:1000});
        const flush=evidence.flush.bind(evidence);
        evidence.flush=force=>{
          const statePath=path.join(directory,'search-load-state.json');
          const persistedState=fs.existsSync(statePath)?JSON.parse(fs.readFileSync(statePath,'utf8')):null;
          const inflight=Object.entries(persistedState?.sites||{}).find(([,site])=>site.inflight);
          flush(force);
          if(force&&inflight&&evidence.records.length) {
            const ledger=JSON.parse(fs.readFileSync(path.join(directory,'ledger.json'),'utf8'));
            assert.ok(ledger.records.some(record=>record.index===evidence.records.at(-1).index));
            assert.equal(inflight[1].measurements.length,0);
            checkpointCount++;
          }
        };
        return evidence;
      },adapterFactory:factory({clock,instances,bodyBytes:32})});
    const state=JSON.parse(fs.readFileSync(path.join(temp.directory,'search-load-state.json'),'utf8'));
    const ledger=JSON.parse(fs.readFileSync(path.join(temp.directory,'ledger.json'),'utf8'));
    assert.equal(checkpointCount,10);
    for(const siteID of siteIDs) {
      const mainRecord=state.sites[siteID].measurements[0].main_record;
      assert.ok(ledger.records.some(record=>record.index===mainRecord));
    }
    assert.equal(verify(temp.directory).ok,true);
  } finally {temp.close();}
});
