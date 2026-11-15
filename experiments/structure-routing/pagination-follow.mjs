import fs from 'node:fs';
import path from 'node:path';
import {acquire} from './sitemap-benchmark.mjs';
import {allowed} from './all-candidates.mjs';
import {createRequire} from 'node:module';

const run = path.resolve(process.argv[2] || 'lab-runs/structure-routing-all-candidates-20261115');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const manifest = read(path.join(run, 'common-inputs/manifest.json'));
const resultFile = path.join(run, 'pagination-results.jsonl');
const completed = fs.existsSync(resultFile) ? fs.readFileSync(resultFile, 'utf8').split(/\r?\n/).filter(Boolean).map(JSON.parse) : [];

if (process.argv.includes('--rules-only')) {
  const require = createRequire(path.resolve('.deps/structure-routing-sitemaps/package.json'));
  const {load} = require('cheerio');
  const rules = read(path.join(run, 'others/autopager-seed1-rules.json'));
  const rows = manifest.cases.map(target => {
    const rule = rules.cases.find(row => row.case === target.id).rule;
    const $ = load(fs.readFileSync(path.join(run, `executor-inputs/autopager/${target.id}.dom.html`), 'utf8'));
    const urls = $(rule.css_selector).toArray().filter(node => {
      const element = $(node), text = element.text().replace(/\s+/g, ' ').trim();
      return rule.alternatives.some(alt => {
        if (alt.text_equals && alt.text_equals !== text) return false;
        if (alt.rel_token) return (element.attr('rel') || '').split(/\s+/).includes(alt.rel_token);
        if (alt.class_token) return (element.attr('class') || '').split(/\s+/).includes(alt.class_token);
        if (alt.text_contains) return text.toLowerCase().includes(alt.text_contains.toLowerCase());
        if (alt.aria_label_contains) return (element.attr('aria-label') || '').toLowerCase().includes(alt.aria_label_contains.toLowerCase());
        if (alt.title_contains) return (element.attr('title') || '').toLowerCase().includes(alt.title_contains.toLowerCase());
        return false;
      });
    }).map(node => new URL($(node).attr('href'), target.heldout_listing_url).href).filter(url => allowed(url, target));
    const native = read(path.join(run, `others/autopager-heldout-${target.id}.json`));
    return {id: target.id, urls: [...new Set(urls)], same_next_as_native: urls.includes(native.next_predictions[0]?.href), model_calls: 0, document_requests: 0, cached_input: true};
  });
  fs.writeFileSync(path.join(run, 'pagination-rule-replay.json'), JSON.stringify({rows, model_calls: 0, document_requests: 0, separate_process: true, process_rss_bytes: process.memoryUsage().rss, target_html_reused_from_native_follow_not_new_acquisition: true}, null, 2) + '\n');
  console.log(JSON.stringify(rows));
} else for (const target of manifest.cases) {
  if (completed.some(row => row.id === target.id)) continue;
  const predictionFile = path.join(run, 'others', `autopager-heldout-${target.id}.json`);
  const prediction = read(predictionFile);
  const selected = prediction.next_predictions[0]?.href;
  if (!selected || !allowed(selected, target)) throw new Error('missing_or_disallowed_prediction');
  const started = Date.now();
  const options = {target, executablePath: process.env.CHROME_PATH, robots: new Map(), metadata: {requests: 0, responses: 0, bytes: 0, lastAt: 0}};
  const counter = {documents: 0, limit: 3, lastAt: 0};
  await new Promise(resolve => setTimeout(resolve, 1000));
  const observation = await acquire(target, selected, run, 'autopager', 'next', options, counter);
  const row = {id: target.id, task: 'pagination', method: 'autopager', observations: [observation], prediction_file: path.relative(run, predictionFile),
    input_was_cached: true, new_input_document_loads: 0, crf_calls: prediction.model_calls, future_page_llm_calls: 0,
    local_crf_seconds: prediction.runtime_seconds, package_import_seconds: prediction.package_import_seconds,
    cost: {document_requests: observation.document_requests, document_responses: observation.document_responses,
      html_http_requests: observation.network.requests, robots: options.metadata, all_http_requests: observation.network.requests + options.metadata.requests,
      xhr_fetch: observation.network.xhrFetch, response_bytes: observation.network.bytes, elapsed_ms: Date.now() - started},
    independent_gold_input: false};
  fs.appendFileSync(resultFile, JSON.stringify(row) + '\n');
  console.log(JSON.stringify({id: row.id, requested_url: selected, page_valid: observation.page_valid, cost: row.cost}));
}
