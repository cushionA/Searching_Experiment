import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {discoverSitemapUrls} from './sitemap-discovery.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const rows = file => fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map(JSON.parse) : [];
const sha = data => crypto.createHash('sha256').update(data).digest('hex');
const write = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n'); };
const append = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.appendFileSync(file, JSON.stringify(value) + '\n'); };

export function safeUrl(value, hosts) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password && (!hosts || hosts.has(url.hostname));
  } catch { return false; }
}

export function groupUrls(input) {
  const unique = [...new Set(input.map(row => typeof row === 'string' ? row : row.url).filter(url => safeUrl(url)))].sort();
  const siblingCounts = new Map();
  for (const value of unique) {
    const url = new URL(value);
    const parts = url.pathname.split('/').filter(Boolean);
    const parent = `${url.hostname}/${parts.slice(0, -1).join('/')}`;
    siblingCounts.set(parent, (siblingCounts.get(parent) || 0) + 1);
  }
  const groups = new Map();
  const prefixes = new Map();
  for (const value of unique) {
    const url = new URL(value);
    const parts = url.pathname.split('/').filter(Boolean);
    const parent = `${url.hostname}/${parts.slice(0, -1).join('/')}`;
    const segments = parts.map((part, index) => {
      if (/^\d+$/.test(part)) return ':number';
      if (/^[\da-f]{8}-[\da-f-]{27,}$/i.test(part)) return ':segment';
      if (index === parts.length - 1 && siblingCounts.get(parent) >= 3 && !/^index\.[a-z]+$/i.test(part)) {
        const extension = part.match(/\.([a-z0-9]{1,8})$/i);
        return extension ? `:slug.${extension[1]}` : ':segment';
      }
      return part;
    });
    const pattern = {hostname: url.hostname, segments, trailing_slash: url.pathname.endsWith('/'), query_keys: [...new Set(url.searchParams.keys())].sort()};
    const key = JSON.stringify(pattern);
    if (!groups.has(key)) groups.set(key, {pattern, urls: []});
    groups.get(key).urls.push(value);
    for (let depth = 0; depth < parts.length; depth++) {
      const pathname = '/' + parts.slice(0, depth).join('/');
      const candidate = new URL(pathname, url.origin).href;
      if (!prefixes.has(candidate)) prefixes.set(candidate, {url: candidate, count: 0});
      prefixes.get(candidate).count++;
    }
  }
  const grouped = [...groups.values()].sort((a, b) => b.urls.length - a.urls.length || JSON.stringify(a.pattern).localeCompare(JSON.stringify(b.pattern)));
  const result = grouped.map((group, index) => ({id: `g${index + 1}`, pattern: group.pattern, count: group.urls.length,
    representatives: [group.urls[0], group.urls[Math.floor(group.urls.length / 2)], group.urls.at(-1)].filter((value, index, all) => all.indexOf(value) === index)}));
  const known = new Set(unique);
  for (const value of unique) {
    const url = new URL(value);
    const parts = url.pathname.split('/').filter(Boolean);
    const parent = `${url.hostname}/${parts.slice(0, -1).join('/')}`;
    if (parts.length <= 2 && siblingCounts.get(parent) <= 30 && !prefixes.has(value)) prefixes.set(value, {url: value, count: 1});
  }
  const hubs = [...prefixes.values()].map(row => ({...row, source: known.has(row.url) ? 'sitemap' : 'derived_prefix'}))
    .sort((a, b) => b.count - a.count || a.url.length - b.url.length || a.url.localeCompare(b.url));
  return {unique_urls: unique.length, groups: result, hub_candidates: hubs};
}

export function buildRequest(target, inventory) {
  const grouped = groupUrls(inventory.urls);
  const scope = target.practice ? ' Training/demo scope is the supplied entry URL. Avoid login, carts, purchases and registration.' : '';
  const request = {request_id: `sitemap-url-choice-${target.id}`, task: `${target.tasks.join(' / ')}. Find item-detail and listing/hub candidates for this task, preferring lists that reveal more links by scrolling.${scope}`,
    user_supplied_entry_scope: target.start_url,
    site_id: String(target.id), domain: target.domain, url_count: grouped.unique_urls, total_groups: grouped.groups.length,
    sitemap_partial: !!inventory.truncated, groups: grouped.groups.slice(0, 32), hub_candidates: grouped.hub_candidates.slice(0, 28),
    observations: 'URLs and counts only. No HTML, link edges, correct routes or evaluator targets are supplied. A derived prefix is a hypothesis, not a known page or link.',
    response_format: {request_id: 'same request id', item_group_ids: ['up to 2 existing group IDs'], listing_urls: ['up to 3 URLs copied exactly from representatives or hub_candidates'], hub_urls: ['up to 2 URLs copied exactly from representatives or hub_candidates'], reason: 'brief URL-based hypothesis', insufficient_evidence: false}};
  while (JSON.stringify(request).length > 14000 && request.groups.length > 4) request.groups.pop();
  request.omitted_groups = grouped.groups.length - request.groups.length;
  return {request, grouped};
}

export async function runBoundedPool(items, concurrency, worker) {
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 4) throw new Error('invalid_site_concurrency');
  const results = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({length: Math.min(concurrency, items.length)}, async () => {
    while (true) {
      const index = next++;
      if (index >= items.length) return;
      results[index] = await worker(items[index], index);
    }
  }));
  return results;
}

export function coldWarmSkipReason(coldResult) {
  if (!coldResult) return null;
  if (coldResult.status === 'access_stopped' || coldResult.status === 'error' || coldResult.stop_reason === 'error' || coldResult.refusal) return 'cold_access_or_error';
  if ((coldResult.selected_items || []).some(item => {
    const reason = item.basic_html_check?.reason || '';
    return reason === 'blocked_or_login' || reason === 'error_page' || /^http_(?!200$)\d+$/.test(reason);
  })) return 'cold_access_or_error';
  return null;
}

export function validateAnswer(request, answer) {
  if (answer.request_id !== request.request_id || !Array.isArray(answer.item_group_ids) || !Array.isArray(answer.listing_urls) || !Array.isArray(answer.hub_urls)) throw new Error('invalid_agent_answer_schema');
  const ids = new Set(request.groups.map(group => group.id));
  const urls = new Set([...request.groups.flatMap(group => group.representatives), ...request.hub_candidates.map(row => row.url)]);
  if (answer.item_group_ids.length > 2 || answer.listing_urls.length > 3 || answer.hub_urls.length > 2 || answer.item_group_ids.some(id => !ids.has(id)) || [...answer.listing_urls, ...answer.hub_urls].some(url => !urls.has(url))) throw new Error('agent_selected_unpresented_candidate');
  if (typeof answer.insufficient_evidence !== 'boolean' || typeof answer.reason !== 'string' || answer.reason.length > 1200) throw new Error('invalid_agent_answer_fields');
  const source = url => request.hub_candidates.find(row => row.url === url)?.source || 'sitemap';
  return {listing_candidates: answer.listing_urls.map((url, index) => ({id: `listing-${index + 1}`, url, source: source(url)})), hub_candidates: answer.hub_urls.map((url, index) => ({id: `hub-${index + 1}`, url, source: source(url)})),
    item_groups: request.groups.filter(group => answer.item_group_ids.includes(group.id)), max_explore_documents: 6, max_scroll_actions: 6, max_items: 3};
}

export function reconcileCompletedPhase(runDir, target, phase) {
  const file = path.join(runDir, 'sites', String(target.id), phase, 'result.json');
  if (!fs.existsSync(file)) return false;
  const outcome = read(file);
  if (String(outcome.id) !== String(target.id) || outcome.phase !== phase) throw new Error('phase_result_identity_mismatch');
  const summary = path.join(runDir, 'results.jsonl');
  if (!rows(summary).some(row => String(row.id) === String(target.id) && row.phase === phase)) append(summary, outcome);
  return true;
}

export function recoverInterruptedPhase(runDir, target, phase) {
  const directory = path.resolve(runDir, 'artifacts', String(target.id), 'sitemap-agent-browser', phase);
  if (!fs.existsSync(directory) || fs.existsSync(path.join(runDir, 'sites', String(target.id), phase, 'result.json'))) return null;
  const progressRows = rows(path.join(directory, 'progress.jsonl'));
  const reservations = rows(path.join(directory, 'document-reservations.jsonl'));
  const attemptsFile = path.join(runDir, 'attempts.jsonl');
  const attempt = rows(attemptsFile).filter(row => String(row.id) === String(target.id) && row.phase === phase).length + 1;
  const preserved = path.resolve(path.dirname(directory), `${phase}-interrupted-${attempt}`);
  const root = path.resolve(runDir);
  for (const file of [directory, preserved]) if (path.relative(root, file).startsWith('..') || path.isAbsolute(path.relative(root, file))) throw new Error('interrupted_artifact_out_of_scope');
  if (fs.existsSync(preserved)) throw new Error('interrupted_artifact_already_preserved');
  fs.renameSync(directory, preserved);
  const record = {id: String(target.id), phase, attempt, status: 'recovered_interrupted_phase', partial_evidence: path.relative(runDir, preserved),
    document_requests: null, document_requests_reserved_upper_bound: reservations.length || (progressRows.length ? progressRows[0].document_cap ?? 9 : 9),
    http_requests: null, xhr_fetch: null, browser_ms: null, recovered_at: new Date().toISOString()};
  append(attemptsFile, record);
  return record;
}

export async function discover(runDir, target, protocol) {
  const directory = path.join(runDir, 'sites', String(target.id));
  const file = path.join(directory, 'inventory.json');
  if (fs.existsSync(file)) return {id: target.id, status: 'already_recorded', urls: read(file).urls.length};
  fs.mkdirSync(directory, {recursive: true});
  const hosts = new Set(target.allowed_hosts);
  const requestFile = path.join(directory, 'metadata-requests.jsonl');
  const responseFile = path.join(directory, 'metadata-responses.jsonl');
  const requestRows = readLedger(requestFile);
  const responseRows = readLedger(responseFile);
  const priorResponseRows = responseRows.length;
  const priorRequests = Math.max(requestRows.length, ...requestRows.map(row => Number(row.sequence) || 0), 0);
  let cacheReads = 0;
  const priorBytes = responseRows.reduce((sum, row) => sum + (Number(row.bytes) || 0), 0);
  const cost = {requests: priorRequests, prior_requests: priorRequests, new_http_requests: 0, cache_reads: 0,
    responses: responseRows.length, responses_exact: responseRows.length === priorRequests, bytes: priorBytes,
    bytes_exact: responseRows.length === priorRequests && responseRows.every(row => Number.isFinite(Number(row.bytes))), elapsed_ms: 0};
  const started = Date.now();
  let lastAt = 0;
  const fetchMetadata = async (url, options) => {
    for (let redirect = 0; redirect < 4; redirect++) {
      if (!safeUrl(url, hosts)) throw new Error('metadata_out_of_scope');
      const cached = cachedMetadataResponse(directory, responseRows, url);
      let response = cached;
      if (response && response.status >= 300 && response.status < 400 && !response.headers.get('location')) response = null;
      if (response) cacheReads++;
      else {
        if (cost.requests >= protocol.metadata_requests_per_site) throw new Error('metadata_request_limit');
        if (lastAt) await new Promise(resolve => setTimeout(resolve, Math.max(0, (target.practice ? 1000 : 5000) - (Date.now() - lastAt))));
        lastAt = Date.now();
        const sequence = ++cost.requests;
        cost.new_http_requests++;
        append(requestFile, {sequence, url, reserved_at: new Date().toISOString()});
        const signal = options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(20000)]) : AbortSignal.timeout(20000);
        response = await fetch(url, {...options, redirect: 'manual', signal});
        cost.responses++;
        const chunks = [];
        let size = 0;
        for await (const chunk of response.body || []) {
          size += chunk.length;
          if (size > protocol.metadata_bytes_per_response) throw new Error('metadata_body_limit');
          chunks.push(Buffer.from(chunk));
        }
        const body = Buffer.concat(chunks);
        cost.bytes += body.length;
        const relative = `metadata/${sequence}.body`;
        fs.mkdirSync(path.join(directory, 'metadata'), {recursive: true});
        fs.writeFileSync(path.join(directory, relative), body);
        const record = {sequence, url, status: response.status, bytes: body.length, file: relative, sha256: sha(body),
          content_type: response.headers.get('content-type'), location: response.headers.get('location'), content_encoding: response.headers.get('content-encoding')};
        append(responseFile, record);
        responseRows.push(record);
        response = {status: response.status, headers: response.headers, arrayBuffer: async () => body};
      }
      cost.cache_reads = cacheReads;
      if ([301, 302, 303, 307, 308].includes(response.status) && response.headers.get('location')) { url = new URL(response.headers.get('location'), url).href; continue; }
      return response;
    }
    throw new Error('metadata_redirect_limit');
  };
  const inventory = await discoverSitemapUrls({startUrl: target.start_url, allowedHosts: target.allowed_hosts, fetch: fetchMetadata, robotsPolicy: protocol.robots_policy || 'enforce',
    limits: {documents: protocol.sitemap_documents_per_site, urls: 20000, bytes: protocol.metadata_bytes_per_response, elapsedMs: 90000}});
  cost.elapsed_ms = Date.now() - started;
  cost.cache_reads = cacheReads;
  const currentResponses = responseRows.length - priorResponseRows;
  if (currentResponses !== cost.new_http_requests) cost.responses_exact = false;
  if (priorRequests !== responseRows.length || currentResponses !== cost.new_http_requests || !responseRows.every(row => Number.isFinite(Number(row.bytes)))) cost.bytes_exact = false;
  write(file, {...inventory, acquisition: priorRequests ? 'resumed' : 'fresh', acquisition_cost: cost});
  return {id: target.id, status: inventory.urls.length ? 'discovered' : 'no_urls', urls: inventory.urls.length, requests: cost.requests};
}

function readLedger(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).flatMap(line => {
    try { return [JSON.parse(line)]; } catch { return []; }
  });
}

export function cachedMetadataResponse(directory, responseRows, url) {
  const row = [...responseRows].reverse().find(value => value.url === url);
  if (!row?.file || !row.sha256 || !Number.isInteger(Number(row.status))) return null;
  const file = path.resolve(directory, row.file);
  const relative = path.relative(directory, file);
  if (relative.startsWith('..') || path.isAbsolute(relative) || !fs.existsSync(file)) return null;
  const body = fs.readFileSync(file);
  if (sha(body) !== row.sha256) return null;
  const headers = new Map();
  if (row.content_type) headers.set('content-type', row.content_type);
  if (row.location) headers.set('location', row.location);
  if (row.content_encoding) headers.set('content-encoding', row.content_encoding);
  return {status: Number(row.status), headers: {get: name => headers.get(String(name).toLowerCase()) || null}, arrayBuffer: async () => body};
}

function verify(runDir) {
  const targets = rows(path.join(runDir, 'targets.jsonl'));
  const resultRows = rows(path.join(runDir, 'results.jsonl'));
  const tuples = new Set();
  const failures = [];
  let checkedFiles = 0;
  let verifiedHtml = 0;
  let documents = 0;
  let abortedDocumentUpperBound = 0;
  const documentsBySite = new Map();
  const protocol = read(path.join(runDir, 'protocol.json'));
  const sharedPriorDocuments = protocol.prior_shared_browser_document_reservations_upper_bound ?? 0;
  for (const [id, count] of Object.entries(protocol.prior_shared_site_document_reservations || {})) documentsBySite.set(String(id), count);
  for (const attempt of rows(path.join(runDir, 'attempts.jsonl'))) {
    if (attempt.document_requests == null && attempt.document_requests_reserved_upper_bound == null) failures.push(`unknown_attempt_document_cost:${attempt.id}:${attempt.phase}`);
    const count = attempt.document_requests ?? attempt.document_requests_reserved_upper_bound ?? 0;
    abortedDocumentUpperBound += count;
    documentsBySite.set(String(attempt.id), (documentsBySite.get(String(attempt.id)) || 0) + count);
  }
  for (const result of resultRows) {
    const key = `${result.id}|${result.phase}`;
    if (tuples.has(key)) failures.push(`duplicate:${key}`);
    tuples.add(key);
    const count = result.budget?.document_reserved ?? result.cost?.document_requests ?? 0;
    documents += count;
    documentsBySite.set(String(result.id), (documentsBySite.get(String(result.id)) || 0) + count);
    if (result.external_llm_calls !== 0 || result.local_inference?.calls !== 0) failures.push(`unexpected_or_unreported_model_usage:${key}`);
    for (const item of result.selected_items || []) {
      for (const [field, hashField] of [['source_html', 'source_sha256'], ['rendered_dom', 'rendered_sha256']]) {
        if (!item[field]) continue;
        const file = path.resolve(runDir, item[field]);
        const relative = path.relative(runDir, file);
        if (relative.startsWith('..') || path.isAbsolute(relative) || !fs.existsSync(file)) { failures.push(`missing_html:${key}:${field}`); continue; }
        if (item[hashField] && sha(fs.readFileSync(file)) !== item[hashField]) failures.push(`html_hash_mismatch:${key}:${field}`);
        verifiedHtml++;
      }
    }
  }
  if (documents + abortedDocumentUpperBound + sharedPriorDocuments > protocol.total_browser_documents_limit) failures.push('global_document_cap');
  for (const target of targets) if ((documentsBySite.get(String(target.id)) || 0) > (target.practice ? protocol.practice_document_cap_cold_warm : protocol.real_document_cap_cold_warm)) failures.push(`site_document_cap:${target.id}`);
  const candidates = [];
  const walk = directory => { for (const entry of fs.readdirSync(directory, {withFileTypes: true})) { const file = path.join(directory, entry.name); if (entry.isDirectory()) walk(file); else if (entry.name.endsWith('.json')) candidates.push(file); } };
  walk(path.join(runDir, 'sites'));
  for (const file of candidates) {
    const value = read(file);
    if (file.endsWith('request.json')) {
      if (value.groups?.some(group => group.html || group.body || group.text) || value.html || value.page_text) failures.push(`non_url_agent_input:${file}`);
      checkedFiles++;
    }
  }
  return {ok: failures.length === 0, sites: targets.length, result_tuples: tuples.size, url_only_requests_checked: checkedFiles, saved_html_files_verified: verifiedHtml, completed_browser_document_requests: documents, aborted_document_reservations_upper_bound: abortedDocumentUpperBound, shared_prior_document_reservations_upper_bound: sharedPriorDocuments, accounted_document_requests_upper_bound: documents + abortedDocumentUpperBound + sharedPriorDocuments, robots_policy: protocol.robots_policy || 'enforce', failures};
}

async function main() {
  const arguments_ = process.argv.slice(2);
  const args = Object.fromEntries(arguments_.flatMap((value, index) => value.startsWith('--') ? [[value.slice(2), arguments_[index + 1]?.startsWith('--') || !arguments_[index + 1] ? true : arguments_[index + 1]]] : []));
  const runDir = path.resolve(String(args.run));
  const protocol = read(path.join(runDir, 'protocol.json'));
  const concurrency = protocol.max_concurrent_sites ?? 4;
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 4) throw new Error('invalid_site_concurrency');
  if (!['enforce', 'sitemap_discovery_only'].includes(protocol.robots_policy || 'enforce')) throw new Error('unknown_robots_policy');
  if (!Number.isSafeInteger(protocol.prior_shared_browser_document_reservations_upper_bound ?? 0) || (protocol.prior_shared_browser_document_reservations_upper_bound ?? 0) < 0) throw new Error('invalid_prior_document_reservations');
  if (Object.values(protocol.prior_shared_site_document_reservations || {}).some(count => !Number.isSafeInteger(count) || count < 0)) throw new Error('invalid_prior_site_document_reservations');
  if (fs.existsSync(path.join(runDir, 'state.json'))) throw new Error('grounding_run_not_supported');
  const selected = new Set(String(args.ids || '').split(',').filter(Boolean));
  const targets = rows(path.join(runDir, 'targets.jsonl')).filter(row => !selected.size || selected.has(String(row.id)));
  let result;
  if (args.action === 'discover') {
    if (!args.live) throw new Error('live_flag_required');
    const outcomes = await runBoundedPool(targets, concurrency, target => discover(runDir, target, protocol));
    result = {action: 'discover', outcomes};
  } else if (args.action === 'prepare') {
    const outcomes = [];
    for (const target of targets) {
      const directory = path.join(runDir, 'sites', String(target.id));
      const inventory = read(path.join(directory, 'inventory.json'));
      if (fs.existsSync(path.join(directory, 'request.json'))) { outcomes.push({id: target.id, status: 'already_prepared'}); continue; }
      const {request, grouped} = buildRequest(target, inventory);
      write(path.join(directory, 'groups.json'), grouped);
      if (inventory.urls.length) fs.writeFileSync(path.join(directory, 'request.json'), JSON.stringify(request) + '\n');
      write(path.join(directory, 'preparation.json'), {urls: grouped.unique_urls, groups: grouped.groups.length, presented_groups: request.groups.length, request_bytes: Buffer.byteLength(JSON.stringify(request)), native_input_tokens: null, external_llm_calls: 0, model_runtime_verified: false});
      outcomes.push({id: target.id, urls: inventory.urls.length, groups: grouped.groups.length, request_chars: JSON.stringify(request).length});
    }
    result = {action: 'prepare', outcomes};
  } else if (args.action === 'execute') {
    if (!args.live) throw new Error('live_flag_required');
    const {executeRoute} = await import('./sitemap-agent-browser.mjs');
    const phase = String(args.phase || 'cold');
    const outcomes = await runBoundedPool(targets, concurrency, async target => {
      const directory = path.join(runDir, 'sites', String(target.id));
      try {
        if (reconcileCompletedPhase(runDir, target, phase)) return {id: target.id, phase, status: 'already_recorded'};
        if (phase === 'warm') {
          const cold = fs.existsSync(path.join(directory, 'cold', 'result.json')) ? read(path.join(directory, 'cold', 'result.json')) : null;
          const reason = coldWarmSkipReason(cold);
          if (reason) return {id: target.id, phase, status: 'skipped', reason};
        }
        if (!fs.existsSync(path.join(directory, 'answer.json'))) return {id: target.id, phase, status: 'no_agent_answer'};
        const request = read(path.join(directory, 'request.json'));
        const answer = read(path.join(directory, 'answer.json'));
        const plan = validateAnswer(request, answer);
        write(path.join(directory, 'plan.json'), plan);
        recoverInterruptedPhase(runDir, target, phase);
        const priorDocuments = (protocol.prior_shared_site_document_reservations?.[String(target.id)] ?? 0) + rows(path.join(runDir, 'attempts.jsonl')).filter(row => String(row.id) === String(target.id)).reduce((sum, row) => sum + (row.document_requests ?? row.document_requests_reserved_upper_bound ?? 0), 0);
        const outcome = await executeRoute({runDir, target, plan, phase, options: {protocol, robots_policy: protocol.robots_policy || 'enforce', executablePath: protocol.chrome_executable, prior_document_requests: priorDocuments}});
        append(path.join(runDir, 'results.jsonl'), outcome);
        return {id: target.id, phase, status: outcome.status, items: outcome.selected_items?.length ?? null, candidate_html_success: outcome.task_success ?? null, candidate_scroll_growth: outcome.scroll_growth_success ?? null};
      } catch (error) {
        const partial = path.join(runDir, 'artifacts', String(target.id), 'sitemap-agent-browser', phase);
        const progressFile = path.join(partial, 'progress.jsonl');
        const ledgerFile = path.join(partial, 'document-reservations.jsonl');
        const hasPartial = fs.existsSync(partial);
        const resultFile = path.join(directory, phase, 'result.json');
        const completed = fs.existsSync(resultFile) ? read(resultFile) : null;
        const reservations = fs.existsSync(ledgerFile) ? rows(ledgerFile).length : completed?.budget?.document_reserved ?? (hasPartial ? null : 0);
        const attemptsFile = path.join(runDir, 'attempts.jsonl');
        const attempt = rows(attemptsFile).filter(row => String(row.id) === String(target.id) && row.phase === phase).length + 1;
        append(attemptsFile, {id: String(target.id), phase, attempt, status: 'site_execution_error', partial_evidence: hasPartial ? path.relative(runDir, partial) : null,
          document_requests: null, document_requests_reserved_upper_bound: reservations, http_requests: null, xhr_fetch: null, browser_ms: null,
          error: String(error?.message || error).slice(0, 500), progress_available: fs.existsSync(progressFile)});
        return {id: target.id, phase, status: 'error', error: String(error?.message || error).slice(0, 500), partial_evidence: hasPartial ? path.relative(runDir, partial) : null};
      }
    });
    result = {action: 'execute', outcomes};
  } else if (args.action === 'verify') {
    result = verify(runDir);
    write(path.join(runDir, 'verification.json'), result);
  } else if (args.action === 'export') {
    result = verify(runDir);
    if (!result.ok) throw new Error('verification_failed');
    const {execFileSync} = await import('node:child_process');
    const packaged = JSON.parse(execFileSync('python', ['-B', path.join(repo, 'experiments/structure-routing/package.py'), '--run', runDir, '--expected-targets', String(rows(path.join(runDir, 'targets.jsonl')).length), '--output', path.resolve(String(args.output))], {encoding: 'utf8'}));
    result = {...result, ...packaged};
  } else throw new Error('unknown_action');
  console.log(JSON.stringify(result));
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) main().catch(error => { console.error(JSON.stringify({error: error.message})); process.exitCode = 1; });
