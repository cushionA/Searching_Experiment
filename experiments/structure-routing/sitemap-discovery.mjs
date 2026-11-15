import {createRequire} from 'node:module';
import {gunzipSync} from 'node:zlib';
import {Buffer} from 'node:buffer';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(path.join(repo, '.deps/structure-routing-sitemaps/package.json'));
const {parseSitemap} = require('@crawlee/utils');
const robotsParser = require('robots-parser');
const KNOWN_SITEMAPS = ['/sitemap.xml', '/sitemap_index.xml', '/sitemap-index.xml'];

function asBytes(response) {
  if (typeof response.arrayBuffer === 'function') return response.arrayBuffer().then(value => Buffer.from(value));
  if (Buffer.isBuffer(response.body)) return Promise.resolve(response.body);
  if (typeof response.body === 'string') return Promise.resolve(Buffer.from(response.body));
  if (typeof response.text === 'function') return response.text().then(value => Buffer.from(value));
  throw new TypeError('fetch response must provide arrayBuffer(), body, or text()');
}

function hostAllowed(value, allowedHosts) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && allowedHosts.has(url.hostname.toLowerCase());
  } catch { return false; }
}

function decodeBody(bytes, response) {
  const encoding = String(response.headers?.get?.('content-encoding') ?? response.headers?.['content-encoding'] ?? '').toLowerCase();
  const type = String(response.headers?.get?.('content-type') ?? response.headers?.['content-type'] ?? '').toLowerCase();
  const isGzip = bytes[0] === 0x1f && bytes[1] === 0x8b;
  if (isGzip) return {text: gunzipSync(bytes, {maxOutputLength: 50 * 1024 * 1024}).toString('utf8'), decompression: 'local-gzip'};
  if (encoding.includes('gzip') || type.includes('application/gzip')) return {text: bytes.toString('utf8'), decompression: 'upstream-decompressed'};
  return {text: bytes.toString('utf8'), decompression: 'none'};
}

export async function discoverSitemapUrls({startUrl, allowedHosts = [], fetch: injectedFetch, limits = {}, robotsUserAgent = '*'} = {}) {
  if (typeof injectedFetch !== 'function') throw new TypeError('fetch is required');
  const start = new URL(startUrl);
  const hosts = new Set([start.hostname.toLowerCase(), ...allowedHosts.map(host => String(host).toLowerCase())]);
  const maxDocuments = limits.documents ?? limits.maxDocuments ?? 20;
  const maxUrls = limits.urls ?? limits.maxUrls ?? 50000;
  const maxBytes = limits.bytes ?? limits.maxBytes ?? 50 * 1024 * 1024;
  const timeoutMs = limits.elapsedMs ?? limits.timeoutMs ?? 120000;
  const deadline = Date.now() + timeoutMs;
  const statuses = [];
  const errors = [];
  const urls = [];
  const seenDocs = new Set();
  const seenUrls = new Set();
  const queue = [];
  let requestCount = 0;
  let responseCount = 0;
  let decodedBufferBytes = 0;
  let wireContentLengthBytes = 0;
  let wireContentLengthKnownResponses = 0;
  let wireContentLengthUnknownResponses = 0;
  let truncated = false;
  const origin = start.origin;
  let robots = null;

  const request = async (url, kind) => {
    if (Date.now() >= deadline) { truncated = true; return null; }
    requestCount++;
    let response;
    try {
      response = await injectedFetch(url, {method: 'GET', headers: {'user-agent': robotsUserAgent}, signal: AbortSignal.timeout(Math.max(1, deadline - Date.now()))});
      responseCount++;
    } catch (error) {
      const row = {url, kind, status: null, outcome: 'error', error: String(error?.message ?? error).slice(0, 240)};
      statuses.push(row); errors.push(row); return null;
    }
    const status = Number(response.status);
    const row = {url, kind, status, outcome: status >= 200 && status < 300 ? 'ok' : status === 404 ? 'unavailable' : 'http-error'};
    const contentLength = Number(response.headers?.get?.('content-length') ?? response.headers?.['content-length']);
    row.wire_size = Number.isFinite(contentLength) && contentLength >= 0
      ? {source: 'content-length', known: true, bytes: contentLength}
      : {source: 'content-length', known: false, bytes: null};
    if (row.wire_size.known) { wireContentLengthBytes += contentLength; wireContentLengthKnownResponses++; }
    else wireContentLengthUnknownResponses++;
    statuses.push(row);
    if (status === 403 || status === 429 || status === 401) { row.outcome = 'refused'; truncated = true; return null; }
    if (status < 200 || status >= 300) return null;
    try {
      const bytes = await asBytes(response);
      decodedBufferBytes += bytes.length;
      if (bytes.length > maxBytes) throw new Error(`response buffer exceeds ${maxBytes} bytes`);
      const decoded = decodeBody(bytes, response);
      row.decompression = decoded.decompression;
      if (Buffer.byteLength(decoded.text) > maxBytes) throw new Error(`decoded response exceeds ${maxBytes} bytes`);
      return decoded.text;
    } catch (error) {
      row.outcome = 'parse-error'; row.error = String(error?.message ?? error).slice(0, 240);
      errors.push(row); return null;
    }
  };

  const robotsUrl = new URL('/robots.txt', origin).href;
  const robotsText = await request(robotsUrl, 'robots');
  let robotStatus = statuses.find(row => row.url === robotsUrl);
  let sitemapSeeds = [];
  if (robotsText !== null) {
    robots = robotsParser(robotsUrl, robotsText);
    sitemapSeeds = [...new Set(robots.getSitemaps().filter(url => hostAllowed(url, hosts)))];
    for (const url of robots.getSitemaps()) if (!hostAllowed(url, hosts)) statuses.push({url, kind: 'sitemap-reference', status: null, outcome: 'observed-out-of-scope'});
    if (robotStatus) robotStatus.robots = 'available';
  } else {
    if (robotStatus?.status === 404 || !robotStatus) { if (robotStatus) robotStatus.robots = 'unavailable'; }
    if (robotStatus?.outcome === 'refused' || robotStatus?.outcome === 'error' || (robotStatus && robotStatus.status !== 404 && robotStatus.status !== null)) {
      return result();
    }
  }

  if (!sitemapSeeds.length) sitemapSeeds = KNOWN_SITEMAPS.map(pathname => new URL(pathname, origin).href);
  queue.push(...sitemapSeeds);
  while (queue.length && seenDocs.size < maxDocuments && urls.length < maxUrls && !truncated) {
    const sitemapUrl = queue.shift();
    if (seenDocs.has(sitemapUrl)) continue;
    if (!hostAllowed(sitemapUrl, hosts)) {
      statuses.push({url: sitemapUrl, kind: 'sitemap', status: null, outcome: 'observed-out-of-scope'}); continue;
    }
    if (new URL(sitemapUrl).origin === origin && robots?.isAllowed(sitemapUrl, robotsUserAgent) === false) {
      statuses.push({url: sitemapUrl, kind: 'sitemap', status: null, outcome: 'blocked-by-robots'}); continue;
    }
    seenDocs.add(sitemapUrl);
    const body = await request(sitemapUrl, 'sitemap');
    if (body === null) continue;
    try {
      for await (const entry of parseSitemap([{type: 'raw', content: body}], undefined, {emitNestedSitemaps: true, maxDepth: 0, sitemapRetries: 0, enqueueStrategy: 'all', reportNetworkErrors: false})) {
        if (entry.originSitemapUrl === null) {
          const child = new URL(entry.loc, sitemapUrl).href;
          if (!hostAllowed(child, hosts)) statuses.push({url: child, kind: 'sitemap-reference', status: null, outcome: 'observed-out-of-scope'});
          else if (!seenDocs.has(child) && !queue.includes(child)) queue.push(child);
        } else {
          const target = new URL(entry.loc, sitemapUrl).href;
          if (!hostAllowed(target, hosts)) statuses.push({url: target, kind: 'page-reference', status: null, outcome: 'observed-out-of-scope'});
          else if (!seenUrls.has(target)) {
            if (urls.length >= maxUrls) { truncated = true; break; }
            seenUrls.add(target); urls.push({url: target, sitemap: sitemapUrl, ...(entry.lastmod ? {lastmod: entry.lastmod.toISOString()} : {})});
          }
        }
      }
    } catch (error) {
      const row = {url: sitemapUrl, kind: 'sitemap', status: 200, outcome: 'parse-error', error: String(error?.message ?? error).slice(0, 240)};
      statuses.push(row); errors.push(row);
    }
  }
  if (queue.length || seenDocs.size >= maxDocuments || urls.length >= maxUrls || Date.now() >= deadline) truncated = true;
  return result();

  function result() {
    return {urls, statuses, errors, truncated, cost: {requests: requestCount, responses: responseCount, decodedBufferBytes, wireContentLengthBytes, wireContentLengthKnownResponses, wireContentLengthUnknownResponses, sitemapDocuments: seenDocs.size, extractedUrls: urls.length}, library: {name: '@crawlee/utils', version: '3.18.2', api: 'parseSitemap', scope: 'XML sitemap URL/index parsing from locally fetched response text; all robots and sitemap HTTP requests, decompression, host scope, and budgets are orchestrated by this module'}};
  }
}
