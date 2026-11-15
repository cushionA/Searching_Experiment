import test from 'node:test';
import assert from 'node:assert/strict';
import {gzipSync} from 'node:zlib';
import {discoverSitemapUrls} from './sitemap-discovery.mjs';

function response(status, body = '', headers = {}) {
  const bytes = Buffer.isBuffer(body) ? body : Buffer.from(body);
  return {status, headers: new Headers(headers), arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)};
}

test('discovers nested XML and gzipped sitemap URLs through only injected fetch, preserving query and hash', async () => {
  const calls = [];
  const fixtures = new Map([
    ['https://shop.test/robots.txt', response(200, 'User-agent: *\nSitemap: https://shop.test/index.xml\n')],
    ['https://shop.test/index.xml', response(200, '<sitemapindex><sitemap><loc>https://shop.test/parts.xml.gz</loc></sitemap></sitemapindex>')],
    ['https://shop.test/parts.xml.gz', response(200, gzipSync(Buffer.from('<urlset><url><loc>https://shop.test/item?id=3#details</loc><lastmod>2026-01-02</lastmod></url><url><loc>https://elsewhere.test/no</loc></url></urlset>')), {'content-type': 'application/gzip'})],
  ]);
  const result = await discoverSitemapUrls({startUrl: 'https://shop.test/', fetch: async (url, init) => {
    calls.push({url, init}); return fixtures.get(url) ?? response(404);
  }});
  assert.deepEqual(calls.map(call => call.url), ['https://shop.test/robots.txt', 'https://shop.test/index.xml', 'https://shop.test/parts.xml.gz']);
  assert.ok(calls.every(call => call.init.method === 'GET'));
  assert.equal(result.urls.length, 1);
  assert.equal(result.urls[0].url, 'https://shop.test/item?id=3#details');
  assert.equal(result.urls[0].sitemap, 'https://shop.test/parts.xml.gz');
  assert.equal(result.urls[0].lastmod, '2026-01-02T00:00:00.000Z');
  assert.ok(result.statuses.some(row => row.outcome === 'observed-out-of-scope' && row.url === 'https://elsewhere.test/no'));
  assert.equal(result.cost.sitemapDocuments, 2);
  assert.equal(result.truncated, false);
});

test('robots sitemap 404 stays unavailable and tries known sitemap locations after an initial 404', async () => {
  const calls = [];
  const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/base', fetch: async url => {
    calls.push(url);
    if (url.endsWith('/robots.txt') || url.endsWith('/sitemap.xml')) return response(404);
    if (url.endsWith('/sitemap_index.xml')) return response(200, '<urlset><url><loc>https://fixture.test/p?q=a</loc></url></urlset>');
    return response(404);
  }});
  assert.deepEqual(calls.slice(0, 3), ['https://fixture.test/robots.txt', 'https://fixture.test/sitemap.xml', 'https://fixture.test/sitemap_index.xml']);
  assert.equal(result.urls[0].url, 'https://fixture.test/p?q=a');
  assert.equal(result.statuses[0].robots, 'unavailable');
  assert.equal(result.truncated, false);
});

test('refusal stops sitemap discovery without trying fallback paths', async () => {
  const calls = [];
  const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/', fetch: async url => {
    calls.push(url); return response(url.endsWith('/robots.txt') ? 200 : 429, url.endsWith('/robots.txt') ? '' : '');
  }});
  assert.deepEqual(calls, ['https://fixture.test/robots.txt', 'https://fixture.test/sitemap.xml']);
  assert.equal(result.truncated, true);
  assert.equal(result.statuses.at(-1).outcome, 'refused');
});

test('accepts upstream-decompressed XML when gzip headers remain and records byte certainty', async () => {
  const calls = [];
  const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/', fetch: async url => {
    calls.push(url);
    if (url.endsWith('/robots.txt')) return response(200, 'Sitemap: https://fixture.test/map.xml.gz\n', {'content-length': '50'});
    if (url.endsWith('.gz')) return response(200, '<urlset><url><loc>https://fixture.test/p</loc></url></urlset>', {'content-encoding': 'gzip', 'content-length': '58'});
    return response(404);
  }});
  assert.equal(result.urls[0].url, 'https://fixture.test/p');
  assert.equal(result.statuses[1].decompression, 'upstream-decompressed');
  assert.equal(result.cost.decodedBufferBytes, Buffer.byteLength('<urlset><url><loc>https://fixture.test/p</loc></url></urlset>') + Buffer.byteLength('Sitemap: https://fixture.test/map.xml.gz\n'));
  assert.equal(result.cost.wireContentLengthBytes, 108);
  assert.equal(result.cost.wireContentLengthKnownResponses, 2);
  assert.equal(result.cost.wireContentLengthUnknownResponses, 0);
  assert.deepEqual(result.statuses[1].wire_size, {source: 'content-length', known: true, bytes: 58});
});
