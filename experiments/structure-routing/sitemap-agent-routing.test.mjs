import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {groupUrls, buildRequest, validateAnswer, safeUrl, reconcileCompletedPhase, recoverInterruptedPhase} from './sitemap-agent-routing.mjs';

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

function temporaryRun(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sitemap-routing-resume-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(root).startsWith('sitemap-routing-resume-'));
    fs.rmSync(root, {recursive: true});
  });
  return root;
}

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
