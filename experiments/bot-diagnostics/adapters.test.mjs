import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {ToolAdapter} from './adapters.mjs';
import {openFourplay} from './fourplay-runtime.mjs';
import {runScenario} from './scenario.mjs';

function adapterWithTimezone(observedTimezone, browserOptions = {}) {
  const results = [];
  const evidence = {policy:'diagnostic',result:result=>results.push(result)};
  let evaluations = 0;
  const page = {evaluate:async()=>{evaluations++;return observedTimezone;}};
  const browser = {page,context:{},runtime:{engine:'chromium'},ua:'fixture-agent',close:async()=>{}};
  let requestedOptions;
  const adapter = new ToolAdapter({tool:'patchright',site:{id:'fixture',origins:['http://127.0.0.1']},evidence,fixture:true,
    browserOpener:async(_tool,options)=>{requestedOptions=options;return browser;},
    clientOpener:async()=>({close:async()=>{}}),...browserOptions});
  return {adapter,results,get requestedOptions(){return requestedOptions;},get evaluations(){return evaluations;}};
}

test('ToolAdapter passes requested timezone and records the observed browser timezone',async()=>{
  const fixture=adapterWithTimezone('Asia/Tokyo',{timezoneId:'Asia/Tokyo'});
  await fixture.adapter.open();
  assert.equal(fixture.requestedOptions.timezoneId,'Asia/Tokyo');
  assert.deepEqual(fixture.results[0].runtime.timezone,{requested:'Asia/Tokyo',observed:'Asia/Tokyo',outcome:'supported'});
  assert.equal(fixture.results[0].outcome,'adapter_ready');
});

test('ToolAdapter records an unobserved timezone as unsupported capability',async()=>{
  const fixture=adapterWithTimezone('UTC',{timezoneId:'Asia/Tokyo'});
  await fixture.adapter.open();
  assert.deepEqual(fixture.results[0].runtime.timezone,{requested:'Asia/Tokyo',observed:'UTC',outcome:'unsupported_capability'});
  assert.equal(fixture.results[0].outcome,'unsupported_capability');
});

test('ToolAdapter rejects an invalid IANA timezone at construction',()=>{
  assert.throws(()=>adapterWithTimezone('UTC',{timezoneId:'Mars/Olympus_Mons'}),/invalid_timezone_id/);
});

test('ToolAdapter keeps the existing timezone behavior when timezoneId is omitted',async()=>{
  const fixture=adapterWithTimezone('UTC');
  await fixture.adapter.open();
  assert.equal(Object.hasOwn(fixture.requestedOptions,'timezoneId'),false);
  assert.equal(fixture.evaluations,0);
  assert.equal(Object.hasOwn(fixture.results[0].runtime,'timezone'),false);
  assert.equal(Object.hasOwn(fixture.results[0].capabilities,'timezoneId'),false);
  assert.equal(fixture.results[0].outcome,'adapter_ready');
});

function adapterWithObservation(options = {}) {
  const results = [];
  const evidence = {policy:'browser_observation',results,records:[],directory:'.',result:result=>results.push(result),blob:()=>null,flush:()=>{}};
  let navigatedAt;
  let elapsedAtContent;
  const page = {
    on:()=>{},off:()=>{},setRequestInterception:async()=>{},
    goto:async()=>{navigatedAt=Date.now();},url:()=> 'http://127.0.0.1/',
    content:async()=>{elapsedAtContent=Date.now()-navigatedAt;return '<html></html>';}
  };
  const browser = {page,context:{},kind:'obscura',runtime:{engine:'obscura'},ua:'fixture-agent',close:async()=>{}};
  const adapter = new ToolAdapter({tool:'patchright',site:{id:'fixture',origins:['http://127.0.0.1']},evidence,fixture:true,
    options:{captureScreenshots:false,...options},browserOpener:async()=>browser,
    clientOpener:async()=>({close:async()=>{}})});
  return {adapter,get elapsedAtContent(){return elapsedAtContent;},results};
}

test('ToolAdapter preserves the fixture observation default when observeMs is omitted',async()=>{
  const fixture=adapterWithObservation();
  await fixture.adapter.open();
  await fixture.adapter.accessLinkOnce('http://127.0.0.1/');
  assert.ok(fixture.elapsedAtContent>=90,`expected the fixture's 100ms observation, got ${fixture.elapsedAtContent}ms`);
});

test('ToolAdapter propagates explicit zero observation duration',async()=>{
  const fixture=adapterWithObservation({observeMs:0});
  await fixture.adapter.open();
  await fixture.adapter.accessLinkOnce('http://127.0.0.1/');
  assert.ok(fixture.elapsedAtContent<90,`expected immediate DOM capture, got ${fixture.elapsedAtContent}ms`);
});

test('ToolAdapter rejects invalid observeMs values at construction',()=>{
  for(const observeMs of [-1,6001,1.5,'0',null])
    assert.throws(()=>adapterWithObservation({observeMs}),/invalid_observe_ms/);
});

test('4play exposes its actual capability limits, skips the HTTP robots client, and enforces site scope',async()=>{
  let navigations=0,robotsOpens=0,closeCalls=0;
  const evidence={policy:'browser_observation',results:[],result(value){this.results.push(value);}};
  const browser={kind:'fourplay',ua:'Firefox',runtime:{engine:'firefox',headless:false},
    navigate:async()=>{navigations++;return {url:'https://site.example/',dom:'',responses:[],errors:[]};},close:async()=>{closeCalls++;}};
  const adapter=new ToolAdapter({tool:'4play',site:{id:'site',origins:['https://site.example'],links:{home:'https://site.example/'}},evidence,
    options:{selectors:true},
    browserOpener:async(_tool,options)=>{assert.equal(options.headful,true);return browser;},
    clientOpener:async()=>{robotsOpens++;throw new Error('4play must not create HTTP client');}});
  assert.equal(adapter.capabilities.headful,true);
  assert.equal(adapter.capabilities.selectorOperations,false);
  assert.equal(adapter.capabilities.challengeActions,false);
  assert.equal(adapter.capabilities.extensions,false);
  await adapter.open();
  assert.equal(evidence.results[0].outcome,'adapter_ready');
  assert.equal((await adapter.selectorProbe()).outcome,'unsupported_capability');
  await assert.rejects(adapter.goto('https://outside.example/'),/outside_site_scope/);
  assert.equal(navigations,0);
  assert.equal(robotsOpens,0);
  await adapter.close();
  assert.equal(closeCalls,1);
});

test('Camoufox fourplay uses native browser events without the HTTP helper and rejects unsupported capabilities',async()=>{
  let robotsOpens=0,closeCalls=0;
  const evidence={policy:'browser_observation',results:[],result(value){this.results.push(value);}};
  const browser={kind:'fourplay-native',ua:'Firefox',runtime:{engine:'firefox',headless:false},close:async()=>{closeCalls++;}};
  const adapter=new ToolAdapter({tool:'camoufox-fourplay',site:{id:'site',origins:['https://site.example']},evidence,
    browserOpener:async(_tool,options)=>{assert.equal(options.headful,true);return browser;},
    clientOpener:async()=>{robotsOpens++;throw new Error('native observation must not create HTTP client');}});
  assert.equal(adapter.capabilities.headful,true);
  assert.equal(adapter.capabilities.extensions,false);
  await adapter.open();
  assert.equal(evidence.results[0].outcome,'adapter_ready');
  assert.equal(robotsOpens,0);
  await adapter.close();
  assert.equal(closeCalls,1);
  assert.throws(()=>new ToolAdapter({tool:'camoufox-fourplay',site:{id:'site'},evidence,timezoneId:'Asia/Tokyo'}),/unsupported_capability:camoufox_fourplay_timezone/);
  assert.throws(()=>new ToolAdapter({tool:'camoufox-fourplay',site:{id:'site'},evidence,profile:{id:'pooled',session_pool:{max_sessions:2}}}),/unsupported_capability:camoufox_fourplay_session_pool/);
  assert.throws(()=>new ToolAdapter({tool:'camoufox-fourplay',site:{id:'site'},evidence,profile:{id:'custom',extensions:['/tmp/ext']}}),/unsupported_capability:camoufox_fourplay_extensions/);
});

function initializationFixture({origin='https://joshinweb.jp',sensorURL=null,postStatus=201,homepageStatus=200,initialStatus=403,revisitStatus=200,initialFinalURL=null,includeSensorEvents=true,sensorEventsBeforeMain=false}={}) {
  sensorURL ||= `${origin}/sensor.js`;
  const site={id:'joshin',origins:[origin],links:{home:`${origin}/`,targets:[`${origin}/shop/`]},
    session_initialization:{kind:'sensor_revisit',sensor_url:'https://joshinweb.jp/sensor.js'}};
  const evidence={policy:'browser_observation',records:[],results:[],sessionPolicyState:{schema:1,sites:{},cooldowns:{}},result(value){this.results.push(value);},flush(){}};
  const adapter=new ToolAdapter({tool:'camoufox-fourplay',site,evidence,profile:{id:'baseline'},
    browserOpener:async()=>({kind:'fourplay-native',ua:'Firefox',context:{},page:{},runtime:{},close:async()=>{}}),
    siteObserver:async(ev,tool,target)=>{
      const role=ev.stageContext.role;
      const status=role==='session_initialization_revisit'?revisitStatus:role==='homepage'?homepageStatus:initialStatus;
      const mainRecord=ev.records.length+(sensorEventsBeforeMain&&includeSensorEvents&&role!=='session_initialization_revisit'&&status===initialStatus?3:1);
      const finalURL=role!=='session_initialization_revisit'&&initialFinalURL?initialFinalURL:target.url;
      if(includeSensorEvents&&role!=='session_initialization_revisit'&&status===initialStatus) {
        const sensorIndex=sensorEventsBeforeMain?mainRecord-2:mainRecord+1;
        ev.records.push({index:sensorIndex,url:sensorURL,resource_type:'script',request_method:'GET',response_status:200,status:200});
        if(postStatus!==null) ev.records.push({index:sensorIndex+1,url:sensorURL,resource_type:'xhr',request_method:'POST',response_status:postStatus,status:postStatus});
      }
      ev.records.push({index:mainRecord,url:finalURL,resource_type:'document',request_method:'GET',response_status:status,status});
      ev.result({client:tool,target:target.name,role:ev.stageContext.role,url:target.url,final_url:finalURL,
        outcome:status===403?'access_denied_observed':'content_observed',main_record:mainRecord,http_status:status});
    }});
  adapter.browser={kind:'fourplay-native',ua:'Firefox',context:{},page:{},runtime:{},close:async()=>{}};
  return {adapter,evidence,site};
}

test('session initialization requires same-observation exact sensor GET 200 and POST 201 then revisits once',async()=>{
  const {adapter,evidence,site}=initializationFixture();
  const results=await runScenario({adapter,site});
  assert.equal(results.state,'navigation_completed');
  assert.equal(evidence.results.filter(result=>result.role==='session_initialization_revisit').length,1);
  assert.equal(evidence.results.find(result=>result.role==='target').http_status,403);
  assert.equal(evidence.results.find(result=>result.role==='session_initialization_revisit').http_status,200);
  const initialization=results.events.find(event=>event.role==='sessionInitialization'&&event.result.outcome==='session_initialization_revisited');
  assert.equal(initialization.result.result.outcome,'content_observed');
  assert.equal(initialization.result.rejected_document_url,site.links.targets[0]);
});

test('session initialization revisits the final same-origin denied document after a redirect',async()=>{
  const deniedURL='https://joshinweb.jp/top.html';
  const {adapter,evidence,site}=initializationFixture({initialFinalURL:deniedURL});
  const initial=await adapter.followLink(site.links.targets[0]);
  const outcome=await adapter.sessionInitialization(site.links.targets[0],initial);
  assert.equal(outcome.outcome,'session_initialization_revisited');
  assert.equal(evidence.results.at(-1).url,deniedURL);
  assert.deepEqual(outcome.requested_url,site.links.targets[0]);
  assert.equal(outcome.revisit_url,deniedURL);
});

test('session initialization accepts loopback HTTP only for fixtures',()=>{
  const site={id:'fixture',origins:['http://127.0.0.1'],links:{home:'http://127.0.0.1/',targets:[]},
    session_initialization:{kind:'sensor_revisit',sensor_url:'http://127.0.0.1/sensor.js'}};
  assert.doesNotThrow(()=>new ToolAdapter({tool:'camoufox-fourplay',site,evidence:{},fixture:true}));
  assert.throws(()=>new ToolAdapter({tool:'camoufox-fourplay',site,evidence:{},fixture:false}),/session_initialization_sensor_outside_site_scope/);
  assert.throws(()=>new ToolAdapter({tool:'camoufox-fourplay',site:{...site,origins:['http://outside.example'],
    session_initialization:{...site.session_initialization,sensor_url:'http://outside.example/sensor.js'}},evidence:{},fixture:true}),/session_initialization_sensor_outside_site_scope/);
});

test('session initialization rejects session-pool profiles that cannot preserve one session',()=>{
  const {site}=initializationFixture();
  assert.throws(()=>new ToolAdapter({tool:'camoufox',site,evidence:{},profile:{id:'pooled',session_pool:{max_pool_size:2}}}),
    /unsupported_capability:session_initialization_session_pool/);
});

test('session initialization ignores unconfigured, different sensor URL, or missing POST 201',async t=>{
  const cases=[
    ['different sensor URL',{sensorURL:'https://joshinweb.jp/other.js'}],
    ['cross-origin sensor',{sensorURL:'https://cdn.example/sensor.js'}],
    ['POST response absent',{postStatus:null}],
    ['POST response is not 201',{postStatus:403}],
  ];
  for(const [name,options] of cases) await t.test(name,async()=>{
    const {adapter,evidence,site}=initializationFixture(options);
    await adapter.open();
    await adapter.followLink(site.links.targets[0]);
    const initial=evidence.results.at(-1);
    const outcome=await adapter.sessionInitialization(site.links.home,initial);
    assert.equal(outcome.outcome,'session_initialization_not_applicable');
    assert.equal(evidence.results.filter(result=>result.role==='adapter_setup').length,1);
    assert.equal(evidence.results.filter(result=>result.role==='target').length,1);
    await adapter.close();
  });
});

test('session initialization stops after one denied revisit and does not revisit a bare 403',async()=>{
  const denied=initializationFixture({revisitStatus:403});
  const first=await denied.adapter.followLink(denied.site.links.targets[0]);
  const outcome=await denied.adapter.sessionInitialization(denied.site.links.targets[0],first);
  assert.equal(outcome.result.http_status,403);
  assert.equal(denied.evidence.results.filter(result=>result.role==='session_initialization_revisit').length,1);
  assert.equal((await denied.adapter.sessionInitialization(denied.site.links.targets[0],first)).outcome,'session_initialization_not_applicable');
  const bare=initializationFixture({postStatus:null});
  const observed=await bare.adapter.followLink(bare.site.links.targets[0]);
  assert.equal((await bare.adapter.sessionInitialization(bare.site.links.targets[0],observed)).outcome,'session_initialization_not_applicable');
  assert.equal(bare.evidence.results.length,1);
});

test('session initialization does not reuse sensor events from an earlier ledger range',async()=>{
  const {adapter,evidence,site}=initializationFixture({includeSensorEvents:false});
  evidence.records.push({index:1,url:'https://joshinweb.jp/sensor.js',resource_type:'script',request_method:'GET',response_status:200},
    {index:2,url:'https://joshinweb.jp/sensor.js',resource_type:'xhr',request_method:'POST',response_status:201});
  const initial=await adapter.followLink(site.links.targets[0]);
  const outcome=await adapter.sessionInitialization(site.links.targets[0],initial);
  assert.equal(outcome.outcome,'session_initialization_not_applicable');
  assert.equal(evidence.results.length,1);
});

test('session initialization ignores sensor responses ordered before the denied main document',async()=>{
  const {adapter,evidence,site}=initializationFixture({sensorEventsBeforeMain:true});
  const initial=await adapter.followLink(site.links.targets[0]);
  const outcome=await adapter.sessionInitialization(site.links.targets[0],initial);
  assert.equal(outcome.outcome,'session_initialization_not_applicable');
  assert.equal(evidence.results.length,1);
});

test('session initialization requires a matching denied HTTP result, not just ledger status',async()=>{
  const {adapter,site}=initializationFixture();
  const initial=await adapter.followLink(site.links.targets[0]);
  initial.outcome='content_observed';
  const outcome=await adapter.sessionInitialization(site.links.targets[0],initial);
  assert.equal(outcome.outcome,'session_initialization_not_applicable');
});

test('a denied revisit ends the scenario without advancing to later targets',async()=>{
  const {adapter,evidence,site}=initializationFixture({revisitStatus:403});
  site.links.targets.push('https://joshinweb.jp/next');
  const scenario=await runScenario({adapter,site});
  assert.equal(scenario.state,'site_rejected');
  assert.equal(evidence.results.filter(result=>result.role==='target').length,1);
  assert.equal(evidence.results.filter(result=>result.role==='session_initialization_revisit').length,1);
});

test('4play bridge opener authenticates diagnostics calls, passes proxy, and closes its session',async()=>{
  const oldDirect=process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD;
  const oldFile=process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD_FILE;
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'bot-diagnostics-fourplay-bridge-'));
  const passwordFile=path.join(root,'password.txt');
  fs.writeFileSync(passwordFile,'fixture-secret\n');
  delete process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD;
  process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD_FILE=passwordFile;
  const calls=[];
  let navigateCalls=0;
  const fetchImpl=async(url,options={})=>{
    calls.push({url,options});
    const data=url.endsWith('/health')?{browser_connected:true,ua:'Firefox',runtime:{engine:'firefox'}}:
      url.endsWith('/diagnostics/session')?{session_id:'session-1',ua:'Firefox',runtime:{engine:'firefox'}}:
      url.endsWith('/diagnostics/navigate')&&++navigateCalls===1?{url:'https://site.example/',dom:'<main>ready</main>',responses:[],errors:[]}:{ok:true};
    return new Response(JSON.stringify(data),{status:url.endsWith('/diagnostics/navigate')&&navigateCalls>1?503:200,
      headers:{'content-type':'application/json'}});
  };
  try {
    const browser=await openFourplay({fixture:true,headful:true,fetchImpl});
    assert.equal(browser.runtime.engine,'firefox');
    assert.equal(browser.runtime.transport,'4play');
    assert.equal(calls[0].url.endsWith('/health'),true);
    assert.equal(calls[0].options.headers.authorization,undefined);
    assert.ok(calls.slice(1).every(call=>call.options.headers.authorization==='Bearer fixture-secret'));
    assert.deepEqual(JSON.parse(calls[1].options.body),{proxy:null,fixture:true});
    const observed=await browser.navigate('https://site.example/',123);
    assert.equal(observed.dom,'<main>ready</main>');
    assert.deepEqual(JSON.parse(calls[2].options.body),{session_id:'session-1',url:'https://site.example/',observe_ms:123});
    await assert.rejects(browser.navigate('https://site.example/error',0),/fourplay_bridge_http_503/);
    await browser.close();
    assert.equal(calls[4].url.endsWith('/diagnostics/close'),true);
  } finally {
    if(oldDirect===undefined) delete process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD;
    else process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD=oldDirect;
    if(oldFile===undefined) delete process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD_FILE;
    else process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD_FILE=oldFile;
    fs.rmSync(root,{recursive:true,force:true});
  }
});

test('4play opener rejects grounding before making a bridge request',async()=>{
  await assert.rejects(openFourplay({profile:'grounding',headful:true,fetchImpl:async()=>{throw new Error('must_not_call');}}),/fourplay_grounding/);
});
