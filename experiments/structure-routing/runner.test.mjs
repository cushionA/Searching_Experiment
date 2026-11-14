import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {pageType, learnRules, selectLinks} from './runner.mjs';

test('classifies block and empty SPA separately from useful content', () => {
  assert.equal(pageType('', {text: 'Access denied', bodyLength: 1200, links: [], repeated: 0}, 'Access denied', 'https://example.test/'), 'block');
  assert.equal(pageType('', {text: '', bodyLength: 0, links: [], repeated: 0}, '', 'https://example.test/'), 'empty_spa');
  assert.equal(pageType('', {text: 'A'.repeat(800), bodyLength: 800, links: [], repeated: 0}, 'Docs', 'https://example.test/docs'), 'content');
});

test('flags a challenge page, blank SPA, and maintenance without treating ordinary prose as a block', () => {
  const base = {bodyLength: 500, links: [], repeated: 0, schemaCount: 0};
  assert.equal(pageType('', {...base, text: 'A'.repeat(2000), heading: 'The history of CAPTCHA'}, 'Article', 'https://example.test/', 200), 'detail');
  assert.equal(pageType('', {...base, text: 'Access denied', heading: 'Access denied'}, 'Access denied', 'https://example.test/', 403), 'block');
  assert.equal(pageType('', {...base, text: '', bodyLength: 0, links: []}, '', 'https://example.test/', 200), 'empty_spa');
  assert.equal(pageType('', {...base, text: 'Temporarily unavailable', heading: 'Maintenance'}, 'Maintenance', 'https://example.test/', 503), 'maintenance');
});

test('learns a repeated DOM link group and applies its selector to new URLs', () => {
  const pages = [{url: 'https://shop.test/list', links: [
    {url: 'https://shop.test/item/a', text: 'A', parent: {tag: 'li', classes: ['product']}},
    {url: 'https://shop.test/item/b', text: 'B', parent: {tag: 'li', classes: ['product']}},
    {url: 'https://shop.test/help', text: 'Help', parent: {tag: 'footer', classes: []}},
  ]}];
  const rules = learnRules(pages);
  assert.equal(rules.repeated_link_groups[0].selector, 'li.product a');
  const current = {links: [{url: 'https://shop.test/item/c', text: 'C', parent: {tag: 'li', classes: ['product']}},
    {url: 'https://shop.test/help', text: 'Help', parent: {tag: 'footer', classes: []}}]};
  const links = selectLinks(current, 'C1', rules, new Set(), new Set(['shop.test']));
  assert.deepEqual(links.map(x => x.url), ['https://shop.test/item/c']);
});

test('preserves URL query and fragment during host filtering', () => {
  const page = {links: [{url: 'https://shop.test/item?id=7#details', text: 'Item', parent: {tag: 'div', classes: []}}]};
  assert.equal(selectLinks(page, 'B1', {repeated_link_groups: [], pagination: []}, new Set(), new Set(['shop.test']))[0].url,
    'https://shop.test/item?id=7#details');
});

test('cold discovery, saved-path replay and frozen-rule generalization work in separate processes', {timeout: 240000}, async () => {
  const server = http.createServer((req, res) => {
    res.setHeader('content-type', 'text/html; charset=utf-8');
    if (req.url === '/robots.txt') { res.setHeader('content-type', 'text/plain'); res.end('User-agent: *\nAllow: /\n'); return; }
    if (req.url === '/') { res.end('<html><head><title>Home</title></head><body><main><a href="/listing">Catalog</a></main><script>const a=document.createElement("a");a.href="/client-only";a.textContent="JS route";document.querySelector("main").append(a)</script></body></html>'); return; }
    if (req.url.startsWith('/listing')) {
      res.end(`<html><head><title>Catalog</title></head><body><main><ul>${Array.from({length: 12}, (_, i) => `<li class="card"><a href="/item/${i + 1}?variant=full#spec">Item ${i + 1}</a></li>`).join('')}</ul></main></body></html>`); return;
    }
    if (req.url.startsWith('/item/')) { res.end(`<html><head><title>Item Detail</title></head><body><main><h1>Item</h1><p>${'Description '.repeat(80)}</p></main></body></html>`); return; }
    if (req.url === '/client-only') { res.end('<html><head><title>JS Detail</title></head><body><main><h1>Client Detail</h1></main></body></html>'); return; }
    res.statusCode = 404; res.end('<html><head><title>Not Found</title></head><body><h1>Not Found</h1></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const root = fs.mkdtempSync(path.join(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../lab-runs/structure-routing-20261115'), 'fixture-e2e-'));
  fs.mkdirSync(root, {recursive: true});
  const startURL = `http://127.0.0.1:${port}/`;
  fs.writeFileSync(path.join(root, 'targets.jsonl'), JSON.stringify({id: 'fixture', domain: 'localhost-fixture', start_url: startURL,
    allowed_hosts: ['127.0.0.1'], tasks: ['navigation', 'pagination'], practice: true}) + '\n');
  const runner = fileURLToPath(new URL('./runner.mjs', import.meta.url));
  const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  const invoke = phase => new Promise(resolve => {
    const child = spawn(process.execPath, [runner, '--run', root, '--ids', 'fixture', '--methods', 'B1,C1', '--observe-limit', '4', '--phase', phase, ...(phase === 'warm' ? ['--resume'] : [])],
      {env: {...process.env, CHROME_PATH: chrome}});
    let stdout = '', stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    const timeout = setTimeout(() => child.kill(), 180000);
    child.on('close', status => { clearTimeout(timeout); resolve({status, stdout, stderr}); });
  });
  try {
    const cold = await invoke('cold');
    const warm = await invoke('warm');
    const log = {cold: {status: cold.status, stdout: cold.stdout, stderr: cold.stderr}, warm: {status: warm.status, stdout: warm.stdout, stderr: warm.stderr}};
    fs.writeFileSync(path.join(root, 'fixture-e2e.log.json'), JSON.stringify(log, null, 2) + '\n');
    assert.equal(cold.status, 0, cold.stderr || cold.stdout);
    assert.equal(warm.status, 0, warm.stderr || warm.stdout);
    const results = fs.readFileSync(path.join(root, 'results.jsonl'), 'utf8').trim().split(/\r?\n/).map(JSON.parse);
    assert.deepEqual(results.map(x => `${x.method}:${x.mode}`).sort(), ['B1:G', 'B1:R', 'B1:cold', 'C1:G', 'C1:R', 'C1:cold'].sort());
    assert.ok(results.filter(x => x.mode === 'G').every(x => x.pages.length > 0 && x.pages.every(p => p.trial_type === 'graph_replay')));
    assert.ok(results.filter(x => x.mode === 'R').every(x => x.rule_provenance === 'frozen_cold_rules'));
    for (const method of ['B1', 'C1']) {
      const home = fs.readdirSync(path.join(root, 'artifacts', 'fixture', method, 'cold')).find(x => x.endsWith('.dom.html'));
      const raw = fs.readdirSync(path.join(root, 'artifacts', 'fixture', method, 'cold')).find(x => x.endsWith('.html') && !x.endsWith('.dom.html'));
      assert.ok(fs.readFileSync(path.join(root, 'artifacts', 'fixture', method, 'cold', home), 'utf8').includes('/client-only'));
      assert.ok(!fs.readFileSync(path.join(root, 'artifacts', 'fixture', method, 'cold', raw), 'utf8').includes('<a href="/client-only"'));
    }
    const generalization = results.filter(x => x.mode === 'R').flatMap(x => x.pages);
    for (const method of ['B1', 'C1']) {
      const coldURLs = new Set(results.find(x => x.method === method && x.mode === 'cold').pages.map(x => x.url));
      const novelPages = results.find(x => x.method === method && x.mode === 'R').pages;
      assert.ok(novelPages.some(x => /\/item\//.test(x.url) && !coldURLs.has(x.url) && x.status === 200));
      const methodRows = results.filter(x => x.method === method);
      assert.ok(methodRows.at(-1).total_method_documents <= 40);
      assert.ok(methodRows.at(-1).total_method_operations <= 100);
    }
    assert.ok(generalization.some(x => /\/item\//.test(x.url)));
  } finally { await new Promise(resolve => server.close(resolve)); }
});
