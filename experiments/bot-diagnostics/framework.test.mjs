import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {injectURL,prepare,classifyGate,runScenario,parseCLI,latestOutcomes,execute} from './framework.mjs';
import {openBrowser} from './runner.mjs';
import {validateSteps,performSteps} from './operations.mjs';
import {ToolAdapter} from './adapters.mjs';
import {providerHints} from './providers.mjs';

const fixtureSite = {id:'fixture',links:{home:'https://example.com/',targets:['https://example.com/a','https://example.com/b']},selectors:{primary:'#search'}};
test('Camoufox joins the same observation scenario and rejects grounding before browser startup',()=>{
  const original=JSON.parse(fs.readFileSync(new URL('./sites.json',import.meta.url)));
  const manifest={...original,tools:['playwright-baseline','camoufox']};
  const planned=prepare(manifest);
  assert.deepEqual(planned.sites,prepare({...manifest,tools:['playwright-baseline']}).sites);
  assert.deepEqual(planned.profiles,prepare({...manifest,tools:['playwright-baseline']}).profiles);
  assert.equal(new ToolAdapter({tool:'camoufox',site:fixtureSite,evidence:{}}).capabilities.headful,true);
  assert.equal(new ToolAdapter({tool:'camoufox',site:fixtureSite,evidence:{}}).capabilities.extensions,false);
  assert.throws(()=>prepare(manifest,{executionPolicy:'grounding'}),/unsupported_capability:camoufox_grounding/);
  assert.deepEqual(JSON.parse(fs.readFileSync(new URL('./sites.json',import.meta.url))).tools,original.tools);
});
test('4play is admitted as a browser tool but its grounding and session-pool paths are rejected',()=>{
  const original=JSON.parse(fs.readFileSync(new URL('./sites.json',import.meta.url)));
  const manifest={...original,tools:['4play']};
  assert.deepEqual(prepare(manifest).tools,['4play']);
  assert.throws(()=>prepare(manifest,{executionPolicy:'grounding'}),/unsupported_capability:fourplay_grounding/);
  assert.throws(()=>prepare({...manifest,profiles:[{id:'pooled',session_pool:{max_sessions:2}}]}),/unsupported_capability:fourplay_session_pool/);
});
test('URL parameters cannot insert a new host or query separator',()=>{
  assert.equal(injectURL('https://example.com/search?q={query}',{query:'x&next=https://outside.example/'}),
    'https://example.com/search?q=x%26next%3Dhttps%3A%2F%2Foutside.example%2F');
  assert.throws(()=>injectURL('https://example.com/{missing}',{}),/missing_parameter/);
});
test('Google is optional and ordered after the other sites; all paths stay in declared origins',()=>{
  const manifest = JSON.parse(fs.readFileSync(new URL('./sites.json',import.meta.url)));
  assert.equal(prepare(manifest).sites.some(s=>s.id==='google'),false);
  assert.equal(prepare(manifest,{includeGoogle:true}).sites.at(-1).id,'google');
  manifest.sites[0].links.targets=['https://outside.example/'];
  assert.throws(()=>prepare(manifest),/outside_site_scope/);
});
test('roles use the same adapter instance across homepage and injected URLs',async()=>{
  const calls=[];
  const adapter={tool:'fake',sessionCookie:null,
    homepage:async url=>{calls.push(url);adapter.sessionCookie='present';return {outcome:'content_observed'};},
    followLink:async url=>{assert.equal(adapter.sessionCookie,'present');calls.push(url);return {outcome:'content_observed'};}};
  const result=await runScenario({adapter,site:fixtureSite});
  assert.equal(result.state,'navigation_completed');
  assert.deepEqual(calls,[fixtureSite.links.home,...fixtureSite.links.targets]);
});
test('TLS/measurement failure stops navigation without being labeled a vendor rejection',async()=>{
  const adapter={tool:'fake',homepage:async()=>({outcome:'navigation_unverified'}),followLink:()=>assert.fail('must not navigate')};
  assert.equal((await runScenario({adapter,site:fixtureSite})).state,'preflight_or_measurement_failure');
  assert.equal(classifyGate({outcome:'robots_denied'}),'robots_stop');
});
test('an unconfirmed challenge operation cannot trigger a homepage revisit',async()=>{
  const adapter={tool:'fake',homepage:async()=>({outcome:'content_observed'}),
    followLink:async()=>({outcome:'challenge_observed'}),
    recoverSimpleChallenge:()=>({outcome:'postcondition_failed'}),returnHome:()=>assert.fail('no confirmed recovery')};
  const result=await runScenario({adapter,site:fixtureSite});
  assert.equal(result.state,'capture_challenge');
  assert.equal(result.events.at(-1).result.outcome,'postcondition_failed');
});
test('a vendor/widget hint cannot fabricate a proprietary score or prove a challenge',()=>{
  const result=providerHints({headers:{'cf-ray':'test'},html:'<script src="https://example.com/recaptcha/api.js"></script>'});
  assert.equal(result.proprietary_score,null);
  assert.ok(result.hints.every(h=>h.kind!=='challenge'));
  assert.equal(providerHints({headers:{'cf-mitigated':'challenge'}}).hints[0].kind,'challenge');
});
test('plan flags do not disappear into a positional directory and invalid options fail',()=>{
  assert.deepEqual(parseCLI(['plan','--all-options']).flags,['--all-options']);
  assert.equal(parseCLI(['run','--selectors','output']).directory,'output');
  assert.throws(()=>parseCLI(['run']),/directory_required/);
  assert.throws(()=>parseCLI(['plan','--unknown']),/Usage/);
});
test('bot diagnostics defaults to normal observation and grounding limits require an explicit mode',()=>{
  const manifest=JSON.parse(fs.readFileSync(new URL('./sites.json',import.meta.url)));
  assert.equal(prepare(manifest).options.executionPolicy,'browser_observation');
  assert.equal(prepare(manifest).options.headful,true);
  assert.equal(prepare(manifest).options.headfulExplicit,false);
  assert.equal(prepare(prepare(manifest)).options.headfulExplicit,false);
  assert.equal(prepare(prepare(manifest),{headful:true}).options.headfulExplicit,true);
  assert.equal(prepare(manifest,{executionPolicy:'grounding'}).options.headful,false);
  assert.equal(prepare({...manifest,options:{executionPolicy:'grounding'}}).options.executionPolicy,'grounding');
  assert.equal(prepare(manifest,{executionPolicy:'grounding',headful:true}).options.headfulExplicit,true);
  manifest.limits.requests_per_tool_site=1;
  assert.equal(prepare(manifest).options.executionPolicy,'browser_observation');
  assert.throws(()=>prepare(manifest,{executionPolicy:'grounding'}),/limits_must_match_executor/);
  assert.ok(parseCLI(['plan','--grounding']).flags.includes('--grounding'));
});
test('headful flag reaches the browser adapter and requires a display without launching a browser',async()=>{
  const manifest=JSON.parse(fs.readFileSync(new URL('./sites.json',import.meta.url)));
  const planned=prepare(manifest,{headful:true});
  assert.equal(planned.options.headful,true);
  assert.equal(planned.options.headfulExplicit,true);
  assert.ok(parseCLI(['run','output','--headful']).flags.includes('--headful'));
  const originalDisplay=process.env.DISPLAY,originalWayland=process.env.WAYLAND_DISPLAY;
  delete process.env.DISPLAY;delete process.env.WAYLAND_DISPLAY;
  try {await assert.rejects(openBrowser('patchright',{headful:true}),/headful_requires_display/);}
  finally {
    if(originalDisplay!==undefined)process.env.DISPLAY=originalDisplay;
    if(originalWayland!==undefined)process.env.WAYLAND_DISPLAY=originalWayland;
  }
  let browserOptions,setup;
  const adapter=new ToolAdapter({tool:'patchright',site:fixtureSite,evidence:{result:value=>setup=value},options:{headful:true},
    browserOpener:async(_tool,options)=>{browserOptions=options;return {ua:'fixture',runtime:{headless:false,display:':fixture',viewport:{width:1280,height:720}},close:async()=>{}};},
    clientOpener:async()=>({close:async()=>{}})});
  await adapter.open();
  assert.equal(browserOptions.headful,true);
  assert.equal(setup.headless,false);assert.equal(setup.display,':fixture');
  assert.deepEqual(setup.viewport,{width:1280,height:720});
  await adapter.close();
});
test('headful-unsupported browser arms are recorded without a runtime failure or launch',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'headful-unsupported-'));
  try {
    const manifest=prepare({schema:1,tools:['rebrowser-lightpanda'],options:{headful:true},sites:[
      {id:'fixture',origins:['http://localhost'],links:{home:'http://localhost/',targets:[]}}]}, {fixture:true});
    assert.equal(manifest.options.headfulExplicit,true);
    const result=await execute({manifest,directory:path.join(root,'run'),fixture:true});
    assert.equal(result.verification.ok,true);
    assert.equal(result.verification.records,0);
    assert.equal(result.has_runtime_failures,false);
    assert.equal(result.outcomes[0].state,'unsupported_capability');
    const rows=JSON.parse(fs.readFileSync(path.join(root,'run','pipeline-results.json')));
    assert.equal(rows[0].capability,'headful');
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
test('Lightpanda profiles keep one tool budget and the selected release for humanlike input',()=>{
  const manifest=JSON.parse(fs.readFileSync(new URL('./lightpanda-sites.json',import.meta.url)));
  const plan=prepare(manifest,{humanlike:true});
  assert.deepEqual(plan.tools,['rebrowser-lightpanda']);
  assert.deepEqual(plan.profiles.at(-1).lightpanda,{release:'1.0.0',profile:'compat'});
  manifest.profiles[0].lightpanda.profile='invented';
  assert.throws(()=>prepare(manifest),/invalid_lightpanda_profile/);
  manifest.profiles[0].lightpanda.profile='baseline';
  manifest.tools.push('patchright');
  assert.throws(()=>prepare(manifest),/lightpanda_profile_requires_lightpanda/);
});
test('operations require bounded steps and a positive postcondition for clicks',()=>{
  assert.throws(()=>validateSteps([{op:'click',selector:'#button'}]),/postcondition_required/);
  assert.throws(()=>validateSteps(Array(4).fill({op:'fill',selector:'#query',value:'x'})),/three_operations/);
  const manifest=JSON.parse(fs.readFileSync(new URL('./sites.json',import.meta.url)));
  assert.equal(prepare(manifest,{selectors:true,humanlike:true,extensions:true}).profiles.length,3);
  assert.equal(prepare(manifest,{selectors:true}).sites[0].operations[0].value,'USBケーブル');
});
test('confirmed recovery returns home, waits, and retries only the original target once',async()=>{
  const calls=[];let challenged=false;
  const adapter={tool:'fake',homepage:async()=>({outcome:'content_observed'}),
    followLink:async url=>{calls.push(['target',url]);if(!challenged){challenged=true;return {outcome:'challenge_observed'};}return {outcome:'content_observed'};},
    recoverSimpleChallenge:async()=>{calls.push(['recover']);return {outcome:'recovery_confirmed'};},
    returnHome:async url=>{calls.push(['home',url]);return {outcome:'content_observed'};}};
  const result=await runScenario({adapter,site:fixtureSite,recoveryPlan:{max_attempts:1,wait_seconds:0}});
  assert.equal(result.state,'navigation_completed');
  assert.deepEqual(calls,[['target',fixtureSite.links.targets[0]],['recover'],['home',fixtureSite.links.home],
    ['target',fixtureSite.links.targets[0]],['target',fixtureSite.links.targets[1]]]);
});
test('a later challenge cannot reset the recovery attempt budget',async()=>{
  let attempts=0;const hits={};
  const adapter={tool:'fake',homepage:async()=>({outcome:'content_observed'}),
    followLink:async url=>({outcome:(hits[url]=(hits[url]||0)+1)===1?'challenge_observed':'content_observed'}),
    recoverSimpleChallenge:async()=>{attempts++;return {outcome:'recovery_confirmed'};},returnHome:async()=>({outcome:'content_observed'})};
  const result=await runScenario({adapter,site:fixtureSite,recoveryPlan:{max_attempts:1,wait_seconds:0}});
  assert.equal(attempts,1);assert.equal(result.state,'recovery_budget_exhausted');
});
test('a successful API return does not establish an unchanged DOM effect',async()=>{
  const page={evaluate:async(_,arg)=>typeof arg==='string'?{count:1,tag:'button',type:'button',text:'Apply'}:true,
    locator:()=>({click:async()=>{}})};
  const result=await performSteps({page,kind:'playwright'},[{op:'click',selector:'#apply',expect:{kind:'text',selector:'#result',value:'applied'}}]);
  assert.equal(result.outcome,'effect_not_established');assert.equal(result.observations[0].api_completed,true);
  assert.equal(result.observations[0].effect_verified,false);
});
test('a recovery attempt in another profile remains charged to the same tool/site',async()=>{
  const evidence={results:[{client:'patchright',target:'fixture',profile:'baseline',role:'recover_simple_challenge'}]};
  const adapter=new ToolAdapter({tool:'patchright',site:{...fixtureSite,recovery:{kind:'simple_button',selector:'#solve'}},evidence,
    profile:{id:'humanlike'},options:{recoverSimple:true,recoveryMaxAttempts:1}});
  assert.equal((await adapter.recoverSimpleChallenge()).outcome,'recovery_budget_exhausted');
});
test('a resumed successful cell supersedes its failure without losing the history',()=>{
  const history=[{tool:'patchright',site:'fixture',profile:'extension',state:'environment_policy_blocked'},
    {tool:'patchright',site:'fixture',profile:'extension',state:'navigation_completed'}];
  assert.equal(latestOutcomes(history).length,1);
  assert.equal(latestOutcomes(history)[0].state,'navigation_completed');
  assert.equal(history.length,2);
});
