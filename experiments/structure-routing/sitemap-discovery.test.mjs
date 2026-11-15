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

test('parses namespaced sitemap entries, decodes XML entities, and ignores image loc and invalid lastmod', async () => {
  const xml = '<sm:urlset xmlns:sm="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1" xmlns:xhtml="http://www.w3.org/1999/xhtml"><sm:url><sm:loc>https://fixture.test/p?a=1&amp;b=2</sm:loc><sm:lastmod>not-a-date</sm:lastmod><image:image><image:loc>https://fixture.test/image.jpg</image:loc></image:image><xhtml:link rel="alternate" href="https://fixture.test/alt"/></sm:url></sm:urlset>';
  const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/', fetch: async url => url.endsWith('/robots.txt') ? response(200, 'Sitemap: https://fixture.test/map.xml\n') : response(200, xml)});
  assert.deepEqual(result.urls, [{url: 'https://fixture.test/p?a=1&b=2', sitemap: 'https://fixture.test/map.xml'}]);
  assert.equal(result.statuses.some(row => row.url.includes('image.jpg') || row.url.includes('/alt')), false);
  assert.equal(result.library.parser.name, 'sax');
  assert.equal(result.library.parser.version, '1.6.1');
  assert.equal(result.library.priorParser.name, '@crawlee/utils');
});

test('malformed and unsupported XML roots are parse errors with no extracted URLs', async () => {
  for (const xml of [
    '<html><url><loc>https://fixture.test/false-positive</loc></url></html>',
    '<urlset><url><loc>https://fixture.test/broken</loc></urlset>',
  ]) {
    const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/', fetch: async url => url.endsWith('/robots.txt') ? response(200, 'Sitemap: https://fixture.test/map.xml\n') : response(200, xml)});
    assert.deepEqual(result.urls, []);
    assert.equal(result.errors.at(-1).outcome, 'parse-error');
  }
});

test('supports newline-separated HTTP(S) sitemap text and rejects non-URL text', async () => {
  const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/', fetch: async url => {
    if (url.endsWith('/robots.txt')) return response(200, 'Sitemap: https://fixture.test/map.txt\n');
    return response(200, 'https://fixture.test/a\n\nhttps://fixture.test/b?q=1&x=2\n', {'content-type': 'text/plain'});
  }});
  assert.deepEqual(result.urls.map(row => row.url), ['https://fixture.test/a', 'https://fixture.test/b?q=1&x=2']);
  assert.equal(result.library.parser.textCompatibility, 'newline-separated absolute HTTP(S) URLs');
  assert.equal(result.statuses.find(row => row.kind === 'sitemap').sitemap_format, 'text');
  for (const body of ['Access denied', 'https://user:secret@fixture.test/private']) {
    const invalid = await discoverSitemapUrls({startUrl: 'https://fixture.test/', fetch: async url => url.endsWith('/robots.txt') ? response(200, 'Sitemap: https://fixture.test/map.txt\n') : response(200, body)});
    assert.deepEqual(invalid.urls, []);
    assert.equal(invalid.errors.at(-1).outcome, 'parse-error');
  }
});

test('bounds SAX sitemap parsing at 20,000 entries without per-URL content hashing', async () => {
  const entries = Array.from({length: 20002}, (_, index) => `<url><loc>https://fixture.test/p${index}</loc></url>`).join('');
  const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/', limits: {urls: 30000}, fetch: async url => url.endsWith('/robots.txt') ? response(200, 'Sitemap: https://fixture.test/map.xml\n') : response(200, `<urlset>${entries}</urlset>`)});
  assert.equal(result.urls.length, 20000);
  assert.equal(result.truncated, true);
  assert.equal(result.library.parser.contentHashCalls, 0);
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

test('sitemap_discovery_only ignores robots XML disallow rules while enforce still applies them', async () => {
  const xml = '<urlset><url><loc>https://fixture.test/p</loc></url></urlset>';
  const callsFor = async robotsPolicy => {
    const calls = [];
    const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/', robotsPolicy, fetch: async url => {
      calls.push(url);
      if (url.endsWith('/robots.txt')) return response(200, 'User-agent: *\nDisallow: /sitemap.xml\nSitemap: https://fixture.test/sitemap.xml\n');
      return response(200, xml);
    }});
    return {calls, result};
  };
  const enforced = await callsFor('enforce');
  assert.deepEqual(enforced.calls, ['https://fixture.test/robots.txt']);
  assert.equal(enforced.result.statuses.at(-1).outcome, 'blocked-by-robots');
  const experimental = await callsFor('sitemap_discovery_only');
  assert.deepEqual(experimental.calls, ['https://fixture.test/robots.txt', 'https://fixture.test/sitemap.xml']);
  assert.equal(experimental.result.urls[0].url, 'https://fixture.test/p');
  assert.equal(experimental.result.robotsPolicy, 'sitemap_discovery_only');
});

test('robots 403 stops by default but sitemap_discovery_only falls back to known sitemap paths', async () => {
  const discover = async robotsPolicy => {
    const calls = [];
    const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/', robotsPolicy, fetch: async url => {
      calls.push(url);
      if (url.endsWith('/robots.txt')) return response(403);
      if (url.endsWith('/sitemap.xml')) return response(200, '<urlset><url><loc>https://fixture.test/p</loc></url></urlset>');
      return response(404);
    }});
    return {calls, result};
  };
  const enforced = await discover('enforce');
  assert.deepEqual(enforced.calls, ['https://fixture.test/robots.txt']);
  assert.equal(enforced.result.truncated, true);
  const experimental = await discover('sitemap_discovery_only');
  assert.deepEqual(experimental.calls.slice(0, 2), ['https://fixture.test/robots.txt', 'https://fixture.test/sitemap.xml']);
  assert.equal(experimental.result.urls[0].url, 'https://fixture.test/p');
  assert.equal(experimental.result.truncated, false);
});

test('robots 503 falls back to known sitemap paths under sitemap_discovery_only', async () => {
  const calls = [];
  const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/', robotsPolicy: 'sitemap_discovery_only', fetch: async url => {
    calls.push(url);
    if (url.endsWith('/robots.txt')) return response(503);
    if (url.endsWith('/sitemap.xml')) return response(200, '<urlset><url><loc>https://fixture.test/p</loc></url></urlset>');
    return response(404);
  }});
  assert.deepEqual(calls.slice(0, 2), ['https://fixture.test/robots.txt', 'https://fixture.test/sitemap.xml']);
  assert.equal(result.urls[0].url, 'https://fixture.test/p');
  assert.equal(result.robotsPolicy, 'sitemap_discovery_only');
});

test('actual sitemap HTTP 401, 403, and 429 still stop discovery under sitemap_discovery_only', async t => {
  for (const status of [401, 403, 429]) await t.test(String(status), async () => {
    const calls = [];
    const result = await discoverSitemapUrls({startUrl: 'https://fixture.test/', robotsPolicy: 'sitemap_discovery_only', fetch: async url => {
      calls.push(url);
      if (url.endsWith('/robots.txt')) return response(200, '');
      return response(status);
    }});
    assert.deepEqual(calls, ['https://fixture.test/robots.txt', 'https://fixture.test/sitemap.xml']);
    assert.equal(result.truncated, true);
    assert.equal(result.statuses.at(-1).outcome, 'refused');
  });
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
