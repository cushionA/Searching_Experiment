import test from 'node:test';
import assert from 'node:assert/strict';
import {allowed, linksFromDom, selectedUrls} from './all-candidates.mjs';

const target = {allowed_hosts: ['example.test']};
const base = 'https://example.test/list?page=2';
const html = '<html><body><nav><a href="/categories/a">Category</a></nav><main><article><h3><a href="/item/1?variant=blue#spec">Item one</a></h3></article><article><h3><a href="/item/2?variant=red#spec">Item two</a></h3></article><a href="?page=3">Next</a></main></body></html>';

test('saved CSS selects actual links and preserves query values and hash', () => {
  const urls = selectedUrls({kind: 'selector', selector: 'article h3 a[href]'}, html, base, {}, target);
  assert.deepEqual(urls, ['https://example.test/item/1?variant=blue#spec', 'https://example.test/item/2?variant=red#spec']);
});

test('missing matches remain a failure instead of substituting all page links', () => {
  assert.deepEqual(selectedUrls({kind: 'selector', selector: '.nonexistent'}, html, base, {}, target), []);
});

test('boundary excludes other hosts, credentials and consequential routes', () => {
  assert.equal(allowed('https://example.test/item/1?q=a#part', target), true);
  for (const url of ['https://other.test/item/1', 'javascript:alert(1)', 'https://user:pass@example.test/item/1', 'https://example.test/checkout', 'https://example.test/login'])
    assert.equal(allowed(url, target), false);
});

test('DOM link features distinguish content from navigation', () => {
  const links = linksFromDom(html, base);
  assert.equal(links[0].region, 'nav');
  assert.equal(links[1].region, 'main');
  assert.equal(links[1].parent.tag, 'h3');
  assert.equal(links[3].url, 'https://example.test/list?page=3');
});

test('frozen structural rule applies to unseen URLs without model inference', () => {
  const rule = {kind: 'structural', source_urls: [], rules: {repeated_link_groups: [{selector: 'h3 a', content_region_count: 2, navigation_region_count: 0}], pagination: []}};
  const urls = selectedUrls(rule, html, base, {}, target);
  assert.deepEqual(urls, ['https://example.test/item/1?variant=blue#spec', 'https://example.test/item/2?variant=red#spec', 'https://example.test/list?page=3']);
});
