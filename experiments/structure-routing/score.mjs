import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const workspace = path.resolve(here, '../..');
const runDir = path.join(workspace, 'lab-runs/structure-routing-20261115');
const argv = process.argv.slice(2);

function arg(name, fallback = null) {
  return argv.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
}

const candidateRun = path.resolve(arg('candidate-run', ''));
if (!arg('candidate-run')) throw new Error('required argument: --candidate-run=PATH');
const candidateFile = path.resolve(arg('results', path.join(candidateRun, 'results.jsonl')));
const artifactsDir = path.resolve(arg('artifacts', path.join(candidateRun, 'artifacts')));
const goldVersion = arg('gold-version', 'v2');
if (!['v1', 'v2'].includes(goldVersion)) throw new Error(`unknown gold version: ${goldVersion}`);
const goldDir = path.join(runDir, goldVersion === 'v2' ? 'gold-private-v2' : 'gold-private');
const outDir = path.resolve(arg('outdir', path.join(candidateRun, 'score')));
const requestedMethods = arg('methods');
const targets = (await fs.readFile(path.join(runDir, 'targets.jsonl'), 'utf8')).trim().split(/\r?\n/).map(line => JSON.parse(line));
const candidateRows = (await fs.readFile(candidateFile, 'utf8')).trim().split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));

function canonicalUrl(value, removeFragment = false) {
  try {
    const url = new URL(value);
    if (removeFragment) url.hash = '';
    return url.href;
  } catch { return ''; }
}

function normalizedText(value) {
  return String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[\s\p{P}\p{S}]+/gu, '');
}

function editDistance(left, right) {
  const a = Array.from(left);
  const b = Array.from(right);
  let previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= b.length; j += 1) current[j] = Math.min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + Number(a[i - 1] !== b[j - 1]));
    previous = current;
  }
  return previous[b.length];
}

function titleMatches(left, right) {
  const a = normalizedText(left);
  const b = normalizedText(right);
  if (!a || !b) return false;
  if (a === b) return true;
  const length = Math.max(Array.from(a).length, Array.from(b).length);
  return length > 0 && 1 - editDistance(a, b) / length >= 0.9;
}

function expectedTypeMatches(expected, actual) {
  if (expected === 'product_detail') return actual === 'detail';
  if (expected === 'article_or_detail_record' || expected === 'detail_record') return actual === 'detail' || actual === 'content';
  return false;
}

function pageStatus(page) {
  const value = Number(page.status ?? page.http_status);
  return Number.isInteger(value) ? value : null;
}

function pageFinalUrl(page) {
  return canonicalUrl(page.url || page.final_url || page.current_url);
}

function pageUrl(page) {
  return pageFinalUrl(page) || pageRequestedUrl(page);
}

function pageRequestedUrl(page) {
  return canonicalUrl(page.requested_url || page.url || page.final_url || page.current_url);
}

function pagesOf(row) {
  return Array.isArray(row.pages) ? row.pages : [];
}

function countValue(value) {
  if (Number.isFinite(Number(value))) return Number(value);
  if (Array.isArray(value)) return value.length;
  return 0;
}

function safeArtifactPath(relative, id, method) {
  if (!relative || typeof relative !== 'string') return '';
  const base = path.resolve(artifactsDir, id, method);
  const normalizedRelative = relative.replaceAll('\\', '/');
  const runRelative = `artifacts/${id}/${method}/`;
  const candidate = path.isAbsolute(relative) ? path.resolve(relative)
    : normalizedRelative.startsWith(runRelative) ? path.resolve(candidateRun, ...normalizedRelative.split('/'))
      : path.resolve(base, ...normalizedRelative.split('/'));
  const rootPath = path.resolve(artifactsDir);
  return candidate === rootPath || candidate.startsWith(`${rootPath}${path.sep}`) ? candidate : '';
}

async function artifactInfo(relative, id, method) {
  const filePath = safeArtifactPath(relative, id, method);
  if (!filePath) return { exists: false, bytes: 0 };
  try {
    const stat = await fs.stat(filePath);
    return { exists: stat.isFile() && stat.size > 0, bytes: stat.isFile() ? stat.size : 0 };
  } catch { return { exists: false, bytes: 0 }; }
}

function goldTargets(gold) {
  const documents = gold.documents || [];
  const validDocuments = documents.filter(document => document.label?.startsWith('target-') && document.candidate_valid === true);
  return validDocuments.map(document => ({
    requested_url: canonicalUrl(document.requested_url),
    final_url: canonicalUrl(document.final_url),
    title: document.title || '',
    expected_type: document.validation?.expected_type || gold.expected_type || '',
    signature: document.validation || {},
    html_path: document.label ? `${document.label}.source.html` : ''
  })).filter(target => target.requested_url || target.final_url);
}

async function coldObservations(candidateSiteRows) {
  const observed = [];
  for (const row of candidateSiteRows.filter(item => item.mode === 'cold')) {
    for (const page of pagesOf(row)) {
      const artifact = await artifactInfo(page.response_html, row.id, row.method);
      observed.push({
        url: pageFinalUrl(page),
        requested_url: pageRequestedUrl(page),
        title: page.title || '',
        page_type: page.page_type || 'unknown',
        status: pageStatus(page),
        navigation_error: page.navigation_error ?? null,
        html_exists: artifact.exists,
        method: row.method,
        id: row.id
      });
    }
  }
  return observed.filter(page => page.url && page.navigation_error === null && page.status === 200 && page.title && !['unknown', 'block', 'login', 'empty_spa', 'not_found'].includes(page.page_type) && page.html_exists);
}

function mechanismResult(row, rowPages, observed) {
  if (row.mode !== 'G' && row.mode !== 'R') return { success: null, reason: 'not_a_replay_mode', pages_attempted: 0, pages_successful: 0 };
  const provenanceExpected = row.mode === 'G' ? 'saved_cold_graph_paths' : 'frozen_cold_rules';
  if (row.rule_provenance !== provenanceExpected) return { success: false, reason: 'rule_provenance_mismatch', pages_attempted: rowPages.length, pages_successful: 0 };
  const observedUrls = new Set(observed.map(page => page.url));
  if (row.mode === 'G') {
    const matchedPages = rowPages.filter(rowPage => observed.some(source => {
      const requestedMatches = rowPage.requested_url === source.url || rowPage.requested_url === source.requested_url;
      const finalMatches = rowPage.url === source.url;
      return requestedMatches && finalMatches && rowPage.navigation_error === null && titleMatches(source.title, rowPage.title) && rowPage.page_type === source.page_type && rowPage.html_exists && rowPage.status === 200;
    }));
    return { success: matchedPages.length > 0, reason: matchedPages.length ? 'cold_url_title_type_replayed' : 'cold_observation_replay_mismatch', pages_attempted: rowPages.length, pages_successful: matchedPages.length };
  }
  const sourcePages = (row.rule_source_pages || []).map(value => canonicalUrl(value)).filter(Boolean);
  if (!sourcePages.length || sourcePages.some(url => !observedUrls.has(url))) return { success: false, reason: 'rule_source_not_cold_observed', pages_attempted: rowPages.length, pages_successful: 0 };
  const coldUrls = new Set(observed.map(page => page.url));
  const matchedPages = row.trial_type === 'generalization' ? rowPages.filter(page => page.url && page.requested_url && !coldUrls.has(page.url) && !coldUrls.has(page.requested_url) && page.navigation_error === null && page.status === 200 && page.title && page.page_type && !['block', 'login', 'empty_spa', 'unknown', 'not_found'].includes(page.page_type) && page.html_exists) : [];
  return { success: matchedPages.length > 0, reason: matchedPages.length ? 'frozen_rule_reached_unobserved_page' : 'unobserved_page_not_validated', pages_attempted: rowPages.length, pages_successful: matchedPages.length };
}

function failureClass(row, targetPages, failedClaims, mechanism) {
  if (failedClaims > 0) return 'false_success';
  if (targetPages.some(page => page.gold_target_valid)) return 'gold_target_success';
  const status = String(row.status || '').toLowerCase();
  const reason = String(row.stop_reason || '').toLowerCase();
  if (['success', 'complete', 'completed', 'ok'].includes(status)) return 'success_without_gold_target';
  if (/robots/.test(`${status} ${reason}`)) return 'robots_restricted';
  if (/429|rate.?limit/.test(`${status} ${reason}`)) return 'http_429';
  if (/403|denied|blocked|captcha/.test(`${status} ${reason}`)) return 'blocked';
  if (/timeout|timed.?out/.test(`${status} ${reason}`)) return 'timeout';
  if (row.mode === 'G' || row.mode === 'R') return mechanism.success === false ? 'mechanism_failure' : 'replay_failure';
  if (!row.pages?.length) return 'no_page_discovered';
  return 'other_failure';
}

function duplicateCounts(pages) {
  const exact = pages.map(page => pageUrl(page)).filter(Boolean);
  const canonical = pages.map(page => canonicalUrl(page.canonical_url || page.url || page.final_url, true)).filter(Boolean);
  const repeated = values => {
    const seen = new Set();
    let duplicates = 0;
    for (const value of values) {
      if (seen.has(value)) duplicates += 1;
      else seen.add(value);
    }
    return duplicates;
  };
  return { exact_url_duplicates: repeated(exact), canonical_url_duplicates: repeated(canonical) };
}

const goldById = new Map();
for (const target of targets) {
  try { goldById.set(target.id, JSON.parse(await fs.readFile(path.join(goldDir, target.id, 'result.json'), 'utf8'))); }
  catch { goldById.set(target.id, null); }
}

const availableMethods = [...new Set(candidateRows.map(row => row.method).filter(Boolean))];
const methods = requestedMethods ? requestedMethods.split(',').filter(Boolean) : [...new Set([...availableMethods, 'B1', 'C1'])];
const scoreRows = [];
const siteRows = [];
const metricsByMethod = new Map();

for (const method of methods) {
  const methodRows = candidateRows.filter(row => row.method === method);
  const bySite = new Map();
  for (const row of methodRows) {
    if (!bySite.has(row.id)) bySite.set(row.id, []);
    bySite.get(row.id).push(row);
  }
  const methodTotals = {
    method,
    all_sites_denominator: targets.length,
    gold_verified_sites_denominator: 0,
    sites_run: targets.filter(target => bySite.has(target.id)).length,
    sites_unrun: targets.length - targets.filter(target => bySite.has(target.id)).length,
    result_rows: methodRows.length,
    gold_target_denominator: 0,
    gold_targets_reached_unique: 0,
    false_successes: 0,
    mechanism_successes_G: 0,
    mechanism_successes_R: 0,
    mechanism_trial_rows_G: 0,
    mechanism_trial_rows_R: 0,
    mechanism_site_denominator_G: 0,
    mechanism_site_denominator_R: 0,
    mechanism_successful_sites_G: 0,
    mechanism_successful_sites_R: 0,
    mechanism_page_attempts_G: 0,
    mechanism_page_attempts_R: 0,
    mechanism_page_successes_G: 0,
    mechanism_page_successes_R: 0,
    document_requests: 0,
    document_responses: 0,
    http_requests: 0,
    xhr_fetch_requests: 0,
    discovered_documents: 0,
    total_method_documents: 0,
    operations: 0,
    manual_interventions: 0,
    elapsed_ms: 0,
    initial_elapsed_ms: 0,
    replay_elapsed_ms: 0,
    repair_elapsed_ms: 0,
    external_llm_calls: 0,
    local_inference_calls: 0,
    local_inference_input_tokens: 0,
    local_inference_runtime_ms: 0,
    html_bytes: 0,
    http_status_counts: {},
    practice_all_sites_denominator: targets.filter(target => target.practice).length,
    practice_sites_run: 0,
    practice_sites_unrun: 0,
    practice_gold_verified_sites_denominator: 0,
    practice_gold_target_denominator: 0,
    practice_gold_targets_reached_unique: 0,
    failure_classes: {}
  };
  const mechanismSites = { G: new Set(), R: new Set() };
  const mechanismSuccessSites = { G: new Set(), R: new Set() };
  const totalDocumentsBySite = new Map();

  for (const target of targets) {
    const gold = goldById.get(target.id);
    const targetGold = gold ? goldTargets(gold) : [];
    const goldVerified = gold?.gold_status === 'verified' && targetGold.length > 0;
    if (goldVerified) methodTotals.gold_verified_sites_denominator += 1;
    if (goldVerified) methodTotals.gold_target_denominator += targetGold.length;
    const rows = bySite.get(target.id) || [];
    if (target.practice && rows.length) methodTotals.practice_sites_run += 1;
    if (target.practice && goldVerified) methodTotals.practice_gold_verified_sites_denominator += 1;
    if (target.practice && goldVerified) methodTotals.practice_gold_target_denominator += targetGold.length;
    const observed = await coldObservations(rows);
    if (!rows.length) {
      const missing = {
        id: target.id,
        domain: target.domain,
        method,
        mode: null,
        trial_type: null,
        status: 'unrun',
        failure_class: 'unrun',
        gold_status: gold?.gold_status || 'unavailable',
        gold_verified_site_denominator_member: goldVerified,
        gold_target_count: targetGold.length,
        gold_targets_reached: 0,
        false_successes: 0,
        pages: [],
        metrics: {}
      };
      scoreRows.push(missing);
      siteRows.push(missing);
      methodTotals.failure_classes.unrun = (methodTotals.failure_classes.unrun || 0) + 1;
      continue;
    }

    const rowScores = [];
    for (const row of rows) {
      const assessedPages = [];
      for (const page of pagesOf(row)) {
        const status = pageStatus(page);
        const finalUrl = pageFinalUrl(page);
        const requestedUrl = pageRequestedUrl(page);
        const claimed = targetGold.filter(goldTarget => [goldTarget.requested_url, goldTarget.final_url].includes(finalUrl) || [goldTarget.requested_url, goldTarget.final_url].includes(requestedUrl));
        const artifact = await artifactInfo(page.response_html, row.id, method);
        const statusOk = status === 200;
        const titleMatch = claimed.some(goldTarget => titleMatches(goldTarget.title, page.title));
        const typeMatch = claimed.some(goldTarget => expectedTypeMatches(goldTarget.expected_type, page.page_type));
        const navigationError = page.navigation_error ?? null;
        const arrivedTargets = claimed.filter(goldTarget => finalUrl && (finalUrl === goldTarget.final_url || finalUrl === goldTarget.requested_url));
        const urlArrived = arrivedTargets.length > 0;
        const sameTargetTitleAndType = arrivedTargets.some(goldTarget => titleMatches(goldTarget.title, page.title) && expectedTypeMatches(goldTarget.expected_type, page.page_type));
        const goldTargetValid = Boolean(urlArrived && navigationError === null && statusOk && sameTargetTitleAndType && artifact.exists);
        assessedPages.push({
          requested_url: page.requested_url || null,
          final_url: page.url || page.final_url || null,
          status,
          navigation_error: navigationError,
          title: page.title || '',
          page_type: page.page_type || 'unknown',
          gold_url_claim: claimed.length > 0,
          gold_url_arrived: Boolean(urlArrived),
          title_match: titleMatch,
          expected_type_match: typeMatch,
          response_html_exists: artifact.exists,
          response_html_bytes: artifact.bytes,
          gold_target_valid: goldTargetValid,
          gold_target_requested_urls: claimed.map(item => item.requested_url)
        });
      }
      const dupes = duplicateCounts(pagesOf(row));
      const falseSuccesses = assessedPages.filter(page => page.gold_url_claim && !page.gold_target_valid).length;
      const mechanism = mechanismResult(row, assessedPages.map(page => ({ ...page, url: canonicalUrl(page.final_url), page_type: page.page_type, html_exists: page.response_html_exists })), observed);
      const record = {
        id: target.id,
        domain: target.domain,
        method,
        mode: row.mode || null,
        trial_type: row.trial_type || null,
        status: row.status || 'unknown',
        stop_reason: row.stop_reason || null,
        rule_provenance: row.rule_provenance || null,
        gold_status: gold?.gold_status || 'unavailable',
        gold_verified_site_denominator_member: goldVerified,
        gold_target_count: targetGold.length,
        gold_targets_reached: new Set(assessedPages.filter(page => page.gold_target_valid).flatMap(page => page.gold_target_requested_urls)).size,
        false_successes: falseSuccesses,
        mechanism_success: mechanism.success,
        mechanism_reason: mechanism.reason,
        mechanism_pages_attempted: mechanism.pages_attempted,
        mechanism_pages_successful: mechanism.pages_successful,
        ...dupes,
        failure_class: failureClass(row, assessedPages, falseSuccesses, mechanism),
        pages: assessedPages,
        metrics: {
          discovered_documents: countValue(row.discovered_documents),
          document_requests: countValue(row.document_requests),
          document_responses: countValue(row.document_responses),
          total_method_documents: countValue(row.total_method_documents),
          total_method_operations: countValue(row.total_method_operations),
          http_requests: countValue(row.http_requests),
          xhr_fetch_requests: countValue(row.xhr_fetch_requests),
          operations: countValue(row.operations),
          manual_interventions: countValue(row.manual_interventions),
          elapsed_ms: countValue(row.elapsed_ms),
          initial_elapsed_ms: countValue(row.initial_elapsed_ms),
          replay_elapsed_ms: countValue(row.replay_elapsed_ms),
          repair_elapsed_ms: countValue(row.repair_elapsed_ms),
          external_llm_calls: countValue(row.external_llm_calls),
          local_inference_calls: countValue(row.local_inference?.calls),
          local_inference_input_tokens: countValue(row.local_inference?.input_tokens),
          local_inference_runtime_ms: countValue(row.local_inference?.runtime_ms),
          page_html_bytes: assessedPages.reduce((sum, page) => sum + page.response_html_bytes, 0)
        }
      };
      rowScores.push(record);
      scoreRows.push(record);
      falseSuccesses && (methodTotals.false_successes += falseSuccesses);
      if (row.mode === 'G') {
        methodTotals.mechanism_trial_rows_G += 1;
        mechanismSites.G.add(target.id);
        methodTotals.mechanism_page_attempts_G += mechanism.pages_attempted;
        methodTotals.mechanism_page_successes_G += mechanism.pages_successful;
        if (mechanism.success) {
          methodTotals.mechanism_successes_G += 1;
          mechanismSuccessSites.G.add(target.id);
        }
      }
      if (row.mode === 'R') {
        methodTotals.mechanism_trial_rows_R += 1;
        mechanismSites.R.add(target.id);
        methodTotals.mechanism_page_attempts_R += mechanism.pages_attempted;
        methodTotals.mechanism_page_successes_R += mechanism.pages_successful;
        if (mechanism.success) {
          methodTotals.mechanism_successes_R += 1;
          mechanismSuccessSites.R.add(target.id);
        }
      }
      methodTotals.discovered_documents += record.metrics.discovered_documents;
      methodTotals.document_requests += record.metrics.document_requests;
      methodTotals.document_responses += record.metrics.document_responses;
      totalDocumentsBySite.set(target.id, Math.max(totalDocumentsBySite.get(target.id) || 0, record.metrics.total_method_documents));
      methodTotals.operations += record.metrics.operations;
      methodTotals.manual_interventions += record.metrics.manual_interventions;
      methodTotals.http_requests += record.metrics.http_requests;
      methodTotals.xhr_fetch_requests += record.metrics.xhr_fetch_requests;
      methodTotals.elapsed_ms += record.metrics.elapsed_ms;
      methodTotals.initial_elapsed_ms += record.metrics.initial_elapsed_ms;
      methodTotals.replay_elapsed_ms += record.metrics.replay_elapsed_ms;
      methodTotals.repair_elapsed_ms += record.metrics.repair_elapsed_ms;
      methodTotals.external_llm_calls += record.metrics.external_llm_calls;
      methodTotals.local_inference_calls += record.metrics.local_inference_calls;
      methodTotals.local_inference_input_tokens += record.metrics.local_inference_input_tokens;
      methodTotals.local_inference_runtime_ms += record.metrics.local_inference_runtime_ms;
      methodTotals.html_bytes += record.metrics.page_html_bytes;
      for (const page of assessedPages) {
        if (page.status !== null) methodTotals.http_status_counts[String(page.status)] = (methodTotals.http_status_counts[String(page.status)] || 0) + 1;
      }
      methodTotals.failure_classes[record.failure_class] = (methodTotals.failure_classes[record.failure_class] || 0) + 1;
    }
    for (const goldTarget of targetGold) {
      const reached = rowScores.some(row => row.pages.some(page => page.gold_target_valid && page.gold_target_requested_urls.includes(goldTarget.requested_url)));
      if (reached) {
        methodTotals.gold_targets_reached_unique += 1;
        if (target.practice) methodTotals.practice_gold_targets_reached_unique += 1;
      }
    }
    const siteStatus = {
      id: target.id,
      domain: target.domain,
      method,
      gold_status: gold?.gold_status || 'unavailable',
      gold_verified_site_denominator_member: goldVerified,
      gold_target_count: targetGold.length,
      candidate_records: rows.length,
      records_with_gold_target_success: rowScores.filter(row => row.pages.some(page => page.gold_target_valid)).length,
      gold_targets_reached_unique: new Set(rowScores.flatMap(row => row.pages.filter(page => page.gold_target_valid).flatMap(page => page.gold_target_requested_urls))).size,
      false_successes: rowScores.reduce((sum, row) => sum + row.false_successes, 0),
      failure_classes: Object.fromEntries([...new Set(rowScores.map(row => row.failure_class))].map(name => [name, rowScores.filter(row => row.failure_class === name).length]))
    };
    siteRows.push(siteStatus);
  }
  methodTotals.total_method_documents = [...totalDocumentsBySite.values()].reduce((sum, value) => sum + value, 0);
  methodTotals.mechanism_site_denominator_G = mechanismSites.G.size;
  methodTotals.mechanism_site_denominator_R = mechanismSites.R.size;
  methodTotals.mechanism_successful_sites_G = mechanismSuccessSites.G.size;
  methodTotals.mechanism_successful_sites_R = mechanismSuccessSites.R.size;
  methodTotals.practice_sites_unrun = methodTotals.practice_all_sites_denominator - methodTotals.practice_sites_run;
  metricsByMethod.set(method, methodTotals);
}

await fs.mkdir(outDir, { recursive: true });
for (const filename of ['summary.json', 'scores.jsonl', 'site-status.jsonl']) {
  try { await fs.access(path.join(outDir, filename)); throw new Error(`refusing to overwrite ${path.join(outDir, filename)}`); }
  catch (error) { if (error?.code !== 'ENOENT') throw error; }
}
const summary = {
  candidate_results: path.relative(candidateRun, candidateFile),
  gold_version: goldVersion,
  all_sites_denominator: targets.length,
  gold_verified_sites_denominator: [...goldById.values()].filter(gold => gold?.gold_status === 'verified' && goldTargets(gold).length > 0).length,
  methods: [...metricsByMethod.values()].map(metrics => ({
    ...metrics,
    mechanism_denominator_unit: 'site',
    gold_target_coverage: metrics.gold_target_denominator ? metrics.gold_targets_reached_unique / metrics.gold_target_denominator : null,
    practice_gold_target_coverage: metrics.practice_gold_target_denominator ? metrics.practice_gold_targets_reached_unique / metrics.practice_gold_target_denominator : null
  }))
};
await fs.writeFile(path.join(outDir, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`, 'utf8');
await fs.writeFile(path.join(outDir, 'scores.jsonl'), `${scoreRows.map(row => JSON.stringify(row)).join('\n')}\n`, 'utf8');
await fs.writeFile(path.join(outDir, 'site-status.jsonl'), `${siteRows.map(row => JSON.stringify(row)).join('\n')}\n`, 'utf8');
console.log(JSON.stringify({ outDir, score_rows: scoreRows.length, site_rows: siteRows.length, methods }));
