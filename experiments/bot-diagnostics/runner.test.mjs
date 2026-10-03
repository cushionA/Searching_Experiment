import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { classify, decodeBody, Evidence, verify, robots, robotsProbeDecision, httpSite, browserRequestBlockReason, browserProfileUserAgent } from './runner.mjs';

test('HTTP 202 AWS challenge is not a successful content response', () => {
  const c = classify(202, '<script src="https://example.token.awswaf.com/challenge.js"></script><script>AwsWafIntegration.getToken()</script>');
  assert.equal(c.outcome, 'challenge_observed');
  assert.equal(c.signals[0].name, 'aws_waf_challenge');
});
test('security-check response retains concrete Cloudflare evidence', () => {
  const c = classify(403, '<title>Security Check</title>Please enable JavaScript', {'cf-mitigated': 'challenge'});
  assert.equal(c.outcome, 'challenge_observed');
  assert.equal(c.site_block_reason, null);
});
test('HTTP 200 visible human verification text is classified as a challenge', () => {
  const c = classify(200, '<title>kagi.com</title><main>Verifying you’re human. This may take a few seconds.</main>');
  assert.equal(c.outcome, 'challenge_observed');
  assert.equal(c.signals[0].name, 'human_verification');
  assert.match(c.visible_text_prefix, /Verifying you’re human/);
  const scriptOnly = classify(200, '<script>const message="Verifying you\'re human";</script><main>Search results</main>');
  assert.equal(scriptOnly.outcome, 'content_observed');
});
test('explicit bot-abuse IQ-test challenge is detected without matching general IQ-test text', () => {
  const challenge = classify(200, '<main>IQ test has been enabled due to bot abuse on the network. Solving this IQ test will let you make 100 searches today.</main>');
  assert.equal(challenge.outcome, 'challenge_observed');
  assert.equal(challenge.signals[0].name, 'iq_test_challenge');
  assert.equal(classify(200, '<main>Our guide explains how an IQ test works and how to prepare.</main>').outcome, 'content_observed');
});
test('shop pages mentioning robots, confirmation, and CAPTCHA scripts are not challenges', () => {
  const c = classify(200, '<title>ショップ</title>クーポンの確認 ロボット掃除機 <script>const captcha=true</script>');
  assert.equal(c.outcome, 'content_observed');
});
test('Shift_JIS content is decoded before classifying and extracting the title', () => {
  assert.equal(decodeBody(Buffer.from([0x82,0xa0]), {'content-type':'text/html; charset=Shift_JIS'}), 'あ');
});
test('errors remain charged on resume; exhausted budgets cannot be reset', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bot-diagnostics-test-'));
  const directory = path.join(root, 'run');
  try {
    let e = new Evidence(directory);
    e.fail(e.reserve('patchright/amazon', 'https://www.amazon.co.jp/', 'main_document'), new Error('TLS failure'));
    e = new Evidence(directory, true);
    assert.equal(e.budgets['patchright/amazon'].requests, 1);
    for (let i = 0; i < 3; i++) e.fail(e.reserve('patchright/amazon', 'https://www.amazon.co.jp/', 'main_document'), new Error('TLS failure'));
    assert.throws(() => e.reserve('patchright/amazon', 'https://www.amazon.co.jp/', 'main_document'), /byte_budget/);
    assert.equal(verify(directory).ok, true);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
test('verification finds a corrupted response body', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bot-diagnostics-test-'));
  const directory = path.join(root, 'run');
  try {
    const e = new Evidence(directory);
    const record = e.reserve('impit/homes', 'https://www.homes.co.jp/', 'main_document');
    e.finish(record, 200, {'content-type':'text/html'}, Buffer.from('original'));
    assert.equal(verify(directory).ok, true);
    fs.writeFileSync(path.join(directory, 'blobs', record.body_sha256), 'changed');
    assert.equal(verify(directory).ok, false);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('robots redirects are charged to one key and follow only explicitly allowed origins', async () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'bot-diagnostics-robots-'));
  try {
    const e=new Evidence(path.join(root,'run'));
    const calls=[];
    const client={get:async url=>{
      calls.push(url);
      const status=calls.length===1?302:200;
      const headers=status===302?{'location':'https://rules.example/robots.txt'}:{'content-type':'text/plain'};
      const body=Buffer.from(status===302?'':'User-agent: *\nDisallow: /private');
      return {status,headers:new Headers(headers),body:new ReadableStream({start(c){c.enqueue(body);c.close();}})};
    }};
    const target={url:'https://site.example/private',robots_redirect_origins:['https://rules.example']};
    const result=await robots(e,'patchright/demo',client,target,'test-agent');
    assert.equal(result.outcome,'robots_denied');
    assert.equal(result.robots_redirect_trace.length,1);
    assert.deepEqual(calls,['https://site.example/robots.txt','https://rules.example/robots.txt']);
    assert.equal(e.budgets['patchright/demo'].requests,2);
    assert.ok(e.records.every(r=>r.key==='patchright/demo'));
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('robots cross-origin redirects outside the allowlist make no request to that origin', async () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'bot-diagnostics-robots-'));
  try {
    const e=new Evidence(path.join(root,'run')), calls=[];
    const client={get:async url=>{calls.push(url);return {status:302,headers:new Headers({location:'https://outside.example/robots.txt'}),body:new ReadableStream({start(c){c.close();}})};}};
    const result=await robots(e,'patchright/demo',client,{url:'https://site.example/'},'test-agent');
    assert.equal(result.outcome,'robots_unavailable');
    assert.equal(result.robots_error,'redirect_outside_allowed_origins');
    assert.deepEqual(calls,['https://site.example/robots.txt']);
    assert.equal(e.budgets['patchright/demo'].requests,1);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('robots unavailable probe admits only configured unavailable cases, never explicit denial', () => {
  const target={robots_unavailable_probe:true};
  assert.equal(robotsProbeDecision(target,{outcome:'robots_unavailable'}),true);
  assert.equal(robotsProbeDecision(target,{outcome:'robots_invalid_html'}),true);
  assert.equal(robotsProbeDecision(target,{outcome:'robots_denied'}),false);
  assert.equal(robotsProbeDecision(target,{outcome:'robots_delay_requires_review'}),false);
  assert.equal(robotsProbeDecision({},{outcome:'robots_unavailable'}),false);
});

test('HTTP sites remain fail-closed when robots is unavailable', async () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'bot-diagnostics-robots-'));
  try {
    const e=new Evidence(path.join(root,'run')), calls=[];
    const client={get:async url=>{calls.push(url);return {status:403,headers:new Headers(),body:new ReadableStream({start(c){c.close();}})};}};
    await httpSite(e,'impit',{name:'demo',url:'https://site.example/'},client);
    assert.deepEqual(calls,['https://site.example/robots.txt']);
    assert.equal(e.results.at(-1).outcome,'robots_unavailable');
    assert.equal(e.budgets['impit/demo'].requests,1);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('browser observation HTTP path skips robots and records cross-origin redirect hops on the same key', async () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'bot-diagnostics-observation-'));
  try {
    const e=new Evidence(path.join(root,'run'),false,{policy:'browser_observation',authorization:'offline runner test'}), calls=[];
    const client={get:async url=>{
      calls.push(url);
      const redirect=url==='https://site.example/start';
      const body=Buffer.from(redirect?'':'<main>Search results</main>');
      return {status:redirect?302:200,headers:new Headers(redirect?{location:'https://cdn.example/results'}:{'content-type':'text/html'}),
        body:new ReadableStream({start(c){c.enqueue(body);c.close();}})};
    }};
    await httpSite(e,'impit',{name:'demo',url:'https://site.example/start'},client);
    assert.deepEqual(calls,['https://site.example/start','https://cdn.example/results']);
    assert.deepEqual(e.records.map(r=>r.url),calls);
    assert.ok(e.records.every(r=>r.key==='impit/demo'));
    assert.equal(e.results.at(-1).robots_outcome,'not_enforced_browser_observation');
    assert.equal(e.results.at(-1).http_status,200);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('browser observation admits cross-origin POST assets and redirects without diagnostic UA suffix', () => {
  assert.equal(browserRequestBlockReason({observation:true,url:'https://cdn.example/font.woff2',origin:'https://site.example',
    method:'POST',resourceType:'font',navigation:true,redirectCount:99,robotsDenied:true}),null);
  assert.equal(browserProfileUserAgent('Mozilla/5.0 Chrome/149','browser_observation'),'Mozilla/5.0 Chrome/149');
});

test('legacy browser profile keeps same-origin GET, resource, robots, and redirect gates', () => {
  const common={origin:'https://site.example'};
  assert.equal(browserRequestBlockReason({...common,url:'https://site.example/form',method:'POST'}),'non_get');
  assert.equal(browserRequestBlockReason({...common,url:'https://cdn.example/app.js'}),'outside_scope');
  assert.equal(browserRequestBlockReason({...common,url:'https://site.example/a.png',resourceType:'image'}),'resource_policy');
  assert.equal(browserRequestBlockReason({...common,url:'https://site.example/private',robotsDenied:true}),'robots_denied');
  assert.equal(browserRequestBlockReason({...common,url:'https://site.example/next',navigation:true,redirectCount:4}),'redirect_limit');
  assert.match(browserProfileUserAgent('Mozilla/5.0 Chrome/149'),/DiscoveryLab/);
});
