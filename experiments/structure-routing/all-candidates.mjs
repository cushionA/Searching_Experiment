import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
import {acquire} from './sitemap-benchmark.mjs';
import {learnRules, selectLinks} from './runner.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(path.join(repo, '.deps/structure-routing-sitemaps/package.json'));
const {load} = require('cheerio');
const args = Object.fromEntries(process.argv.slice(2).reduce((out, value, index, all) => {
  if (value.startsWith('--')) out.push([value.slice(2), all[index + 1]]);
  return out;
}, []));
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const write = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n'); };
const append = (file, value) => fs.appendFileSync(file, JSON.stringify(value) + '\n');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

export function allowed(value, target) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && target.allowed_hosts.includes(url.hostname) &&
      !url.pathname.split('/').some(part => /^(cart|checkout|order|login|logout|register|account|contact|purchase)$/i.test(part));
  } catch { return false; }
}

export function linksFromDom(html, base) {
  const $ = load(html);
  return $('a[href]').toArray().map(node => {
    const anchor = $(node), parent = anchor.parent();
    let region = 'body';
    for (const ancestor of anchor.parents().toArray()) {
      const current = $(ancestor);
      const marker = `${ancestor.tagName} ${current.attr('role') || ''} ${current.attr('id') || ''} ${current.attr('class') || ''}`;
      if (/\b(nav|header|footer|aside|navigation|breadcrumb|pagination|sidebar)\b/i.test(marker)) { region = 'nav'; break; }
      if (/^(main|article|section)$/i.test(ancestor.tagName)) region = ancestor.tagName;
    }
    try { return {url: new URL(anchor.attr('href'), base).href, text: anchor.text().trim(), parent: {tag: parent[0]?.tagName || 'div', classes: (parent.attr('class') || '').split(/\s+/).filter(Boolean)}, region}; }
    catch { return null; }
  }).filter(Boolean);
}

export function selectedUrls(rule, html, base, observation, target) {
  if (rule.kind === 'bfs' || rule.kind === 'structural') {
    const links = linksFromDom(html, base);
    return (rule.kind === 'bfs' ? links : selectLinks({links}, 'C1', rule.rules, new Set(rule.source_urls), new Set(target.allowed_hosts))).map(link => link.url);
  }
  if (typeof rule.selector !== 'string' || rule.selector.length > 2048) throw new Error('missing_or_oversize_selector');
  if (rule.selector_type === 'xpath' || rule.selector.startsWith('xpath=')) {
    const expression = rule.selector.replace(/^xpath=/, '');
    const program = "import json,sys; from lxml import html; r=html.parse(sys.argv[1]); xs=r.xpath(sys.argv[2]); print(json.dumps([x.get('href') if hasattr(x,'get') else str(x) for x in xs]))";
    const values = JSON.parse(execFileSync('python', ['-B', '-c', program, observation.rendered_file_absolute, expression], {encoding: 'utf8', windowsHide: true, timeout: 15000}));
    return values.filter(Boolean).map(value => new URL(value, base).href);
  }
  const $ = load(html);
  return $(rule.selector).toArray().map(node => $(node).attr(rule.attribute || 'href')).filter(Boolean).map(value => new URL(value, base).href);
}

function costs(rows, options, started) {
  const sum = (key, sub) => rows.reduce((total, row) => total + Number(sub ? row.network?.[key] || 0 : row[key] || 0), 0);
  return {document_requests: sum('document_requests'), document_responses: sum('document_responses'),
    html_http_requests: sum('requests', true), xhr_fetch: sum('xhrFetch', true), response_bytes: sum('bytes', true),
    robots: {...options.metadata}, all_http_requests: sum('requests', true) + options.metadata.requests,
    operations: sum('operations', true), acquire_ms: sum('elapsed_ms'), elapsed_ms: Date.now() - started};
}

function optionsFor(target) {
  return {target, executablePath: args.executable || process.env.CHROME_PATH, robots: new Map(), metadata: {requests: 0, responses: 0, bytes: 0, lastAt: 0}};
}

async function collect(target, url, directory, method, phase, options, counter) {
  await delay(1000);
  const observation = await acquire(target, url, directory, method, phase, options, counter);
  if (!observation) throw new Error('acquisition_limit');
  return observation;
}

function stopped(observation) {
  return observation.robots?.reason || ['blocked', 'blocked_or_login', 'maintenance', 'http_401', 'http_403', 'http_429', 'robots_disallowed', 'robots_refused'].includes(observation.page_classification);
}

async function freezeGold(run, manifest) {
  const directory = path.join(run, 'evaluation-private');
  const output = path.join(directory, 'frozen-gold.json');
  if (fs.existsSync(output)) throw new Error('gold_already_frozen');
  const patterns = { '01': {selector: 'article.product_pod h3 a[href]'}, '08': {selector: 'a[href]', href_regex: '^https://sandbox\\.oxylabs\\.io/products/[0-9]+$'}};
  write(path.join(directory, 'selection-protocol.json'), {fixed_before_acquisition: true, selection: 'First three distinct matching links in DOM order. Evaluator-only patterns; never sent to models.', patterns, human_evaluator_patterns: 2});
  const gold = {created_at: new Date().toISOString(), sites: {}};
  for (const target of manifest.cases) {
    const started = Date.now(), options = optionsFor(target), counter = {documents: 0, limit: 12, lastAt: 0}, observations = [];
    const listing = await collect(target, target.heldout_listing_url, directory, 'evaluator', 'listing', options, counter);
    observations.push(listing);
    const targets = [];
    if (!stopped(listing) && listing.rendered_dom && listing.page_valid) {
      const html = fs.readFileSync(path.join(directory, listing.rendered_dom), 'utf8');
      const rule = patterns[target.id];
      const candidates = selectedUrls({...rule, kind: 'selector'}, html, listing.final_url, listing, target);
      const urls = [...new Set(candidates)].filter(url => allowed(url, target) && (!rule.href_regex || new RegExp(rule.href_regex).test(url))).slice(0, 3);
      for (const url of urls) {
        const observation = await collect(target, url, directory, 'evaluator', 'targets', options, counter);
        observations.push(observation);
        targets.push({url, title: observation.title?.split('|')[0].trim(), headings: observation.headings, candidate_valid: observation.page_valid === true, source_html: observation.source_html, rendered_dom: observation.rendered_dom, source_sha256: observation.source_sha256, status: observation.status});
        if (stopped(observation)) break;
      }
    }
    gold.sites[target.id] = {cohort: 'body_verified', expected_type: 'product_detail', targets, fixed_denominator: 3, listing_url: target.heldout_listing_url, listing, acquisition_cost: costs(observations, options, started)};
  }
  write(output, gold);
  const protocol = read(path.join(run, 'protocol.json'));
  protocol.gold_frozen = true;
  protocol.gold_sha256 = sha(fs.readFileSync(output));
  write(path.join(run, 'protocol.json'), protocol);
  console.log(JSON.stringify({status: 'gold_frozen', sites: Object.fromEntries(Object.entries(gold.sites).map(([id, site]) => [id, {targets: site.targets.length, verified: site.targets.filter(t => t.candidate_valid).length, cost: site.acquisition_cost}]))}));
}

function makeBaselines(run, manifest) {
  for (const target of manifest.cases) {
    const sourcePages = target.seeds.map(seed => ({url: seed.url, links: linksFromDom(fs.readFileSync(path.join(repo, seed.dom), 'utf8'), seed.url)}));
    for (const method of ['B1', 'C1']) write(path.join(run, 'rules', method, `${target.id}.json`), {kind: method === 'B1' ? 'bfs' : 'structural', source_urls: sourcePages.map(page => page.url), rules: method === 'C1' ? learnRules(sourcePages) : null, representative_pages: sourcePages.length, local_inference: {calls: 0}, external_api_calls: 0});
  }
}

async function replay(run, manifest) {
  if (!read(path.join(run, 'protocol.json')).gold_frozen) throw new Error('independent_gold_not_frozen');
  if (!/^[a-zA-Z0-9_-]+$/.test(args.candidate || '')) throw new Error('invalid_candidate_name');
  const target = manifest.cases.find(item => item.id === args.case);
  if (!target || !args.rule) throw new Error('required_case_and_rule');
  const candidate = args.candidate, resultFile = path.join(run, 'artifacts', target.id, candidate, 'warm', 'result.json');
  if (fs.existsSync(resultFile)) throw new Error('replay_already_completed');
  const ruleFile = path.resolve(args.rule), rule = read(ruleFile), started = Date.now();
  const options = optionsFor(target), counter = {documents: 0, limit: 12, lastAt: 0}, observations = [];
  let urls = [], error = null;
  try {
    const listing = await collect(target, target.heldout_listing_url, run, candidate, 'warm', options, counter);
    observations.push(listing);
    if (!stopped(listing) && listing.page_valid) {
      const domFile = path.join(run, listing.rendered_dom);
      const html = fs.readFileSync(domFile, 'utf8');
      urls = selectedUrls(rule, html, listing.final_url, {...listing, rendered_file_absolute: domFile}, target);
      const sourceUrls = new Set([target.heldout_listing_url, ...(rule.source_urls || target.seeds.map(seed => seed.url))]);
      urls = [...new Set(urls)].filter(url => allowed(url, target) && !sourceUrls.has(url)).slice(0, 3);
      for (const url of urls) {
        const observation = await collect(target, url, run, candidate, 'warm', options, counter);
        observations.push(observation);
        if (stopped(observation)) break;
      }
    }
  } catch (problem) { error = {type: problem.name, message: String(problem.message).slice(0, 500)}; }
  const result = {id: target.id, domain: target.domain, method: candidate, phase: 'warm', status: error ? 'execution_error' : observations.some(row => stopped(row)) ? 'access_stopped' : urls.length ? 'html_collected' : 'no_rule_matches', task: target.task, observations, selected_urls: urls, rule_file: path.relative(run, ruleFile), rule_sha256: sha(fs.readFileSync(ruleFile)), candidate_gold_input: false, source_html_cache_counted_as_acquisition: false, local_inference: {calls: 0, input_tokens: 0, runtime_ms: 0}, external_llm_calls: 0, external_embedding_tokens: 0, error, cost: costs(observations, options, started), source_sha256: sha(fs.readFileSync(fileURLToPath(import.meta.url)))};
  write(resultFile, result);
  append(path.join(run, 'results.jsonl'), result);
  console.log(JSON.stringify({id: target.id, method: candidate, status: result.status, targets: urls.length, valid_saved: observations.filter(row => row.page_valid).length, cost: result.cost}));
}

async function main() {
  const run = path.resolve(args.run || 'lab-runs/structure-routing-all-candidates-20261115');
  const manifest = read(path.join(run, 'common-inputs/manifest.json'));
  if (args.mode === 'baselines') return makeBaselines(run, manifest);
  if (args.live !== 'true') throw new Error('explicit_live_true_required');
  if (args.mode === 'freeze-gold') return freezeGold(run, manifest);
  if (args.mode === 'replay') return replay(run, manifest);
  throw new Error('mode_must_be_baselines_freeze_gold_or_replay');
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url)
  main().catch(error => { console.error(JSON.stringify({type: error.name, error: error.message})); process.exitCode = 1; });
