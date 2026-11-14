import {environmentContextOptions} from './environment.mjs';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {installDocumentGuard} from './document-guard.mjs';
import {pageType} from './runner.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(path.join(repo, '.deps/bot-diagnostics/package.json'));
const {chromium} = require('playwright');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const args = Object.fromEntries(process.argv.slice(2).reduce((out, value, i, all) => {
  if (!value.startsWith('--')) return out;
  const [key, inline] = value.slice(2).split('=', 2);
  out.push([key, inline ?? (all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : 'true')]);
  return out;
}, []));
const MAX_DOCS_PER_SITE = 3;
const GLOBAL_DOCUMENT_BUDGET = 3500;

function jsonl(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
}
function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');
}
function appendJsonl(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.appendFileSync(file, JSON.stringify(value) + '\n');
}
function urlKey(value) {
  try { const url = new URL(value); url.hash = ''; return url.href; } catch { return value; }
}
function hostAllowed(value, hosts) {
  try { const url = new URL(value); return /^https?:$/.test(url.protocol) && hosts.has(url.hostname.toLowerCase()); }
  catch { return false; }
}
function completedIds(file) { return new Set(jsonl(file).map(row => String(row.id))); }

// Reserve a conservative whole-run estimate before touching any site.  v1's
// request count is deliberately charged as documents per the handoff.
export function globalCommitted(runDir, fixture = null) {
  const readRows = (name) => fixture?.[name] ?? jsonl(path.join(runDir, name));
  const interruptedFile = path.join(runDir, 'interrupted-executions.json');
  const interruptedCost = !fixture && fs.existsSync(interruptedFile) ? JSON.parse(fs.readFileSync(interruptedFile, 'utf8')).main_unlogged_reserved_documents || 0 : 0;
  const rows = readRows('results.jsonl');
  const candidates = rows.filter(row => ['B1', 'C1'].includes(row.method));
  const candidateCost = candidates.reduce((sum, row) => sum + Math.max(Number(row.document_requests || 0), Number(row.document_responses || 0)), 0);
  const challengeFiles = [path.join(runDir, 'challenge-results.jsonl'), path.join(runDir, 'challenges-results.jsonl')];
  const challengeRows = challengeFiles.flatMap(file => readRows(path.basename(file)));
  const challengeCost = challengeRows.reduce((sum, row) => sum + Math.max(Number(row.document_requests || 0), Number(row.document_responses || 0)), 0);
  const v1Rows = readRows('evaluator-results.preflight-v1.jsonl');
  const v1Documents = v1Rows.length ? v1Rows.reduce((sum, row) => sum + Number(row.requests?.count ?? row.requests ?? 0), 0) : 742;
  const v2Documents = readRows('evaluator-results-v2.jsonl')
    .reduce((sum, row) => sum + Number(row.document_attempts || 0), 0);
  const b0Rows = readRows('b0-results.jsonl');
  const b0Cost = b0Rows.reduce((sum, row) => sum + Math.max(Number(row.document_requests || 0), Number(row.document_responses || 0)), 0);
  return {v1_conservative_documents: v1Documents, v2_document_attempts: v2Documents,
    candidate_rows_conservative: candidateCost, challenge_rows_conservative: challengeCost,
    b0_reserved_documents: b0Cost, ...(interruptedCost ? {interrupted_unlogged_reserved_documents: interruptedCost} : {}), committed: v1Documents + v2Documents + candidateCost + challengeCost + b0Cost + interruptedCost};
}

export function chooseManifestURLs(gold, limit = MAX_DOCS_PER_SITE, allowedHosts = null) {
  const allowed = allowedHosts ? new Set(allowedHosts.map(host => host.toLowerCase())) : null;
  const candidates = gold.documents.filter(doc => doc.label.startsWith('target-') && doc.candidate_valid === true && doc.requested_url
    && (!allowed || (urlHost(doc.requested_url) && allowed.has(urlHost(doc.requested_url)))));
  return candidates.slice(0, limit).map(doc => doc.requested_url);
}
function urlHost(value) { try { return new URL(value).hostname.toLowerCase(); } catch { return null; } }

async function robotsFor(context, cache, url, robotsParser) {
  const origin = new URL(url).origin;
  if (!cache.has(origin)) {
    let value;
    try {
      const response = await context.request.get(new URL('/robots.txt', origin).href, {timeout: 10000});
      const text = await response.text();
      value = {status: response.status(), bytes: Buffer.byteLength(text), source: response.status() >= 400 ? 'unavailable' : 'robots-parser', parser:
        response.status() >= 400 ? null : robotsParser(new URL('/robots.txt', origin).href, text)};
      value.request_count = 1; value.response_count = 1;
    } catch (error) { value = {status: null, source: 'unavailable', error: String(error.message).slice(0, 200), parser: null, request_count: 1, response_count: 0}; }
    cache.set(origin, value);
  }
  const row = cache.get(origin);
  return {status: row.status, source: row.source, ...(row.error ? {error: row.error} : {}),
    allowed: [401, 403, 429].includes(row.status) ? false : row.parser ? row.parser.isAllowed(url, '*') !== false : true,
    crawl_delay_ms: row.parser ? Math.ceil((row.parser.getCrawlDelay('*') || 0) * 1000) : 0};
}

async function capture(page, requestedURL, index, dir, hosts, responseInfo) {
  const url = page.url() || requestedURL;
  const data = await page.evaluate(allowedHosts => {
    const links = [...document.querySelectorAll('a[href]')].map(a => ({url: new URL(a.getAttribute('href'), location.href).href,
      text: (a.innerText || a.textContent || '').trim()})).filter(x => {
      try { return allowedHosts.includes(new URL(x.url).hostname.toLowerCase()); } catch { return false; }
    });
    return {html: document.documentElement?.outerHTML || '', title: document.title || '', links,
      bodyLength: (document.body?.innerText || '').length, text: document.body?.innerText || '',
      heading: document.querySelector('h1')?.innerText || '', canonicalHref: document.querySelector('link[rel="canonical"]')?.href || null,
      schemaTypes: [...document.querySelectorAll('[itemtype]')].map(el => el.getAttribute('itemtype')).filter(Boolean),
      schemaCount: document.querySelectorAll('[itemscope], [itemtype]').length,
      repeated: Math.max(0, ...[...document.querySelectorAll('main,article,section')].map(el => el.querySelectorAll('a[href]').length)),
      stateId: `${location.pathname}${location.search}${location.hash}:${document.title}:${(document.body?.innerText || '').slice(0, 120)}`};
  }, [...hosts]);
  const html = responseInfo?.body ? responseInfo.body.toString('utf8') : '';
  const status = responseInfo?.status ?? null;
  const pageKind = pageType(html, data, data.title, url, status);
  const stem = `${String(index).padStart(3, '0')}-${Buffer.from(url).toString('base64url').slice(0, 90)}`;
  const sourceFile = path.join(dir, `${stem}.html`);
  const domFile = path.join(dir, `${stem}.dom.html`);
  if (html) fs.writeFileSync(sourceFile, html);
  fs.writeFileSync(domFile, data.html);
  return {url, requested_url: requestedURL, title: data.title, page_type: pageKind, state_id: data.stateId, status,
    response_html: html ? path.posix.join('cold', path.basename(sourceFile)) : null,
    rendered_dom: path.posix.join('cold', path.basename(domFile)), body_length: data.bodyLength,
    link_count: data.links.length, repeated_link_max: data.repeated, html_length: data.html.length,
    canonical_href: data.canonicalHref, schema_types: data.schemaTypes, schema_item_count: data.schemaCount,
    links: data.links, navigation_error: null};
}

async function runSite(target, gold, runDir, priorSiteRows, documentAllowance = MAX_DOCS_PER_SITE) {
  const id = String(target.id);
  const dir = path.join(runDir, 'artifacts', id, 'B0', 'cold');
  fs.mkdirSync(dir, {recursive: true});
  const hosts = new Set([new URL(target.start_url).hostname, ...(target.allowed_hosts || [])].map(host => host.toLowerCase()));
  const targets = chooseManifestURLs(gold, Math.min(MAX_DOCS_PER_SITE, documentAllowance), [...hosts]);
  const started = Date.now();
  const browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
  const context = await browser.newContext({...environmentContextOptions(), serviceWorkers: 'block', ignoreHTTPSErrors: false, locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: {width: 1365, height: 900}});
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.setCacheDisabled', {cacheDisabled: true});
  const network = [], responseMap = new Map();
  let requests = 0, xhr = 0, interval = target.practice ? 1000 : 5000, accessStop = null, lastDocumentStart = 0;
  let pacingChain = Promise.resolve();
  page.on('request', request => { requests++; if (['xhr', 'fetch'].includes(request.resourceType())) xhr++;
    network.push({kind: 'request', url: request.url(), method: request.method(), type: request.resourceType()}); });
  page.on('response', response => {
    const request = response.request();
    if (request.resourceType() === 'document' && [401, 403, 429].includes(response.status())) accessStop = `http_${response.status()}`;
    network.push({kind: 'response', url: response.url(), status: response.status(), type: request.resourceType()});
    if (request.resourceType() === 'document') response.body().then(body => responseMap.set(urlKey(response.url()), {body, status: response.status()})).catch(() => {});
  });
  const robotsCache = new Map();
  const robotsParser = require('robots-parser');
  let guard;
  try {
    guard = await installDocumentGuard(page, {limit: targets.length, beforeRequest: async ({url, method, resourceType}) => {
      if (resourceType !== 'Document' || method !== 'GET' || accessStop || !hostAllowed(url, hosts)) return false;
      const robots = await robotsFor(context, robotsCache, url, robotsParser);
      if (!robots.allowed) accessStop = 'robots_disallowed';
      interval = Math.max(interval, robots.crawl_delay_ms || 0);
      let release;
      const priorPacing = pacingChain;
      pacingChain = new Promise(resolve => { release = resolve; });
      await priorPacing;
      try {
        const spacing = Math.max(0, interval - (Date.now() - lastDocumentStart));
        if (spacing) await delay(spacing);
        lastDocumentStart = Date.now();
      } finally { release(); }
      return robots.allowed;
    }});
    const existingStop = priorSiteRows.find(row => ['blocked', 'login_page', 'maintenance', 'robots_disallowed'].includes(row.status));
    const observations = [];
    let status = existingStop ? 'skipped_due_access_condition' : 'completed';
    let stopReason = existingStop ? 'prior_access_condition_stop' : 'fixed_gold_direct_urls';
    if (!existingStop) {
      for (const url of targets) {
        if (Date.now() - started >= 600000) { status = 'time_limit'; stopReason = 'site_time_limit_10min'; break; }
        if (guard.counters.reserved >= MAX_DOCS_PER_SITE) { stopReason = 'document_limit'; break; }
        const robots = await robotsFor(context, robotsCache, url, robotsParser);
        if (!robots.allowed) { status = 'robots_disallowed'; stopReason = 'robots_disallow'; break; }
        let navigationError = null;
        try { await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 30000}); }
        catch (error) { navigationError = String(error.message).slice(0, 300); }
        if (Date.now() - started >= 600000) { status = 'time_limit'; stopReason = 'site_time_limit_10min'; }
        await delay(Math.min(1000, interval));
        const finalURL = page.url() || url;
        const responseInfo = responseMap.get(urlKey(finalURL));
        const row = await capture(page, url, observations.length, dir, hosts, responseInfo);
        row.navigation_error = navigationError;
        row.robots = robots;
        row.document_reserved = guard.counters.reserved;
        observations.push(row);
        if (['block', 'login', 'maintenance'].includes(row.page_type)) {
          status = row.page_type === 'block' ? 'blocked' : row.page_type === 'login' ? 'login_page' : 'maintenance';
          stopReason = `${row.page_type}_page`; break;
        }
        if (accessStop) { status = accessStop.startsWith('http_') ? 'blocked' : 'robots_disallowed'; stopReason = accessStop; break; }
        if (status === 'time_limit') break;
      }
    }
    const robots = [...robotsCache.entries()].map(([origin, info]) => ({origin, status: info.status, source: info.source, error: info.error,
      requests: info.request_count || 0, responses: info.response_count || 0, bytes: info.bytes || 0}));
    writeJson(path.join(dir, 'observations.json'), {observations, network, robots, document_guard: guard.counters});
    return {id, domain: target.domain, method: 'B0', mode: 'cold', trial_type: 'fixed_gold_direct', status, stop_reason: stopReason,
      robots, gold_input: 'private_manifest_only_for_B0', fixed_url_count: targets.length,
      requested_document_allowance: documentAllowance,
      budget_limited: documentAllowance < MAX_DOCS_PER_SITE && chooseManifestURLs(gold, MAX_DOCS_PER_SITE, [...hosts]).length > documentAllowance,
      discovered_documents: observations.filter(row => row.status !== null && !row.navigation_error).length,
      document_requests: guard.counters.reserved, document_responses: network.filter(row => row.kind === 'response' && row.type === 'document').length,
      document_denied: guard.counters.denied, http_requests: requests + robots.reduce((sum, row) => sum + row.requests, 0),
      robots_requests: robots.reduce((sum, row) => sum + row.requests, 0), robots_responses: robots.reduce((sum, row) => sum + row.responses, 0),
      xhr_fetch_requests: xhr, operations: 0, browser_version: browser.version(), environment_layer: 'current_chromium_ja-JP_Asia-Tokyo',
      elapsed_ms: Date.now() - started, external_llm_calls: 0, local_inference: {calls: 0, input_tokens: 0, runtime_ms: 0}, pages: observations};
  } finally {
    if (guard) await guard.cleanup();
    await context.close(); await browser.close();
  }
}

async function main() {
  if (!args.run) throw new Error('required_argument:--run');
  const runDir = path.resolve(args.run);
  const targetsPath = path.resolve(args.targets || path.join(runDir, 'targets.jsonl'));
  const targets = jsonl(targetsPath);
  const only = new Set(String(args.ids || '').split(',').filter(Boolean));
  const selected = targets.filter(target => !only.size || only.has(String(target.id)));
  if (!selected.length) throw new Error('no_targets_selected');
  const resultsFile = path.join(runDir, 'b0-results.jsonl');
  const priorRows = jsonl(resultsFile);
  const done = completedIds(resultsFile);
  const commitment = globalCommitted(runDir);
  for (const target of selected) {
    const id = String(target.id);
    if (done.has(id)) continue;
    const remaining = GLOBAL_DOCUMENT_BUDGET - commitment.committed;
    if (remaining <= 0) {
      appendJsonl(resultsFile, {id, domain: target.domain, method: 'B0', mode: 'cold', status: 'budget_unavailable', stop_reason: 'global_3500_document_budget_reserved', ...commitment});
      continue;
    }
    const file = path.join(runDir, 'gold-private-v2', id, 'result.json');
    if (!fs.existsSync(file)) {
      appendJsonl(resultsFile, {id, domain: target.domain, method: 'B0', mode: 'cold', status: 'missing_fixed_manifest', stop_reason: 'gold_v2_manifest_missing'});
      continue;
    }
    const gold = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!Array.isArray(gold.documents)) throw new Error(`invalid_gold_manifest:${id}`);
    const siteHosts = [new URL(target.start_url).hostname, ...(target.allowed_hosts || [])];
    const requestedCount = Math.min(MAX_DOCS_PER_SITE, chooseManifestURLs(gold, MAX_DOCS_PER_SITE, siteHosts).length, remaining);
    if (!requestedCount) {
      appendJsonl(resultsFile, {id, domain: target.domain, method: 'B0', mode: 'cold', status: 'no_valid_manifest_targets', stop_reason: 'no_candidate_valid_target_urls'});
      continue;
    }
    const challengeRows = [path.join(runDir, 'challenge-results.jsonl'), path.join(runDir, 'challenges-results.jsonl')].flatMap(jsonl);
    const siteRows = [...jsonl(path.join(runDir, 'results.jsonl')).filter(row => String(row.id) === id),
      ...challengeRows.filter(row => String(row.id ?? row.site_id) === id)];
    const result = await runSite(target, gold, runDir, siteRows, requestedCount);
    commitment.b0_reserved_documents += Math.max(Number(result.document_requests || 0), Number(result.document_responses || 0));
    commitment.committed += Math.max(Number(result.document_requests || 0), Number(result.document_responses || 0));
    result.global_budget = {...commitment, remaining: GLOBAL_DOCUMENT_BUDGET - commitment.committed};
    appendJsonl(resultsFile, result);
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url)
  main().catch(error => { console.error(JSON.stringify({error: error.message})); process.exitCode = 1; });
