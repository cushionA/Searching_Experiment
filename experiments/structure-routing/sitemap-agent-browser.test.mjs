import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {matchesGroup, repeatedAnchorGroups, routeUrlShape, selectObservedItems, selectRouteItems, prioritizeFreshLinks, chooseRoute, executeRoute, observePage} from './sitemap-agent-browser.mjs';

test('URL group patterns match segments while retaining query and slash constraints', () => {
  const group = {pattern: {hostname: 'shop.example', segments: ['product', ':number'], trailing_slash: false, query_keys: ['variant']}};
  assert.equal(matchesGroup('https://shop.example/product/12?variant=blue&utm_source=x', group), true);
  assert.equal(matchesGroup('https://shop.example/product/12', group), false);
  assert.equal(matchesGroup('https://shop.example/product/item?variant=blue', group), false);
  assert.equal(matchesGroup('https://other.example/product/12?variant=blue', group), false);
});

test('selection requires a repeated observed anchor group and the selected URL pattern', () => {
  const links = [
    {url: 'https://shop.example/product/1', text: 'one', parent: {tag: 'li', classes: ['card']}, region: 'main'},
    {url: 'https://shop.example/product/2', text: 'two', parent: {tag: 'li', classes: ['card']}, region: 'main'},
    {url: 'https://shop.example/cart/3', text: 'cart', parent: {tag: 'li', classes: ['card']}, region: 'main'},
    {url: 'https://shop.example/product/4', text: 'single', parent: {tag: 'aside', classes: ['related']}, region: 'aside'},
  ];
  const plan = {selected_item_group_id: 'products', item_groups: [{id: 'products', pattern: {hostname: 'shop.example', segments: ['product', ':number'], trailing_slash: false, query_keys: []}}]};
  assert.equal(repeatedAnchorGroups(links).length, 1);
  assert.deepEqual(selectObservedItems(links, plan).map(row => row.url), ['https://shop.example/product/1', 'https://shop.example/product/2']);
});

test('URL shape is deterministic for numeric and slug paths', () => {
  assert.equal(routeUrlShape('https://shop.example/catalog/123?color=blue'), 'shop.example/catalog/:number?color#');
  assert.equal(routeUrlShape('https://shop.example/item/name.html'), 'shop.example/item/:slug.html?#');
});

test('fresh selected links take priority and route defaults use the declared candidate and group', () => {
  const links = [{url: '/old'}, {url: '/new'}, {url: '/newer'}];
  assert.deepEqual(prioritizeFreshLinks(links, new Set(['/old']), 2).map(row => row.url), ['/new', '/newer']);
  const plan = {listing_candidates: [{id: 'first', url: 'https://example.test/list', source: 'sitemap'}],
    hub_candidates: [{id: 'hub', url: 'https://example.test/', source: 'sitemap'}], item_groups: [{id: 'items'}]};
  assert.deepEqual(chooseRoute(plan).candidate, plan.listing_candidates[0]);
  assert.deepEqual(chooseRoute(plan).group, plan.item_groups[0]);
  const scrollPlan = {listing_candidates: [{id: 'pagination', url: 'https://example.test/category/page/2'}, {id: 'scroll', url: 'https://example.test/category/scroll'}], item_groups: []};
  assert.equal(chooseRoute(scrollPlan).candidate.id, 'scroll');
});

test('empty item group plans infer only repeated non-navigation URL shapes', () => {
  const links = [
    {url: 'https://shop.example/p/1', parent: {tag: 'li', classes: ['card']}, region: 'main'},
    {url: 'https://shop.example/p/2', parent: {tag: 'li', classes: ['card']}, region: 'main'},
    {url: 'https://shop.example/login', parent: {tag: 'li', classes: ['nav']}, region: 'nav'},
    {url: 'https://shop.example/account', parent: {tag: 'li', classes: ['nav']}, region: 'nav'},
  ];
  const picked = selectRouteItems(links, {});
  assert.equal(picked.fallback, 'DOM repeated-link group');
  assert.equal(picked.links.length, 2);
  assert.equal(picked.url_shape, 'shop.example/p/:number?#');
});

test('snapshot reader bounds a response body that never finishes', async () => {
  const started = Date.now();
  const observed = await observePage({evaluate: async () => { throw new Error('fixture page unavailable'); }, url: () => 'http://127.0.0.1/stream', content: async () => '<html></html>'},
    {body: () => new Promise(() => {})});
  assert.equal(observed.source, null);
  assert.ok(observed.errors.some(error => error.includes('response_body')));
  assert.ok(Date.now() - started < 7000);
});

test('local browser route follows observed listing edges, saves new scroll links, and replays warm separately', async t => {
  const requests = [];
  const server = http.createServer((req, res) => {
    requests.push(req.url);
    res.setHeader('content-type', 'text/html; charset=utf-8');
    if (req.url === '/robots.txt') { res.end('User-agent: *\nAllow: /\n'); return; }
    const listings = {'/browse': '<a href="/catalog">Catalog</a>', '/catalog': listingPage([1, 2], [3, 4, 5], '/archive'), '/archive': listingPage([10, 11], [12, 13, 14], null)};
    if (Object.hasOwn(listings, req.url)) { res.end(listings[req.url]); return; }
    const match = req.url.match(/^\/product\/(\d+)$/);
    if (match) { res.end(`<html><head><title>Product ${match[1]}</title></head><body><main><h1>Product ${match[1]}</h1><p>${'Detail content '.repeat(12)}</p></main></body></html>`); return; }
    res.statusCode = 404;
    res.end('<title>Not found</title><h1>Not found</h1>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sitemap-route-browser-'));
  t.after(() => {
    const tmpRoot = path.resolve(os.tmpdir());
    const resolved = path.resolve(root);
    assert.ok(resolved.startsWith(`${tmpRoot}${path.sep}`) && path.basename(resolved).startsWith('sitemap-route-browser-'));
    fs.rmSync(resolved, {recursive: true, force: true});
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  const target = {id: 'fixture', start_url: `${origin}/browse`, allowed_hosts: ['127.0.0.1'], practice: true};
  const plan = {listing_candidates: [
    {id: 'browse', url: `${origin}/browse`, source: 'sitemap'},
    {id: 'catalog', url: `${origin}/catalog/`, source: 'derived_prefix'},
    {id: 'archive', url: `${origin}/archive`, source: 'sitemap'},
  ], hub_candidates: [], item_groups: [],
    max_explore_documents: 6, max_scroll_actions: 3};
  const cold = await executeRoute({runDir: root, target, plan, phase: 'cold'});
  assert.equal(cold.listing_found, true);
  assert.ok(cold.graph.some(row => row.from.endsWith('/browse') && row.edges.some(url => url.endsWith('/catalog'))));
  assert.ok(cold.listing_candidates_visited.some(row => row.id === 'catalog' && row.url === `${origin}/catalog`));
  assert.equal(cold.scroll_growth_success, true, JSON.stringify({status: cold.status, items: cold.selected_items, fresh: cold.fresh_item_urls_after_scroll, saved: cold.fresh_item_urls_saved_after_scroll, errors: cold.snapshots.observation_errors}));
  assert.deepEqual(cold.fresh_item_urls_after_scroll.map(url => new URL(url).pathname).sort(), ['/product/3', '/product/4', '/product/5']);
  assert.equal(cold.item_group_fallback, 'DOM repeated-link group');
  assert.equal(cold.selected_items.length, 3);
  assert.ok(cold.selected_items.every(row => /\/(3|4|5)$/.test(row.url)));
  assert.ok(cold.selected_items.every(row => row.source_sha256 && row.rendered_sha256 && fs.statSync(path.join(root, row.source_html)).size > 0));
  assert.ok(cold.snapshots.before_source_sha256);
  assert.equal(cold.external_llm_calls, 0);
  const coldResult = fs.readFileSync(path.join(root, 'sites/fixture/cold/result.json'), 'utf8');
  const warm = await executeRoute({runDir: root, target, plan, phase: 'warm'});
  assert.equal(warm.seed_url, `${origin}/archive`);
  assert.equal(warm.scroll_growth_success, true);
  assert.ok(warm.selected_items.every(row => /\/(12|13|14)$/.test(row.url)));
  assert.equal(fs.readFileSync(path.join(root, 'sites/fixture/cold/result.json'), 'utf8'), coldResult);
  assert.ok(fs.existsSync(path.join(root, 'artifacts/fixture/sitemap-agent-browser/warm/listing-before.source.html')));
  assert.ok(requests.includes('/archive'));
  assert.ok(cold.cost.browser_document_requests <= 9 && warm.cost.browser_document_requests <= 4);
  assert.ok(cold.cost.ui_operations > 0 && cold.cost.http_requests >= cold.cost.browser_document_requests);
  assert.ok(cold.cost.browser_document_responses > 0 && cold.cost.http_responses >= cold.cost.browser_document_responses);
});

function listingPage(initial, afterScroll, next) {
  const anchors = ids => ids.map(id => `<li class="card"><a href="/product/${id}">Product ${id}</a></li>`).join('');
  return `<html><head><title>Catalog</title><script>
    window.addEventListener('scroll', () => {
      if (window.scrollY > 50 && !window.added) { window.added = true; document.querySelector('.cards').insertAdjacentHTML('beforeend', ${JSON.stringify(anchors(afterScroll))}); }
    });
  </script></head><body><main><h1>Catalog</h1>${next ? `<a href="${next}">Archive</a>` : ''}<div style="height:1300px"></div><ul class="cards">${anchors(initial)}</ul><p>${'Catalog copy '.repeat(20)}</p></main></body></html>`;
}
