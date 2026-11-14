import {environmentContextOptions} from './environment.mjs';
import {installDocumentGuard} from './document-guard.mjs';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(path.join(repo, '.deps/bot-diagnostics/package.json'));
const {chromium} = require('playwright');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const args = Object.fromEntries(process.argv.slice(2).reduce((out, item, i, all) => {
  if (!item.startsWith('--')) return out;
  const [key, inline] = item.slice(2).split('=', 2);
  out.push([key, inline ?? (all[i + 1] === undefined || all[i + 1].startsWith('--') ? 'true' : all[i + 1])]);
  return out;
}, []));

function jsonl(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
}
function saveJson(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');
}
function appendJsonl(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.appendFileSync(file, JSON.stringify(value) + '\n');
}
function pageType(html, dom, title, url, status) {
  const text = dom.text.replace(/\s+/g, ' ').trim();
  const low = `${title} ${text.slice(0, 5000)}`.toLowerCase();
  const challenge = /verify you are human|access denied|robot check|unusual traffic|request blocked|captcha verification|complete the security check/i;
  if (status === 403 || status === 429 || challenge.test(`${dom.heading} ${title} ${text.length < 1200 ? text : ''}`)) return 'block';
  if (/sign in|log in|ログイン|サインイン/.test(`${title} ${url}`.toLowerCase()) && /password|パスワード/.test(low)) return 'login';
  if (/maintenance|temporarily unavailable|メンテナンス中|ただいまご利用いただけません/i.test(`${title} ${dom.heading}`)) return 'maintenance';
  if (/not found|page not found|404|ページが見つかりません/i.test(`${title} ${dom.heading}`)) return 'not_found';
  if (/error|エラー/i.test(`${title} ${dom.heading}`) && dom.bodyLength < 1800) return 'error_page';
  if (dom.bodyLength < 200 && dom.links.length < 2 && !dom.heading) return 'empty_spa';
  if (dom.repeated >= 3 || dom.schemaCount >= 3) return 'listing';
  if (dom.heading && dom.bodyLength > 10) return 'detail';
  if (dom.bodyLength > 400) return 'content';
  return 'unknown';
}
function cleanURL(value) {
  try { return new URL(value).href; } catch { return null; }
}
function documentKey(value) {
  try { const url = new URL(value); url.hash = ''; return url.href; } catch { return value; }
}
function allowed(url, hosts) {
  try { return hosts.has(new URL(url).hostname.toLowerCase()); } catch { return false; }
}
function globalDocumentBudget(runDir) {
  const read = name => jsonl(path.join(runDir, name));
  const sumV1 = read('evaluator-results.preflight-v1.jsonl').reduce((sum, row) => sum + Number(row.requests?.count ?? row.requests ?? 0), 0);
  const v1 = Math.max(742, sumV1);
  const v2 = read('evaluator-results-v2.jsonl').reduce((sum, row) => sum + Number(row.document_attempts || 0), 0);
  const candidateRows = [...read('results.jsonl'), ...read('recovery-results.jsonl')].filter(row => ['B1', 'C1'].includes(row.method));
  const candidate = candidateRows.reduce((sum, row) => sum + Math.max(Number(row.document_requests || 0), Number(row.document_responses || 0)), 0);
  const challenges = read('challenge-results.jsonl').reduce((sum, row) => sum + Math.max(Number(row.document_requests || 0), Number(row.document_responses || 0)), 0);
  const b0 = read('b0-results.jsonl').reduce((sum, row) => sum + Math.max(Number(row.document_requests || 0), Number(row.document_responses || 0)), 0);
  const interruptedFile = path.join(runDir, 'interrupted-executions.json');
  const interrupted = fs.existsSync(interruptedFile) ? JSON.parse(fs.readFileSync(interruptedFile, 'utf8')) : {};
  const reservationRows = [...(interrupted.main || []), ...(interrupted.network_recovery || interrupted.recovery || [])];
  const interruptedDocuments = reservationRows.reduce((sum, row) => sum +
    Object.values(row.method_unlogged_document_reservation || {}).reduce((subtotal, value) => subtotal + Number(value || 0), 0), 0);
  return {v1_requests_as_documents: v1, v2_document_attempts: v2, candidate_documents: candidate,
    challenge_documents: challenges, b0_documents: b0, interrupted_documents: interruptedDocuments,
    spent: v1 + v2 + candidate + challenges + b0 + interruptedDocuments};
}
function selectorFor(link) {
  const p = link.parent || {};
  const tag = String(p.tag || 'div').toLowerCase();
  const classes = (p.classes || []).filter(x => /^[a-zA-Z_][\w-]*$/.test(x)).slice(0, 2);
  return `${tag}${classes.map(x => `.${x}`).join('')} a`;
}
function learnRules(pages) {
  const counts = new Map();
  for (const page of pages) for (const link of page.links) {
    const selector = selectorFor(link);
    const old = counts.get(selector) || {selector, count: 0, pathSegments: new Map(), samples: [], content: 0, navigation: 0};
    old.count++;
    if (link.region === 'main' || link.region === 'article' || link.region === 'section') old.content++;
    if (link.region === 'nav' || link.region === 'header' || link.region === 'footer') old.navigation++;
    const seg = new URL(link.url).pathname.split('/').filter(Boolean).length;
    old.pathSegments.set(seg, (old.pathSegments.get(seg) || 0) + 1);
    if (old.samples.length < 4) old.samples.push(link.url);
    counts.set(selector, old);
  }
  const groups = [...counts.values()].filter(x => x.count >= 2).sort((a, b) => (b.content - b.navigation) - (a.content - a.navigation) || b.count - a.count);
  return {
    version: 1,
    source_pages: pages.map(x => x.url),
    repeated_link_groups: groups.map(x => ({selector: x.selector, observed_count: x.count, content_region_count: x.content, navigation_region_count: x.navigation,
      common_path_depth: [...x.pathSegments].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null, examples: x.samples})),
    pagination: pages.flatMap(p => p.links.filter(a => /^(next|older|more|次へ|次|›|»|→)/i.test(a.text.trim()) || /(?:^|[?&/])(page|offset|cursor)=?\d*/i.test(a.url))
      .map(a => ({source: p.url, selector: selectorFor(a), url: a.url, text: a.text.slice(0, 120)}))),
  };
}
function selectLinks(page, method, rules, known, hosts) {
  let links = page.links.filter(link => allowed(link.url, hosts) && /^https?:$/.test(new URL(link.url).protocol)
    && !/(?:cart|checkout|order|account|login|register|signup|contact|submit|purchase|buy)/i.test(new URL(link.url).pathname));
  if (method === 'C1') {
    const selectors = new Set(rules.repeated_link_groups.filter(x => x.content_region_count >= x.navigation_region_count).map(x => x.selector));
    const pagination = new Set(rules.pagination.map(x => x.selector));
    const chosen = links.filter(link => selectors.has(selectorFor(link)) || pagination.has(selectorFor(link)) ||
      /^(next|older|more|次へ|次|›|»|→)/i.test(link.text.trim()));
    if (chosen.length) links = chosen;
  }
  return links.filter(link => !known.has(link.url));
}
export {pageType, learnRules, selectLinks};

async function runOne(target, method, mode, runDir, resume) {
  const started = Date.now();
  const id = target.id;
  const measurement = args.measurement || 'main';
  if (!['main', 'network_recovery'].includes(measurement)) throw new Error('measurement_must_be_main_or_network_recovery');
  const methodBase = path.join(runDir, 'artifacts', id, method);
  const base = measurement === 'network_recovery' ? path.join(methodBase, 'network_recovery') : methodBase;
  const dir = path.join(base, mode);
  const resultFile = path.join(runDir, measurement === 'network_recovery' ? 'recovery-results.jsonl' : 'results.jsonl');
  fs.mkdirSync(dir, {recursive: true});
  const mainRows = jsonl(path.join(runDir, 'results.jsonl'));
  const recoveryRows = jsonl(path.join(runDir, 'recovery-results.jsonl'));
  const sitePriorRows = [...mainRows, ...recoveryRows].filter(x => String(x.id) === String(id));
  const priorRows = sitePriorRows.filter(x => x.method === method);
  const statePath = measurement === 'network_recovery'
    ? path.join(runDir, 'site-state', `${id}.network_recovery.json`)
    : path.join(runDir, 'site-state', `${id}.json`);
  const state = fs.existsSync(statePath) ? JSON.parse(fs.readFileSync(statePath, 'utf8')) : {completed: []};
  const stateKey = `${id}:${method}:${mode}`;
  if (state.completed.includes(stateKey)) {
    if (!resume) throw new Error(`already_complete:${stateKey}:use_--resume`);
    return {id, method, mode, status: 'already_complete'};
  }
  const accessStop = sitePriorRows.find(x => ['blocked', 'login_page', 'maintenance', 'robots_disallowed'].includes(x.status));
  if (accessStop) {
    const result = {id, domain: target.domain, method, mode,
      trial_type: mode === 'G' ? 'graph_replay' : mode === 'R' ? 'generalization' : 'discovery',
      status: 'skipped_due_access_condition', stop_reason: 'prior_access_condition_stop',
      stop_trigger: {method: accessStop.method, mode: accessStop.mode, status: accessStop.status}, robots: accessStop.robots,
      discovered_documents: 0, document_requests: 0, document_responses: 0,
      total_method_documents: priorRows.reduce((sum, x) => sum + Math.max(x.document_requests || 0, x.document_responses || 0), 0),
      total_method_operations: priorRows.reduce((sum, x) => sum + (x.operations || 0), 0), http_requests: 0,
      xhr_fetch_requests: 0, operations: 0, elapsed_ms: 0,
      external_llm_calls: 0, local_inference: {calls: 0, input_tokens: 0, runtime_ms: 0},
      initial_elapsed_ms: 0, replay_elapsed_ms: 0, repair_elapsed_ms: 0, manual_interventions: 0, pages: []};
      if (measurement === 'network_recovery') result.execution_origin = measurement;
      state.completed.push(stateKey); saveJson(statePath, state); appendJsonl(resultFile, result);
    return result;
  }
  const practice = !!target.practice;
  const configuredObserve = Number(args.observeLimit || args['observe-limit'] || 0);
  const baseExplore = practice ? 6 : 3;
  const explorationLimit = configuredObserve ? Math.min(baseExplore, configuredObserve) : baseExplore;
  if (!Number.isInteger(explorationLimit) || explorationLimit < 1) throw new Error('observe_limit_must_be_positive_integer');
  const totalLimit = method === 'B0' ? 3 : practice ? 40 : 15;
  const interruptedFile = path.join(runDir, 'interrupted-executions.json');
  const interrupted = fs.existsSync(interruptedFile) ? JSON.parse(fs.readFileSync(interruptedFile, 'utf8')) : {};
  const interruptedRows = measurement === 'network_recovery'
    ? [...(interrupted.main || []), ...(interrupted.network_recovery || interrupted.recovery || [])]
    : (interrupted.main || []);
  const interruptedReservation = interruptedRows.filter(x => String(x.id) === String(id))
    .reduce((sum, row) => sum + Number(row.method_unlogged_document_reservation?.[method] || 0), 0);
  const priorDocuments = interruptedReservation + priorRows.reduce((sum, x) => sum + Math.max(x.document_requests || 0, x.document_responses || 0), 0);
  const priorOperations = priorRows.reduce((sum, x) => sum + (x.operations || 0), 0);
  const priorElapsed = priorRows.reduce((sum, x) => sum + (x.elapsed_ms || 0), 0);
  const globalBudget = measurement === 'network_recovery' ? globalDocumentBudget(runDir) : null;
  const globalAllowance = globalBudget ? Math.max(0, 3500 - globalBudget.spent) : Infinity;
  const documentAllowance = Math.max(0, Math.min(totalLimit - priorDocuments, globalAllowance));
  if (documentAllowance === 0) {
    const globalUnavailable = globalAllowance === 0;
    const result = {id, domain: target.domain, method, mode, status: globalUnavailable ? 'global_budget_unavailable' : interruptedReservation ? 'interrupted_budget_reserved' : 'document_budget_reached',
      stop_reason: globalUnavailable ? 'global_3500_document_budget' : interruptedReservation ? 'unlogged_interrupted_cost_reserved_not_reacquired' : 'method_document_budget',
      trial_type: mode === 'G' ? 'graph_replay' : mode === 'R' ? 'generalization' : 'discovery',
      document_requests: 0, document_responses: 0, http_requests: 0, xhr_fetch_requests: 0, operations: 0,
      total_method_documents: priorDocuments, total_method_operations: priorOperations, elapsed_ms: 0,
      interrupted_document_reservation: interruptedReservation, pages: [], external_llm_calls: 0,
      local_inference: {calls:0,input_tokens:0,runtime_ms:0}, environment_layer: process.env.STRUCTURE_ENVIRONMENT_LAYER || 'unspecified',
      ...(measurement === 'network_recovery' ? {execution_origin: measurement} : {}),
      ...(globalBudget ? {global_budget: {...globalBudget, remaining: globalAllowance}} : {})};
    state.completed.push(stateKey); saveJson(statePath, state); appendJsonl(resultFile, result); return result;
  }
  let interval = practice ? 1000 : 5000;
  const hosts = new Set([new URL(target.start_url).hostname.toLowerCase(), ...(target.allowed_hosts || [])].map(x => x.toLowerCase()));
  const browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {})});
  const context = await browser.newContext({...environmentContextOptions(), serviceWorkers: 'block', ignoreHTTPSErrors: false, locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: {width: 1365, height: 900}});
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.setCacheDisabled', {cacheDisabled: true});
  let docLoads = 0, docRequests = 0, requests = 0, xhr = 0, operations = 0, lastNavigationStart = 0;
  const network = [], responseStatus = new Map(), responseBodies = new Map();
  page.on('request', request => {
    requests++;
    const type = request.resourceType();
    if (type === 'xhr' || type === 'fetch') xhr++;
    network.push({url: request.url(), method: request.method(), type});
  });
  page.on('response', response => {
    if (response.request().resourceType() === 'document') {
      docLoads++;
      responseStatus.set(documentKey(response.url()), response.status());
      response.body().then(body => responseBodies.set(documentKey(response.url()), body)).catch(() => {});
    }
  });
  const documentGuard = await installDocumentGuard(page, {
    limit: documentAllowance,
    onReserved: (_info, counters) => { docRequests = counters.reserved; },
    beforeRequest: async ({url, mainFrame, method}) => {
      if (method !== 'GET') return false;
      if (mainFrame) {
        if (!allowed(url, hosts) || !/^https?:$/.test(new URL(url).protocol)) return false;
        const check = await fetchRobots(url);
        if (!check.allowed) return false;
        interval = Math.max(interval, check.crawl_delay_ms || 0);
        const spacing = Math.max(0, interval - (Date.now() - lastNavigationStart));
        if (spacing) await wait(spacing);
        lastNavigationStart = Date.now();
      }
      return true;
    }
  });
  const robotsByOrigin = new Map();
  const fetchRobots = async url => {
    const origin = new URL(url).origin;
    if (robotsByOrigin.has(origin)) {
      const cached = robotsByOrigin.get(origin);
      return {...cached.metadata, allowed: cached.metadata.access_refused ? false : cached.parser ? cached.parser.isAllowed(url, '*') !== false : true};
    }
    try {
      requests++;
      const response = await context.request.get(new URL('/robots.txt', origin).href, {timeout: 10000});
      const text = await response.text();
      if (response.status() >= 400) {
        const result = {status: response.status(), source: 'unavailable', access_refused: [401,403,429].includes(response.status())};
        robotsByOrigin.set(origin, {metadata: result, parser: null}); return {...result, allowed: !result.access_refused};
      }
      const parser = require('robots-parser')(new URL('/robots.txt', origin).href, text);
      const result = {status: response.status(), source: 'robots-parser',
        crawl_delay_ms: Math.ceil((parser.getCrawlDelay('*') || 0) * 1000)};
      robotsByOrigin.set(origin, {metadata: result, parser}); return {...result, allowed: parser.isAllowed(url, '*') !== false};
    } catch (error) {
      const result = {allowed: true, source: 'unavailable', error: String(error.message).slice(0, 200)};
      robotsByOrigin.set(origin, {metadata: result, parser: null}); return result;
    }
  };
  const robots = sitePriorRows.find(x => x.robots)?.robots || await fetchRobots(target.start_url);
  const observations = [], savedPages = [];
  let status = 'completed', stopReason = 'queue_empty';
  try {
    if (!robots.allowed) { status = 'robots_disallowed'; stopReason = 'robots_disallow'; }
    else if (method === 'B0') {
      await page.goto(target.start_url, {waitUntil: 'domcontentloaded', timeout: 30000});
      await wait(interval);
      const capture = await capturePage(page, target.start_url, hosts, dir, 0, responseStatus.get(documentKey(page.url())), responseBodies.get(documentKey(page.url())), methodBase);
      observations.push(capture.meta); savedPages.push(capture.page);
      stopReason = 'direct_navigation';
    } else {
      const isWarm = mode !== 'cold';
      const graphPath = path.join(base, 'cold', 'graph.json');
      const rulesPath = path.join(base, 'cold', 'rules.json');
      const graph = isWarm && fs.existsSync(graphPath) ? JSON.parse(fs.readFileSync(graphPath, 'utf8')) : {nodes: [], edges: []};
      const priorRules = isWarm && fs.existsSync(rulesPath) ? JSON.parse(fs.readFileSync(rulesPath, 'utf8')) : null;
      const predecessors = new Map([[target.start_url, null]]);
      for (const edge of graph.edges) if (!predecessors.has(edge.to) && predecessors.has(edge.from))
        predecessors.set(edge.to, {from: edge.from, url: edge.to});
      const savedPath = url => {
        const steps = []; let current = url;
        while (current && current !== target.start_url && predecessors.has(current)) {
          const step = predecessors.get(current); if (!step) break;
          steps.unshift(step); current = step.from;
        }
        return current === target.start_url ? steps : null;
      };
      const coldKnown = new Set((graph.nodes || []).map(x => x.url));
      const queue = mode === 'G' ? graph.nodes.filter(x => x.url !== target.start_url && savedPath(x.url)).map(x => ({
        url: x.url, from: null, kind: 'saved_graph_path', path: savedPath(x.url)
      })) : mode === 'R' ? graph.edges.filter(edge => coldKnown.has(edge.from) && !coldKnown.has(edge.to))
        .map(edge => ({...edge, url: edge.to, kind: 'frozen_rule_candidate', path: [...(savedPath(edge.from) || []), {from: edge.from, url: edge.to}]}))
        : [{url: target.start_url, from: null, kind: 'entry', path: []}];
      const visited = new Set();
      const discoveryPages = [];
      const maxObserve = isWarm ? Math.min(explorationLimit, Math.max(1, graph.nodes.length)) : explorationLimit;
      while (queue.length && observations.length < maxObserve && observations.length < totalLimit) {
        if (docRequests >= documentAllowance || priorElapsed + Date.now() - started >= 600000 || priorOperations + operations >= (practice ? 100 : 30)) {
          stopReason = docRequests >= documentAllowance ? 'document_budget' : priorOperations + operations >= (practice ? 100 : 30) ? 'operation_budget' : 'time_budget';
          break;
        }
        const item = queue.shift();
        if (visited.has(item.url)) continue;
        visited.add(item.url);
        if (!allowed(item.url, hosts)) continue;
        const pageRobots = await fetchRobots(item.url);
        if (!pageRobots.allowed) { status = 'robots_disallowed'; stopReason = 'robots_disallow'; break; }
        interval = Math.max(interval, pageRobots.crawl_delay_ms || 0);
        let navigationError = null;
        try {
          await page.goto(target.start_url, {waitUntil: 'domcontentloaded', timeout: 30000});
          for (const step of item.path) {
            if (docRequests >= documentAllowance || priorOperations + operations >= (practice ? 100 : 30)) throw new Error(docRequests >= documentAllowance ? 'document_budget' : 'operation_budget');
            const linkIndex = await page.locator('a[href]').evaluateAll((elements, expected) => elements.findIndex(a => {
              try { return new URL(a.getAttribute('href'), location.href).href === expected; } catch { return false; }
            }), step.url);
            if (linkIndex < 0) throw new Error('saved_path_link_missing');
            await page.locator('a[href]').nth(linkIndex).click({timeout: 10000});
            operations++;
            await page.waitForLoadState('domcontentloaded', {timeout: 15000}).catch(() => {});
            await wait(Math.min(500, interval));
          }
        }
        catch (error) { navigationError = String(error.message).slice(0, 300); }
        await wait(Math.min(1200, interval));
        const capture = await capturePage(page, item.url, hosts, dir, observations.length, responseStatus.get(documentKey(page.url())), responseBodies.get(documentKey(page.url())), methodBase);
        const record = {...capture.meta, trial_type: mode === 'G' ? 'graph_replay' : mode === 'R' ? 'generalization' : 'discovery',
          from: item.from, link_kind: item.kind, navigation_error: navigationError};
        observations.push(record);
        savedPages.push(capture.page);
        discoveryPages.push(capture.page);
        if (capture.meta.page_type === 'block') { status = 'blocked'; stopReason = 'block_page'; break; }
        if (capture.meta.page_type === 'login') { status = 'login_page'; stopReason = 'login_page'; break; }
        if (capture.meta.page_type === 'maintenance') { status = 'maintenance'; stopReason = 'maintenance_page'; break; }
        const activeRules = isWarm ? (priorRules || learnRules(discoveryPages)) : learnRules(discoveryPages);
        const coldObserved = new Set((graph.nodes || []).map(x => x.url));
        const known = new Set([...visited, ...queue.map(x => x.url), ...(isWarm ? coldObserved : [])]);
        const links = mode === 'G' ? [] : selectLinks(capture.page, method, activeRules, known, hosts);
        for (const link of links) {
          graph.edges.push({from: item.url, to: link.url, selector: selectorFor(link), text: link.text.slice(0, 120), parent: link.parent, region: link.region});
          queue.push({url: link.url, from: item.url, kind: /next|older|more|次へ/i.test(link.text) ? 'pagination' : 'link',
            path: [...item.path, {from: item.url, url: link.url}]});
        }
        if (!navigationError) graph.nodes.push({url: capture.meta.url, page_type: capture.meta.page_type, title: capture.meta.title});
        if (requests > 1500) { stopReason = 'request_budget'; break; }
      }
      if (queue.length && observations.length >= maxObserve) stopReason = 'exploration_document_limit';
      if (observations.length >= totalLimit) stopReason = 'total_document_limit';
      const learned = isWarm ? (priorRules || learnRules(discoveryPages)) : learnRules(discoveryPages);
      if (!isWarm) {
        saveJson(path.join(base, 'cold', 'graph.json'), graph);
        saveJson(path.join(base, 'cold', 'rules.json'), learned);
      }
      saveJson(path.join(dir, 'graph.json'), graph);
      saveJson(path.join(dir, 'rules.json'), learned);
      saveJson(path.join(dir, 'path.json'), {nodes: observations.map(x => x.url), edges: graph.edges.filter(x => observations.some(o => o.url === x.from))});
    }
  } catch (error) {
    status = 'error'; stopReason = String(error.message).slice(0, 300);
  } finally {
    await documentGuard.seal();
    await context.close(); await browser.close();
  }
  saveJson(path.join(dir, 'observations.json'), {observations, network, robots});
  state.completed.push(stateKey); saveJson(statePath, state);
  const result = {id, domain: target.domain, method, mode,
    ...(measurement === 'network_recovery' ? {execution_origin: measurement} : {}),
    trial_type: mode === 'G' ? 'graph_replay' : mode === 'R' ? 'generalization' : 'discovery',
    environment_layer: process.env.STRUCTURE_ENVIRONMENT_LAYER || 'unspecified', browser_version: browser.version(),
    status, stop_reason: stopReason, robots, rule_provenance: mode === 'R' ? 'frozen_cold_rules' : mode === 'G' ? 'saved_cold_graph_paths' : null,
    rule_source_pages: mode === 'R' && fs.existsSync(path.join(base, 'cold', 'rules.json'))
      ? JSON.parse(fs.readFileSync(path.join(base, 'cold', 'rules.json'), 'utf8')).source_pages : [],
    document_guard: documentGuard.counters, discovered_documents: observations.length, document_requests: docRequests, document_responses: docLoads,
    total_method_documents: priorDocuments + docRequests, total_method_operations: priorOperations + operations, http_requests: requests,
    xhr_fetch_requests: xhr, operations, elapsed_ms: Date.now() - started, external_llm_calls: 0,
    local_inference: {calls: 0, input_tokens: 0, runtime_ms: 0}, initial_elapsed_ms: mode === 'cold' ? Date.now() - started : 0,
    replay_elapsed_ms: mode === 'G' || mode === 'R' ? Date.now() - started : 0, repair_elapsed_ms: 0, manual_interventions: 0,
    pages: observations.map(x => ({...x, duplicate: observations.findIndex(y => y.url === x.url) !== observations.indexOf(x)})),
    ...(globalBudget ? {global_budget: {...globalBudget, spent_after: globalBudget.spent + Math.max(docRequests, docLoads),
      remaining_after: 3500 - globalBudget.spent - Math.max(docRequests, docLoads)}} : {})};
  appendJsonl(resultFile, result);
  return result;
}

async function capturePage(page, requestedURL, hosts, dir, index, status = null, responseBody = null, artifactPathBase = path.dirname(dir)) {
  const url = page.url() || requestedURL;
  const safe = await page.evaluate(({hosts}) => {
    const links = [...document.querySelectorAll('a[href]')].map(a => {
      const p = a.parentElement;
      const candidates = [...(p?.classList || [])];
      const region = a.closest('main,article,section,nav,header,footer')?.tagName?.toLowerCase() || 'body';
      return {url: new URL(a.getAttribute('href'), location.href).href, text: (a.innerText || a.textContent || '').trim(),
        parent: {tag: p?.tagName?.toLowerCase() || 'div', classes: candidates}, region};
    }).filter(a => { try { return hosts.includes(new URL(a.url).hostname.toLowerCase()); } catch { return false; } });
    const counts = new Map();
    for (const a of links) { const k = `${a.region}:${a.parent.tag}.${a.parent.classes.join('.')}`; counts.set(k, (counts.get(k) || 0) + 1); }
    return {html: document.documentElement?.outerHTML || '', title: document.title || '', links,
      bodyLength: (document.body?.innerText || '').length, text: document.body?.innerText || '',
      heading: document.querySelector('h1')?.innerText || '',
      repeated: Math.max(0, ...[...counts].filter(([key]) => /^(main|article|section):/.test(key)).map(([, value]) => value)),
      schemaCount: document.querySelectorAll('[itemscope], [itemtype]').length,
      canonicalHref: document.querySelector('link[rel="canonical"]')?.href || null,
      schemaTypes: [...document.querySelectorAll('[itemtype]')].map(x => x.getAttribute('itemtype')).filter(Boolean),
      htmlLength: document.documentElement?.outerHTML?.length || 0,
      stateId: `${location.pathname}${location.search}${location.hash}:${document.title}:${document.body?.innerText?.slice(0, 120) || ''}`};
  }, {hosts: [...hosts]});
  const responseHTML = responseBody?.toString('utf8') || '';
  const type = pageType(responseHTML, safe, safe.title, url, status);
  const linkShapes = {};
  for (const link of safe.links) {
    const parsed = new URL(link.url);
    const segments = parsed.pathname.split('/').filter(Boolean).slice(0, 3).map(segment => /^\d+$/.test(segment) ? ':id' : segment.replace(/\d+/g, ':n'));
    const shape = `${parsed.hostname}/${segments.join('/') || '<root>'}`;
    linkShapes[shape] = (linkShapes[shape] || 0) + 1;
  }
  const stem = `${String(index).padStart(3, '0')}-${Buffer.from(url).toString('base64url').slice(0, 90)}`;
  const responseFile = path.join(dir, `${stem}.html`);
  const domFile = path.join(dir, `${stem}.dom.html`);
  if (responseHTML) fs.writeFileSync(responseFile, responseHTML);
  fs.writeFileSync(domFile, safe.html);
  const meta = {url, requested_url: requestedURL, title: safe.title, page_type: type, state_id: safe.stateId,
    status, response_html: responseHTML ? path.relative(artifactPathBase, responseFile) : null,
    rendered_dom: path.relative(artifactPathBase, domFile),
    body_length: safe.bodyLength, link_count: safe.links.length, repeated_link_max: safe.repeated, html_length: safe.htmlLength,
    canonical_href: safe.canonicalHref, schema_types: safe.schemaTypes, schema_item_count: safe.schemaCount, link_path_shapes: linkShapes};
  return {meta, page: {url, title: safe.title, page_type: type, links: safe.links, state_id: safe.stateId}};
}

async function main() {
  const runDir = path.resolve(args.run || '');
  if (!args.run) throw new Error('required_argument:--run');
  const measurement = args.measurement || 'main';
  if (!['main', 'network_recovery'].includes(measurement)) throw new Error('measurement_must_be_main_or_network_recovery');
  const targetsPath = path.resolve(args.targets || path.join(runDir, 'targets.jsonl'));
  const ids = new Set(String(args.ids || '').split(',').filter(Boolean));
  const methods = String(args.methods || 'B1,C1').split(',').filter(Boolean);
  if (methods.some(x => !['B0', 'B1', 'C1'].includes(x))) throw new Error('methods_must_be_B0_B1_C1');
  if (measurement === 'network_recovery' && methods.some(method => !['B1', 'C1'].includes(method)))
    throw new Error('network_recovery_supports_only_B1_C1');
  const targets = jsonl(targetsPath).filter(x => !ids.size || ids.has(String(x.id)));
  if (!targets.length) throw new Error('no_targets_selected');
  const phase = args.phase || 'both';
  if (!['cold', 'warm', 'both'].includes(phase)) throw new Error('phase_must_be_cold_warm_or_both');
  for (const target of targets) {
    for (const method of methods) {
      if (method === 'B0') {
        if (phase === 'warm') continue;
        const result = await runOne(target, method, 'cold', runDir, args.resume === 'true');
        if (result.status !== 'already_complete') continue;
      } else {
        const modes = phase === 'both' ? ['cold', 'G', 'R'] : phase === 'warm' ? ['G', 'R'] : ['cold'];
        for (const mode of modes) {
          await runOne(target, method, mode, runDir, args.resume === 'true');
        }
      }
    }
  }
}
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url)
  main().catch(error => { console.error(JSON.stringify({error: error.message})); process.exitCode = 1; });
