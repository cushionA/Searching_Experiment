import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import {loadCrawler, runAction} from './representative-code.mjs';

function putJson(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');
}

function cleanup(t, root, prefix) {
  t.after(() => {
    const tmp = path.resolve(os.tmpdir());
    const resolved = path.resolve(root);
    assert.ok(resolved.startsWith(`${tmp}${path.sep}`) && path.basename(resolved).startsWith(prefix));
    fs.rmSync(resolved, {recursive: true, force: true});
  });
}

async function listen(server) {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${server.address().port}`;
}

function fixtureServer({entryStatus = 200} = {}) {
  const server = http.createServer((req, res) => {
    res.setHeader('content-type', 'text/html; charset=utf-8');
    if (req.url === '/entry') {
      res.statusCode = entryStatus;
      res.end(entryStatus === 200 ? '<title>Home</title><a href="/listing">Catalog</a>' : '<title>Forbidden</title><h1>Access denied</h1>');
      return;
    }
    if (req.url === '/listing') {
      res.end('<title>Catalog</title><h1>Catalog</h1><ul><li class="item"><a href="/item/1">One</a></li><li class="item"><a href="/item/2">Two</a></li></ul><div style="height:2000px"></div><script>window.addEventListener("scroll",()=>{if(!document.querySelector("#new-3")){for(const id of [3,4,5]){const n=document.createElement("li");n.id="new-"+id;n.className="item";n.innerHTML="<a href=/item/"+id+">New after scroll</a>";document.querySelector("ul").append(n)}}})</script>');
      return;
    }
    if (req.url === '/holdout') {
      res.end('<title>Holdout</title><h1>Holdout</h1><ul><li class="item"><a href="/item/8">Old</a></li></ul><div style="height:2000px"></div><script>window.addEventListener("scroll",()=>{if(!document.querySelector("#new-9")){for(const id of [9,10,11]){const n=document.createElement("li");n.id="new-"+id;n.className="item";n.innerHTML="<a href=/item/"+id+">New after scroll</a>";document.querySelector("ul").append(n)}}})</script>');
      return;
    }
    if (req.url.startsWith('/item/')) {
      const id = req.url.split('/').pop();
      res.end(`<title>Item ${id}</title><main><h1>Item ${id}</h1><p>${'Details '.repeat(20)}</p></main>`);
      return;
    }
    if (req.url === '/frame') { res.end('<title>frame</title>'); return; }
    res.statusCode = 404;
    res.end('<title>Not found</title><h1>Not found</h1>');
  });
  return server;
}

function makeRun(root, origin, chrome) {
  putJson(path.join(root, 'protocol.json'), {chrome_executable: chrome || 'C:/Program Files/Google/Chrome/Application/chrome.exe', robots_policy: 'sitemap_discovery_only',
    max_phase_seconds: 600, global_doc_cap: 150, practice_site_doc_cap: 40, real_site_doc_cap: 15, practice_site_ui_cap: 100, real_site_ui_cap: 30,
    max_sampling_scroll_actions: 3, max_concurrent_sites: 2, practice_wait_ms: 0, real_wait_ms: 0});
  fs.writeFileSync(path.join(root, 'targets.jsonl'), JSON.stringify({id: 'fixture', domain: '127.0.0.1', start_url: `${origin}/entry`, allowed_hosts: ['127.0.0.1'], practice: true, tasks: ['Open item details']}) + '\n');
}

test('representative sampling and frozen code crawl unseen scroll items in a fresh warm phase', async t => {
  const server = fixtureServer();
  const origin = await listen(server);
  t.after(() => server.close());
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'representative-code-'));
  cleanup(t, root, 'representative-code-');
  makeRun(root, origin);
  const dir = path.join(root, 'sites', 'fixture');

  const discovered = await runAction({runDir: root, action: 'discover', ids: ['fixture'], live: true});
  assert.equal(discovered.action, 'discover');
  const entry = JSON.parse(fs.readFileSync(path.join(dir, 'representative.json'), 'utf8'));
  assert.equal(entry.status, 200);
  assert.ok(entry.representative_html_projection);
  putJson(path.join(dir, 'sampling-answer.json'), {listing_url: `${origin}/listing`, item_selector: '.item a', max_scroll_actions: 1});
  await runAction({runDir: root, action: 'prepare', ids: ['fixture'], live: true});
  const reps = JSON.parse(fs.readFileSync(path.join(dir, 'representatives.json'), 'utf8'));
  assert.equal(reps.first_sample_url, `${origin}/item/1`);
  assert.ok(reps.initial_document_urls.includes(`${origin}/item/1`));
  putJson(path.join(dir, 'holdout-start.json'), {url: `${origin}/holdout`});
  await runAction({runDir: root, action: 'prepare', ids: ['fixture'], phase: 'warm', live: true});
  assert.ok(fs.existsSync(path.join(dir, 'evaluation', 'listing-after.json')));
  assert.ok(!fs.readFileSync(path.join(dir, 'code-request.json'), 'utf8').includes('/holdout'));
  fs.writeFileSync(path.join(dir, 'crawl.js'), `async function crawl(api, input) { if (input.max_loop_iterations < 1) throw new Error('missing loop limit'); const prior = await api.links('.item a'); const known = new Set(prior.map(row => row.url)); const after = await api.scroll(); for (const row of after.links) if (!known.has(row.url)) await api.save(row.url); }\n`);
  const cold = await runAction({runDir: root, action: 'execute', ids: ['fixture'], phase: 'cold', live: true});
  assert.equal(cold.outcomes[0].status, 'html_saved');
  const coldResult = JSON.parse(fs.readFileSync(path.join(dir, 'cold', 'result.json'), 'utf8'));
  assert.deepEqual(coldResult.selected_items.filter(item => item.saved).map(item => item.url), [3, 4, 5].map(id => `${origin}/item/${id}`));
  const code = fs.readFileSync(path.join(dir, 'crawl.js'));
  fs.appendFileSync(path.join(dir, 'crawl.js'), '\n');
  const frozen = await runAction({runDir: root, action: 'execute', ids: ['fixture'], phase: 'warm', live: true});
  assert.equal(frozen.outcomes[0].status, 'stopped');
  assert.match(frozen.outcomes[0].error, /frozen_code_changed/);
  fs.writeFileSync(path.join(dir, 'crawl.js'), code);
  const warm = await runAction({runDir: root, action: 'execute', ids: ['fixture'], phase: 'warm', live: true});
  assert.equal(warm.outcomes[0].status, 'html_saved');
  const result = JSON.parse(fs.readFileSync(path.join(dir, 'warm', 'result.json'), 'utf8'));
  assert.deepEqual(result.selected_items.filter(item => item.saved).map(item => item.url), [9, 10, 11].map(id => `${origin}/item/${id}`), JSON.stringify(result));
  assert.ok(result.selected_items.every(item => item.source_sha256 && item.rendered_sha256));
  const saved = result.selected_items.find(item => item.url === `${origin}/item/9`);
  assert.equal(saved.source_sha256, crypto.createHash('sha256').update(fs.readFileSync(path.resolve(root, saved.source_html))).digest('hex'));
  assert.ok(fs.existsSync(path.resolve(root, saved.rendered_dom)));
  const resultsFile = path.join(root, 'results.jsonl');
  const portableRows = fs.readFileSync(resultsFile, 'utf8').trim().split('\n').map(JSON.parse);
  for (const row of portableRows) for (const item of row.selected_items) for (const field of ['source_html', 'rendered_dom']) if (item[field]) item[field] = item[field].replaceAll('/', '\\');
  fs.writeFileSync(resultsFile, portableRows.map(row => JSON.stringify(row)).join('\n') + '\n');
  assert.deepEqual(await runAction({runDir: root, action: 'verify'}).then(r => r.failures), []);
});

test('blocked main document terminates discovery while child host denial leaves a valid main page', async t => {
  const blockedServer = fixtureServer({entryStatus: 403});
  const origin = await listen(blockedServer);
  t.after(() => blockedServer.close());
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'representative-blocked-'));
  cleanup(t, root, 'representative-blocked-');
  makeRun(root, origin);
  const blocked = await runAction({runDir: root, action: 'discover', ids: ['fixture'], live: true});
  assert.equal(blocked.outcomes[0].status, 'access_stopped');
  const ledger = fs.readFileSync(path.join(root, 'cost-ledger.jsonl'), 'utf8');
  assert.equal((await runAction({runDir: root, action: 'prepare', ids: ['fixture'], live: true})).outcomes[0].status, 'access_stopped');
  assert.equal((await runAction({runDir: root, action: 'execute', ids: ['fixture'], phase: 'cold', live: true})).outcomes[0].status, 'access_stopped');
  assert.equal(fs.readFileSync(path.join(root, 'cost-ledger.jsonl'), 'utf8'), ledger);

  const server = http.createServer((req, res) => {
    res.setHeader('content-type', 'text/html; charset=utf-8');
    res.end(`<title>Valid</title><h1>Valid</h1><iframe src="http://localhost:${server.address().port}/frame"></iframe><a href="/listing">Listing</a>`);
  });
  const validOrigin = await listen(server);
  t.after(() => server.close());
  const validRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'representative-child-'));
  cleanup(t, validRoot, 'representative-child-');
  makeRun(validRoot, validOrigin);
  const valid = await runAction({runDir: validRoot, action: 'discover', ids: ['fixture'], live: true});
  assert.equal(JSON.parse(fs.readFileSync(path.join(validRoot, 'sites/fixture/representative.json'), 'utf8')).status, 200);
  assert.equal(valid.outcomes[0].status, 'representative_saved');
  const denied = fs.readFileSync(path.join(validRoot, 'artifacts/fixture/discover/session-1/document-denials.jsonl'), 'utf8');
  assert.match(denied, /localhost/);
  assert.match(denied, /"main_frame":false/);
});

test('document cap stops before another navigation without reporting access refusal', async t => {
  const server = fixtureServer();
  const origin = await listen(server);
  t.after(() => server.close());
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'representative-budget-'));
  cleanup(t, root, 'representative-budget-');
  makeRun(root, origin);
  const protocolFile = path.join(root, 'protocol.json');
  putJson(protocolFile, {...JSON.parse(fs.readFileSync(protocolFile)), practice_site_doc_cap: 1});
  const dir = path.join(root, 'sites', 'fixture');
  putJson(path.join(dir, 'representatives.json'), {listing_url: `${origin}/listing`, initial_document_urls: []});
  putJson(path.join(dir, 'code-request.json'), {});
  fs.writeFileSync(path.join(dir, 'crawl.js'), 'async function crawl(api, input) { const links = await api.links(".item a"); await api.save(links[0].url); }\n');
  await runAction({runDir: root, action: 'execute', ids: ['fixture'], phase: 'cold', live: true});
  const result = JSON.parse(fs.readFileSync(path.join(dir, 'cold', 'result.json')));
  assert.equal(result.error, 'document_budget');
  assert.equal(result.status, 'error');
  assert.equal(result.refusal, null);
  assert.equal(result.cost.document_reserved, 1);
  assert.equal(result.selected_items.length, 0);
  assert.deepEqual((await runAction({runDir: root, action: 'verify'})).failures, []);
});

test('VM rejects process and network primitives in generated crawl code', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'representative-vm-'));
  cleanup(t, root, 'representative-vm-');
  const file = path.join(root, 'crawl.js');
  fs.writeFileSync(file, 'async function crawl(api, input) { return process.env; }');
  assert.throws(() => loadCrawler(file), /generated_code_outside_contract/);
  fs.writeFileSync(file, 'async function crawl(api, input) { return fetch(input.start_url); }');
  assert.throws(() => loadCrawler(file), /generated_code_outside_contract/);
});
