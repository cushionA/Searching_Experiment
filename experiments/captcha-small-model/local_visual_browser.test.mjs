import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {startFixtureServer} from './local_visual_browser.fixture.mjs';
import {runVisualChallenge} from './local_visual_browser.mjs';
import {repo} from '../bot-diagnostics/runtime.mjs';

const require = createRequire(path.join(repo, '.deps/bot-diagnostics/package.json'));
const {chromium} = require('playwright');
const browserPath = '/usr/bin/chromium';

async function setup(t, mode) {
  const fixture = await startFixtureServer();
  let browser, directory, closed=false;
  t.after(async () => {
    if(closed) return;
    closed=true;
    if(browser) await browser.close();
    await fixture.close();
    if(directory) await fs.rm(directory,{recursive:true,force:true});
  });
  browser = await chromium.launch({headless:true, executablePath:browserPath, args:['--no-sandbox'], proxy: process.env.HTTPS_PROXY ? {server:process.env.HTTPS_PROXY, bypass:'127.0.0.1,localhost'} : undefined});
  const page = await browser.newPage();
  await page.goto(`${fixture.origin}/?mode=${mode}`,{waitUntil:'domcontentloaded',timeout:5000});
  directory = await fs.mkdtemp(path.join(os.tmpdir(), 'local-visual-browser-'));
  return {page,directory};
}
const prediction = (selection, count=9) => ({predict:async () => ({selection,scores:Array(count).fill(0.99),elapsed_ms:1,model:'fixture'})});
const run = (args, predictor, extra={}) => runVisualChallenge({ ...args, predictor, initialWaitMs:500, pollMs:20, timeoutMs:5000, refreshTimeoutMs:600, maxAttempts:8, maxRounds:8, ...extra });

test('static A tiles map to 0-based indices, submit once, and avoid duplicate clicks', async t => {
  const x=await setup(t,'static');
  const result=await run(x,prediction([1,5]));
  assert.equal(result.outcome,'passed_after_image_challenge');
  assert.equal(result.attempts,1);
  assert.deepEqual(await x.page.evaluate(()=>window.record.clicks),[1,5]);
  assert.equal(await x.page.evaluate(()=>window.record.submits),1);
});

test('B 16-tile challenge captures and predicts the full grid', async t => {
  const x=await setup(t,'B'); let request;
  const predictor={predict:async r=>{request=r;return {selection:[0,3,7,12],scores:Array(16).fill(0.99),elapsed_ms:1,model:'fixture'}}};
  const result=await run(x,predictor);
  assert.equal(result.outcome,'passed_after_image_challenge');
  assert.equal(request.type,'B'); assert.equal(request.rows,4); assert.equal(request.columns,4);
  assert.equal(request.images.length,1);
  for(const image of request.images) assert.ok((await fs.stat(image)).size>0);
});

test('waits for dynamic tile refresh before requesting the next prediction', async t => {
  const x=await setup(t,'dynamic'); let calls=0;
  const result=await run(x,{predict:async r=>{calls++;return {selection:calls===1?[1]:[],scores:Array(9).fill(0.99),elapsed_ms:1,model:'fixture'};}});
  assert.equal(result.outcome,'passed_after_image_challenge');
  assert.ok(calls>=2); assert.equal(await x.page.evaluate(()=>window.record.refreshes),1);
});

test('reports refresh_timeout when selected tile never refreshes', async t => {
  const x=await setup(t,'no-refresh');
  const result=await run(x,prediction([1]),{refreshTimeoutMs:120});
  assert.equal(result.outcome,'refresh_timeout');
});

test('checkbox-only and absent-widget paths have separate outcomes', async t => {
  const c=await setup(t,'checkbox');
  assert.equal((await run(c,prediction([]))).outcome,'passed_without_image_challenge');
  const n=await setup(t,'none');
  assert.equal((await run(n,prediction([]))).outcome,'captcha_not_observed');
});

test('rejects invalid prediction indices without clicking', async t => {
  const x=await setup(t,'static');
  const result=await run(x,prediction([99]));
  assert.equal(result.outcome,'failed');
  assert.deepEqual(await x.page.evaluate(()=>window.record.clicks),[]);
});

test('repeated wrong answers stop with no_progress', async t => {
  const x=await setup(t,'static');
  let calls=0;
  const result=await run(x,{predict:async()=>{calls++;return {selection:[0],scores:Array(9).fill(0.99),elapsed_ms:1,model:'fixture'};}});
  assert.equal(result.outcome,'no_progress');
  assert.equal(await x.page.evaluate(()=>window.record.submits),1);
  assert.equal(calls,2);
});

test('surfaces a service rejection', async t => {
  const x=await setup(t,'error');
  assert.equal((await run(x,prediction([1,5]))).outcome,'service_rejected');
});

test('honors an exhausted attempt limit', async t => {
  const x=await setup(t,'static');
  const result=await run(x,prediction([0]),{maxAttempts:1});
  assert.equal(result.outcome,'attempt_limit');
});

test('discards a prediction when the displayed image changes during inference', async t => {
  const x = await setup(t, 'static');
  let calls = 0;
  const predictor = {predict: async () => {
    calls++;
    if (calls === 1) {
      const frame = x.page.frames().find(frame => frame !== x.page.mainFrame()
        && frame !== x.page.frames()[1]);
      await frame.evaluate(() => {
        const image = document.querySelector('.rc-imageselect-tile img');
        image.src = image.src.replace('data:image/svg+xml,', 'data:image/svg+xml;charset=utf-8,');
      });
    }
    return {selection: calls === 1 ? [0] : [1, 5], scores: Array(9).fill(0.9)};
  }};
  const result = await run(x, predictor);
  assert.equal(result.outcome, 'passed_after_image_challenge');
  assert.deepEqual(await x.page.evaluate(() => window.record.clicks), [1, 5]);
  assert.match(await fs.readFile(path.join(x.directory, 'events.jsonl'), 'utf8'), /stale_prediction/);
});

test('a hung prediction reaches the overall deadline without clicking', async t => {
  const x = await setup(t, 'static');
  const result = await run(x, {predict: () => new Promise(() => {})}, {timeoutMs: 1500});
  assert.equal(result.outcome, 'deadline_exceeded');
  assert.deepEqual(await x.page.evaluate(() => window.record.clicks), []);
});
