import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {environmentContextOptions} from './environment.mjs';
import {installDocumentGuard} from './document-guard.mjs';
import {discoverSitemapUrls} from './sitemap-discovery.mjs';
import {learnRules, selectLinks} from './runner.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(path.join(repo, '.deps/bot-diagnostics/package.json'));
const {chromium} = require('playwright');
const robotsParser = require('robots-parser');
const args = Object.fromEntries(process.argv.slice(2).reduce((out, item, i, all) => {
  if (!item.startsWith('--')) return out;
  const [key, value] = item.slice(2).split('=', 2);
  out.push([key, value ?? (all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : 'true')]);
  return out;
}, []));

const readJsonl = file => fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map(JSON.parse) : [];
const writeJson = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n'); };
const appendJsonl = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.appendFileSync(file, JSON.stringify(value) + '\n'); };
const sha = value => require('node:crypto').createHash('sha256').update(value).digest('hex');

export function urlShape(value) {
  const url = new URL(value);
  const parts = url.pathname.split('/');
  const pathShape = parts.map((part, index) => {
    if (!part) return '';
    if (/^[0-9]+$/.test(part)) return ':number';
    if (/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(part)) return ':uuid';
    if (/^[0-9a-f]{16,}$/i.test(part)) return ':hex';
    if (/\.[a-z0-9]{1,8}$/i.test(part)) return /^index\.[a-z0-9]{1,8}$/i.test(part) ? part.toLowerCase() : `:slug.${part.split('.').pop().toLowerCase()}`;
    if (index === parts.length - 1) return ':slug';
    return part.toLowerCase();
  }).join('/');
  return `${url.hostname.toLowerCase()}${pathShape}?${[...new Set([...url.searchParams.keys()])].sort().join(',')}#${url.hash ? 'hash' : ''}`;
}

function inScope(url, hosts) {
  try { return ['http:', 'https:'].includes(new URL(url).protocol) && hosts.has(new URL(url).hostname.toLowerCase()); } catch { return false; }
}

async function snapshot(page, response) {
  const [dom, body] = await Promise.all([
    page.evaluate(() => {
      const body = document.body;
      const text = (body?.innerText || '').replace(/\s+/g, ' ').trim();
      const links = [...document.querySelectorAll('a[href]')].map(raw => {
        const p = raw.parentElement;
        let region = 'body';
        for (let node = p; node && node !== document.documentElement; node = node.parentElement) {
          const marker = `${node.tagName} ${node.getAttribute('role') || ''} ${node.id || ''} ${node.className || ''}`.toLowerCase();
          if (/\b(nav|header|footer|aside)\b|\b(navigation|breadcrumb|pagination|sidebar)\b/.test(marker)) { region = 'nav'; break; }
          if (/^(MAIN|ARTICLE|SECTION)$/.test(node.tagName)) region = node.tagName.toLowerCase();
        }
        return {url: new URL(raw.getAttribute('href'), location.href).href, text: (raw.innerText || raw.getAttribute('aria-label') || '').trim().slice(0, 160),
          parent: {tag: p?.tagName?.toLowerCase() || 'div', classes: [...(p?.classList || [])]}, region};
      }).filter(x => /^https?:/.test(x.url));
      const headings = [...document.querySelectorAll('h1,h2')].map(x => (x.innerText || '').trim()).filter(Boolean).slice(0, 12);
      const main = document.querySelector('main,article,[role="main"]');
      const contentLength = (main?.innerText || text).replace(/\s+/g, ' ').trim().length;
      return {title: document.title, heading: headings[0] || '', headings, bodyLength: text.length, contentLength, text: text.slice(0, 5000), links,
        canonical: document.querySelector('link[rel="canonical"]')?.href || null, htmlLength: document.documentElement?.outerHTML?.length || 0,
        challengeElement: Boolean(document.querySelector('[id*="captcha" i], [class*="captcha" i], iframe[src*="challenge" i], input[type="password"]')),
        stateId: `${location.href}|${document.title}|${headings[0] || ''}|${text.slice(0, 120)}`};
    }),
    response ? response.body().catch(() => null) : Promise.resolve(null),
  ]);
  return {dom, source: body};
}

function validPage(status, data) {
  const text = `${data.dom.title} ${data.dom.heading} ${data.dom.text}`;
  if (status !== 200) return {ok: false, type: `http_${status ?? 'error'}`};
  if (data.dom.challengeElement) return {ok: false, type: 'blocked_or_login'};
  if (/verify you are human|access denied|robot check|captcha|request blocked|security check/i.test(`${data.dom.title} ${data.dom.heading}`)) return {ok: false, type: 'blocked'};
  if (/maintenance|temporarily unavailable|メンテナンス中/i.test(`${data.dom.title} ${data.dom.heading}`)) return {ok: false, type: 'maintenance'};
  if (data.dom.bodyLength < 80 || data.dom.htmlLength < 100 || data.dom.contentLength < 40) return {ok: false, type: 'empty_or_shell'};
  if (/not found|page not found|404|ページが見つかりません/i.test(text)) return {ok: false, type: 'not_found'};
  return {ok: Boolean(data.dom.title || data.dom.heading) && data.dom.bodyLength >= 80, type: 'content'};
}

function safeRoute(url) {
  const blocked = new Set(['cart', 'checkout', 'order', 'account', 'login', 'logout', 'register', 'signup', 'contact', 'submit', 'purchase']);
  try { return !new URL(url).pathname.split('/').filter(Boolean).some(part => blocked.has(decodeURIComponent(part).toLowerCase())); }
  catch { return false; }
}

async function robotPolicy(url, options) {
  const origin = new URL(url).origin;
  if (!options.robots.has(origin)) {
    const robotUrl = new URL('/robots.txt', origin).href;
    let current = robotUrl;
    let response = null;
    for (let redirects = 0; redirects <= 5; redirects++) {
      await waitMetadata(options);
      options.metadata.requests++;
      let fetched;
      try { fetched = await (options.fetch || fetch)(current, {method: 'GET', redirect: 'manual', headers: {'user-agent': 'StructureRoutingResearch/1.0'}}); }
      catch (error) { options.robots.set(origin, {status: null, allowed: false, reason: String(error?.message || error)}); return options.robots.get(origin); }
      options.metadata.responses++;
      const body = Buffer.from(await fetched.arrayBuffer().catch(() => new ArrayBuffer(0)));
      options.metadata.bytes += body.length;
      response = {status: fetched.status, headers: fetched.headers, body};
      if (![301, 302, 303, 307, 308].includes(fetched.status)) break;
      const location = fetched.headers.get('location');
      if (!location) break;
      current = new URL(location, current).href;
    }
    if (response && response.status >= 500) options.robots.set(origin, {status: response.status, allowed: false, reason: 'robots_server_error'});
    else if (response && [401, 403, 429].includes(response.status)) options.robots.set(origin, {status: response.status, allowed: false, reason: 'robots_refused'});
    else {
      const text = response && response.status >= 200 && response.status < 300 ? response.body.toString('utf8') : '';
      const parser = robotsParser(robotUrl, text);
      options.robots.set(origin, {status: response?.status ?? null, allowed: true, parser, url: robotUrl});
    }
  }
  const policy = options.robots.get(origin);
  if (!policy.allowed) return policy;
  return {...policy, allowed: policy.parser.isAllowed(url, '*') !== false};
}

async function waitMetadata(options) {
  const delayMs = options.target.practice ? 1000 : 5000;
  if (options.metadata.lastAt) await new Promise(resolve => setTimeout(resolve, Math.max(0, delayMs - (Date.now() - options.metadata.lastAt))));
  options.metadata.lastAt = Date.now();
}

function resultPath(runDir, target, method, phase) {
  return path.join(runDir, 'artifacts', String(target.id), method, phase);
}

function saveResult(runDir, target, method, phase, result) {
  writeJson(path.join(runDir, 'artifacts', String(target.id), method, phase, 'result.json'), result);
  appendJsonl(path.join(runDir, 'results.jsonl'), result);
}

function accessStop(rows) {
  const stop = rows.find(row => row.robots?.reason || ['blocked', 'blocked_or_login', 'maintenance', 'http_401', 'http_403', 'http_429', 'robots_refused', 'robots_disallowed', 'robots_server_error'].includes(row.page_classification));
  return stop ? {access_stop: true, stop_reason: stop.page_classification || stop.robots.reason || 'access_refused'} : {access_stop: false, stop_reason: null};
}

async function openFreshPage(target, initialUrl, options, budget) {
  const browser = await chromium.launch({headless: true, executablePath: options.executablePath || 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--disable-background-networking', '--disable-component-update']});
  const context = await browser.newContext({locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: {width: 1365, height: 900}, serviceWorkers: 'block', ...environmentContextOptions()});
  await context.clearCookies();
  const page = await context.newPage();
  const hosts = new Set([new URL(target.start_url || target.url || target.entry_url).hostname.toLowerCase(), ...(target.allowed_hosts || []).map(x => String(x).toLowerCase())]);
  const network = {requests: 0, responses: 0, xhrFetch: 0, documentRequests: 0, documentResponses: 0, bytes: 0, operations: 1};
  const requestRows = [];
  page.on('request', request => {
    const kind = request.resourceType();
    network.requests++;
    if (kind === 'xhr' || kind === 'fetch') network.xhrFetch++;
    if (kind === 'document') { network.documentRequests++; requestRows.push({url: request.url(), method: request.method(), resourceType: kind}); }
  });
  page.on('response', response => {
    network.responses++;
    if (response.request().resourceType() === 'document') network.documentResponses++;
    void response.body().then(bytes => { network.bytes += bytes.length; }).catch(() => {});
  });
  const guard = await installDocumentGuard(page, {limit: budget, beforeRequest: info => !info.mainFrame || (info.method === 'GET' && inScope(info.url, hosts))});
  const response = await page.goto(initialUrl, {waitUntil: 'domcontentloaded', timeout: 45000}).catch(() => null);
  await page.waitForLoadState('networkidle', {timeout: target.practice ? 1000 : 5000}).catch(() => {});
  await page.waitForTimeout(500);
  const data = await snapshot(page, response).catch(() => ({dom: {title: '', heading: '', headings: [], bodyLength: 0, contentLength: 0, text: '', links: [], htmlLength: 0, stateId: '', challengeElement: false}, source: null}));
  return {browser, context, page, guard, response, data, network, requestRows, hosts};
}

async function closePage(session) {
  await session.guard.seal().catch(() => {});
  await session.context.close().catch(() => {});
  await session.browser.close().catch(() => {});
  await session.guard.cleanup().catch(() => {});
}

async function acquire(target, url, runDir, method, phase, options, counter) {
  if (counter.documents >= counter.limit) return null;
  const started = Date.now();
  const hosts = new Set([new URL(target.start_url || target.url || target.entry_url).hostname.toLowerCase(), ...(target.allowed_hosts || []).map(x => String(x).toLowerCase())]);
  if (!inScope(url, hosts)) return {requested_url: url, status: null, page_valid: false, page_classification: 'out_of_scope', document_requests: 0, document_responses: 0, network: {requests: 0, responses: 0, xhrFetch: 0, bytes: 0, operations: 0}, elapsed_ms: 0};
  if (!safeRoute(url)) return {requested_url: url, status: null, page_valid: false, page_classification: 'unsafe_route', document_requests: 0, document_responses: 0, network: {requests: 0, responses: 0, xhrFetch: 0, bytes: 0, operations: 0}, elapsed_ms: 0};
  const robot = await robotPolicy(url, options);
  if (!robot.allowed) return {requested_url: url, status: robot.status, page_valid: false, page_classification: robot.reason || 'robots_disallowed', robots: {status: robot.status, reason: robot.reason || 'disallowed'}, document_requests: 0, document_responses: 0, network: {requests: 0, responses: 0, xhrFetch: 0, bytes: 0, operations: 0}, elapsed_ms: Date.now() - started};
  const waitMs = target.practice ? 1000 : 5000;
  if (counter.lastAt) await new Promise(resolve => setTimeout(resolve, Math.max(0, waitMs - (Date.now() - counter.lastAt))));
  counter.lastAt = Date.now();
  const session = await openFreshPage(target, url, options, counter.limit - counter.documents);
  const status = session.response?.status() ?? null;
  const finalUrl = session.page.url();
  const classification = validPage(status, session.data);
  const base = resultPath(runDir, target, method, phase);
  fs.mkdirSync(base, {recursive: true});
  const index = counter.documents;
  counter.documents += session.guard.counters.reserved;
  const stem = `${String(index + 1).padStart(3, '0')}-${sha(`${url}|${index}`).slice(0, 10)}`;
  const domFile = path.join(base, `${stem}.dom.html`);
  fs.writeFileSync(domFile, await session.page.content().catch(() => ''));
  let sourceFile = null;
  if (session.data.source) { sourceFile = path.join(base, `${stem}.source.html`); fs.writeFileSync(sourceFile, session.data.source); }
  const observation = {requested_url: url, final_url: finalUrl, status, title: session.data.dom.title, headings: session.data.dom.headings,
    body_length: session.data.dom.bodyLength, content_length: session.data.dom.contentLength, page_valid: classification.ok, page_classification: classification.type,
    source_html: sourceFile ? path.relative(runDir, sourceFile) : null, rendered_dom: path.relative(runDir, domFile),
    source_sha256: sourceFile ? sha(fs.readFileSync(sourceFile)) : null, rendered_sha256: sha(fs.readFileSync(domFile)),
    link_count: session.data.dom.links.length, links: session.data.dom.links, canonical_href: session.data.dom.canonical,
    state_id: session.data.dom.stateId, robots: {status: robot.status, allowed: true}, document_requests: session.guard.counters.reserved, document_responses: session.network.documentResponses, guard: session.guard.counters,
    network: session.network, elapsed_ms: Date.now() - started};
  await closePage(session);
  return observation;
}

function prefixMatch(value, prefix) {
  try {
    const pathname = new URL(value).pathname;
    if (prefix === '/') return true;
    const root = prefix.endsWith('/') ? prefix.slice(0, -1) : prefix;
    return pathname === root || pathname.startsWith(`${root}/`);
  } catch { return false; }
}

function pickRepresentatives(urls, count, seen = new Set(), startUrl = '') {
  let eligible = urls.filter(row => !seen.has(row.url));
  let scope = {path_prefix: new URL(startUrl || eligible[0]?.url || 'http://invalid/').pathname, mode: 'path_prefix'};
  if (startUrl) {
    const matches = eligible.filter(row => prefixMatch(row.url, scope.path_prefix));
    if (matches.length) eligible = matches;
    else scope = {...scope, mode: 'host_only_fallback'};
  }
  const groups = new Map();
  for (const row of eligible) {
    const shape = urlShape(row.url);
    if (!groups.has(shape)) groups.set(shape, []);
    groups.get(shape).push(row);
  }
  const ordered = [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  const result = [];
  for (let offset = 0; result.length < count; offset++) {
    let added = false;
    for (const [shape, rows] of ordered) {
      if (rows[offset]) { result.push({shape, row: rows[offset]}); added = true; if (result.length === count) break; }
    }
    if (!added) break;
  }
  return result.map(item => ({...item, scope}));
}

async function runB2(target, phase, runDir, options) {
  const recipeFile = path.join(runDir, 'artifacts', String(target.id), 'B2', 'recipe.json');
  const inventoryFile = path.join(runDir, 'artifacts', String(target.id), 'B2', 'url-inventory.jsonl');
  const inventoryJsonFile = path.join(runDir, 'artifacts', String(target.id), 'B2', 'inventory.json');
  const resultFile = path.join(runDir, 'results.jsonl');
  const existing = readJsonl(resultFile);
  if (existing.some(row => row.id === target.id && row.method === 'B2' && row.phase === phase)) throw new Error(`phase_already_recorded:${target.id}:B2:${phase}`);
  const startUrl = target.start_url || target.url || target.entry_url;
  if (!startUrl) throw new Error(`target_missing_url:${target.id}`);
  const hosts = [...new Set([new URL(startUrl).hostname, ...(target.allowed_hosts || [])])];
  const network = {requests: 0, responses: 0, bytes: 0, elapsed_ms: 0};
  const metadataBefore = {...options.metadata};
  const fetchMeter = async (input, init = {}) => {
    let current = new URL(input).href;
    for (let redirects = 0; redirects <= 5; redirects++) {
      if (!inScope(current, new Set(hosts.map(host => host.toLowerCase())))) throw new Error('metadata_redirect_out_of_scope');
      await waitMetadata(options);
      network.requests++;
      const response = await (options.fetch || fetch)(current, {...init, redirect: 'manual'});
      const bytes = Buffer.from(await response.arrayBuffer());
      network.responses++;
      network.bytes += bytes.length;
      if (![301, 302, 303, 307, 308].includes(response.status)) return new Response(bytes, {status: response.status, headers: response.headers});
      const location = response.headers.get('location');
      if (!location) return new Response(bytes, {status: response.status, headers: response.headers});
      current = new URL(location, current).href;
    }
    throw new Error('metadata_redirect_limit');
  };
  let inventory;
  let recipe = null;
  let candidates;
  if (phase === 'cold') {
    const began = Date.now();
    inventory = await discoverSitemapUrls({startUrl, allowedHosts: hosts, fetch: fetchMeter,
      limits: {documents: target.practice ? 15 : 6, urls: 50000, bytes: 50 * 1024 * 1024, elapsedMs: 120000}});
    network.elapsed_ms = Date.now() - began;
    fs.mkdirSync(path.dirname(inventoryFile), {recursive: true});
    fs.writeFileSync(inventoryFile, inventory.urls.map(row => JSON.stringify(row)).join('\n') + (inventory.urls.length ? '\n' : ''));
    writeJson(inventoryJsonFile, {source: 'sitemap', urls: inventory.urls, sitemap_statuses: inventory.statuses, errors: inventory.errors, truncated: inventory.truncated});
    candidates = pickRepresentatives(inventory.urls, 3, new Set(), startUrl);
  } else {
    if (!fs.existsSync(recipeFile)) throw new Error(`missing_cold_recipe:${target.id}`);
    recipe = JSON.parse(fs.readFileSync(recipeFile, 'utf8'));
    inventory = {urls: readJsonl(inventoryFile), statuses: [], errors: [], truncated: false, cost: {request_count: 0, response_count: 0, response_bytes: 0}};
    const prior = new Set(recipe.cold_requested_urls || []);
    candidates = pickRepresentatives(recipe.inventory.filter(row => recipe.valid_shapes.includes(urlShape(row.url))), 3, prior, startUrl);
    if (!candidates.length) candidates = (recipe.warm_candidates || []).map(row => ({shape: row.shape, row, remaining: []}));
    network.elapsed_ms = 0;
  }
  const results = [];
  const counter = {documents: 0, limit: phase === 'cold' ? (target.practice ? 4 : 6) : 3, lastAt: 0};
  const shapeResults = [];
  for (const candidate of candidates) {
    if (results.length >= 3) break;
    const observation = await acquire(target, candidate.row.url, runDir, 'B2', phase, options, counter);
    if (!observation) break;
    results.push(observation);
    shapeResults.push({shape: candidate.shape, url: candidate.row.url, valid: observation.page_valid});
    if (observation.page_valid) candidate.valid = true;
    if (accessStop([observation]).access_stop) break;
  }
  const validShapes = phase === 'cold' ? shapeResults.filter(row => row.valid).map(row => row.shape) : recipe.valid_shapes;
  if (phase === 'cold') {
    const used = new Set(results.map(row => row.requested_url));
    const warmCandidates = pickRepresentatives(inventory.urls, 12, used, startUrl).filter(group => validShapes.includes(group.shape)).map(group => ({shape: group.shape, ...group.row, scope: group.scope}));
    recipe = {version: 1, id: target.id, source: 'robots+sitemap+url-shape', start_url: startUrl, allowed_hosts: hosts,
      cold_requested_urls: results.map(row => row.requested_url), valid_shapes: [...new Set(validShapes)], warm_candidates: warmCandidates,
      inventory: inventory.urls, discovery_statuses: inventory.statuses, truncated: inventory.truncated,
      discovery_errors: inventory.errors, discovery_cost: {...network}, rules: {url_shapes: [...new Set(validShapes)], saved_manifest: path.relative(runDir, inventoryFile)},
      external_llm_calls: 0, local_inference: {calls: 0, input_tokens: 0, runtime_ms: 0}};
    writeJson(recipeFile, recipe);
  }
  const stopped = accessStop(results);
  const discoveryRefusal = inventory.statuses.find(row => row.outcome === 'refused' || [401, 403, 429].includes(row.status));
  const stop = stopped.access_stop ? stopped : discoveryRefusal ? {access_stop: true, stop_reason: `sitemap_${discoveryRefusal.kind || 'metadata'}_${discoveryRefusal.status || 'refused'}`} : stopped;
  const result = {id: target.id, domain: target.domain, method: 'B2', phase,
    status: results.some(row => row.page_valid) ? 'html_saved' : inventory.urls.length ? 'urls_found_html_unvalidated' : 'no_urls_found',
    candidate_urls: inventory.urls.length, shape_groups: new Set(inventory.urls.map(row => urlShape(row.url))).size,
    shape_results: shapeResults, observation_scope: candidates[0]?.scope || null, observations: results,
    cost: {sitemap_discovery: phase === 'cold' ? network : {requests: 0, responses: 0, bytes: 0, elapsed_ms: 0},
      robots: {requests: options.metadata.requests - metadataBefore.requests, responses: options.metadata.responses - metadataBefore.responses, bytes: options.metadata.bytes - metadataBefore.bytes},
      document_requests: results.reduce((sum, row) => sum + Number(row.document_requests || 0), 0), document_responses: results.reduce((sum, row) => sum + Number(row.document_responses || 0), 0),
      xhr_fetch: results.reduce((sum, row) => sum + row.network.xhrFetch, 0), http_requests: results.reduce((sum, row) => sum + row.network.requests, 0),
      response_bytes: results.reduce((sum, row) => sum + row.network.bytes, 0), browser_ms: results.reduce((sum, row) => sum + row.elapsed_ms, 0)},
    discovery_transport: 'Node fetch + Crawlee XML parser; page HTML via Chromium/Playwright', external_llm_calls: 0,
    local_inference: {calls: 0, input_tokens: 0, runtime_ms: 0}, rule_source_pages: phase === 'cold' ? results.map(x => x.requested_url) : recipe.cold_requested_urls,
    recipe: path.relative(runDir, recipeFile), html_cache_counted_as_acquisition: false, gold_input: false, ...stop};
  saveResult(runDir, target, 'B2', phase, result);
}

async function runGraphMethod(target, method, phase, runDir, options) {
  const resultFile = path.join(runDir, 'results.jsonl');
  const existing = readJsonl(resultFile);
  if (existing.some(row => row.id === target.id && row.method === method && row.phase === phase)) throw new Error(`phase_already_recorded:${target.id}:${method}:${phase}`);
  const startUrl = target.start_url || target.url || target.entry_url;
  const hosts = new Set([new URL(startUrl).hostname.toLowerCase(), ...(target.allowed_hosts || []).map(x => String(x).toLowerCase())]);
  const recipeFile = path.join(runDir, 'artifacts', String(target.id), method, 'recipe.json');
  let queue;
  let sourcePages = [];
  let priorKnown = new Set();
  let rules = null;
  if (phase === 'cold') queue = [startUrl];
  else {
    if (!fs.existsSync(recipeFile)) throw new Error(`missing_cold_recipe:${target.id}:${method}`);
    const recipe = JSON.parse(fs.readFileSync(recipeFile, 'utf8'));
    rules = recipe.rules; sourcePages = recipe.sourcePages; priorKnown = new Set(recipe.seen || []);
    queue = recipe.frontier.filter(url => !priorKnown.has(url));
  }
  const limit = phase === 'cold' ? (target.practice ? 4 : 6) : 3;
  const counter = {documents: 0, limit, lastAt: 0};
  const metadataBefore = {...options.metadata};
  const observations = [];
  const pages = [];
  const seen = new Set(priorKnown);
  while (queue.length && observations.length < limit && counter.documents < limit) {
    const url = queue.shift();
    if (seen.has(url) || !inScope(url, hosts)) continue;
    seen.add(url);
    const observation = await acquire(target, url, runDir, method, phase, options, counter);
    if (!observation) break;
    observations.push(observation);
    if (accessStop([observation]).access_stop) break;
    pages.push({url: observation.final_url, links: observation.links});
    const structural = method !== 'B1';
    const learned = phase === 'cold' && structural ? learnRules(pages) : rules;
    const selectedLinks = !structural ? observation.links.filter(link => inScope(link.url, hosts))
      : selectLinks({links: observation.links}, 'C1', learned || {repeated_link_groups: [], pagination: []}, seen, hosts);
    for (const link of selectedLinks) if (!seen.has(link.url) && !queue.includes(link.url)) queue.push(link.url);
    if (observations.length < limit) await new Promise(resolve => setTimeout(resolve, target.practice ? 1000 : 5000));
  }
  if (phase === 'cold') {
    sourcePages = pages;
    rules = method !== 'B1' ? learnRules(pages) : {version: 1, strategy: 'observed-anchor-bfs', source_pages: pages.map(x => x.url)};
    writeJson(recipeFile, {version: 1, id: target.id, method, variant: method === 'C1-nav-aware' ? 'nav-region-aware-generic' : 'baseline', sourcePages, rules, seen: [...seen], frontier: queue, gold_input: false});
    const urls = new Map();
    for (const page of pages) for (const link of page.links) if (!urls.has(link.url)) urls.set(link.url, {url: link.url, source_url: page.url, text: link.text, region: link.region});
    writeJson(path.join(runDir, 'artifacts', String(target.id), method, 'inventory.json'), {source: 'observed_links', urls: [...urls.values()], visited_urls: [...seen]});
  }
  const result = {id: target.id, domain: target.domain, method, phase,
    status: observations.some(x => x.page_valid) ? 'html_saved' : 'no_valid_html', variant: method === 'C1-nav-aware' ? 'nav-region-aware-generic' : 'baseline', observations,
    rule_source_pages: phase === 'cold' ? pages.map(x => x.url) : sourcePages.map(x => x.url), recipe: path.relative(runDir, recipeFile),
    cost: {robots: {requests: options.metadata.requests - metadataBefore.requests, responses: options.metadata.responses - metadataBefore.responses,
      bytes: options.metadata.bytes - metadataBefore.bytes}, document_requests: observations.reduce((sum, row) => sum + Number(row.document_requests || 0), 0), document_responses: observations.reduce((sum, row) => sum + Number(row.document_responses || 0), 0),
      http_requests: observations.reduce((sum, row) => sum + row.network.requests, 0), xhr_fetch: observations.reduce((sum, row) => sum + row.network.xhrFetch, 0),
      response_bytes: observations.reduce((sum, row) => sum + row.network.bytes, 0), browser_ms: observations.reduce((sum, row) => sum + row.elapsed_ms, 0)},
    external_llm_calls: 0, local_inference: {calls: 0, input_tokens: 0, runtime_ms: 0}, gold_input: false, ...accessStop(observations)};
  saveResult(runDir, target, method, phase, result);
}

export async function runSitemapBenchmark({runDir, targets, method, phase, executablePath} = {}) {
  if (!['B1', 'C1', 'C1-nav-aware', 'B2'].includes(method)) throw new Error('method_must_be_B1_C1_C1_NAV_AWARE_B2');
  if (!['cold', 'warm'].includes(phase)) throw new Error('phase_must_be_cold_or_warm');
  const options = {executablePath, robots: new Map(), metadata: {requests: 0, responses: 0, bytes: 0, lastAt: 0}};
  for (const target of targets) {
    options.target = target;
    if (method === 'B2') await runB2(target, phase, runDir, options);
    else await runGraphMethod(target, method, phase, runDir, options);
  }
}

async function main() {
  const runDir = path.resolve(args.run || '');
  if (!args.run) throw new Error('required_argument:--run');
  if (args.live !== 'true') throw new Error('live_mode_requires_explicit_--live_true');
  const targetsFile = path.resolve(args.targets || path.join(runDir, 'targets.jsonl'));
  const ids = new Set(String(args.ids || '').split(',').filter(Boolean));
  const targets = readJsonl(targetsFile).filter(row => !ids.size || ids.has(String(row.id)));
  if (!targets.length) throw new Error('no_targets_selected');
  await runSitemapBenchmark({runDir, targets, method: args.method || 'B2', phase: args.phase || 'cold', executablePath: args.executable});
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url)
  main().catch(error => { console.error(JSON.stringify({error: error.message})); process.exitCode = 1; });
