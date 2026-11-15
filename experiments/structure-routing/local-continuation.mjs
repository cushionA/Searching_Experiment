import {environmentContextOptions} from './environment.mjs';
import {installDocumentGuard} from './document-guard.mjs';
import {pageType} from './runner.mjs';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(path.join(repo, '.deps/bot-diagnostics/package.json'));
const {chromium} = require('playwright');
const robotsParser = require('robots-parser');
const DEFAULT_RUN = path.join(repo, 'lab-runs/structure-routing-20261115-local-continuation');
const DEFAULT_BASELINE = path.join(repo, 'lab-runs/structure-routing-20261115-cloud-restored');
const MAX_SITE_DOCS = 1;
const MAX_BULK_DOCS = 125;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith('--')) throw new Error(`unexpected_argument:${argv[i]}`);
    const key = argv[i].slice(2);
    const value = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : 'true';
    out[key] = value;
  }
  return out;
}
function readJSON(file) { return JSON.parse(fs.readFileSync(file, 'utf8')); }
function readJSONL(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map(JSON.parse);
}
function writeJSON(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');
}
function appendJSONL(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.appendFileSync(file, JSON.stringify(value) + '\n');
}
function sha256(file) { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); }
function normalizeTarget(target) {
  const hosts = new Set([new URL(target.start_url).hostname.toLowerCase(), ...(target.allowed_hosts || []).map(x => x.toLowerCase())]);
  return hosts;
}
function safeGet(value, hosts) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && hosts.has(url.hostname.toLowerCase());
  } catch { return false; }
}

export function initialBudget() {
  return {version: 1, baseline_run: 'lab-runs/structure-routing-20261115-cloud-restored',
    main: {cap: 3500, spent_before: 1794, spent: 1794, reserved: 0, remaining: 1706},
    stageC: {cap: 400, spent_before: 124, spent: 124, reserved: 0, remaining: 276}};
}

export function reserveBudget(budget, category, amount) {
  if (!['main', 'stageC'].includes(category) || !Number.isSafeInteger(amount) || amount < 1)
    throw new TypeError('invalid_budget_reservation');
  const account = budget[category];
  const available = account.cap - account.spent - account.reserved;
  if (amount > available) throw new Error(`budget_unavailable:${category}:${available}`);
  account.reserved += amount;
  account.remaining = account.cap - account.spent - account.reserved;
}

export function finalizeBudget(budget, category, reservation, actual, interrupted = false) {
  if (!Number.isSafeInteger(reservation) || reservation < 1 || !Number.isSafeInteger(actual) || actual < 0 || actual > reservation)
    throw new TypeError('invalid_budget_finalization');
  const charged = interrupted ? reservation : actual;
  const account = budget[category];
  if (account.reserved < reservation) throw new Error(`reservation_missing:${category}`);
  account.reserved -= reservation;
  account.spent += charged;
  account.remaining = account.cap - account.spent - account.reserved;
  return charged;
}

export function selectObservedNext(pagination, entryURL) {
  return pagination.find(item => item.source === entryURL && /^(?:next|次)/i.test(String(item.text || '').trim()) &&
    typeof item.url === 'string' && item.url.length) || null;
}

export function safeUniqueTargets(values, currentURL, hosts, limit = 3) {
  const seen = new Set();
  const out = [];
  for (const value of values) {
    let url;
    try { url = new URL(value, currentURL); } catch { continue; }
    const normalized = url.href;
    if (!safeGet(normalized, hosts) || !/^https?:$/.test(url.protocol) ||
      /\/(?:login|logout|register|cart|checkout|account|api|privacy|terms)(?:[/?#]|$)/i.test(url.pathname) || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
    if (out.length >= limit) break;
  }
  return out;
}

function initRun(runDir, baselineDir) {
  const targetsFile = path.join(runDir, 'targets.jsonl');
  if (!fs.existsSync(targetsFile)) {
    const source = path.join(baselineDir, 'targets.jsonl');
    if (!fs.existsSync(source)) throw new Error('missing_source_targets_jsonl');
    fs.mkdirSync(runDir, {recursive: true});
    fs.copyFileSync(source, targetsFile);
  }
  const budgetFile = path.join(runDir, 'budget.json');
  const referenceFile = path.join(runDir, 'baseline-ref.json');
  if (!fs.existsSync(budgetFile)) {
    const baselineTargets = path.join(baselineDir, 'targets.jsonl');
    writeJSON(budgetFile, initialBudget());
    writeJSON(referenceFile, {baseline_run: path.resolve(baselineDir), baseline_targets_sha256: fs.existsSync(baselineTargets) ? sha256(baselineTargets) : null,
      spent_before: {main: 1794, stageC: 124}, created_at: new Date().toISOString()});
  }
  return {budgetFile, budget: readJSON(budgetFile)};
}

function loadTargets(runDir, ids) {
  const requested = new Set(ids);
  const targets = readJSONL(path.join(runDir, 'targets.jsonl')).filter(x => !requested.size || requested.has(String(x.id)));
  if (targets.length !== requested.size && requested.size) {
    const found = new Set(targets.map(x => String(x.id)));
    throw new Error(`unknown_target_ids:${[...requested].filter(x => !found.has(x)).join(',')}`);
  }
  if (!targets.length) throw new Error('no_targets_selected');
  return targets;
}

function recordReservation(budgetFile, category, amount, metadata) {
  const budget = readJSON(budgetFile);
  reserveBudget(budget, category, amount);
  budget.updated_at = new Date().toISOString();
  budget.last_reservation = {category, amount, ...metadata, status: 'reserved', reserved_at: budget.updated_at};
  writeJSON(budgetFile, budget);
}
function recordFinal(budgetFile, category, amount, actual, metadata, interrupted = false) {
  const budget = readJSON(budgetFile);
  const charged = finalizeBudget(budget, category, amount, actual, interrupted);
  budget.updated_at = new Date().toISOString();
  budget.last_reservation = {...budget.last_reservation, ...metadata, status: interrupted ? 'interrupted_reserved' : 'finalized',
    actual, charged, finalized_at: interrupted ? null : budget.updated_at};
  writeJSON(budgetFile, budget);
  return charged;
}

function acquireRunLock(runDir) {
  const lockFile = path.join(runDir, 'budget.lock');
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const fd = fs.openSync(lockFile, 'wx');
      fs.writeFileSync(fd, JSON.stringify({pid: process.pid, started_at: new Date().toISOString()}));
      return () => { fs.closeSync(fd); fs.rmSync(lockFile, {force: true}); };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      let owner;
      try { owner = readJSON(lockFile); } catch { throw new Error('budget_lock_unreadable'); }
      try { process.kill(owner.pid, 0); throw new Error(`budget_run_already_active:${owner.pid}`); }
      catch (probeError) {
        if (probeError.code !== 'ESRCH') throw probeError;
        fs.rmSync(lockFile, {force: true});
      }
    }
  }
  throw new Error('budget_lock_acquisition_failed');
}

async function robotsAllowed(context, cache, url, onRequest = () => {}) {
  const origin = new URL(url).origin;
  if (!cache.has(origin)) {
    let info;
    try {
      const response = await context.request.get(`${origin}/robots.txt`, {timeout: 15000});
      const body = await response.text();
      const status = response.status();
      onRequest({status, bytes: Buffer.byteLength(body)});
      info = {status, bytes: Buffer.byteLength(body), parser: status === 200 ? robotsParser(`${origin}/robots.txt`, body) : null,
        unavailable: status >= 500 || [401, 403, 429].includes(status)};
    } catch (error) { onRequest({status: null, bytes: 0}); info = {status: null, bytes: 0, parser: null, unavailable: true, error: String(error.message).slice(0, 200)}; }
    cache.set(origin, info);
  }
  const info = cache.get(origin);
  return {allowed: !info.unavailable && (!info.parser || info.parser.isAllowed(url, '*') !== false),
    status: info.status, bytes: info.bytes, error: info.error || null};
}

async function runHealth(target, runDir, budgetFile, baselineDir) {
  const id = String(target.id);
  const reservation = MAX_SITE_DOCS;
  recordReservation(budgetFile, 'main', reservation, {mode: 'health', id});
  const directory = path.join(runDir, 'health', id, `attempt-${Date.now()}`);
  fs.mkdirSync(directory, {recursive: true});
  const started = Date.now();
  let browser, context, guard;
  const result = {id, category: 'local_health_diagnostic', mode: 'health', status: 'started', started_at: new Date(started).toISOString(),
    baseline_run: path.resolve(baselineDir), document_reservation: reservation, document_requests: 0, document_responses: 0,
    requests: 0, xhr_fetch: 0, bytes: 0, duration_ms: null, external_llm_calls: 0, local_inference_calls: 0};
  const save = () => writeJSON(path.join(directory, 'result.json'), result);
  save();
  try {
    const hosts = normalizeTarget(target);
    browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
    context = await browser.newContext({...environmentContextOptions(), locale: 'ja-JP', timezoneId: 'Asia/Tokyo',
      viewport: {width: 1365, height: 900}, serviceWorkers: 'block'});
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.setCacheDisabled', {cacheDisabled: true});
    cdp.on('Network.loadingFinished', event => { result.bytes += event.encodedDataLength || 0; });
    page.on('request', request => {
      result.requests++;
      if (['xhr', 'fetch'].includes(request.resourceType())) result.xhr_fetch++;
    });
    const responseBodies = new Map();
    page.on('response', response => {
      if (response.request().resourceType() !== 'document') return;
      result.document_responses++;
      response.body().then(body => responseBodies.set(response.url(), body)).catch(() => {});
    });
    const robotCache = new Map();
    let lastDocument = 0;
    guard = await installDocumentGuard(page, {limit: reservation,
      onReserved: (_info, counters) => { result.document_requests = counters.reserved; save(); },
      beforeRequest: async info => {
        if (!info.mainFrame || info.method !== 'GET' || !safeGet(info.url, hosts)) return false;
        const robots = await robotsAllowed(context, robotCache, info.url, metric => {
          result.requests++;
          result.robots_requests = (result.robots_requests || 0) + 1;
          result.bytes += metric.bytes;
        });
        result.robots = [...robotCache.entries()].map(([origin, value]) => ({origin, status: value.status, bytes: value.bytes, error: value.error || null}));
        result.robots_allowed = robots.allowed;
        save();
        if (!robots.allowed) return false;
        await delay(Math.max(0, 1000 - (Date.now() - lastDocument)));
        lastDocument = Date.now();
        return true;
      }});
    if (guard.counters.reserved >= reservation) throw new Error('health_document_budget_exhausted');
    const response = await page.goto(target.start_url, {waitUntil: 'domcontentloaded', timeout: 30000});
    await delay(1000);
    const dom = await page.evaluate(() => ({html: document.documentElement?.outerHTML || '', title: document.title || '',
      heading: document.querySelector('h1,h2')?.innerText?.trim() || '', text: document.body?.innerText || '',
      bodyLength: (document.body?.innerText || '').length, links: [...document.querySelectorAll('a[href]')].map(a => ({url: a.href, text: (a.innerText || a.textContent || '').trim()})),
      repeated: Math.max(0, ...[...document.querySelectorAll('main,article,section')].map(el => el.querySelectorAll('a[href]').length)),
      schemaCount: document.querySelectorAll('[itemscope], [itemtype]').length}));
    const status = response?.status() ?? null;
    const responseHTML = responseBodies.get(page.url()) || Buffer.alloc(0);
    const kind = pageType(responseHTML.toString('utf8'), dom, dom.title, page.url(), status);
    const stem = `001-${Buffer.from(page.url()).toString('base64url').slice(0, 80)}`;
    fs.writeFileSync(path.join(directory, `${stem}.html`), responseHTML);
    fs.writeFileSync(path.join(directory, `${stem}.dom.html`), dom.html);
    result.observation = {url: page.url(), requested_url: target.start_url, status, page_type: kind, title: dom.title,
      heading: dom.heading, body_length: dom.bodyLength, html_length: dom.html.length,
      response_html: `${stem}.html`, rendered_dom: `${stem}.dom.html`, usable: status === 200 && ['listing', 'detail', 'content'].includes(kind) && Boolean(dom.heading) && dom.bodyLength > 100};
    result.status = result.observation.usable ? 'healthy' : kind === 'block' ? 'blocked' : kind === 'empty_spa' ? 'empty_spa' : 'unusable';
    result.document_guard = guard.counters;
  } catch (error) {
    result.status = 'error';
    result.error = String(error.message).slice(0, 300);
    if (guard) result.document_guard = guard.counters;
  } finally {
    if (guard) await guard.cleanup();
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    result.duration_ms = Date.now() - started;
    result.finished_at = new Date().toISOString();
    const actual = Math.max(result.document_requests, result.document_responses);
    result.document_actual = actual;
    recordFinal(budgetFile, 'main', reservation, actual, {mode: 'health', id, output: path.relative(runDir, directory)});
    save();
    appendJSONL(path.join(runDir, 'health-results.jsonl'), result);
  }
  return result;
}

function pageMetrics(result) {
  return {requests: result.requests, xhr_fetch: result.xhr_fetch, bytes: result.bytes,
    document_requests: result.document_requests, document_responses: result.document_responses};
}

async function runModelProbe(target, runDir, budgetFile, baselineDir) {
  const id = String(target.id);
  const reservation = 4;
  const directory = path.join(runDir, 'model-probe', id);
  const domDirectory = path.join(directory, 'DOM');
  if (fs.existsSync(directory)) throw new Error(`model_probe_output_already_exists:${id}`);
  const modelFile = path.join(runDir, 'minilm-books-representative.json');
  const rulesFile = path.join(runDir, 'refined-rules', id, 'rules.json');
  const rulesMetadataFile = path.join(runDir, 'refined-rules', id, 'metadata.json');
  const healthRow = readJSONL(path.join(runDir, 'health-results.jsonl')).find(row => String(row.id) === id && row.status === 'healthy');
  if (!healthRow) throw new Error(`missing_healthy_entry:${id}`);
  const model = readJSON(modelFile);
  const rules = readJSON(rulesFile);
  const rulesMetadata = readJSON(rulesMetadataFile);
  if (model.status !== 'ok' || model.external_api_calls !== 0 || model.future_page_model_calls !== 0 ||
    !model.frozen_rule?.selector || model.metrics?.device !== 'cpu' || !model.model?.revision)
    throw new Error(`invalid_frozen_model_probe_input:${id}`);
  if (rulesMetadata.variant !== 'C1.1_navigation_region_fix' || rulesMetadata.id !== id || rulesMetadata.source_url !== healthRow.observation?.url ||
    rulesMetadata.rule_sha256?.toLowerCase() !== sha256(rulesFile) || rulesMetadata.gold_input !== false)
    throw new Error(`invalid_refined_rules_provenance:${id}`);
  if (String(model.frozen_rule.selector).length > 500 || !/^[\w\s.#[\]>="'-]+$/.test(model.frozen_rule.selector))
    throw new Error('invalid_frozen_selector');
  const entryURL = healthRow.observation?.url;
  const next = selectObservedNext(rules.pagination || [], entryURL);
  if (!next || !safeGet(next.url, normalizeTarget(target))) throw new Error(`observed_next_url_missing_or_unsafe:${id}`);
  const targetFile = path.join(directory, 'result.json');
  const rulesHash = sha256(rulesFile);
  const modelHash = sha256(modelFile);
  const started = Date.now();
  const result = {id, category: 'main_model_probe', mode: 'model-probe', status: 'started', variant: 'MiniLM_frozen_selector_pilot',
    purpose: 'small_additive_value_pilot_only', started_at: new Date(started).toISOString(),
    baseline_run: path.resolve(baselineDir), entry_url: entryURL, observed_next: next,
    rules_source: path.relative(runDir, rulesFile), rules_sha256: rulesHash,
    rules_metadata: {file: path.relative(runDir, rulesMetadataFile), sha256: sha256(rulesMetadataFile), source_dom: rulesMetadata.source_dom,
      source_dom_sha256: rulesMetadata.source_dom_sha256, rule_learning_ms: rulesMetadata.rule_learning_ms,
      new_document_loads: rulesMetadata.new_document_loads, gold_input: rulesMetadata.gold_input},
    model_source: path.relative(runDir, modelFile), model_source_sha256: modelHash,
    frozen_selector: model.frozen_rule.selector, model_profile: {repo_id: model.model.repo_id, revision: model.model.revision,
      device: model.metrics.device, inference_seconds: model.metrics.inference_seconds, forward_calls: model.metrics.forward_calls,
      tokenized_inputs: model.metrics.tokenized_inputs, input_token_total: model.metrics.input_token_total,
      external_api_calls: model.external_api_calls, future_page_model_calls: model.future_page_model_calls},
    external_llm_calls: 0, local_inference_calls: 0, future_page_model_calls: 0,
    document_reservation: reservation, document_requests: 0, document_responses: 0, requests: 0, xhr_fetch: 0, bytes: 0,
    elapsed_ms: null, observations: [], stop_reason: null};
  fs.mkdirSync(directory, {recursive: true});
  const persist = () => writeJSON(targetFile, result);
  fs.mkdirSync(domDirectory, {recursive: true});
  recordReservation(budgetFile, 'main', reservation, {mode: 'model-probe', id, model_source_sha256: modelHash, rules_sha256: rulesHash});
  persist();
  const hosts = normalizeTarget(target);
  const robotCache = new Map();
  let browser, context, guard, page;
  let previousDocument = 0;
  try {
    browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
    context = await browser.newContext({...environmentContextOptions(), locale: 'ja-JP', timezoneId: 'Asia/Tokyo',
      viewport: {width: 1365, height: 900}, serviceWorkers: 'block'});
    page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.setCacheDisabled', {cacheDisabled: true});
    cdp.on('Network.loadingFinished', event => { result.bytes += event.encodedDataLength || 0; });
    page.on('request', request => { result.requests++; if (['xhr', 'fetch'].includes(request.resourceType())) result.xhr_fetch++; });
    page.on('response', response => {
      if (response.request().resourceType() === 'document') {
        result.document_responses++;
      }
    });
    guard = await installDocumentGuard(page, {limit: reservation,
      onReserved: (_info, counters) => { result.document_requests = counters.reserved; persist(); },
      beforeRequest: async info => {
        if (!info.mainFrame || info.method !== 'GET' || !safeGet(info.url, hosts)) return false;
        const robots = await robotsAllowed(context, robotCache, info.url, metric => {
          result.requests++;
          result.robots_requests = (result.robots_requests || 0) + 1;
          result.bytes += metric.bytes;
        });
        result.robots = [...robotCache.entries()].map(([origin, value]) => ({origin, status: value.status, bytes: value.bytes, error: value.error || null}));
        if (!robots.allowed) { result.stop_reason = 'robots_disallowed'; return false; }
        await delay(Math.max(0, 1000 - (Date.now() - previousDocument)));
        previousDocument = Date.now();
        return true;
      }});
    const observe = async (requestedURL, kind, referrer = null) => {
      const before = pageMetrics(result);
      const startedAt = Date.now();
      let response = null;
      let navigationError = null;
      try { response = await page.goto(requestedURL, {waitUntil: 'domcontentloaded', timeout: 30000, ...(referrer ? {referer: referrer} : {})}); }
      catch (error) { navigationError = String(error.message).slice(0, 300); }
      await delay(100);
      const finalURL = page.url() || requestedURL;
      let dom = {html: '', title: '', heading: '', text: '', bodyLength: 0, links: [], repeated: 0, schemaCount: 0, stateId: ''};
      if (!navigationError && response) {
        try { dom = await page.evaluate(() => ({html: document.documentElement?.outerHTML || '', title: document.title || '',
          heading: document.querySelector('h1,h2')?.innerText?.trim() || '', text: document.body?.innerText || '',
          bodyLength: (document.body?.innerText || '').length,
          links: [...document.querySelectorAll('a[href]')].map(a => ({url: new URL(a.getAttribute('href'), location.href).href,
            text: (a.innerText || a.textContent || '').trim()})),
          repeated: Math.max(0, ...[...document.querySelectorAll('main,article,section')].map(el => el.querySelectorAll('a[href]').length)),
          schemaCount: document.querySelectorAll('[itemscope], [itemtype]').length,
          stateId: `${location.pathname}${location.search}${location.hash}:${document.title}:${(document.body?.innerText || '').slice(0, 120)}`})); }
        catch (error) { navigationError ||= String(error.message).slice(0, 300); }
      }
      const status = response?.status() ?? null;
      let responseHTML = Buffer.alloc(0);
      if (response) { try { responseHTML = await response.body(); } catch {} }
      const pageKind = pageType(responseHTML.toString('utf8'), dom, dom.title, finalURL, status);
      const stem = `${String(result.observations.length + 1).padStart(2, '0')}-${Buffer.from(finalURL).toString('base64url').slice(0, 70)}`;
      fs.writeFileSync(path.join(domDirectory, `${stem}.html`), responseHTML);
      fs.writeFileSync(path.join(domDirectory, `${stem}.dom.html`), dom.html);
      const observation = {index: result.observations.length, kind, requested_url: requestedURL, final_url: finalURL,
        expected_requested_vs_final: {requested: requestedURL, final: finalURL, matched: requestedURL === finalURL},
        referer: referrer, status, page_type: pageKind, title: dom.title, heading: dom.heading,
        body_length: dom.bodyLength, state_id: dom.stateId,
        source_html: `DOM/${stem}.html`, rendered_dom: `DOM/${stem}.dom.html`,
        source_html_sha256: crypto.createHash('sha256').update(responseHTML).digest('hex'),
        rendered_dom_sha256: crypto.createHash('sha256').update(dom.html).digest('hex'),
        navigation_error: navigationError, elapsed_ms: Date.now() - startedAt,
        cost: {before, after: pageMetrics(result), delta: Object.fromEntries(Object.keys(before).map(key => [key, pageMetrics(result)[key] - before[key]]))}};
      result.observations.push(observation);
      persist();
      if ([401, 403, 429].includes(status) || ['block', 'login', 'empty_spa', 'maintenance'].includes(pageKind)) {
        result.stop_reason = `${pageKind}_or_access_status`;
        return {observation, stopped: true, dom};
      }
      if (navigationError) { result.stop_reason ||= 'navigation_error'; return {observation, stopped: true, dom}; }
      return {observation, stopped: false, dom};
    };
    const listResult = await observe(next.url, 'observed_next_page');
    if (!listResult.stopped && guard.counters.reserved < reservation) {
      const hrefs = await page.locator(model.frozen_rule.selector).evaluateAll(elements => elements.map(element => {
        const value = element.getAttribute('href');
        return value == null ? null : new URL(value, location.href).href;
      }).filter(Boolean));
      result.selector_applied_to = listResult.observation.final_url;
      result.selector_matches = hrefs.length;
      const targets = safeUniqueTargets(hrefs, listResult.observation.final_url, hosts, 3);
      result.target_count = targets.length;
      result.target_duplicates_removed = hrefs.length - new Set(hrefs).size;
      result.target_rejected = hrefs.length - targets.length - result.target_duplicates_removed;
      for (const url of targets) {
        if (guard.counters.reserved >= reservation) { result.stop_reason = 'document_budget'; break; }
        const row = await observe(url, 'frozen_selector_target', listResult.observation.final_url);
        if (row.stopped) break;
      }
    }
    result.stop_reason ||= guard.counters.reserved >= reservation ? 'four_document_pilot_cap' : 'pilot_targets_exhausted';
    result.document_guard = guard.counters;
    result.browser_version = browser.version();
  } catch (error) {
    result.status = 'error';
    result.error = String(error.message).slice(0, 300);
    result.stop_reason ||= 'execution_error';
    if (guard) result.document_guard = guard.counters;
  } finally {
    if (guard) await guard.cleanup();
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    result.elapsed_ms = Date.now() - started;
    result.finished_at = new Date().toISOString();
    result.document_actual = Math.max(result.document_requests, result.document_responses);
    if (result.status === 'started') result.status = result.observations.length ? 'completed' : 'failed';
    recordFinal(budgetFile, 'main', reservation, Math.min(reservation, result.document_actual),
      {mode: 'model-probe', id, output: path.relative(runDir, directory), model_source_sha256: modelHash, rules_sha256: rulesHash});
    persist();
    appendJSONL(path.join(runDir, 'model-probe-results.jsonl'), result);
  }
  return result;
}

function prepareBulkChild(target, {runDir, childRoot, sourceRules}) {
  const id = String(target.id);
  const child = path.join(runDir, childRoot, id);
  if (fs.existsSync(path.join(child, 'artifacts', id, 'C1', 'bulk', 'result.json')))
    throw new Error(`bulk_child_already_complete:${id}`);
  fs.mkdirSync(child, {recursive: true});
  writeJSONL(path.join(child, 'targets.jsonl'), [target]);
  if (!fs.existsSync(sourceRules)) throw new Error(`missing_baseline_rules:${id}`);
  const targetRules = path.join(child, 'artifacts', id, 'C1', 'cold', 'rules.json');
  fs.mkdirSync(path.dirname(targetRules), {recursive: true});
  fs.copyFileSync(sourceRules, targetRules);
  return {child, rules_sha256: sha256(sourceRules)};
}

function writeJSONL(file, values) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, values.map(value => JSON.stringify(value)).join('\n') + '\n');
}

async function runBulk(target, {baselineDir, runDir, budgetFile, childRoot = 'bulk-children', sourceRules, outputFile = 'bulk-results.jsonl',
  variant = 'C1', category = 'stageC_local_bulk', comparison = null, metadataFile = null}) {
  const id = String(target.id);
  const reservation = MAX_BULK_DOCS;
  const rulesPath = sourceRules || path.join(baselineDir, 'artifacts', id, 'C1', 'cold', 'rules.json');
  const prepared = prepareBulkChild(target, {runDir, childRoot, sourceRules: rulesPath});
  const rulesStat = fs.statSync(rulesPath);
  const rulesMeta = {rules_sha256: prepared.rules_sha256, rules_source: path.relative(runDir, rulesPath), rules_source_mtime: rulesStat.mtime.toISOString()};
  let refinement = null;
  if (metadataFile) {
    const metadata = readJSON(metadataFile);
    const sourceDOM = path.resolve(runDir, metadata.source_dom || '');
    if (metadata.id !== id || metadata.variant !== variant || metadata.rule_sha256?.toLowerCase() !== prepared.rules_sha256 ||
      metadata.representative_pages !== 1 || metadata.new_document_loads !== 0 || metadata.html_cache_counted_as_acquisition !== false ||
      metadata.external_llm_calls !== 0 || metadata.local_model_inference_calls !== 0 || metadata.site_specific_selectors !== 0 ||
      metadata.common_rule_repairs !== 1 || metadata.gold_input !== false || !metadata.source_dom_sha256 || !metadata.source_url ||
      !fs.existsSync(sourceDOM) || sha256(sourceDOM) !== metadata.source_dom_sha256.toLowerCase())
      throw new Error(`invalid_refinement_metadata:${id}`);
    refinement = {metadata_file: path.relative(runDir, metadataFile), metadata_sha256: sha256(metadataFile), ...metadata};
  }
  const auditMeta = {...rulesMeta, ...(refinement ? {refinement} : {})};
  recordReservation(budgetFile, 'stageC', reservation, {mode: variant === 'C1' ? 'bulk' : 'repaired-bulk', id, variant, ...auditMeta});
  const bulk = path.join(path.dirname(fileURLToPath(import.meta.url)), 'bulk.mjs');
  const child = await spawnNode(bulk, ['--run', prepared.child, '--id', id, '--documents', String(reservation)]);
  const resultFile = path.join(prepared.child, 'artifacts', id, 'C1', 'bulk', 'result.json');
  let result = null;
  if (fs.existsSync(resultFile)) result = readJSON(resultFile);
  const actual = result ? Math.max(Number(result.document_attempts || 0), Number(result.document_responses || 0)) : reservation;
  recordFinal(budgetFile, 'stageC', reservation, actual, {mode: variant === 'C1' ? 'bulk' : 'repaired-bulk', id, variant, ...auditMeta,
    output: path.relative(runDir, path.dirname(resultFile)), exit_code: child.status}, child.status !== 0 && !result);
  const wrapperResult = {id, category, variant, baseline_run: path.resolve(baselineDir), child_run: path.relative(runDir, prepared.child),
    status: result?.stop_reason || (child.status === 0 ? 'completed' : 'interrupted'), exit_code: child.status,
    reserved_documents: reservation, actual_documents: actual, ...auditMeta, ...(comparison ? {comparison} : {}), bulk_result: result};
  appendJSONL(path.join(runDir, outputFile), wrapperResult);
  return wrapperResult;
}

function spawnNode(script, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script, ...args], {cwd: repo, env: process.env, stdio: 'inherit', windowsHide: true});
    child.once('error', reject);
    child.once('close', status => resolve({status}));
  });
}

export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const mode = args.mode;
  if (!['health', 'bulk', 'repaired-bulk', 'model-probe'].includes(mode)) throw new Error('mode_must_be_health_bulk_repaired_bulk_or_model_probe');
  if (args.live !== 'true') throw new Error('live_execution_requires_explicit_--live');
  const runDir = path.resolve(args.run || DEFAULT_RUN);
  const baselineDir = path.resolve(args.baseline || DEFAULT_BASELINE);
  const {budgetFile} = initRun(runDir, baselineDir);
  const releaseLock = acquireRunLock(runDir);
  try {
  if (mode === 'health') {
    const ids = String(args.ids || '').split(',').filter(Boolean);
    if (ids.some(id => !['01', '08'].includes(id))) throw new Error('health_ids_must_be_01_or_08');
    const targets = loadTargets(runDir, ids);
    if (args.documents && Number(args.documents) !== MAX_SITE_DOCS) throw new Error('health_documents_must_equal_1');
    for (const target of targets) {
      const prior = readJSONL(path.join(runDir, 'health-results.jsonl')).find(row => String(row.id) === String(target.id));
      if (prior) throw new Error(`health_site_already_attempted:${target.id}`);
      const result = await runHealth(target, runDir, budgetFile, baselineDir);
      if (result.status !== 'healthy') break;
    }
    return;
  }
  if (mode === 'model-probe') {
    const id = String(args.id || '');
    if (id !== '01') throw new Error('model_probe_id_must_be_01');
    const target = loadTargets(runDir, [id])[0];
    const budget = readJSON(budgetFile);
    if (budget.main.remaining < 4) throw new Error(`main_reservation_unavailable:${budget.main.remaining}`);
    await runModelProbe(target, runDir, budgetFile, baselineDir);
    process.stdout.write(JSON.stringify(readJSON(path.join(runDir, 'model-probe', id, 'result.json'))) + '\n');
    return;
  }
  const id = String(args.id || '');
  if (!id) throw new Error('bulk_requires_id');
  if (mode === 'repaired-bulk' ? id !== '01' : !['01', '08'].includes(id)) throw new Error('bulk_id_not_authorized');
  const target = loadTargets(runDir, [id])[0];
  if (target.practice !== true) throw new Error(`stageC_requires_practice_target:${id}`);
  const budget = readJSON(budgetFile);
  if (budget.stageC.remaining < MAX_BULK_DOCS) throw new Error(`stageC_reservation_unavailable:${budget.stageC.remaining}`);
  const repaired = mode === 'repaired-bulk';
  let comparison = null;
  if (repaired) {
    const prior = readJSONL(path.join(runDir, 'bulk-results.jsonl')).find(row => String(row.id) === id && row.category === 'stageC_local_bulk');
    if (!prior) throw new Error(`missing_prior_c1_bulk_result:${id}`);
    comparison = {prior_result: path.join('bulk-children', id, 'artifacts', id, 'C1', 'bulk', 'result.json'),
      prior_actual_documents: prior.actual_documents, stageC_spent_before_repair: budget.stageC.spent,
      stageC_remaining_before_repair: budget.stageC.remaining,
      hypothesis: 'generic_navigation_region_selector_reduces_listing_overselection'};
  }
  const result = await runBulk(target, {baselineDir, runDir, budgetFile,
    ...(repaired ? {childRoot: 'repair-children', sourceRules: path.join(runDir, 'refined-rules', id, 'rules.json'),
      metadataFile: path.join(runDir, 'refined-rules', id, 'metadata.json'),
      outputFile: 'repair-bulk-results.jsonl', variant: 'C1.1_navigation_region_fix', category: 'stageC_repaired_bulk', comparison} : {})});
  process.stdout.write(JSON.stringify(result) + '\n');
  } finally {
    releaseLock();
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url)
  main().catch(error => { console.error(JSON.stringify({error: error.message})); process.exitCode = 1; });
