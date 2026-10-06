import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {ToolAdapter} from './adapters.mjs';
import {openFourplay} from './fourplay-runtime.mjs';

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
