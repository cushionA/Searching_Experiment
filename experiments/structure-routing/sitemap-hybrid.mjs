import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {learnRules, selectLinks} from './runner.mjs';
import {runSitemapBenchmark} from './sitemap-benchmark.mjs';

const args = Object.fromEntries(process.argv.slice(2).flatMap((value, i, all) => value.startsWith('--') ? [[value.slice(2), value.includes('=') ? value.split('=').slice(1).join('=') : all[i + 1]]] : []));
const readJson = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const readJsonl = file => fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map(JSON.parse) : [];
const writeJson = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n'); };
const sha256 = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function sourceRow(runs, id, method, requireObservations = true) {
  for (const run of runs) {
    const row = readJsonl(path.join(run, 'results.jsonl')).find(x => String(x.id) === id && x.method === method && x.phase === 'cold' && (!requireObservations || x.observations?.length));
    if (row) return {run, row, file: path.join(run, 'results.jsonl'), hash: sha256(path.join(run, 'results.jsonl'))};
  }
  if (method === 'C1') for (const run of runs) {
    const resultFile = path.join(run, 'results.jsonl');
    const legacy = readJsonl(resultFile).find(x => String(x.id) === id && x.method === 'C1' && x.mode === 'cold');
    const observationsFile = path.join(run, 'artifacts', id, 'C1', 'cold', 'observations.json');
    if (!legacy || !fs.existsSync(observationsFile)) continue;
    const saved = readJson(observationsFile);
    const observations = saved.observations.map(row => ({url: row.url, requested_url: row.requested_url || row.url, final_url: row.url, links: row.links || []}));
    if (requireObservations && !observations.length) continue;
    return {run, row: {...legacy, observations, cost: {document_requests: legacy.document_requests, document_responses: legacy.document_responses,
      http_requests: legacy.http_requests, response_bytes: saved.network?.reduce((n, event) => n + Number(event.bytes || 0), 0) ?? null, elapsed_ms: legacy.elapsed_ms}},
      file: observationsFile, hash: sha256(observationsFile), resultsHash: sha256(resultFile)};
  }
  return null;
}

function sourceInventory(source, id) {
  if (!source) return {urls: [], file: null, hash: null};
  const file = path.join(source.run, 'artifacts', id, 'B2', 'inventory.json');
  const recipe = path.join(source.run, 'artifacts', id, 'B2', 'recipe.json');
  const value = fs.existsSync(file) ? readJson(file) : fs.existsSync(recipe) ? readJson(recipe) : {};
  const urls = value.urls ?? value.inventory ?? [];
  return {urls: urls.map(x => typeof x === 'string' ? x : x.url).filter(Boolean), file: fs.existsSync(file) ? file : fs.existsSync(recipe) ? recipe : null,
    hash: fs.existsSync(file) ? sha256(file) : fs.existsSync(recipe) ? sha256(recipe) : null};
}

async function main() {
  if (!args['source-run'] || !args.run || !args.ids || args.live !== 'true') throw new Error('required: --source-run --run --ids --live true');
  const runs = String(args['source-run']).split(',').map(x => path.resolve(x));
  const runDir = path.resolve(args.run);
  if (runs.includes(runDir) || ['results.jsonl', 'targets.jsonl', 'transferred-setup.json'].some(name => fs.existsSync(path.join(runDir, name)))) throw new Error('child_run_must_be_new');
  const ids = String(args.ids).split(',').filter(Boolean);
  if (ids.some(id => !['01', '17'].includes(id))) throw new Error('only_ids_01_17_are_allowed');
  const targets = [], setup = {source_runs: runs, live_candidate_execution: true, independent_cold_rerun: false, sites: {}};
  for (const id of ids) {
    const targetSource = runs.map(run => ({run, row: readJsonl(path.join(run, 'targets.jsonl')).find(x => String(x.id) === id)})).find(x => x.row);
    if (!targetSource) throw new Error(`missing_source_target:${id}`);
    const b2Any = sourceRow(runs, id, 'B2', false);
    const b2 = sourceRow(runs, id, 'B2');
    const c1 = b2 ? null : sourceRow(runs, id, 'C1');
    const source = b2 || c1;
    if (!source) throw new Error(`missing_b2_or_c1_cold_observations:${id}`);
    const observations = source.row.observations;
    const pages = observations.map(row => ({url: row.final_url || row.url || row.requested_url, links: row.links || []})).filter(page => page.url);
    const startUrl = targetSource.row.start_url;
    const hosts = new Set([new URL(startUrl).hostname.toLowerCase(), ...(targetSource.row.allowed_hosts || []).map(x => String(x).toLowerCase())]);
    const seen = new Set(observations.flatMap(row => [row.requested_url, row.final_url, row.url]).filter(Boolean));
    const rules = learnRules(pages);
    const unique = new Map();
    for (const page of pages) for (const link of selectLinks({links: page.links}, 'C1', rules, seen, hosts)) if (!unique.has(link.url)) unique.set(link.url, link);
    const selected = [...unique.values()];
    const inventory = sourceInventory(b2Any || b2, id);
    const inInventory = new Set(inventory.urls);
    const preferred = selected.filter(link => inInventory.has(link.url));
    const frontier = [...preferred, ...selected.filter(link => !inInventory.has(link.url))].map(link => link.url);
    const recipe = {version: 1, id, method: 'C1', variant: 'baseline', source: b2 ? 'B2-cold-observations-plus-inventory' : 'C1-cold-observations-fallback',
      sourcePages: pages, rules, seen: [...seen], frontier, inventory: inventory.urls, gold_input: false};
    writeJson(path.join(runDir, 'artifacts', id, 'C1', 'recipe.json'), recipe);
    targets.push(targetSource.row);
    setup.sites[id] = {selected_target_source_run: targetSource.run, source_method: b2 ? 'B2' : 'C1', source_run: source.run,
      source_results_sha256: source.hash, source_cost: source.row.cost ?? null, source_observation_count: observations.length,
      b2_cold: b2Any ? {run: b2Any.run, results_sha256: b2Any.hash, cost: b2Any.row.cost ?? null, observations: b2Any.row.observations?.length ?? 0} : null,
      c1_cold: (() => { const row = sourceRow(runs, id, 'C1', false); return row ? {run: row.run, source_sha256: row.hash, results_sha256: row.resultsHash ?? row.hash, cost: row.row.cost ?? null, observations: row.row.observations?.length ?? 0} : null; })(),
      source_inventory: inventory, preferred_intersection_count: preferred.length, fallback_link_count: selected.length - preferred.length,
      source_pages: pages.map(page => page.url), recipe: path.join('artifacts', id, 'C1', 'recipe.json'),
      notes: b2 ? 'Reuse saved B2 cold observations; not an independent cold rerun.' : 'B2 cold observations empty; reuse original C1 cold observations; not an independent cold rerun.'};
  }
  fs.mkdirSync(runDir, {recursive: true});
  fs.writeFileSync(path.join(runDir, 'targets.jsonl'), targets.map(x => JSON.stringify(x)).join('\n') + '\n');
  for (const id of ids) setup.sites[id].target_master_sha256 = sha256(path.join(setup.sites[id].selected_target_source_run, 'targets.jsonl'));
  writeJson(path.join(runDir, 'transferred-setup.json'), setup);
  await runSitemapBenchmark({runDir, targets, method: 'C1', phase: 'warm', executablePath: args.executable});
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) main().catch(error => { console.error(JSON.stringify({error: error.message})); process.exitCode = 1; });
