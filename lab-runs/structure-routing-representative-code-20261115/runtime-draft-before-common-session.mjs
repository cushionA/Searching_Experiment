import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {installDocumentGuard} from './document-guard.mjs';
import {environmentContextOptions} from './environment.mjs';
import {makeBrowserPage, observePage} from './sitemap-agent-browser.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const require = createRequire(path.join(root, '.deps/bot-diagnostics/package.json'));
const {chromium} = require('playwright');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const readJson = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const writeJson = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n'); };
const appendJson = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.appendFileSync(file, JSON.stringify(value) + '\n'); };
const hashFile = file => sha(fs.readFileSync(file));
const safeParts = new Set(['cart', 'checkout', 'order', 'account', 'login', 'logout', 'register', 'signup', 'contact', 'submit', 'purchase']);

function safeUrl(value, hosts, observed = null) {
  let u;
  try { u = new URL(value); } catch { return false; }
  if (!['http:', 'https:'].includes(u.protocol) || !hosts.has(u.hostname.toLowerCase())) return false;
  if (u.username || u.password || u.pathname.split('/').some(p => safeParts.has(decodeURIComponent(p).toLowerCase()))) return false;
  if (observed && !observed.has(u.href)) return false;
  return true;
}

function projection(html, max = 24000) {
  const compact = html.replace(/<(script|style|svg|noscript)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, '').replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\s+/g, ' ').trim();
  return compact.slice(0, max);
}

function extractMeta(html) {
  const links = [...html.matchAll(/<a\b([^>]*?)>([\s\S]*?)<\/a\s*>/gi)].slice(0, 120);
  const buttons = [...html.matchAll(/<(?:button|input)\b([^>]*?)>/gi)].slice(0, 60);
  const attr = (s, key) => s.match(new RegExp(`\\b${key}\\s*=\\s*(["'])(.*?)\\1`, 'i'))?.[2] || '';
  return {links: links.map(([, a, t]) => ({href: attr(a, 'href'), text: t.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 100)})),
    buttons: buttons.map(([, a]) => ({text: attr(a, 'aria-label') || attr(a, 'value') || a.match(/\bname=["']?([^\s"']+)/i)?.[1] || '', type: attr(a, 'type') || 'button'}))};
}

function targetHosts(target) { return new Set([new URL(target.entry_url || target.url || target.start_url).hostname.toLowerCase(), ...(target.allowed_hosts || []).map(h => String(h).toLowerCase())]); }
function getTargets(runDir) { return fs.readFileSync(path.join(runDir, 'targets.jsonl'), 'utf8').split(/\r?\n/).filter(Boolean).map(JSON.parse); }
function siteDir(runDir, id) { return path.join(runDir, 'sites', String(id)); }
function selected(targets, ids) { return ids?.length ? targets.filter(t => ids.includes(String(t.id))) : targets; }

function costBefore(runDir, id) {
  const file = path.join(runDir, 'cost-ledger.jsonl');
  if (!fs.existsSync(file)) return {documents: 0, ui: 0};
  return fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map(JSON.parse).filter(x => String(x.id) === String(id))
    .reduce((a, x) => ({documents: a.documents + (x.document_reserved || 0), ui: a.ui + (x.ui_operations || 0)}), {documents: 0, ui: 0});
}
function saveCost(runDir, id, phase, session) {
  appendJson(path.join(runDir, 'cost-ledger.jsonl'), {id, phase, document_reserved: session.guard.counters.reserved, ui_operations: session.metadata.operations, http_requests: session.metadata.requests, xhr_fetch: session.metadata.xhr_fetch, response_bytes: session.metadata.bytes, browser_ms: session.metadata.browser_ms || null});
}

async function capture(target, url, runDir, tag, outputDir = null) {
  const hosts = targetHosts(target);
  if (!safeUrl(url, hosts)) throw new Error('url_out_of_scope_or_unsafe');
  const protocol = readJson(path.join(runDir, 'protocol.json'));
  const used = costBefore(runDir, target.id);
  const uiCap = target.practice ? protocol.practice_site_ui_cap : protocol.real_site_ui_cap;
  const docCap = target.practice ? protocol.practice_site_doc_cap : protocol.real_site_doc_cap;
  const deadlineMs = Date.now() + protocol.max_phase_seconds * 1000;
  const session = await makeBrowserPage({...target, start_url: target.start_url || url}, url,
    {executablePath: protocol.chrome_executable, robots_policy: protocol.robots_policy},
    {documents: Math.max(0, Math.min(docCap - used.documents, protocol.global_doc_cap)), operations: Math.max(0, uiCap - used.ui), deadlineMs,
      cumulativeDocumentCap: docCap, cumulativeDocumentRequests: used.documents, progressFile: path.join(runDir, 'progress.jsonl'), ledgerFile: path.join(runDir, 'document-ledger.jsonl'), denialFile: path.join(runDir, 'denials.jsonl')});
  const {page, guard, metadata} = session;
  try {
    const observation = await observePage(page, session.response, {tag, progressFile: path.join(runDir, 'progress.jsonl')});
    const status = session.response?.status() ?? null;
    const blocked = status === 401 || status === 403 || status === 429 || observation.dom.challenge || /login|sign in|access denied|challenge/i.test(`${observation.dom.title} ${observation.dom.headings.join(' ')}`);
    const bundle = {url: page.url(), status, raw_html: observation.source || null, rendered_dom: observation.html || '', dom: observation.dom, errors: observation.errors || [], blocked, requests: metadata, document_urls: [], counters: guard.counters};
    return bundle;
  } finally {
    metadata.browser_ms = protocol.max_phase_seconds * 1000 - Math.max(0, deadlineMs - Date.now());
    saveCost(runDir, target.id, tag, session);
    clearTimeout(session.hardTimer);
    await guard.cleanup().catch(() => {});
    await session.context.close().catch(() => {});
    await session.browser.close().catch(() => {});
  }
}

function saveCapture(dir, name, data) {
  const raw = data.raw_html || '';
  const dom = data.rendered_dom || '';
  fs.mkdirSync(dir, {recursive: true});
  fs.writeFileSync(path.join(dir, `${name}.html`), raw);
  fs.writeFileSync(path.join(dir, `${name}.dom.html`), dom);
  const meta = {...data, raw_html: undefined, rendered_dom: undefined, source_sha256: sha(raw), rendered_sha256: sha(dom), source_bytes: Buffer.byteLength(raw), rendered_bytes: Buffer.byteLength(dom)};
  writeJson(path.join(dir, `${name}.json`), meta);
  return meta;
}

async function discover(runDir, target) {
  const dir = siteDir(runDir, target.id);
  if (fs.existsSync(path.join(dir, 'representative.json'))) return {id: target.id, skipped: true};
  const url = target.entry_url || target.url || target.start_url;
  const captureData = await capture(target, url, runDir, 'discover');
  if (captureData.blocked) throw new Error(`main_document_blocked:${captureData.status}`);
  const meta = saveCapture(dir, 'representative', captureData);
  const html = captureData.rendered_dom;
  const bootstrap = {entry_url: url, task: target.task || target.tasks?.[0] || target.query || 'Identify the listing page and repeated item links.', representative_html_projection: projection(html), observed_links: captureData.dom?.links || [], links_and_buttons: extractMeta(html)};
  writeJson(path.join(dir, 'bootstrap-request.json'), bootstrap);
  return {id: target.id, status: meta.status, source_sha256: meta.source_sha256, links: bootstrap.observed_links.length};
}

async function prepare(runDir, target, warm = false) {
  const dir = siteDir(runDir, target.id);
  if (warm) {
    const held = readJson(path.join(dir, 'holdout-start.json')).url;
    const heldBefore = await capture(target, held, runDir, 'evaluation-heldout-before');
    const beforeMeta = saveCapture(path.join(dir, 'evaluation'), 'listing-before', heldBefore);
    let heldAfter = heldBefore;
    const config = readJson(path.join(runDir, 'protocol.json'));
    const browser = await chromium.launch({headless: true, executablePath: config.chrome_executable || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
    const context = await browser.newContext({...environmentContextOptions(), locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: {width: 1365, height: 900}, serviceWorkers: 'block'});
    const page = await context.newPage();
    const hosts = targetHosts(target);
    let response;
    const guard = await installDocumentGuard(page, {limit: 4, beforeRequest: info => !info.mainFrame || (info.method === 'GET' && safeUrl(info.url, hosts))});
    try {
      response = await page.goto(held, {waitUntil: 'domcontentloaded', timeout: 45000});
      for (let i = 0; i < Math.min(3, config.max_sampling_scroll_actions || 3); i++) {
        await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
        await page.waitForTimeout(700);
        const obs = await observePage(page, response, {tag: `evaluation-heldout-after-${i + 1}`});
        heldAfter = {url: page.url(), status: response?.status(), raw_html: obs.source || obs.html || '', rendered_dom: obs.html || '', dom: obs.dom, blocked: false};
      }
    } finally { await guard.cleanup().catch(() => {}); await context.close().catch(() => {}); await browser.close().catch(() => {}); }
    const afterMeta = saveCapture(path.join(dir, 'evaluation'), 'listing-after', heldAfter);
    appendJson(path.join(runDir, 'evaluation-cost.jsonl'), {id: target.id, phase: 'target_selection', document_reserved: (heldBefore.document_urls || []).length + (heldAfter.document_urls || []).length, ui_operations: Math.min(3, config.max_sampling_scroll_actions || 3), http_requests: (heldBefore.requests || []).length + (heldAfter.requests || []).length, source_sha256: beforeMeta.source_sha256, after_sha256: afterMeta.source_sha256});
    return {id: target.id, action: 'prepare_warm', status: heldBefore.status, heldout_listing_sha256: beforeMeta.source_sha256};
  }
  const answer = readJson(path.join(dir, 'sampling-answer.json'));
  const entryFile = path.join(dir, 'representative.json');
  const entry = readJson(entryFile);
  const entryUrl = entry.url;
  const entryMeta = entry;
  const observedEntry = new Set((readJson(path.join(dir, 'bootstrap-request.json')).observed_links || []).map(x => x.url));
  const listingUrl = answer.listing_url;
  if (listingUrl !== entryUrl && !observedEntry.has(listingUrl)) throw new Error('listing_url_not_observed_from_entry');
  const snapshots = [];
  let listing;
  if (listingUrl === entryUrl) {
    const raw = fs.readFileSync(path.join(dir, 'representative.html'), 'utf8');
    const domRaw = fs.readFileSync(path.join(dir, 'representative.dom.html'), 'utf8');
    listing = {...entryMeta, raw_html: raw, rendered_dom: domRaw};
  } else {
    listing = await capture(target, listingUrl, runDir, 'prepare-listing');
    const meta = saveCapture(dir, 'listing-before', listing); snapshots.push({name: 'listing-before', ...meta, html: undefined});
  }
  const pageTarget = target;
  let after = listing;
  let scrolls = 0;
  if (answer.max_scroll_actions != null && (!Number.isInteger(answer.max_scroll_actions) || answer.max_scroll_actions < 0 || answer.max_scroll_actions > 3)) throw new Error('max_scroll_actions_must_be_0_to_3');
  const maxScroll = answer.max_scroll_actions || 0;
  if (maxScroll) {
    const browser = await chromium.launch({headless: true, executablePath: (fs.existsSync(path.join(runDir, 'protocol.json')) && readJson(path.join(runDir, 'protocol.json')).chrome_executable) || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
    const context = await browser.newContext({...environmentContextOptions(), locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: {width: 1365, height: 900}, serviceWorkers: 'block'});
    const page = await context.newPage();
    const hosts = targetHosts(pageTarget);
    let response = null;
    const guard = await installDocumentGuard(page, {limit: 4, beforeRequest: info => !info.mainFrame || (info.method === 'GET' && safeUrl(info.url, hosts))});
    try {
      response = await page.goto(listingUrl, {waitUntil: 'domcontentloaded', timeout: 45000});
      for (let i = 0; i < maxScroll; i++) {
        await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
        await page.waitForTimeout(700);
        const obs = await observePage(page, response, {tag: `prepare-after-${i + 1}`});
        after = {url: page.url(), status: response?.status(), raw_html: obs.source || obs.html || '', rendered_dom: obs.html || '', dom: obs.dom, blocked: false};
        scrolls++;
      }
    } finally { await guard.cleanup().catch(() => {}); await context.close().catch(() => {}); await browser.close().catch(() => {}); }
    if (scrolls) { const meta = saveCapture(dir, 'listing-after', after); snapshots.push({name: 'listing-after', ...meta}); }
  }
  const baseHtml = listing.rendered_dom;
  const selector = answer.item_selector;
  const links = listing.dom?.links || [];
  let selectedHref = null;
  if (selector) {
    const config = readJson(path.join(runDir, 'protocol.json'));
    const browser = await chromium.launch({headless: true, executablePath: config.chrome_executable || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
    const context = await browser.newContext({...environmentContextOptions(), locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: {width: 1365, height: 900}, serviceWorkers: 'block'});
    const page = await context.newPage();
    const guard = await installDocumentGuard(page, {limit: 4, beforeRequest: info => !info.mainFrame || (info.method === 'GET' && safeUrl(info.url, targetHosts(target)))});
    try {
      await page.goto(listingUrl, {waitUntil: 'domcontentloaded', timeout: 45000});
      const loc = page.locator(selector).first();
      if (await loc.count()) selectedHref = await loc.evaluate(el => { const a = el.closest('a[href]') || (el.matches('a[href]') ? el : null); return a ? new URL(a.getAttribute('href'), location.href).href : null; });
    } finally { await guard.cleanup().catch(() => {}); await context.close().catch(() => {}); await browser.close().catch(() => {}); }
  }
  const first = links.find(l => l.url === selectedHref && l.url !== listingUrl && l.url !== entryUrl);
  let sample = null;
  if (first && targetHosts(target) && safeUrl(first.url, targetHosts(target))) {
    sample = await capture(target, first.url, runDir, 'sample-detail');
    const meta = saveCapture(dir, 'sample-detail', sample); snapshots.push({name: 'sample-detail', ...meta});
  }
  const reps = {entry_url: entryUrl, listing_url: listingUrl, listing_sha256: sha(baseHtml), before_scroll: listing.dom || {}, after_scroll: after.dom || {}, scroll_actions: scrolls, first_sample_url: sample?.url || null, initial_document_urls: [...new Set([...(entry.document_urls || []), ...(listing.document_urls || []), ...(sample?.document_urls || [])])], captures: snapshots};
  writeJson(path.join(dir, 'representatives.json'), reps);
  const codeReq = {contract: 'Export exactly: async function crawl(api, input) { ... }', start_url: listingUrl, task: target.task || target.tasks?.[0] || target.query || '', representative_html: projection(fs.readFileSync(path.join(dir, 'representative.dom.html'), 'utf8')), listing_html_before_scroll: projection(baseHtml), listing_html_after_scroll: projection(after.rendered_dom || baseHtml), sample_detail_html: sample ? projection(sample.rendered_dom || '') : null, observed_links: links, excluded_urls: reps.initial_document_urls, limits: {max_items: 3, max_scroll_actions: 3}, api: 'open(url) -> {url,status,blocked}; links(css) -> [{url,text}]; exists/count; scroll -> {url,state_id,links,blocked}; click(css) -> {url,status,state_id,blocked}; save(url) -> {saved,url,final_url,status,source_sha256,rendered_sha256}; wait(ms<=5000); state/info' };
  writeJson(path.join(dir, 'code-request.json'), codeReq);
  return {id: target.id, first_sample_url: reps.first_sample_url, doc_count: reps.initial_document_urls.length};
}

function loadCrawler(file) {
  const source = fs.readFileSync(file, 'utf8').trim();
  if (!/^async\s+function\s+crawl\s*\(/.test(source) || /\b(?:import|require|process|fetch|eval|Function|XMLHttpRequest|WebSocket|document|window)\b/.test(source)) throw new Error('crawl_source_outside_contract');
  const script = new vm.Script(`(${source})`, {filename: file});
  const context = vm.createContext(Object.freeze({}), {codeGeneration: {strings: false, wasm: false}});
  const fn = script.runInContext(context, {timeout: 1000});
  if (typeof fn !== 'function') throw new Error('crawl_export_not_function');
  return {fn, hash: sha(source), source};
}

async function execute(runDir, target, phase) {
  const dir = siteDir(runDir, target.id);
  const codePath = path.join(dir, 'crawl.js');
  const loaded = loadCrawler(codePath);
  const manifestPath = path.join(dir, 'code-manifest.json');
  if (fs.existsSync(manifestPath) && readJson(manifestPath).sha256 !== loaded.hash) throw new Error('frozen_code_changed');
  if (!fs.existsSync(manifestPath)) writeJson(manifestPath, {sha256: loaded.hash, frozen_at: new Date().toISOString()});
  const reps = readJson(path.join(dir, 'representatives.json'));
  const start = phase === 'warm' ? readJson(path.join(dir, 'holdout-start.json')).url : reps.listing_url;
  const excluded = new Set(phase === 'warm' ? reps.initial_document_urls : []);
  const cap = target.practice ? 40 : 15;
  const outDir = path.join(dir, phase);
  fs.mkdirSync(outDir, {recursive: true});
  const saved = [];
  const visitLog = [];
  const hosts = targetHosts(target);
  const config = fs.existsSync(path.join(runDir, 'protocol.json')) ? readJson(path.join(runDir, 'protocol.json')) : {};
  const browser = await chromium.launch({headless: true, executablePath: config.chrome_executable || 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--disable-background-networking', '--disable-component-update']});
  const context = await browser.newContext({...environmentContextOptions(), locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: {width: 1365, height: 900}, serviceWorkers: 'block'});
  await context.clearCookies();
  const page = await context.newPage();
  let currentResponse = null;
  let blocked = false;
  const seenLinks = new Set();
  const eventLog = [];
  page.on('response', r => { if (r.request().isNavigationRequest() && r.frame() === page.mainFrame()) { currentResponse = r; const s = r.status(); if (s === 401 || s === 403 || s === 429) blocked = true; } });
  page.on('request', r => eventLog.push({kind: 'request', url: r.url(), method: r.method(), resource: r.resourceType()}));
  page.on('requestfailed', r => eventLog.push({kind: 'requestfailed', url: r.url(), error: r.failure()?.errorText}));
  const guard = await installDocumentGuard(page, {limit: cap, beforeRequest: info => {
    const allowed = !info.mainFrame || (info.method === 'GET' && safeUrl(info.url, hosts));
    if (!allowed) eventLog.push({kind: 'denied', url: info.url, method: info.method});
    return allowed;
  }, onReserved: info => { if (info.resourceType === 'Document') visitLog.push({url: info.url, mainFrame: info.mainFrame, at: Date.now()}); }});
  const hard = setTimeout(() => void context.close().catch(() => {}), 600000);
  const observe = async tag => {
    const result = await observePage(page, currentResponse, {tag});
    for (const l of result.dom?.links || []) seenLinks.add(l.url);
    const status = currentResponse?.status() ?? null;
    const isBlocked = status === 401 || status === 403 || status === 429 || result.dom?.challenge || /access denied|verify you are human|captcha|log in|sign in/i.test(`${result.dom?.title} ${(result.dom?.headings || []).join(' ')}`);
    if (isBlocked) blocked = true;
    return {result, status, isBlocked};
  };
  const api = {
    async open(url) { if (!safeUrl(url, hosts)) throw new Error('open_url_denied'); const response = await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 45000}); currentResponse = response; const o = await observe('crawler-open'); return {url: page.url(), status: response?.status() ?? null, blocked: o.isBlocked}; },
    async links(css = 'a[href]') { const rows = await page.locator(css).evaluateAll(nodes => nodes.map(n => ({url: new URL(n.getAttribute('href'), location.href).href, text: (n.innerText || n.getAttribute('aria-label') || '').trim().slice(0, 160)}))); rows.forEach(r => seenLinks.add(r.url)); return rows.filter(r => safeUrl(r.url, hosts)); },
    async exists(css) { return page.locator(css).count().then(n => n > 0); },
    async count(css) { return page.locator(css).count(); },
    async scroll() { await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight)); await page.waitForTimeout(500); const o = await observe('crawler-scroll'); return {url: page.url(), state_id: o.result.dom?.state_id, links: o.result.dom?.links || [], blocked: o.isBlocked}; },
    async click(css) { const locator = page.locator(css).first(); const info = await locator.evaluate(el => ({tag: el.tagName, type: el.getAttribute('type'), text: (el.innerText || el.value || '').trim(), href: el.getAttribute('href')})); if (info.tag === 'FORM' || /submit|cart|checkout|login|sign in|purchase|order/i.test(`${info.type} ${info.text} ${info.href}`)) throw new Error('unsafe_click'); await locator.click({timeout: 5000}); const o = await observe('crawler-click'); return {url: page.url(), status: currentResponse?.status() ?? null, state_id: o.result.dom?.state_id, blocked: o.isBlocked}; },
    async save(url) { if (url === input.start_url) return {saved: false, url, reason: 'current_start_url'}; if (!safeUrl(url, hosts, seenLinks)) return {saved: false, url, reason: 'not_observed_or_unsafe'}; if (excluded.has(url)) return {saved: false, url, reason: 'excluded_initial_document'}; if (saved.length >= 3) return {saved: false, url, reason: 'item_limit'}; const response = await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 45000}); currentResponse = response; const o = await observe('crawler-save'); const index = saved.length + 1; const base = path.join(outDir, `item-${index}`); const meta = saveCapture(outDir, `item-${index}`, {url: page.url(), status: response?.status() ?? null, raw_html: o.result.source || null, rendered_dom: o.result.html || '', dom: o.result.dom, blocked: o.isBlocked, request_urls: visitLog.map(x => x.url), errors: o.result.errors || []}); const item = {url, final_url: page.url(), status: response?.status() ?? null, saved: true, source_html: path.basename(`${base}.html`), rendered_dom: path.basename(`${base}.dom.html`), source_sha256: meta.source_sha256, rendered_sha256: meta.rendered_sha256, basic_html_check: !o.isBlocked && response?.status() === 200 && Boolean(o.result.source), provenance: {phase, action: 'api.save', page_url: page.url()}}; saved.push(item); return {saved: true, url, final_url: page.url(), status: item.status, source_sha256: item.source_sha256, rendered_sha256: item.rendered_sha256}; },
    async wait(ms) { if (!Number.isFinite(ms) || ms < 0 || ms > 5000) throw new Error('wait_out_of_bounds'); await page.waitForTimeout(ms); return this.state(); },
    async state() { const o = await observe('crawler-state'); return {url: page.url(), state_id: o.result.dom?.state_id, status: currentResponse?.status() ?? null, blocked: o.isBlocked}; },
    async info() { const o = await observe('crawler-info'); return {url: page.url(), title: o.result.dom?.title || '', headings: o.result.dom?.headings || [], links: o.result.dom?.links || []}; },
  };
  const timer = setTimeout(() => void context.close().catch(() => {}), 600000);
  let crawlError = null;
  try {
    const response = await page.goto(start, {waitUntil: 'domcontentloaded', timeout: 45000}); currentResponse = response;
    const initial = await observe('crawler-start');
    const input = {start_url: start, phase, max_items: 3, max_scroll_actions: 3, exclude_urls: [...excluded]};
    await Promise.race([loaded.fn(api, Object.freeze(input)), new Promise((_, reject) => setTimeout(() => reject(new Error('phase_timeout_600s')), 600000))]).catch(e => { crawlError = String(e?.message || e); });
  } finally {
    clearTimeout(timer); clearTimeout(hard);
    await guard.cleanup().catch(() => {}); await context.close().catch(() => {}); await browser.close().catch(() => {});
  }
  const result = {id: target.id, phase, start_url: start, crawl_sha256: loaded.hash, zero_llm_calls: true, llm_calls: 0, local_model_calls: 0,
    status: blocked ? 'blocked' : crawlError ? 'failed' : 'complete', error: crawlError, blocked, selected_items: saved, fresh_item_urls_after_scroll: saved.map(x => x.url),
    document_requests: visitLog, document_count: visitLog.length, document_cap: cap, xhr_fetch_events: eventLog.filter(e => ['xhr', 'fetch'].includes(e.resource)).length,
    network_event_count: eventLog.length, request_events: eventLog, cold_representative_urls: [...excluded], started_at: new Date().toISOString()};
  writeJson(path.join(outDir, 'result.json'), result);
  appendJson(path.join(runDir, 'results.jsonl'), result);
  return {id: target.id, phase, status: result.status, items: saved.length, document_count: result.document_count};
}

export async function runAction({runDir, action, ids = [], phase = 'cold', live = false}) {
  runDir = path.resolve(runDir);
  const targets = selected(getTargets(runDir), ids);
  if (!targets.length) throw new Error('no_targets_selected');
  if (action === 'discover') { const out = []; for (const t of targets) out.push(await discover(runDir, t)); return {action, outcomes: out}; }
  if (action === 'prepare') { const out = []; for (const t of targets) out.push(await prepare(runDir, t, phase === 'warm')); return {action, outcomes: out}; }
  if (action === 'execute') { if (!live) throw new Error('execute_requires_live_flag'); const out = []; for (const t of targets) out.push(await execute(runDir, t, phase)); return {action, outcomes: out}; }
  if (action === 'verify') return verify(runDir, targets);
  if (action === 'export') return exportRun(runDir, targets);
  throw new Error('unknown_action');
}

export function verify(runDir, targets = getTargets(runDir)) {
  const errors = [];
  const evaluation = path.join(runDir, 'evaluation-cost.jsonl');
  const evalRows = fs.existsSync(evaluation) ? fs.readFileSync(evaluation, 'utf8').split(/\r?\n/).filter(Boolean).map(JSON.parse) : [];
  const seen = new Set();
  let docs = 0;
  for (const row of targets) {
    const key = `${row.id}`;
    if (seen.has(key)) errors.push(`duplicate_target:${key}`); seen.add(key);
    const dir = siteDir(runDir, row.id);
    for (const phase of ['cold', 'warm']) {
      const resultPath = path.join(dir, phase, 'result.json');
      if (!fs.existsSync(resultPath)) continue;
      const r = readJson(resultPath); docs += r.document_count || 0;
      const cap = row.practice ? 40 : 15;
      if (r.document_count > cap) errors.push(`document_cap:${key}:${phase}`);
      if (r.llm_calls !== 0 || r.local_model_calls !== 0) errors.push(`model_call:${key}:${phase}`);
      const hash = hashFile(path.join(dir, 'crawl.js'));
      if (hash !== r.crawl_sha256) errors.push(`code_hash:${key}:${phase}`);
    }
    if (fs.existsSync(path.join(dir, 'representatives.json'))) {
      const reps = readJson(path.join(dir, 'representatives.json'));
      const pre = (reps.initial_document_urls || []).length;
      if (pre + (row.practice ? 40 : 15) > (row.practice ? 40 : 15) && pre > (row.practice ? 40 : 15)) errors.push(`site_doc_cap:${key}`);
    }
  }
  const evalDocs = evalRows.reduce((n, x) => n + (x.documents || x.document_count || 0), 0);
  for (const t of targets) {
    const perSite = evalRows.filter(r => String(r.id) === String(t.id)).reduce((n, x) => n + (x.documents || x.document_count || 0), 0);
    const used = docs + perSite;
    if (used > (t.practice ? 40 : 15)) errors.push(`cumulative_site_cap:${t.id}`);
  }
  return {ok: errors.length === 0, errors, target_count: targets.length, execution_documents: docs, evaluation_documents: evalDocs, llm_calls: 0};
}

export function exportRun(runDir, targets = getTargets(runDir)) {
  const check = verify(runDir, targets);
  if (!check.ok) throw new Error(`verify_failed:${check.errors.join(',')}`);
  const output = path.join(runDir, 'representative-code.zip');
  if (fs.existsSync(output)) throw new Error('export_exists');
  const {spawnSync} = require('node:child_process');
  const result = spawnSync('python3', [path.join(here, 'package.py'), '--run', runDir, '--output', output, '--expected-targets', String(targets.length)], {encoding: 'utf8'});
  if (result.status !== 0) throw new Error(result.stderr || 'package_failed');
  return JSON.parse(result.stdout.trim());
}

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const v = argv[i];
    if (v === '--live') out.live = true;
    else if (v.startsWith('--')) out[v.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = argv[++i];
  }
  if (out.ids) out.ids = String(out.ids).split(',').filter(Boolean);
  return out;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runAction(args(process.argv.slice(2))).then(value => process.stdout.write(JSON.stringify(value) + '\n')).catch(error => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1; });
}
