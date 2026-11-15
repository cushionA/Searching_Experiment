import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import {groupUrls, buildRequest, validateAnswer, safeUrl, reconcileCompletedPhase, recoverInterruptedPhase, runBoundedPool, coldWarmSkipReason, cachedMetadataResponse, discover} from './sitemap-agent-routing.mjs';

test('grouping preserves exact URL identity while compressing numeric detail paths', () => {
  const grouped = groupUrls(['https://example.test/products/1?variant=blue#spec', 'https://example.test/products/2?variant=red#spec', 'https://example.test/products/3?variant=green']);
  assert.equal(grouped.unique_urls, 3);
  assert.equal(grouped.groups.length, 1);
  assert.deepEqual(grouped.groups[0].pattern.segments, ['products', ':number']);
  assert.ok(grouped.groups[0].representatives.includes('https://example.test/products/1?variant=blue#spec'));
  assert.deepEqual(grouped.hub_candidates.find(row => row.url === 'https://example.test/products'), {url: 'https://example.test/products', count: 3, source: 'derived_prefix'});
});

test('trailing slash leaf slugs group without treating a path prefix as a proven edge', () => {
  const grouped = groupUrls(['https://example.test/catalog/a/', 'https://example.test/catalog/b/', 'https://example.test/catalog/c/']);
  assert.equal(grouped.groups.length, 1);
  assert.deepEqual(grouped.groups[0].pattern.segments, ['catalog', ':segment']);
  assert.equal(grouped.groups[0].pattern.trailing_slash, true);
  assert.equal(grouped.hub_candidates.find(row => row.url === 'https://example.test/catalog').source, 'derived_prefix');
});

test('agent answer is restricted to candidates actually included in its request', () => {
  const {request} = buildRequest({id: 'fixture', domain: 'example.test', practice: true, start_url: 'https://example.test/', tasks: ['Public product details']}, {urls: ['https://example.test/products/1', 'https://example.test/products/2']});
  const answer = {request_id: request.request_id, item_group_ids: ['g1'], listing_urls: ['https://example.test/products'], hub_urls: [], reason: 'derived listing hypothesis', insufficient_evidence: false};
  assert.equal(validateAnswer(request, answer).item_groups[0].id, 'g1');
  assert.throws(() => validateAnswer(request, {...answer, listing_urls: ['https://example.test/unpresented']}), /unpresented/);
  assert.throws(() => validateAnswer(request, {...answer, item_group_ids: ['hidden-group']}), /unpresented/);
});

test('URL-only request contains no observed HTML or invented page links', () => {
  const {request} = buildRequest({id: 'fixture', domain: 'example.test', practice: true, start_url: 'https://example.test/', tasks: ['Public product details']}, {urls: ['https://example.test/products/1'], html: '<secret-page-body>'});
  assert.ok(!JSON.stringify(request).includes('<secret-page-body>'));
  assert.equal(safeUrl('https://user:secret@example.test/'), false);
  assert.equal(safeUrl('javascript:alert(1)'), false);
});

test('only practice requests receive the training/demo scope guidance', () => {
  const inventory = {urls: ['https://example.test/a']};
  assert.match(buildRequest({id: 'practice', domain: 'example.test', practice: true, start_url: 'https://example.test/', tasks: ['Find public product records']}, inventory).request.task, /Training\/demo scope/);
  assert.doesNotMatch(buildRequest({id: 'news', domain: 'example.test', practice: false, start_url: 'https://example.test/', tasks: ['Find recent articles']}, inventory).request.task, /Training\/demo scope|operator blog/);
});

test('bounded worker pool honors its concurrency cap and preserves item order', async () => {
  let active = 0;
  let maximum = 0;
  const values = await runBoundedPool([0, 1, 2, 3, 4, 5, 6], 4, async value => {
    active++;
    maximum = Math.max(maximum, active);
    await new Promise(resolve => setTimeout(resolve, value === 0 ? 15 : 2));
    active--;
    return value * 2;
  });
  assert.equal(maximum, 4);
  assert.deepEqual(values, [0, 2, 4, 6, 8, 10, 12]);
  await assert.rejects(runBoundedPool([1], 5, async value => value), /invalid_site_concurrency/);
});

test('warm execution is skipped only after cold access refusal or page error', () => {
  assert.equal(coldWarmSkipReason(null), null);
  assert.equal(coldWarmSkipReason({status: 'html_saved'}), null);
  assert.equal(coldWarmSkipReason({status: 'no_valid_detail', selected_items: [{basic_html_check: {reason: 'http_503'}}]}), 'cold_access_or_error');
  assert.equal(coldWarmSkipReason({status: 'access_stopped', refusal: 'blocked_or_login'}), 'cold_access_or_error');
});

test('cached metadata replay verifies body hash and preserves refusal status without a network fetch', async t => {
  const root = temporaryRun(t);
  const directory = path.join(root, 'site');
  const body = Buffer.from('refused body');
  fs.mkdirSync(path.join(directory, 'metadata'), {recursive: true});
  fs.writeFileSync(path.join(directory, 'metadata', '1.body'), body);
  const row = {url: 'https://example.test/sitemap.xml', status: 403, file: 'metadata/1.body', sha256: crypto.createHash('sha256').update(body).digest('hex'), content_type: 'text/plain'};
  const cached = cachedMetadataResponse(directory, [row], row.url);
  assert.equal(cached.status, 403);
  assert.equal(cached.headers.get('content-type'), 'text/plain');
  assert.deepEqual(Buffer.from(await cached.arrayBuffer()), body);
  fs.writeFileSync(path.join(directory, 'metadata', '1.body'), 'changed');
  assert.equal(cachedMetadataResponse(directory, [row], row.url), null);
});

function temporaryRun(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sitemap-routing-resume-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(root).startsWith('sitemap-routing-resume-'));
    fs.rmSync(root, {recursive: true});
  });
  return root;
}

test('metadata acquisition parses saved response bytes and resumes without repeating HTTP', async t => {
  const root = temporaryRun(t);
  let requests = 0;
  let origin;
  const server = http.createServer((req, res) => {
    requests++;
    if (req.url === '/robots.txt') {
      res.setHeader('content-type', 'text/plain');
      res.end(`User-agent: *\nSitemap: ${origin}/sitemap.xml\n`);
    } else {
      res.setHeader('content-type', 'application/xml');
      res.end(`<urlset><url><loc>${origin}/products/1</loc></url></urlset>`);
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  t.after(() => server.close());
  const target = {id: 'fixture', start_url: origin, allowed_hosts: ['127.0.0.1'], practice: true};
  const protocol = {metadata_requests_per_site: 4, sitemap_documents_per_site: 2, metadata_bytes_per_response: 100000, robots_policy: 'sitemap_discovery_only'};
  assert.equal((await discover(root, target, protocol)).urls, 1);
  const inventoryFile = path.join(root, 'sites/fixture/inventory.json');
  fs.renameSync(inventoryFile, `${inventoryFile}.first`);
  assert.equal((await discover(root, target, protocol)).urls, 1);
  const inventory = JSON.parse(fs.readFileSync(inventoryFile, 'utf8'));
  assert.equal(requests, 2);
  assert.equal(inventory.acquisition_cost.prior_requests, 2);
  assert.equal(inventory.acquisition_cost.new_http_requests, 0);
  assert.equal(inventory.acquisition_cost.cache_reads, 2);
});

test('resume reconciles a completed phase missing from the root summary exactly once', t => {
  const root = temporaryRun(t);
  const directory = path.join(root, 'sites', 'fixture', 'cold');
  fs.mkdirSync(directory, {recursive: true});
  fs.writeFileSync(path.join(directory, 'result.json'), JSON.stringify({id: 'fixture', phase: 'cold', status: 'html_saved'}));
  assert.equal(reconcileCompletedPhase(root, {id: 'fixture'}, 'cold'), true);
  assert.equal(reconcileCompletedPhase(root, {id: 'fixture'}, 'cold'), true);
  assert.equal(fs.readFileSync(path.join(root, 'results.jsonl'), 'utf8').trim().split('\n').length, 1);
});

test('interrupted phase preserves evidence and carries document reservations into accounting', t => {
  const root = temporaryRun(t);
  const directory = path.join(root, 'artifacts', 'fixture', 'sitemap-agent-browser', 'cold');
  fs.mkdirSync(directory, {recursive: true});
  fs.writeFileSync(path.join(directory, 'progress.jsonl'), JSON.stringify({event: 'phase_start', document_cap: 9}) + '\n');
  fs.writeFileSync(path.join(directory, 'document-reservations.jsonl'), '{"ordinal":1}\n{"ordinal":2}\n');
  const result = recoverInterruptedPhase(root, {id: 'fixture'}, 'cold');
  assert.equal(result.document_requests_reserved_upper_bound, 2);
  assert.ok(fs.existsSync(path.join(root, result.partial_evidence, 'document-reservations.jsonl')));
  assert.equal(fs.existsSync(directory), false);
  assert.equal(recoverInterruptedPhase(root, {id: 'fixture'}, 'cold'), null);
});
