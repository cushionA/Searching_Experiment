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
  const request = {request_id: `sitemap-url-choice-${target.id}`, task: `${target.tasks.join(' / ')}. Find item-detail and listing/hub candidates for this task, preferring lists that reveal more links by scrolling. Training/demo scope is the supplied entry URL, not the operator blog, documentation or scraper marketplace. Avoid login, carts, purchases and registration.`,
    user_supplied_entry_scope: target.start_url,
    site_id: String(target.id), domain: target.domain, url_count: grouped.unique_urls, total_groups: grouped.groups.length,
    sitemap_partial: !!inventory.truncated, groups: grouped.groups.slice(0, 32), hub_candidates: grouped.hub_candidates.slice(0, 28),
    observations: 'URLs and counts only. No HTML, link edges, correct routes or evaluator targets are supplied. A derived prefix is a hypothesis, not a known page or link.',
    response_format: {request_id: 'same request id', item_group_ids: ['up to 2 existing group IDs'], listing_urls: ['up to 3 URLs copied exactly from representatives or hub_candidates'], hub_urls: ['up to 2 URLs copied exactly from representatives or hub_candidates'], reason: 'brief URL-based hypothesis', insufficient_evidence: false}};
  while (JSON.stringify(request).length > 14000 && request.groups.length > 4) request.groups.pop();
  request.omitted_groups = grouped.groups.length - request.groups.length;
  return {request, grouped};
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

async function discover(runDir, target, protocol) {
  const directory = path.join(runDir, 'sites', String(target.id));
  const file = path.join(directory, 'inventory.json');
  if (fs.existsSync(file)) return {id: target.id, status: 'already_recorded', urls: read(file).urls.length};
  fs.mkdirSync(directory, {recursive: true});
  const hosts = new Set(target.allowed_hosts);
  const cost = {requests: 0, responses: 0, bytes: 0, elapsed_ms: 0};
  const started = Date.now();
  let lastAt = 0;
  const fetchMetadata = async (url, options) => {
    for (let redirect = 0; redirect < 4; redirect++) {
      if (!safeUrl(url, hosts)) throw new Error('metadata_out_of_scope');
      if (cost.requests >= protocol.metadata_requests_per_site) throw new Error('metadata_request_limit');
      if (lastAt) await new Promise(resolve => setTimeout(resolve, Math.max(0, (target.practice ? 1000 : 5000) - (Date.now() - lastAt))));
      lastAt = Date.now();
      cost.requests++;
      append(path.join(directory, 'metadata-requests.jsonl'), {sequence: cost.requests, url, reserved_at: new Date().toISOString()});
      const signal = options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(20000)]) : AbortSignal.timeout(20000);
      const response = await fetch(url, {...options, redirect: 'manual', signal});
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
      const relative = `metadata/${cost.requests}.body`;
      fs.mkdirSync(path.join(directory, 'metadata'), {recursive: true});
      fs.writeFileSync(path.join(directory, relative), body);
      append(path.join(directory, 'metadata-responses.jsonl'), {sequence: cost.requests, url, status: response.status, bytes: body.length, file: relative, sha256: sha(body), content_type: response.headers.get('content-type')});
      if ([301, 302, 303, 307, 308].includes(response.status) && response.headers.get('location')) { url = new URL(response.headers.get('location'), url).href; continue; }
      return {status: response.status, headers: response.headers, arrayBuffer: async () => body};
    }
    throw new Error('metadata_redirect_limit');
  };
  const inventory = await discoverSitemapUrls({startUrl: target.start_url, allowedHosts: target.allowed_hosts, fetch: fetchMetadata,
    limits: {documents: protocol.sitemap_documents_per_site, urls: 20000, bytes: protocol.metadata_bytes_per_response, elapsedMs: 90000}});
  cost.elapsed_ms = Date.now() - started;
  write(file, {...inventory, acquisition: 'fresh', acquisition_cost: cost});
  return {id: target.id, status: inventory.urls.length ? 'discovered' : 'no_urls', urls: inventory.urls.length, requests: cost.requests};
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
  for (const attempt of rows(path.join(runDir, 'attempts.jsonl'))) {
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
  if (documents + abortedDocumentUpperBound > protocol.total_browser_documents_limit) failures.push('global_document_cap');
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
  return {ok: failures.length === 0, sites: targets.length, result_tuples: tuples.size, url_only_requests_checked: checkedFiles, saved_html_files_verified: verifiedHtml, completed_browser_document_requests: documents, aborted_document_reservations_upper_bound: abortedDocumentUpperBound, accounted_document_requests_upper_bound: documents + abortedDocumentUpperBound, failures};
}

async function main() {
  const arguments_ = process.argv.slice(2);
  const args = Object.fromEntries(arguments_.flatMap((value, index) => value.startsWith('--') ? [[value.slice(2), arguments_[index + 1]?.startsWith('--') || !arguments_[index + 1] ? true : arguments_[index + 1]]] : []));
  const runDir = path.resolve(String(args.run));
  const protocol = read(path.join(runDir, 'protocol.json'));
  if (fs.existsSync(path.join(runDir, 'state.json'))) throw new Error('grounding_run_not_supported');
  const selected = new Set(String(args.ids || '').split(',').filter(Boolean));
  const targets = rows(path.join(runDir, 'targets.jsonl')).filter(row => !selected.size || selected.has(String(row.id)));
  let result;
  if (args.action === 'discover') {
    if (!args.live) throw new Error('live_flag_required');
    const outcomes = [];
    for (let index = 0; index < targets.length; index += 4) outcomes.push(...await Promise.all(targets.slice(index, index + 4).map(target => discover(runDir, target, protocol))));
    result = {action: 'discover', outcomes};
  } else if (args.action === 'prepare') {
    const outcomes = [];
    for (const target of targets) {
      const directory = path.join(runDir, 'sites', String(target.id));
      const inventory = read(path.join(directory, 'inventory.json'));
      if (fs.existsSync(path.join(directory, 'request.json'))) { outcomes.push({id: target.id, status: 'already_prepared'}); continue; }
      const {request, grouped} = buildRequest(target, inventory);
      write(path.join(directory, 'groups.json'), grouped);
      if (inventory.urls.length) write(path.join(directory, 'request.json'), request);
      write(path.join(directory, 'preparation.json'), {urls: grouped.unique_urls, groups: grouped.groups.length, presented_groups: request.groups.length, request_bytes: Buffer.byteLength(JSON.stringify(request)), native_input_tokens: null, external_llm_calls: 0, model_runtime_verified: false});
      outcomes.push({id: target.id, urls: inventory.urls.length, groups: grouped.groups.length, request_chars: JSON.stringify(request).length});
    }
    result = {action: 'prepare', outcomes};
  } else if (args.action === 'execute') {
    if (!args.live) throw new Error('live_flag_required');
    const {executeRoute} = await import('./sitemap-agent-browser.mjs');
    const outcomes = [];
    for (const target of targets) {
      const directory = path.join(runDir, 'sites', String(target.id));
      const phase = String(args.phase || 'cold');
      const resultFile = path.join(directory, phase, 'result.json');
      if (reconcileCompletedPhase(runDir, target, phase)) { outcomes.push({id: target.id, phase, status: 'already_recorded'}); continue; }
      if (!fs.existsSync(path.join(directory, 'answer.json'))) { outcomes.push({id: target.id, phase, status: 'no_agent_answer'}); continue; }
      const request = read(path.join(directory, 'request.json'));
      const answer = read(path.join(directory, 'answer.json'));
      const plan = validateAnswer(request, answer);
      write(path.join(directory, 'plan.json'), plan);
      recoverInterruptedPhase(runDir, target, phase);
      const priorDocuments = rows(path.join(runDir, 'attempts.jsonl')).filter(row => String(row.id) === String(target.id)).reduce((sum, row) => sum + (row.document_requests ?? row.document_requests_reserved_upper_bound ?? 0), 0);
      const outcome = await executeRoute({runDir, target, plan, phase, options: {protocol, executablePath: protocol.chrome_executable, prior_document_requests: priorDocuments}});
      append(path.join(runDir, 'results.jsonl'), outcome);
      outcomes.push({id: target.id, phase, status: outcome.status, items: outcome.selected_items?.length ?? null, candidate_html_success: outcome.task_success ?? null, candidate_scroll_growth: outcome.scroll_growth_success ?? null});
    }
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
