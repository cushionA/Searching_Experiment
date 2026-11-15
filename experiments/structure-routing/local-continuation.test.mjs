import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {spawn} from 'node:child_process';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {finalizeBudget, initialBudget, reserveBudget, safeUniqueTargets, selectObservedNext} from './local-continuation.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const runner = path.join(repo, 'experiments/structure-routing/local-continuation.mjs');

test('main and Stage C reservations stay separate and interrupted spend remains reserved', () => {
  const budget = initialBudget();
  reserveBudget(budget, 'main', 1);
  assert.equal(budget.main.remaining, 1705);
  assert.equal(budget.stageC.remaining, 276);
  assert.equal(finalizeBudget(budget, 'main', 1, 1), 1);
  reserveBudget(budget, 'stageC', 125);
  assert.equal(budget.stageC.remaining, 151);
  assert.equal(finalizeBudget(budget, 'stageC', 125, 0, true), 125);
  assert.equal(budget.stageC.spent, 249);
  assert.equal(budget.main.spent, 1795);
});

test('health records a local fixture response, robots check and rendered/source HTML', {timeout: 60000}, async () => {
  const server = http.createServer((request, response) => {
    if (request.url === '/robots.txt') { response.end('User-agent: *\nAllow: /'); return; }
    response.setHeader('content-type', 'text/html; charset=utf-8');
    response.end(`<html><head><title>Fixture</title></head><body><main><h1>Fixture health</h1><p>${'Public fixture content '.repeat(30)}</p></main></body></html>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'structure-local-health-'));
  const baseline = path.join(root, 'baseline');
  const run = path.join(root, 'run');
  fs.mkdirSync(baseline, {recursive: true});
  const target = {id: '01', domain: 'fixture.local', start_url: `http://127.0.0.1:${server.address().port}/`,
    allowed_hosts: ['127.0.0.1'], practice: true};
  fs.writeFileSync(path.join(baseline, 'targets.jsonl'), `${JSON.stringify(target)}\n`);
  const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  try {
    const outcome = await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [runner, '--mode', 'health', '--ids', '01', '--live', '--baseline', baseline, '--run', run, '--documents', '1'],
        {cwd: repo, env: {...process.env, CHROME_PATH: chrome}, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true});
      let stdout = '', stderr = '';
      child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
      child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
      child.once('error', reject);
      child.once('close', status => resolve({status, stdout, stderr}));
    });
    assert.equal(outcome.status, 0, outcome.stderr || outcome.stdout);
    const result = JSON.parse(fs.readFileSync(path.join(run, 'health-results.jsonl'), 'utf8').trim());
    assert.equal(result.status, 'healthy');
    assert.equal(result.observation.status, 200);
    assert.equal(result.observation.usable, true);
    assert.equal(result.document_requests, 1);
    assert.equal(result.document_responses, 1);
    assert.equal(result.external_llm_calls, 0);
    assert.equal(result.local_inference_calls, 0);
    assert.ok(result.robots_allowed);
    assert.ok(fs.existsSync(path.join(run, 'budget.json')));
    assert.equal(JSON.parse(fs.readFileSync(path.join(run, 'budget.json'), 'utf8')).main.remaining, 1705);
    const attemptDir = path.join(run, 'health', '01', fs.readdirSync(path.join(run, 'health', '01'))[0]);
    assert.ok(fs.readFileSync(path.join(attemptDir, result.observation.response_html), 'utf8').includes('Fixture health'));
    assert.ok(fs.readFileSync(path.join(attemptDir, result.observation.rendered_dom), 'utf8').includes('Fixture health'));
  } finally {
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(root, {recursive: true, force: true});
  }
});

test('repaired bulk copies only refined rules and reserves/settles Stage C cost', {timeout: 90000}, async () => {
  const server = http.createServer((request, response) => {
    if (request.url === '/robots.txt') { response.end('User-agent: *\nAllow: /'); return; }
    response.setHeader('content-type', 'text/html; charset=utf-8');
    if (request.url === '/') {
      response.end('<html><head><title>Catalog</title></head><body><main><ul><li class="card"><a href="/item/a">A</a></li><li class="card"><a href="/item/b">B</a></li></ul></main></body></html>');
      return;
    }
    response.end(`<html><head><title>Detail</title></head><body><main><h1>${request.url}</h1><p>${'Fixture detail '.repeat(20)}</p></main></body></html>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'structure-repaired-bulk-'));
  const baseline = path.join(root, 'baseline');
  const run = path.join(root, 'run');
  fs.mkdirSync(baseline, {recursive: true});
  const target = {id: '01', domain: 'fixture.local', start_url: `http://127.0.0.1:${server.address().port}/`,
    allowed_hosts: ['127.0.0.1'], practice: true};
  fs.writeFileSync(path.join(baseline, 'targets.jsonl'), `${JSON.stringify(target)}\n`);
  const refinedRules = path.join(run, 'refined-rules', '01', 'rules.json');
  fs.mkdirSync(path.dirname(refinedRules), {recursive: true});
  const rulesValue = {source_pages: [target.start_url], pagination: [],
    repeated_link_groups: [{selector: 'li.card a', content_region_count: 2, navigation_region_count: 0}]};
  fs.writeFileSync(refinedRules, JSON.stringify(rulesValue));
  const sourceDOM = path.join(run, 'health', '01', 'attempt-fixture', 'representative.dom.html');
  fs.mkdirSync(path.dirname(sourceDOM), {recursive: true});
  fs.writeFileSync(sourceDOM, '<html><body><main><h1>Representative fixture</h1></main></body></html>');
  const metadata = {variant: 'C1.1_navigation_region_fix', id: '01', source_dom: 'health\\01\\attempt-fixture\\representative.dom.html',
    source_dom_sha256: crypto.createHash('sha256').update(fs.readFileSync(sourceDOM)).digest('hex'), representative_pages: 1,
    new_document_loads: 0, html_cache_counted_as_acquisition: false, rule_learning_ms: 1,
    rule_sha256: crypto.createHash('sha256').update(fs.readFileSync(refinedRules)).digest('hex'), candidate_links: 2,
    navigation_links: 1, external_llm_calls: 0, local_model_inference_calls: 0, site_specific_selectors: 0,
    common_rule_repairs: 1, gold_input: false, source_url: target.start_url};
  fs.writeFileSync(path.join(path.dirname(refinedRules), 'metadata.json'), JSON.stringify(metadata));
  const priorResult = path.join(run, 'bulk-children', '01', 'artifacts', '01', 'C1', 'bulk', 'result.json');
  fs.mkdirSync(path.dirname(priorResult), {recursive: true});
  fs.writeFileSync(priorResult, JSON.stringify({document_attempts: 103, stop_reason: 'candidate_limit'}));
  fs.writeFileSync(path.join(run, 'bulk-results.jsonl'), `${JSON.stringify({id: '01', category: 'stageC_local_bulk', actual_documents: 103})}\n`);
  const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  try {
    const outcome = await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [runner, '--mode', 'repaired-bulk', '--id', '01', '--live', '--baseline', baseline, '--run', run],
        {cwd: repo, env: {...process.env, CHROME_PATH: chrome}, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true});
      let stdout = '', stderr = '';
      child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
      child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
      child.once('error', reject);
      child.once('close', status => resolve({status, stdout, stderr}));
    });
    assert.equal(outcome.status, 0, outcome.stderr || outcome.stdout);
    const wrapper = JSON.parse(fs.readFileSync(path.join(run, 'repair-bulk-results.jsonl'), 'utf8').trim());
    assert.equal(wrapper.variant, 'C1.1_navigation_region_fix');
    assert.equal(wrapper.child_run, path.join('repair-children', '01'));
    assert.equal(wrapper.reserved_documents, 125);
    assert.equal(wrapper.refinement.source_dom_sha256, metadata.source_dom_sha256);
    assert.equal(wrapper.refinement.rule_sha256, metadata.rule_sha256);
    assert.ok(wrapper.refinement.metadata_sha256);
    assert.ok(wrapper.actual_documents > 0 && wrapper.actual_documents <= 125);
    assert.equal(wrapper.bulk_result.external_llm_calls, 0);
    assert.ok(fs.existsSync(path.join(run, 'repair-children', '01', 'artifacts', '01', 'C1', 'cold', 'rules.json')));
    assert.ok(!fs.existsSync(path.join(run, 'repair-children', '01', 'gold-private')));
    const budget = JSON.parse(fs.readFileSync(path.join(run, 'budget.json'), 'utf8'));
    assert.equal(budget.stageC.reserved, 0);
    assert.equal(budget.stageC.spent, 124 + wrapper.actual_documents);
    assert.equal(budget.main.spent, 1794);
  } finally {
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(root, {recursive: true, force: true});
  }
});

test('frozen model-probe selection deduplicates safe URLs and preserves query/hash', () => {
  const hosts = new Set(['books.test']);
  assert.deepEqual(selectObservedNext([
    {source: 'https://books.test/', url: 'https://books.test/page2', text: 'next'},
    {source: 'https://books.test/', url: 'https://books.test/page3', text: 'previous'},
  ], 'https://books.test/'), {source: 'https://books.test/', url: 'https://books.test/page2', text: 'next'});
  assert.deepEqual(safeUniqueTargets([
    'https://books.test/p/1?sku=one#info', 'https://books.test/p/1?sku=one#info',
    '/cart/2', 'https://outside.test/p/2', '/p/3?q=two#review', '/p/4',
  ], 'https://books.test/page2?batch=1', hosts), [
    'https://books.test/p/1?sku=one#info', 'https://books.test/p/3?q=two#review', 'https://books.test/p/4',
  ]);
});

test('model-probe opens observed next then three frozen-selector targets under four-document budget', {timeout: 90000}, async () => {
  const requests = [];
  const server = http.createServer((request, response) => {
    requests.push({url: request.url, referer: request.headers.referer || null});
    if (request.url === '/robots.txt') { response.end('User-agent: *\nAllow: /'); return; }
    response.setHeader('content-type', 'text/html; charset=utf-8');
    if (request.url.startsWith('/list')) {
      response.end('<html><head><title>Page 2</title></head><body><h1>Page 2</h1><section class="cards">' +
        '<a href="/product/1?sku=one#info">one</a><a href="/product/1?sku=one#info">duplicate</a>' +
        '<a href="/cart/2">cart</a><a href="https://outside.test/product/9">outside</a>' +
        '<a href="/product/2?sku=two">two</a><a href="/product/3#review">three</a></section></body></html>');
      return;
    }
    response.end(`<html><head><title>Product</title></head><body><main><h1>Product ${request.url}</h1><p>${'Product detail '.repeat(20)}</p></main></body></html>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'structure-model-probe-'));
  const baseline = path.join(root, 'baseline');
  const run = path.join(root, 'run');
  fs.mkdirSync(baseline, {recursive: true});
  const origin = `http://127.0.0.1:${server.address().port}`;
  const entry = `${origin}/`;
  const nextURL = `${origin}/list?page=2`;
  const target = {id: '01', domain: 'books-fixture', start_url: entry, allowed_hosts: ['127.0.0.1'], practice: true};
  fs.writeFileSync(path.join(baseline, 'targets.jsonl'), `${JSON.stringify(target)}\n`);
  const refined = path.join(run, 'refined-rules', '01');
  fs.mkdirSync(refined, {recursive: true});
  const rulesFile = path.join(refined, 'rules.json');
  const rules = {source_pages: [entry], pagination: [{source: entry, url: nextURL, text: 'next', selector: 'li.next a'}]};
  fs.writeFileSync(rulesFile, JSON.stringify(rules));
  const sourceDOM = path.join(run, 'health', '01', 'attempt-fixture', 'entry.dom.html');
  fs.mkdirSync(path.dirname(sourceDOM), {recursive: true});
  fs.writeFileSync(sourceDOM, '<html><body><h1>Entry</h1></body></html>');
  const rulesMetadata = {variant: 'C1.1_navigation_region_fix', id: '01', source_url: entry, source_dom: 'health\\01\\attempt-fixture\\entry.dom.html',
    source_dom_sha256: crypto.createHash('sha256').update(fs.readFileSync(sourceDOM)).digest('hex'),
    rule_sha256: crypto.createHash('sha256').update(fs.readFileSync(rulesFile)).digest('hex'), gold_input: false};
  fs.writeFileSync(path.join(refined, 'metadata.json'), JSON.stringify(rulesMetadata));
  fs.writeFileSync(path.join(run, 'health-results.jsonl'), `${JSON.stringify({id: '01', status: 'healthy',
    observation: {url: entry, requested_url: entry, status: 200}})}\n`);
  const model = {status: 'ok', task: 'Navigate from a product catalog to a product detail page.', candidate_count: 94,
    group_count: 6, metrics: {device: 'cpu', inference_seconds: 2.034, forward_calls: 3, tokenized_inputs: 95, input_token_total: 11049},
    model: {repo_id: 'McGill-NLP/MiniLM-L6-dmr', revision: 'fixture-pinned-revision'},
    external_api_calls: 0, external_llm_calls: 0, future_page_model_calls: 0,
    frozen_rule: {selector: 'section.cards a[href]', score_aggregation: 'top_candidate_model_score'}};
  fs.writeFileSync(path.join(run, 'minilm-books-representative.json'), JSON.stringify(model));
  const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  try {
    const outcome = await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [runner, '--mode', 'model-probe', '--id', '01', '--live', '--baseline', baseline, '--run', run],
        {cwd: repo, env: {...process.env, CHROME_PATH: chrome}, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true});
      let stdout = '', stderr = '';
      child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
      child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
      child.once('error', reject);
      child.once('close', status => resolve({status, stdout, stderr}));
    });
    assert.equal(outcome.status, 0, outcome.stderr || outcome.stdout);
    const result = JSON.parse(fs.readFileSync(path.join(run, 'model-probe-results.jsonl'), 'utf8').trim());
    assert.equal(result.status, 'completed');
    assert.equal(result.document_reservation, 4);
    assert.equal(result.document_actual, 4);
    assert.equal(result.local_inference_calls, 0);
    assert.equal(result.external_llm_calls, 0);
    assert.equal(result.observations.length, 4);
    assert.equal(result.selector_matches, 6);
    assert.equal(result.target_count, 3);
    assert.equal(result.target_duplicates_removed, 1);
    const requested = result.observations.slice(1).map(row => row.requested_url);
    assert.ok(requested.includes(`${origin}/product/1?sku=one#info`));
    assert.ok(requested.includes(`${origin}/product/2?sku=two`));
    assert.ok(requested.includes(`${origin}/product/3#review`));
    assert.ok(requested.every(url => !url.includes('/cart/') && !url.includes('outside.test')));
    assert.ok(result.observations.slice(1).every(row => row.referer === nextURL));
    assert.ok(result.observations.every(row => row.expected_requested_vs_final && row.source_html_sha256 && row.rendered_dom_sha256));
    assert.ok(fs.existsSync(path.join(run, 'model-probe', '01', 'DOM')));
    const budget = JSON.parse(fs.readFileSync(path.join(run, 'budget.json'), 'utf8'));
    assert.equal(budget.main.spent, 1798);
    assert.equal(budget.stageC.spent, 124);
    const detailRequests = requests.filter(row => row.url.startsWith('/product/'));
    assert.equal(detailRequests.length, 3);
    assert.ok(detailRequests.every(row => row.referer === `${origin}/list?page=2`), JSON.stringify(detailRequests));
  } finally {
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(root, {recursive: true, force: true});
  }
});
