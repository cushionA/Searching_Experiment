import test from 'node:test';
import assert from 'node:assert/strict';
import {ToolAdapter} from './adapters.mjs';

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
