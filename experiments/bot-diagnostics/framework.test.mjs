import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {injectURL,prepare,classifyGate,runScenario} from './framework.mjs';
import {providerHints} from './providers.mjs';

const fixtureSite = {id:'fixture',links:{home:'https://example.com/',targets:['https://example.com/a','https://example.com/b']},selectors:{primary:'#search'}};
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
test('a challenge is captured and operations stay deferred; no homepage revisit is invented',async()=>{
  const adapter={tool:'fake',homepage:async()=>({outcome:'content_observed'}),
    followLink:async()=>({outcome:'challenge_observed'}),
    recoverSimpleChallenge:()=>({outcome:'deferred_by_user'}),returnHome:()=>assert.fail('no confirmed recovery')};
  const result=await runScenario({adapter,site:fixtureSite});
  assert.equal(result.state,'capture_challenge');
  assert.equal(result.events.at(-1).result.outcome,'deferred_by_user');
});
test('a vendor/widget hint cannot fabricate a proprietary score or prove a challenge',()=>{
  const result=providerHints({headers:{'cf-ray':'test'},html:'<script src="https://example.com/recaptcha/api.js"></script>'});
  assert.equal(result.proprietary_score,null);
  assert.ok(result.hints.every(h=>h.kind!=='challenge'));
  assert.equal(providerHints({headers:{'cf-mitigated':'challenge'}}).hints[0].kind,'challenge');
});
