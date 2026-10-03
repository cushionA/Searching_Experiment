#!/usr/bin/env node
// Local-only regression for Obscura's native document redirect allowance.
// Usage: node obscura-redirect-smoke.mjs OUTPUT_DIRECTORY
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {ToolAdapter} from './adapters.mjs';
import {Evidence, verify} from './evidence.mjs';
import {snapshotSources, sha} from './runtime.mjs';

const outputArg = process.argv[2];
if (!outputArg || process.argv.length !== 3 || outputArg.startsWith('--')) {
  throw new Error('Usage: node obscura-redirect-smoke.mjs OUTPUT_DIRECTORY');
}
const output = path.resolve(outputArg);
if (fs.existsSync(output)) throw new Error(`Refusing to overwrite ${output}`);

const hits = [];
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  hits.push({method: request.method, path: url.pathname, at: new Date().toISOString()});
  response.setHeader('cache-control', 'no-store');
  if (url.pathname === '/robots.txt') {
    response.end('User-agent: *\nAllow: /\n');
    return;
  }
  const match = /^\/(long|short)\/(\d+)$/.exec(url.pathname);
  if (match) {
    const [, chain, rawHop] = match;
    const hop = Number(rawHop);
    const lastHop = chain === 'long' ? 4 : 3;
    if (hop < lastHop) {
      response.writeHead(302, {location: `/${chain}/${hop + 1}`});
      response.end(`redirect ${chain} ${hop}`);
    } else {
      response.setHeader('content-type', 'text/html; charset=utf-8');
      response.end(`<!doctype html><title>${chain}-final</title><p id="landing">${chain}-final</p>`);
    }
    return;
  }
  response.writeHead(404);
  response.end('not found');
});

const checkResults = [];
function check(name, fn) {
  try { fn(); checkResults.push({name, ok: true}); }
  catch (error) { checkResults.push({name, ok: false, error: String(error?.stack || error)}); }
}

let adapter;
let activeObservation = null;
const requestEvents = [];
try {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  const site = {id: 'obscura-redirect-fixture', origins: [origin],
    links: {home: `${origin}/short/0`, targets: []}};
  const evidence = new Evidence(output);
  evidence.save('sources.json', snapshotSources(evidence, fileURLToPath(import.meta.url)));
  evidence.save('fixture.json', {origin, routes: {
    long: '4 redirects; expected to stop before /long/4',
    short: '3 redirects; expected to reach /short/3',
  }});

  adapter = new ToolAdapter({tool: 'obscura-patched', site, evidence, fixture: true});
  await adapter.open();
  const requestListener = request => {
    if (!activeObservation) return;
    let chain = [];
    try { chain = request.redirectChain().map(previous => previous.url()); } catch {}
    requestEvents.push({observation: activeObservation, url: request.url(),
      method: request.method(), resource_type: request.resourceType(),
      is_navigation: request.isNavigationRequest(), redirect_chain: chain});
  };
  adapter.browser.page.on('request', requestListener);

  activeObservation = 'over-limit';
  const longResult = await adapter.goto(`${origin}/long/0`);
  activeObservation = null;
  const longReceipts = hits.filter(hit => hit.path.startsWith('/long/')).map(hit => hit.path);
  const longBlocked = longResult.blocked_requests || [];
  check('over-limit chain receipts stop before hop four', () =>
    assert.deepEqual(longReceipts, ['/long/0', '/long/1', '/long/2', '/long/3']));
  check('over-limit chain records redirect_limit for hop four', () =>
    assert(longBlocked.some(item => item.url === `${origin}/long/4` && item.reason === 'redirect_limit')));

  activeObservation = 'within-limit-reset';
  const shortResult = await adapter.goto(`${origin}/short/0`);
  activeObservation = null;
  const shortReceipts = hits.filter(hit => hit.path.startsWith('/short/')).map(hit => hit.path);
  check('three-hop chain succeeds after the prior observation exhausted its allowance', () => {
    assert.deepEqual(shortReceipts, ['/short/0', '/short/1', '/short/2', '/short/3']);
    assert.equal(shortResult.final_url, `${origin}/short/3`);
    assert.equal(shortResult.http_status, 200);
    assert.equal(adapter.browser.page.url(), `${origin}/short/3`);
  });
  const landing = await adapter.browser.page.$eval('#landing', node => node.textContent);
  check('allowed landing DOM is present', () => assert.equal(landing, 'short-final'));

  const ledger = JSON.parse(fs.readFileSync(path.join(output, 'ledger.json'), 'utf8'));
  const receiptCounts = new Map();
  for (const hit of hits) {
    const key = `${hit.method} ${hit.path}`;
    receiptCounts.set(key, (receiptCounts.get(key) || 0) + 1);
  }
  const recordCounts = new Map();
  for (const record of ledger.records) {
    const key = `GET ${new URL(record.url).pathname}`;
    recordCounts.set(key, (recordCounts.get(key) || 0) + 1);
  }
  check('evidence ledger counts match fixture receipts including robots', () =>
    assert.deepEqual(Object.fromEntries([...recordCounts].sort()), Object.fromEntries([...receiptCounts].sort())));

  const longEvents = requestEvents.filter(event => event.observation === 'over-limit' && /\/long\//.test(event.url));
  check('Puppeteer exposes the empty native redirect-chain metadata used by the fallback guard', () => {
    assert(longEvents.length >= 4);
    assert(longEvents.every(event => event.is_navigation && event.redirect_chain.length === 0));
  });

  const verification = verify(output);
  evidence.save('verification.json', verification);
  const summary = {
    outcome: checkResults.every(result => result.ok) && verification.ok ? 'passed' : 'failed',
    origin,
    runtime: adapter.browser.runtime,
    server_receipts: hits,
    puppeteer_requests: requestEvents,
    long_navigation: longResult,
    within_limit_navigation: shortResult,
    evidence: {ledger_records: ledger.records.length, budgets: ledger.budgets, verification},
    checks: checkResults,
    binary_sha256: await sha(fs.readFileSync(adapter.browser.runtime.executable)),
  };
  evidence.save('redirect-smoke.json', summary);
  console.log(JSON.stringify({directory: output, outcome: summary.outcome,
    checks: checkResults.map(({name, ok}) => ({name, ok})),
    receipts: hits.map(hit => hit.path), verification}, null, 2));
  if (summary.outcome !== 'passed') process.exitCode = 1;
} catch (error) {
  const summary = {outcome: 'error', error: String(error?.stack || error),
    server_receipts: hits, puppeteer_requests: requestEvents, checks: checkResults};
  if (fs.existsSync(output)) fs.writeFileSync(path.join(output, 'redirect-smoke.json'), JSON.stringify(summary, null, 2) + '\n');
  console.error(JSON.stringify(summary, null, 2));
  process.exitCode = 1;
} finally {
  activeObservation = null;
  try { await adapter?.close(); } catch {}
  if (server.listening) await new Promise(resolve => server.close(resolve));
}
