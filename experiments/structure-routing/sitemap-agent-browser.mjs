import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {installDocumentGuard} from './document-guard.mjs';
import {environmentContextOptions} from './environment.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(path.join(repo, '.deps/bot-diagnostics/package.json'));
const {chromium} = require('playwright');
const robotsParser = require('robots-parser');
const safeSegments = new Set(['cart', 'checkout', 'order', 'account', 'login', 'logout', 'register', 'signup', 'contact', 'submit', 'purchase']);
const writeJson = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n'); };
const sha = value => require('node:crypto').createHash('sha256').update(value).digest('hex');

function withTimeout(promise, timeoutMs, label) {
  let timer;
  return Promise.race([Promise.resolve(promise), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`timeout:${label}`)), Math.max(1, timeoutMs)); })])
    .finally(() => clearTimeout(timer));
}

function progress(file, event, fields = {}) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.appendFileSync(file, JSON.stringify({at: new Date().toISOString(), event, ...fields}) + '\n');
}

function resultBudget(session, budget, priorDocuments = 0) {
  return {cumulative_document_cap: budget.siteDocumentCap, prior_document_requests: priorDocuments,
    cumulative_document_requests: priorDocuments + (session?.guard.counters.reserved || 0), document_cap: budget.documents,
    document_reserved: session?.guard.counters.reserved || 0, operation_cap: budget.operations, operations: session?.metadata.operations || 0};
}

export function routeUrlShape(value) {
  const url = new URL(value);
  const parts = url.pathname.split('/').map((part, index, all) => {
    if (!part) return '';
    if (/^\d+$/.test(part)) return ':number';
    if (/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(part)) return ':uuid';
    if (/^[0-9a-f]{16,}$/i.test(part)) return ':hex';
    if (/\.[a-z0-9]{1,8}$/i.test(part)) return `:slug.${part.split('.').pop().toLowerCase()}`;
    return index === all.length - 1 ? ':slug' : part.toLowerCase();
  }).join('/');
  return `${url.hostname.toLowerCase()}${parts}?${[...new Set([...url.searchParams.keys()])].sort().join(',')}#${url.hash ? 'hash' : ''}`;
}

export function repeatedAnchorGroups(links) {
  const groups = new Map();
  for (const link of links || []) {
    const parent = link.parent || {};
    const key = `${link.region || 'body'}|${parent.tag || 'div'}|${(parent.classes || []).slice().sort().join('.')}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(link);
  }
  return [...groups.entries()].filter(([, rows]) => rows.length > 1).map(([signature, rows]) => ({signature, links: rows}));
}

export function matchesGroup(value, group) {
  try {
    const url = new URL(value);
    const pattern = group.pattern || group;
    if (pattern.hostname && url.hostname.toLowerCase() !== String(pattern.hostname).toLowerCase()) return false;
    const actual = url.pathname.split('/').filter(Boolean);
    const expected = (pattern.segments || []).filter(Boolean);
    if (actual.length !== expected.length) return false;
    for (let i = 0; i < expected.length; i++) {
      const segment = expected[i];
      if (segment === ':number' && /^\d+$/.test(actual[i])) continue;
      if (segment === ':segment' && actual[i].length) continue;
      if (segment === ':slug.html' && /^[^/]+\.html$/i.test(actual[i])) continue;
      if (segment === ':slug' && actual[i].length) continue;
      if (segment !== actual[i]) return false;
    }
    if (Boolean(url.pathname.endsWith('/')) !== Boolean(pattern.trailing_slash)) return false;
    const required = new Set(pattern.query_keys || []);
    return [...required].every(key => url.searchParams.has(key));
  } catch { return false; }
}

function hostAllowed(value, hosts) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && hosts.has(url.hostname.toLowerCase()); } catch { return false; }
}

function safeRoute(value) {
  try { return new URL(value).pathname.split('/').filter(Boolean).every(part => !safeSegments.has(decodeURIComponent(part).toLowerCase())); } catch { return false; }
}

function routePattern(plan) {
  const group = plan.item_group || plan.selected_group || plan.group || plan.item_groups?.find(row => row.id === plan.selected_item_group_id) || {};
  return {id: group.id || null, pattern: group.pattern || null, url_shape: group.url_shape || plan.url_shape || null,
    url_pattern: group.url_pattern || plan.url_pattern || null, anchor_text: group.anchor_text || null};
}

export function selectObservedItems(links, plan, limit = 3) {
  const pattern = routePattern(plan);
  const selectedGroup = plan.item_group || plan.selected_group || plan.group || plan.item_groups?.find(row => row.id === plan.selected_item_group_id);
  const groups = repeatedAnchorGroups(links);
  const matching = groups.flatMap(group => group.links).filter(link => {
    if (selectedGroup && !matchesGroup(link.url, selectedGroup)) return false;
    if (pattern.url_shape && routeUrlShape(link.url) !== pattern.url_shape) return false;
    if (pattern.url_pattern && !new RegExp(pattern.url_pattern, 'i').test(new URL(link.url).pathname)) return false;
    if (pattern.anchor_text && !(link.text || '').toLowerCase().includes(String(pattern.anchor_text).toLowerCase())) return false;
    return true;
  });
  const seen = new Set();
  return matching.filter(link => !seen.has(link.url) && seen.add(link.url)).slice(0, limit);
}

export function selectRouteItems(links, group = {}) {
  const rows = repeatedAnchorGroups(links).filter(row => row.links[0]?.region !== 'nav').flatMap(row => row.links);
  if (group.pattern || group.segments) return {links: rows.filter(link => matchesGroup(link.url, group)), fallback: null, url_shape: group.url_shape || null};
  if (group.url_shape) return {links: rows.filter(link => routeUrlShape(link.url) === group.url_shape), fallback: group.fallback || null, url_shape: group.url_shape};
  const shapes = new Map();
  for (const link of rows) {
    const shape = routeUrlShape(link.url);
    if (!shapes.has(shape)) shapes.set(shape, []);
    shapes.get(shape).push(link);
  }
  const selected = [...shapes.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0])).find(([, matches]) => matches.length >= 2);
  return selected ? {links: selected[1], fallback: 'DOM repeated-link group', url_shape: selected[0]} : {links: [], fallback: 'DOM repeated-link group', url_shape: null};
}

export function prioritizeFreshLinks(links, previousUrls, limit = 3) {
  const previous = previousUrls instanceof Set ? previousUrls : new Set(previousUrls || []);
  const seen = new Set();
  const unique = (links || []).filter(link => !seen.has(link.url) && seen.add(link.url));
  return [...unique.filter(link => !previous.has(link.url)), ...unique.filter(link => previous.has(link.url))].slice(0, limit);
}

async function observe(page, response, {progressFile = null, tag = 'observe'} = {}) {
  const errors = [];
  if (progressFile) progress(progressFile, 'observe_start', {tag});
  let dom;
  try { dom = await withTimeout(page.evaluate(() => {
    const text = (document.body?.innerText || '').replace(/\s+/g, ' ').trim();
    const links = [...document.querySelectorAll('a[href]')].map(anchor => {
      const parent = anchor.parentElement;
      let region = 'body';
      for (let node = parent; node && node !== document.documentElement; node = node.parentElement) {
        const marker = `${node.tagName} ${node.getAttribute('role') || ''} ${node.id || ''} ${node.className || ''}`.toLowerCase();
        if (/\b(nav|header|footer|aside)\b|\b(navigation|breadcrumb|pagination|sidebar)\b/.test(marker)) { region = 'nav'; break; }
        if (/^(MAIN|ARTICLE|SECTION)$/.test(node.tagName)) region = node.tagName.toLowerCase();
      }
      return {url: new URL(anchor.getAttribute('href'), location.href).href, text: (anchor.innerText || anchor.getAttribute('aria-label') || '').trim().slice(0, 160),
        parent: {tag: parent?.tagName?.toLowerCase() || 'div', classes: [...(parent?.classList || [])]}, region};
    }).filter(link => /^https?:/.test(link.url));
    const headings = [...document.querySelectorAll('h1,h2')].map(node => (node.innerText || '').trim()).filter(Boolean).slice(0, 12);
    const main = document.querySelector('main,article,[role="main"]');
    const content = (main?.innerText || text).replace(/\s+/g, ' ').trim();
    return {url: location.href, title: document.title, headings, text: text.slice(0, 5000), body_length: text.length, content_length: content.length,
      links, html_length: document.documentElement?.outerHTML?.length || 0,
      state_basis: `${location.href}|${document.title}|${headings[0] || ''}|${text.slice(0, 120)}|${links.map(link => link.url).join('|')}`,
      challenge: Boolean(document.querySelector('[id*="captcha" i], [class*="captcha" i], iframe[src*="challenge" i], input[type="password"]'))};
  }), 15000, `${tag}:evaluate`); }
  catch (error) {
    errors.push(String(error?.message || error));
    dom = {url: page.url(), title: '', headings: [], text: '', body_length: 0, content_length: 0, links: [], html_length: 0,
      state_id: sha(`${page.url()}|observation_error`), challenge: false};
  }
  if (dom.state_basis) {
    dom.state_id = sha(dom.state_basis);
    delete dom.state_basis;
  }
  let html = '';
  try { html = await withTimeout(page.content(), 15000, `${tag}:content`); }
  catch (error) { errors.push(String(error?.message || error)); }
  let source = null;
  if (response) {
    if (progressFile) progress(progressFile, 'raw_body_start', {tag});
    try { source = await withTimeout(response.body(), 5000, `${tag}:response_body`); }
    catch (error) { errors.push(String(error?.message || error)); }
    if (progressFile) progress(progressFile, 'raw_body_end', {tag, bytes: source?.length || 0, error: errors.at(-1) || null});
  }
  if (progressFile) progress(progressFile, 'observe_end', {tag, state_id: dom.state_id, links: dom.links.length, html_bytes: Buffer.byteLength(html), errors});
  return {dom, html, source, errors};
}

export async function observePage(page, response, options = {}) {
  return observe(page, response, options);
}

function basicValidity(status, data, listing, hosts) {
  const d = data.dom;
  if (status !== 200) return {basic_html_ok: false, reason: `http_${status ?? 'error'}`};
  if (!hostAllowed(d.url, hosts) || !safeRoute(d.url)) return {basic_html_ok: false, reason: 'main_scope'};
  if (d.challenge || /verify you are human|access denied|robot check|captcha|request blocked|security check|log in|sign in/i.test(`${d.title} ${d.headings.join(' ')}`)) return {basic_html_ok: false, reason: 'blocked_or_login'};
  if (/maintenance|temporarily unavailable|not found|page not found|404|ページが見つかりません/i.test(`${d.title} ${d.headings.join(' ')}`)) return {basic_html_ok: false, reason: 'error_page'};
  if (d.body_length < 80 || d.html_length < 100 || d.content_length < 40) return {basic_html_ok: false, reason: 'empty_or_shell'};
  if (d.state_id === listing.state_id || d.url === listing.url) return {basic_html_ok: false, reason: 'listing_repeated'};
  if (!d.title && !d.headings.length) return {basic_html_ok: false, reason: 'no_detail_heading'};
  return {basic_html_ok: true, reason: 'detail_html'};
}

function safeLoggedUrl(value) {
  try { const url = new URL(value); url.username = ''; url.password = ''; url.search = ''; url.hash = ''; return url.href; } catch { return 'invalid_url'; }
}

async function makeBrowserPage(target, seed, options, budget) {
  let browser = null;
  let context = null;
  let hardTimer = null;
  try {
  browser = await withTimeout(chromium.launch({headless: true, executablePath: options.executablePath || 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--disable-background-networking', '--disable-component-update']}), 30000, 'browser_launch');
  context = await withTimeout(browser.newContext({locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: {width: 1365, height: 900}, serviceWorkers: 'block', ...environmentContextOptions()}), 30000, 'browser_context');
  hardTimer = setTimeout(() => { void context.close().catch(() => {}); }, Math.max(1, budget.deadlineMs - Date.now()));
  await withTimeout(context.clearCookies(), 10000, 'clear_cookies');
  const page = await withTimeout(context.newPage(), 15000, 'new_page');
  const hosts = new Set([new URL(target.start_url || target.url || target.entry_url).hostname.toLowerCase(), ...(target.allowed_hosts || []).map(host => String(host).toLowerCase())]);
  const robots = options.robots || new Map();
  const denialEvents = options.denialEvents || [];
  const robotsPolicy = options.robots_policy === 'sitemap_discovery_only' ? 'sitemap_discovery_only' : 'enforced';
  const metadata = {requests: 0, responses: 0, xhr_fetch: 0, bytes: 0, operations: 0, robot_requests: 0, documents: 0, document_requests: 0, document_responses: 0};
  let lastAt = 0;
  const delay = target.practice ? 1000 : 5000;
  const robotAllowed = async url => {
    if (robotsPolicy === 'sitemap_discovery_only') return true;
    const origin = new URL(url).origin;
    if (!robots.has(origin)) {
      const robotsUrl = new URL('/robots.txt', origin).href;
      let response = null;
      let current = robotsUrl;
      try {
        for (let redirects = 0; redirects <= 5; redirects++) {
          if (!hostAllowed(current, hosts)) throw new Error('robots_redirect_out_of_scope');
          if (metadata.robot_requests >= 4) throw new Error('robots_request_limit');
          metadata.robot_requests++;
          response = await withTimeout((options.fetch || fetch)(current, {redirect: 'manual', signal: AbortSignal.timeout(20000), headers: {'user-agent': 'StructureRoutingResearch/1.0'}}), 20000, 'robots_fetch');
          if (![301, 302, 303, 307, 308].includes(response.status)) break;
          const location = response.headers.get('location');
          if (!location) break;
          current = new URL(location, current).href;
        }
      }
      catch (error) { robots.set(origin, {allowed: false, reason: `robots_error:${error.message}`}); }
      if (response) {
        const body = await withTimeout(response.text(), 5000, 'robots_body');
        if ([401, 403, 429].includes(response.status) || response.status >= 500) robots.set(origin, {allowed: false, reason: `robots_http_${response.status}`});
        else robots.set(origin, {allowed: true, parser: robotsParser(robotsUrl, response.ok ? body : '')});
      }
    }
    const entry = robots.get(origin);
    return entry?.allowed === true && entry.parser?.isAllowed(url, '*') !== false;
  };
  const guard = await withTimeout(installDocumentGuard(page, {limit: budget.documents, beforeRequest: async info => {
    if (info.resourceType !== 'Document') return true;
    let reason = null;
    if (info.method !== 'GET') reason = 'method';
    else if (!hostAllowed(info.url, hosts)) reason = 'host_scope';
    else if (!safeRoute(info.url)) reason = 'unsafe_route';
    if (reason) {
      const event = {url: safeLoggedUrl(info.url), main_frame: info.mainFrame, reason};
      denialEvents.push(event);
      if (budget.denialFile) progress(budget.denialFile, 'document_denied', event);
      return false;
    }
    const allowed = await robotAllowed(info.url);
    if (!allowed) {
      const event = {url: safeLoggedUrl(info.url), main_frame: info.mainFrame, reason: 'robots'};
      denialEvents.push(event);
      if (budget.denialFile) progress(budget.denialFile, 'document_denied', event);
      return false;
    }
    if (info.mainFrame) {
      if (lastAt) await new Promise(resolve => setTimeout(resolve, Math.max(0, delay - (Date.now() - lastAt))));
      lastAt = Date.now();
    }
    return true;
  }, onReserved: info => {
    metadata.documents++;
    if (budget.ledgerFile) progress(budget.ledgerFile, 'document_reserved', {url: info.url, method: info.method, main_frame: info.mainFrame, ordinal: metadata.documents});
  }}), 20000, 'document_guard_install');
  page.on('request', request => {
    metadata.requests++;
    if (request.resourceType() === 'document') metadata.document_requests++;
    if (['xhr', 'fetch'].includes(request.resourceType())) metadata.xhr_fetch++;
  });
  page.on('response', async res => {
    metadata.responses++;
    if (res.request().resourceType() === 'document') metadata.document_responses++;
    const responseTag = `${res.status()}-${sha(res.url()).slice(0, 8)}`;
    if (budget.progressFile) progress(budget.progressFile, 'network_body_start', {tag: responseTag, resource_type: res.request().resourceType()});
    const body = await withTimeout(res.body(), 5000, `network_body:${responseTag}`).catch(() => Buffer.alloc(0));
    metadata.bytes += body.length;
    if (budget.progressFile) progress(budget.progressFile, 'network_body_end', {tag: responseTag, bytes: body.length});
  });
  if (budget.progressFile) progress(budget.progressFile, 'goto_start', {url: seed, kind: 'initial'});
  const response = await withTimeout(page.goto(seed, {waitUntil: 'domcontentloaded', timeout: Math.max(1, Math.min(45000, budget.deadlineMs - Date.now()))}), Math.max(1, Math.min(46000, budget.deadlineMs - Date.now())), 'initial_goto').catch(() => null);
  if (budget.progressFile) progress(budget.progressFile, 'goto_end', {url: seed, status: response?.status() ?? null, kind: 'initial'});
  await withTimeout(page.waitForLoadState('networkidle', {timeout: target.practice ? 1000 : 5000}), target.practice ? 1500 : 6000, 'initial_networkidle').catch(() => {});
  await withTimeout(page.waitForTimeout(500), 1000, 'initial_pause').catch(() => {});
  metadata.operations++;
  return {browser, context, page, guard, response, hosts, metadata, robots, robotsPolicy, denialEvents, hardTimer};
  } catch (error) {
    clearTimeout(hardTimer);
    await withTimeout(context?.close().catch(() => {}), 10000, 'failed_context_close').catch(() => {});
    await withTimeout(browser?.close().catch(() => {}), 10000, 'failed_browser_close').catch(() => {});
    throw error;
  }
}

function artifactDir(runDir, target) { return path.join(runDir, 'artifacts', String(target.id), 'sitemap-agent-browser'); }

function phaseDir(runDir, target, phase) { return path.join(artifactDir(runDir, target), phase); }

function declaredCandidates(plan) {
  return [...(plan.listing_candidates || []), ...(plan.hub_candidates || [])].filter(row => row?.url);
}

function orderedCandidates(plan) {
  const original = declaredCandidates(plan);
  const selectedId = plan.selected_listing_candidate_id || plan.selected_hub_candidate_id;
  if (selectedId && original.some(row => row.id === selectedId)) return original;
  return original.map((row, index) => ({row, index, scrollRoute: /(?:^|[-_/])(?:scroll|infinite)(?:$|[-_/])/i.test(new URL(row.url).pathname)}))
    .sort((a, b) => Number(b.scrollRoute) - Number(a.scrollRoute) || a.index - b.index).map(entry => entry.row);
}

function routeGroup(plan) {
  return plan.item_group || plan.selected_group || plan.group || plan.item_groups?.find(row => row.id === plan.selected_item_group_id) || plan.item_groups?.[0] || {};
}

export function chooseRoute(plan, {phase = 'cold', recipe = null} = {}) {
  const candidates = orderedCandidates(plan);
  if (phase === 'warm') {
    const visited = new Set(recipe?.visited_candidate_ids || []);
    const observed = new Set(recipe?.observed_candidate_urls || []);
    const next = candidates.find(row => !visited.has(row.id) && row.url !== recipe?.seed_url && [...observed].some(url => matchesCandidatePrefix(url, row)));
    return {candidate: next || candidates.find(row => row.url === recipe?.seed_url) || {id: recipe?.selected_candidate_id, url: recipe?.seed_url, source: recipe?.selected_candidate_source}, group: recipe?.selected_group || routeGroup(plan)};
  }
  const selectedId = plan.selected_listing_candidate_id || plan.selected_hub_candidate_id;
  return {candidate: candidates.find(row => row.id === selectedId) || candidates[0] || (plan.seed_url ? {id: null, url: plan.seed_url, source: null} : null), group: routeGroup(plan)};
}

function matchesCandidatePrefix(url, candidate) {
  if (candidate.url === url) return true;
  const prefix = candidate.prefix || candidate.url_prefix || (candidate.source === 'derived_prefix' ? new URL(candidate.url).pathname : null);
  if (!prefix) return false;
  try {
    const parsed = new URL(url);
    const base = new URL(candidate.url);
    const normalized = String(prefix).endsWith('/') ? String(prefix) : `${prefix}/`;
    return parsed.hostname.toLowerCase() === base.hostname.toLowerCase() && (parsed.pathname === String(prefix).replace(/\/$/, '') || parsed.pathname.startsWith(normalized));
  } catch { return false; }
}

function scrollAction(page) {
  return page.evaluate(() => {
    const candidates = [...document.querySelectorAll('body *')].filter(node => {
      const style = getComputedStyle(node);
      return /(auto|scroll)/.test(`${style.overflowY}`) && node.scrollHeight > node.clientHeight + 100 && node.clientHeight > 100;
    }).sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
    const container = candidates[0];
    if (container) {
      container.scrollBy({top: Math.max(container.clientHeight * 0.8, 400), behavior: 'instant'});
      return {scroll_y: container.scrollTop, scroll_height: container.scrollHeight, container: container.tagName.toLowerCase()};
    }
    window.scrollBy({top: Math.max(window.innerHeight * 0.8, 500), behavior: 'instant'});
    return {scroll_y: window.scrollY, scroll_height: document.scrollingElement?.scrollHeight || 0, container: 'document'};
  });
}

export async function executeRoute({runDir, target, plan, phase, options = {}} = {}) {
  if (!runDir || !target || !plan) throw new Error('runDir_target_plan_required');
  if (!['cold', 'warm'].includes(phase)) throw new Error('phase_must_be_cold_or_warm');
  const recipeFile = path.join(artifactDir(runDir, target), 'recipe.json');
  let recipe;
  if (phase === 'warm') {
    if (!fs.existsSync(recipeFile)) throw new Error('missing_cold_recipe');
    recipe = JSON.parse(fs.readFileSync(recipeFile, 'utf8'));
  }
  const candidates = orderedCandidates(plan);
  const originalCandidates = declaredCandidates(plan);
  if (!candidates.length && phase === 'cold' && !plan.seed_url) throw new Error('plan_missing_listing_candidates');
  const route = chooseRoute(plan, {phase, recipe});
  const robotsPolicy = options.robots_policy === 'sitemap_discovery_only' || (options.robots_policy == null && phase === 'warm' && recipe?.robots_policy === 'sitemap_discovery_only') ? 'sitemap_discovery_only' : 'enforced';
  const executionPlan = {...plan, selected_group: route.group};
  const initialCandidate = route.candidate;
  const seed = initialCandidate?.url || plan.seed_url;
  const selectedCandidateId = initialCandidate?.id;
  if (!seed) throw new Error('plan_missing_listing_seed');
  if (!hostAllowed(seed, new Set([new URL(target.start_url || target.url || target.entry_url).hostname.toLowerCase(), ...(target.allowed_hosts || []).map(x => String(x).toLowerCase())]))) throw new Error('seed_out_of_scope');
  const siteDocumentCap = target.practice ? 40 : 15;
  const priorDocuments = phase === 'cold' ? Number(options.prior_document_requests || 0) : 0;
  if (!Number.isSafeInteger(priorDocuments) || priorDocuments < 0 || priorDocuments > siteDocumentCap) throw new Error(`prior_document_requests_must_be_0_to_${siteDocumentCap}`);
  const cumulativeBase = phase === 'cold' ? priorDocuments : Number(recipe?.cumulative_document_requests || 0);
  const warmRemaining = Math.max(0, siteDocumentCap - cumulativeBase);
  const budget = {documents: phase === 'cold' ? Math.min(siteDocumentCap - priorDocuments, Math.min(6, Number(plan.max_explore_documents || 6)) + 3) : Math.min(warmRemaining, 12),
    operations: target.practice ? 100 : 30, siteDocumentCap, priorDocuments};
  const explorationLimit = phase === 'warm' ? Math.max(1, Math.min(6, budget.documents - 3)) : Math.min(6, Number(plan.max_explore_documents || 6));
  const browserStarted = Date.now();
  const phaseDeadline = browserStarted + 600000;
  const base = phaseDir(runDir, target, phase);
  fs.mkdirSync(base, {recursive: true});
  const progressFile = path.join(base, 'progress.jsonl');
  const ledgerFile = path.join(base, 'document-reservations.jsonl');
  const beforeFile = path.join(base, 'listing-before.json');
  const afterFile = path.join(base, 'listing-after.json');
  const beforeHtml = path.join(base, 'listing-before.dom.html');
  const afterHtml = path.join(base, 'listing-after.dom.html');
  const listingSourceFile = path.join(base, 'listing-before.source.html');
  const denialFile = path.join(base, 'document-denials.jsonl');
  budget.deadlineMs = phaseDeadline;
  budget.progressFile = progressFile;
  budget.ledgerFile = ledgerFile;
  budget.denialFile = denialFile;
  progress(progressFile, 'phase_start', {phase, seed, document_cap: budget.documents, prior_document_requests: priorDocuments});
  const group = routeGroup(executionPlan);
  const graph = [];
  const visited = [];
  const queue = [initialCandidate].filter(Boolean);
  const queued = new Set(queue.map(row => row.url));
  const scrolls = [];
  const itemResults = [];
  let listing = null;
  let listingCandidate = null;
  let explorationPages = 0;
  let refusal = null;
  let explorationStop = null;
  const denialEvents = [];
  let session = null;
  let result = null;
  try {
  session = await makeBrowserPage(target, seed, {...options, robots_policy: robotsPolicy, denialEvents}, budget);
  if (denialEvents.some(event => event.main_frame)) refusal = 'main_scope';
  for (let i = 0; !refusal && i < queue.length && explorationPages < explorationLimit; i++) {
    const currentCandidate = queue[i];
    if (session.guard.counters.reserved >= budget.documents || session.metadata.operations >= budget.operations) {
      explorationStop = 'budget';
      progress(denialFile, 'document_budget', {main_frame: true, reason: 'reserved_cap', reserved: session.guard.counters.reserved, cap: budget.documents});
      break;
    }
    let response = session.response;
    const candidateDenialStart = denialEvents.length;
    if (currentCandidate.url !== session.page.url()) {
      session.metadata.operations++;
      const remainingMs = phaseDeadline - Date.now();
      if (remainingMs <= 0) { explorationStop = 'budget'; break; }
      progress(progressFile, 'goto_start', {url: currentCandidate.url, kind: 'candidate'});
      response = await withTimeout(session.page.goto(currentCandidate.url, {waitUntil: 'domcontentloaded', timeout: Math.min(45000, remainingMs)}), Math.min(46000, remainingMs), `candidate_goto:${i}`).catch(() => null);
      progress(progressFile, 'goto_end', {url: currentCandidate.url, status: response?.status() ?? null, kind: 'candidate'});
    }
    const observed = await observe(session.page, response, {progressFile, tag: `candidate-${i + 1}`});
    explorationPages++;
    visited.push({id: currentCandidate.id || null, url: currentCandidate.url, source: currentCandidate.source || null});
    const matching = selectRouteItems(observed.dom.links, group).links;
    graph.push({from: currentCandidate.url, source: currentCandidate.source || null, links_observed: observed.dom.links.length,
      group_link_count: matching.length, repeated_group_count: repeatedAnchorGroups(observed.dom.links).length,
      edges: observed.dom.links.filter(link => candidates.some(candidate => matchesCandidatePrefix(link.url, candidate))).map(link => link.url)});
    if (denialEvents.slice(candidateDenialStart).some(event => event.main_frame)) { refusal = 'main_scope'; break; }
    if (response && [401, 403, 429].includes(response.status())) { refusal = `http_${response.status()}`; break; }
    if (observed.dom.challenge || /verify you are human|access denied|robot check|captcha|request blocked|security check|log in|sign in/i.test(`${observed.dom.title} ${observed.dom.headings.join(' ')}`)) { refusal = 'blocked_or_login'; break; }
    if (matching.length >= 2) { listing = observed; listingCandidate = currentCandidate; break; }
    for (const link of observed.dom.links) {
      const next = candidates.find(candidate => matchesCandidatePrefix(link.url, candidate));
      if (next && !queued.has(link.url) && visited.length + queue.length < 6) { queue.push({...next, url: link.url, declared_url: next.url}); queued.add(link.url); }
    }
  }
  if (!listing && !refusal) explorationStop = explorationStop || (explorationPages >= 6 ? 'budget' : 'content_unchanged');
  const beforeObservation = listing || await observe(session.page, null, {progressFile, tag: 'listing-before-fallback'});
  const before = beforeObservation.dom;
  const beforeLinks = new Set(before.links.map(link => link.url));
  writeJson(beforeFile, before);
  fs.writeFileSync(beforeHtml, beforeObservation.html || '');
  if (beforeObservation.source) fs.writeFileSync(listingSourceFile, beforeObservation.source);
  try {
    if (session.response && [401, 403, 429].includes(session.response.status())) refusal = `http_${session.response.status()}`;
    if (before.challenge || /verify you are human|access denied|robot check|captcha|request blocked|security check|log in|sign in/i.test(`${before.title} ${before.headings.join(' ')}`)) refusal = 'blocked_or_login';
    const actions = listing ? (phase === 'warm' ? recipe.scroll_actions.length : Math.min(6, Math.max(3, Number(plan.max_scroll_actions) || 4))) : 0;
    for (let i = 0; i < actions && !refusal; i++) {
      if (Date.now() >= phaseDeadline) { explorationStop = 'budget'; break; }
      if (session.metadata.operations >= budget.operations) { explorationStop = 'budget'; break; }
      session.metadata.operations++;
      progress(progressFile, 'scroll_start', {index: i + 1, state_id: before.state_id});
      const scrolled = await withTimeout(scrollAction(session.page), 15000, `scroll:${i + 1}`).catch(() => ({scroll_y: null, scroll_height: null, container: null}));
      await withTimeout(session.page.waitForTimeout(target.practice ? 1000 : 5000), target.practice ? 1500 : 6000, `scroll_wait:${i + 1}`).catch(() => {});
      const current = await observe(session.page, null, {progressFile, tag: `scroll-${i + 1}`});
      const stateFile = path.join(base, `scroll-${i + 1}.json`);
      const stateHtmlFile = path.join(base, `scroll-${i + 1}.dom.html`);
      writeJson(stateFile, current.dom);
      fs.writeFileSync(stateHtmlFile, current.html || '');
      scrolls.push({index: i + 1, state_id: current.dom.state_id, link_count: current.dom.links.length,
        fresh_links: current.dom.links.filter(link => !beforeLinks.has(link.url)).length, ...scrolled, snapshot: path.relative(runDir, stateFile),
        rendered_dom: path.relative(runDir, stateHtmlFile), observation_errors: current.errors});
      progress(progressFile, 'scroll_end', {index: i + 1, state_id: current.dom.state_id, links: current.dom.links.length});
    }
  } catch (error) { refusal = `browser_error:${error.message}`; }
  const after = listing ? await observe(session.page, null, {progressFile, tag: 'listing-after-scroll'}).catch(error => ({...listing, errors: [...(listing.errors || []), String(error?.message || error)]})) : null;
  if (after) {
    writeJson(afterFile, after.dom);
    fs.writeFileSync(afterHtml, after.html || '');
  }
  const freshLinks = after ? after.dom.links.filter(link => !beforeLinks.has(link.url)) : [];
  const itemSelection = after ? selectRouteItems(after.dom.links, group) : {links: [], fallback: null, url_shape: null};
  const observedItems = itemSelection.links;
  const picked = prioritizeFreshLinks(observedItems, beforeLinks, 3).filter(link => hostAllowed(link.url, session.hosts) && safeRoute(link.url));
  for (const [index, link] of picked.entries()) {
    if (itemResults.length >= 3 || refusal) break;
    if (session.guard.counters.reserved >= budget.documents) {
      explorationStop = 'budget';
      progress(denialFile, 'document_budget', {main_frame: true, reason: 'reserved_cap', reserved: session.guard.counters.reserved, cap: budget.documents});
      break;
    }
    const remainingMs = phaseDeadline - Date.now();
    if (remainingMs <= 0) { explorationStop = 'budget'; break; }
    const denialEventCountBefore = denialEvents.length;
    session.metadata.operations++;
    progress(progressFile, 'goto_start', {url: link.url, kind: 'item', index: index + 1});
    const response = await withTimeout(session.page.goto(link.url, {waitUntil: 'domcontentloaded', timeout: Math.min(45000, remainingMs)}), Math.min(46000, remainingMs), `item_goto:${index + 1}`).catch(() => null);
    progress(progressFile, 'goto_end', {url: link.url, status: response?.status() ?? null, kind: 'item', index: index + 1});
    const status = response?.status() ?? null;
    const data = await observe(session.page, response, {progressFile, tag: `item-${index + 1}`});
    const mainDenied = denialEvents.slice(denialEventCountBefore).some(event => event.main_frame);
    const htmlCheck = mainDenied ? {basic_html_ok: false, reason: 'main_scope'} : basicValidity(status, data, before, session.hosts);
    const stem = `item-${index + 1}-${sha(link.url).slice(0, 10)}`;
    const htmlFile = path.join(base, `${stem}.dom.html`);
    const sourceFile = path.join(base, `${stem}.source.html`);
    fs.writeFileSync(htmlFile, data.html);
    if (data.source) fs.writeFileSync(sourceFile, data.source);
    itemResults.push({url: link.url, final_url: data.dom.url, status, basic_html_check: htmlCheck, title: data.dom.title, headings: data.dom.headings,
      html_length: data.html.length, observation_errors: data.errors, rendered_sha256: sha(data.html), source_sha256: data.source ? sha(data.source) : null,
      rendered_dom: path.relative(runDir, htmlFile), source_html: data.source ? path.relative(runDir, sourceFile) : null,
      saved: Boolean(htmlCheck.basic_html_ok && data.source && data.html)});
    if (['blocked_or_login', 'http_401', 'http_403', 'http_429', 'main_scope'].includes(htmlCheck.reason)) refusal = htmlCheck.reason;
  }
  await withTimeout(session.guard.seal(), 6000, 'document_guard_seal').catch(() => {});
  const afterListing = after;
  if (!afterListing) {
    writeJson(afterFile, before);
    fs.writeFileSync(afterHtml, beforeObservation.html || '');
  }
  const listingSource = beforeObservation.source;
  const selectedItemGroup = group.pattern || group.segments ? routePattern(executionPlan) : itemSelection.url_shape ? {id: group.id || null, url_shape: itemSelection.url_shape, fallback: itemSelection.fallback} : routePattern(executionPlan);
  const stableRecipe = {version: 1, robots_policy: robotsPolicy, seed_url: listingCandidate?.url || seed, selected_candidate_id: listingCandidate?.id || initialCandidate?.id || null,
    selected_candidate_source: listingCandidate?.source || initialCandidate?.source || null, selected_group: selectedItemGroup,
    scroll_actions: scrolls.map(row => row.index), observed_state_ids: scrolls.map(row => row.state_id), visited_candidate_ids: visited.map(row => row.id).filter(Boolean),
    observed_candidate_urls: [...new Set(graph.flatMap(row => row.edges))], candidate_order_rule: 'prioritize scroll/infinite path tokens, otherwise original agent order',
    original_candidate_ids: originalCandidates.map(row => row.id), execution_candidate_ids: candidates.map(row => row.id),
    cumulative_document_requests: cumulativeBase + session.guard.counters.reserved, no_page_contents: true};
  if (phase === 'cold') writeJson(recipeFile, stableRecipe);
  const matchingFresh = new Set(freshLinks.filter(link => itemSelection.url_shape ? routeUrlShape(link.url) === itemSelection.url_shape : matchesGroup(link.url, group)).map(link => link.url));
  const taskSuccess = itemResults.some(item => item.saved);
  const savedItemUrls = new Set(itemResults.filter(item => item.saved).map(item => item.url));
  const freshSaved = [...matchingFresh].filter(url => savedItemUrls.has(url));
  const scrollGrowthSuccess = freshSaved.length > 0;
  if (session.guard.counters.denied > denialEvents.length && session.guard.counters.reserved >= budget.documents) {
    progress(denialFile, 'document_budget', {main_frame: null, reason: 'reserved_cap', reserved: session.guard.counters.reserved, denied: session.guard.counters.denied, cap: budget.documents});
  }
  const childDenialWithMainOk = denialEvents.some(event => !event.main_frame) && !denialEvents.some(event => event.main_frame)
    && hostAllowed(before.url, session.hosts) && safeRoute(before.url);
  const sourceFailureStage = refusal === 'main_scope' ? 'main_scope'
    : /^http_(401|403|429)$/.test(refusal || '') ? 'main_http_status'
      : refusal === 'blocked_or_login' ? 'main_content_block'
    : explorationStop === 'budget' || (session.guard.counters.denied > 0 && session.guard.counters.reserved >= budget.documents && !taskSuccess) ? 'document_budget'
      : childDenialWithMainOk ? 'child_scope_denied_but_main_ok'
        : !taskSuccess && itemResults.some(item => item.status == null) ? 'main_navigation_failed'
          : !taskSuccess ? 'detail_invalid' : null;
  result = {id: target.id, phase, robots_policy: robotsPolicy, status: refusal ? 'access_stopped' : taskSuccess ? 'html_saved' : 'no_valid_detail', task_success: taskSuccess,
    scroll_growth_success: scrollGrowthSuccess, external_llm_calls: 0, local_inference: {calls: 0},
    seed_url: listingCandidate?.url || seed, selected_candidate_id: listingCandidate?.id || initialCandidate?.id || null,
    selected_candidate_source: listingCandidate?.source || initialCandidate?.source || null, listing_found: Boolean(listing),
    candidate_order: {rule: 'prioritize scroll/infinite path tokens, otherwise original agent order', original_ids: originalCandidates.map(row => row.id), execution_ids: candidates.map(row => row.id)},
    stop_reason: refusal?.startsWith('browser_error:') ? 'error' : refusal ? 'access' : explorationStop || (scrolls.length && !freshLinks.length ? 'content_unchanged' : listing ? null : 'content_unchanged'),
    item_group_fallback: itemSelection.fallback, item_url_shape: itemSelection.url_shape,
    scroll_actions: scrolls.length, before_link_count: before.links.length, after_link_count: afterListing?.dom.links.length ?? before.links.length,
    fresh_links_after_scroll: freshLinks.length, repeated_anchor_groups_before: repeatedAnchorGroups(before.links).length,
    all_new_link_urls: freshLinks.map(link => link.url), fresh_item_urls_after_scroll: [...matchingFresh], fresh_item_urls_saved_after_scroll: freshSaved,
    repeated_anchor_groups_after: afterListing ? repeatedAnchorGroups(afterListing.dom.links).length : 0, selected_items: itemResults, graph,
    listing_candidates_visited: visited, scroll_states: scrolls,
    snapshots: {before: path.relative(runDir, beforeFile), after: path.relative(runDir, afterFile), before_html: path.relative(runDir, beforeHtml), after_html: path.relative(runDir, afterHtml),
      before_source_html: listingSource ? path.relative(runDir, listingSourceFile) : null, before_source_sha256: listingSource ? sha(listingSource) : null,
      before_rendered_sha256: sha(beforeObservation.html || ''), observation_errors: beforeObservation.errors || []},
    state_ids: {before: before.state_id, after: afterListing?.dom.state_id || before.state_id},
    cost: {http_requests: session.metadata.requests, http_responses: session.metadata.responses, xhr_fetch: session.metadata.xhr_fetch, response_bytes: session.metadata.bytes,
      robots_requests: session.metadata.robot_requests, robots: session.metadata.robot_requests, browser_document_requests: session.metadata.document_requests, browser_document_responses: session.metadata.document_responses,
      ui_operations: session.metadata.operations, browser_ms: Date.now() - browserStarted}, budget: {cumulative_document_cap: siteDocumentCap, cumulative_document_requests: stableRecipe.cumulative_document_requests,
      document_cap: budget.documents, document_reserved: session.guard.counters.reserved, operation_cap: budget.operations, operations: session.metadata.operations},
    refusal, source_failure_stage: sourceFailureStage, document_denials: path.relative(runDir, denialFile), partial: false, progress: path.relative(runDir, progressFile), document_ledger: path.relative(runDir, ledgerFile), recipe: path.relative(runDir, recipeFile)};
  result.budget = resultBudget(session, budget, cumulativeBase);
  } catch (error) {
    result = {id: target.id, phase, robots_policy: robotsPolicy, status: 'error', task_success: false, scroll_growth_success: false, external_llm_calls: 0, local_inference: {calls: 0},
      seed_url: listingCandidate?.url || seed, selected_candidate_id: listingCandidate?.id || initialCandidate?.id || null, listing_found: Boolean(listing),
      stop_reason: String(error?.message || error).startsWith('timeout:') ? 'budget' : 'error', partial: true,
      selected_items: itemResults, graph, listing_candidates_visited: visited, scroll_states: scrolls,
      progress: path.relative(runDir, progressFile), document_ledger: path.relative(runDir, ledgerFile),
      snapshots: {before: fs.existsSync(beforeFile) ? path.relative(runDir, beforeFile) : null,
        after: fs.existsSync(afterFile) ? path.relative(runDir, afterFile) : null},
      error: String(error?.message || error), cost: {http_requests: session?.metadata.requests || 0, http_responses: session?.metadata.responses || 0,
        xhr_fetch: session?.metadata.xhr_fetch || 0, response_bytes: session?.metadata.bytes || 0, robots_requests: session?.metadata.robot_requests || 0,
        robots: session?.metadata.robot_requests || 0,
        browser_document_requests: session?.metadata.document_requests || 0, browser_document_responses: session?.metadata.document_responses || 0,
        ui_operations: session?.metadata.operations || 0, browser_ms: Date.now() - browserStarted},
      budget: resultBudget(session, budget, cumulativeBase), recipe: path.relative(runDir, recipeFile)};
  } finally {
    if (session) {
      clearTimeout(session.hardTimer);
      await withTimeout(session.guard.seal(), 6000, 'final_guard_seal').catch(() => {});
      await withTimeout(session.context.close(), 10000, 'context_close').catch(() => {});
      await withTimeout(session.browser.close(), 10000, 'browser_close').catch(() => {});
      await withTimeout(session.guard.cleanup(), 10000, 'guard_cleanup').catch(() => {});
    }
    result ||= {id: target.id, phase, status: 'error', task_success: false, scroll_growth_success: false, stop_reason: 'error'};
    result.budget ||= resultBudget(session, budget, cumulativeBase);
    writeJson(path.join(runDir, 'sites', String(target.id), phase, 'result.json'), result);
    progress(progressFile, 'phase_end', {status: result.status, task_success: result.task_success, stop_reason: result.stop_reason,
      document_reserved: session?.guard.counters.reserved || 0, ui_operations: session?.metadata.operations || 0});
  }
  return result;
}

function parseArgs(argv) {
  const values = {};
  for (let i = 0; i < argv.length; i++) if (argv[i].startsWith('--')) values[argv[i].slice(2)] = argv[i + 1]?.startsWith('--') || !argv[i + 1] ? 'true' : argv[++i];
  return values;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.run || !args.id || !['cold', 'warm'].includes(args.phase)) throw new Error('usage: --run RUN_DIR --id ID --phase cold|warm');
  const runDir = path.resolve(args.run);
  const target = JSON.parse(fs.readFileSync(path.join(runDir, 'targets.jsonl'), 'utf8').split(/\r?\n/).filter(Boolean).find(row => String(JSON.parse(row).id) === String(args.id)) || 'null');
  if (!target) throw new Error('target_not_found');
  const inputFile = args.input || path.join(runDir, 'artifacts', String(args.id), 'sitemap-agent-browser', 'plan.json');
  const plan = JSON.parse(fs.readFileSync(inputFile, 'utf8'));
  const result = await executeRoute({runDir, target, plan, phase: args.phase, options: {executablePath: args.executable,
    prior_document_requests: Number(args['prior-document-requests'] || 0)}});
  process.stdout.write(JSON.stringify({id: result.id, status: result.status, scroll_actions: result.scroll_actions, fresh_links_after_scroll: result.fresh_links_after_scroll, selected_items: result.selected_items.length}) + '\n');
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) main().catch(error => { console.error(JSON.stringify({error: error.message})); process.exitCode = 1; });
