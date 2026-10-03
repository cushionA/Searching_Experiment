#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {ToolAdapter} from './adapters.mjs';
import {Evidence, verify} from './evidence.mjs';
import {snapshotSources} from './runtime.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const availableTools = [
  'rebrowser-lightpanda', 'patchright', 'obscura', 'obscura-stealth', 'obscura-no-render', 'obscura-patched',
];
const lightpandaRelease = '1.0.0';
const pageSize = Number(os.constants?.PAGE_SIZE) || 4096;
const iterationsLimit = 20;

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const middle = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[middle] : (xs[middle - 1] + xs[middle]) / 2;
}

function parseArgs(argv) {
  let output;
  let iterations = 5;
  let pages = 8;
  // Obscura's redirect interception does not support the budgeted pipeline.
  // The separate core benchmark compares all engines on a controlled fixture.
  let tools = ['rebrowser-lightpanda', 'patchright'];
  for (const arg of argv) {
    const numeric = /^--(iterations|pages)=(\d+)$/.exec(arg);
    const toolList = /^--tools=(.+)$/.exec(arg);
    if (numeric) {
      const value = Number(numeric[2]);
      if (!Number.isSafeInteger(value) || value < 1) throw new Error(`invalid ${numeric[1]}`);
      if (numeric[1] === 'iterations') iterations = value;
      else pages = value;
    } else if (toolList) {
      tools = toolList[1].split(',');
    } else if (arg.startsWith('--')) {
      throw new Error(`unknown option: ${arg}`);
    } else if (output) {
      throw new Error('provide one output directory');
    } else {
      output = arg;
    }
  }
  if (!output) throw new Error('usage: node engine-comparison.mjs NEW_OUTPUT_DIRECTORY [--iterations=5] [--pages=8] [--tools=a,b]');
  if (iterations > iterationsLimit) throw new Error(`iterations is limited to ${iterationsLimit}`);
  if (pages > 11) throw new Error('pages is limited to 11 by the unchanged 25-request evidence budget');
  if (!tools.length || new Set(tools).size !== tools.length || tools.some(tool => !availableTools.includes(tool))) {
    throw new Error('invalid --tools list');
  }
  return {output: path.resolve(output), iterations, pages, tools};
}

function procStat(pid) {
  try {
    const raw = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    const closeParen = raw.lastIndexOf(')');
    const fields = raw.slice(closeParen + 2).trim().split(/\s+/);
    return {
      pid: Number(pid), ppid: Number(fields[1]), start: fields[19],
      rss: Number(fields[21]) * pageSize,
    };
  } catch {
    return null;
  }
}

function treeSnapshot(rootPid, seen) {
  if (process.platform !== 'linux') return {bytes: null, pids: []};
  const processes = [];
  try {
    for (const name of fs.readdirSync('/proc')) {
      if (!/^\d+$/.test(name)) continue;
      const processInfo = procStat(name);
      if (processInfo) processes.push(processInfo);
    }
  } catch {}

  const byPid = new Map(processes.map(processInfo => [processInfo.pid, processInfo]));
  const root = byPid.get(rootPid);
  const descendants = new Set(root ? [`${root.pid}:${root.start}`] : []);
  // Previously observed descendants remain roots if reparented. PID start times
  // prevent a later unrelated process from inheriting an old observation.
  for (const identity of seen) {
    const [pidText, start] = identity.split(':');
    const processInfo = byPid.get(Number(pidText));
    if (processInfo?.start === start) descendants.add(identity);
  }
  let changed = true;
  while (changed) {
    changed = false;
    for (const processInfo of processes) {
      const identity = `${processInfo.pid}:${processInfo.start}`;
      if (descendants.has(identity)) continue;
      const parent = byPid.get(processInfo.ppid);
      if (parent && descendants.has(`${parent.pid}:${parent.start}`)) {
        descendants.add(identity);
        changed = true;
      }
    }
  }

  let bytes = 0;
  const livePids = [];
  for (const identity of descendants) {
    seen.add(identity);
    const [pidText, start] = identity.split(':');
    const processInfo = byPid.get(Number(pidText));
    if (processInfo?.start === start) {
      bytes += processInfo.rss;
      livePids.push(processInfo.pid);
    }
  }
  return {bytes, pids: livePids};
}

function makeFixture() {
  const counts = new Map();
  const cookieChecks = new Map();
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    const cell = url.searchParams.get('cell') || 'unattributed';
    counts.set(cell, (counts.get(cell) || 0) + 1);
    if (url.pathname === '/robots.txt') {
      response.writeHead(200, {'content-type': 'text/plain'});
      response.end('User-agent: *\nAllow: /\n');
      return;
    }
    if (url.pathname !== '/home' && url.pathname !== '/page') {
      response.writeHead(404);
      response.end('not found');
      return;
    }

    const id = url.pathname === '/home' ? 'warmup' : (url.searchParams.get('id') || 'missing');
    if (id === 'warmup') {
      response.setHeader('set-cookie', 'benchmark_session=present; Path=/; SameSite=Lax');
    } else {
      const checks = cookieChecks.get(cell) || [];
      checks.push(/(?:^|;\s*)benchmark_session=present(?:;|$)/.test(request.headers.cookie || ''));
      cookieChecks.set(cell, checks);
    }
    const data = {id, title: `Engine fixture ${id}`, expected: `expected-${id}`, values: [id, 'json-data', 42]};
    const loadNodes = Array.from({length: 150}, (_, n) =>
      `<div class="load" data-n="${n}">node-${n}</div>`).join('');
    response.writeHead(200, {'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store'});
    response.end(`<!doctype html><html><head><meta charset="utf-8"><title>${data.title}</title></head><body>
      <main id="fixture" data-page="${id}"><script id="payload" type="application/json">${JSON.stringify(data)}</script>
      ${loadNodes}<div id="generated"></div></main>
      <script>const root=document.querySelector('#fixture');const item=document.createElement('p');
      item.textContent='generated-'+root.getAttribute('data-page');document.querySelector('#generated').appendChild(item);</script>
      </body></html>`);
  });
  return {
    counts,
    cookieChecks,
    async start() {
      await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(0, '127.0.0.1', resolve);
      });
      return `http://127.0.0.1:${server.address().port}`;
    },
    async close() {
      if (server.listening) await new Promise(resolve => server.close(resolve));
    },
  };
}

function profileFor(tool) {
  return {
    id: 'pooled',
    session_pool: true,
    ...(tool === 'rebrowser-lightpanda'
      ? {lightpanda: {release: lightpandaRelease, profile: 'compat'}}
      : {}),
  };
}

function extractPage(page) {
  return page.evaluate(() => {
    const root = document.querySelector('#fixture');
    const payload = document.querySelector('#payload');
    const generated = document.querySelector('#generated p');
    return {
      title: document.title,
      page: root?.getAttribute('data-page') || null,
      json: payload?.textContent || null,
      generated: generated?.textContent || null,
      load_nodes: document.querySelectorAll('.load').length,
    };
  });
}

async function runCell(job) {
  const fixture = makeFixture();
  const seenProcesses = new Set();
  let adapter;
  let sampler;
  let workPhase = false;
  let peakTotalRss = 0;
  let peakWorkRss = 0;
  const observedPids = new Set();
  const evidence = new Evidence(job.evidence);
  evidence.save('sources.json', snapshotSources(evidence));
  evidence.save('settings.json', {
    tool: job.tool, iteration: job.iteration, cell_id: job.cell,
    pages: job.pages, profile: profileFor(job.tool), fixture: 'loopback only', timeout_ms: 60000,
  });

  const origin = await fixture.start();
  const site = {
    id: `engine-${job.tool}`,
    origins: [origin],
    links: {home: `${origin}/home?cell=${encodeURIComponent(job.cell)}`, targets: []},
  };
  const row = {
    tool: job.tool, iteration: job.iteration, cell_id: job.cell, profile: 'pooled',
    startup_ms: null, warmup_ms: null, latencies_ms: [], requests: 0,
    evidence_requests: 0, evidence_bytes: 0, main_cookie_checks: [], errors: [], runtime: null,
    peak_total_rss_bytes: null, peak_work_rss_bytes: null,
  };
  const sample = () => {
    const current = treeSnapshot(process.pid, seenProcesses);
    if (current.bytes === null) return;
    peakTotalRss = Math.max(peakTotalRss, current.bytes);
    if (workPhase) peakWorkRss = Math.max(peakWorkRss, current.bytes);
    for (const pid of current.pids) observedPids.add(pid);
  };
  const started = performance.now();
  try {
    adapter = new ToolAdapter({
      tool: job.tool, site, evidence, fixture: true, profile: profileFor(job.tool),
      options: {captureScreenshots: false},
    });
    sampler = setInterval(sample, 20);
    sample();
    await adapter.open();
    row.startup_ms = performance.now() - started;
    row.runtime = adapter.browser?.runtime || null;
    sample();

    const visit = async (url, id, warmup = false) => {
      const began = performance.now();
      const result = await adapter.goto(url);
      if (result.outcome !== 'content_observed') {
        throw new Error(`navigation outcome for ${id}: ${result.outcome || 'missing'}`);
      }
      const extracted = await extractPage(adapter.browser.page);
      const expected = {
        id, title: `Engine fixture ${id}`, expected: `expected-${id}`,
        values: [id, 'json-data', 42],
      };
      if (extracted.page !== id || extracted.title !== expected.title
          || extracted.generated !== `generated-${id}` || extracted.load_nodes !== 150
          || extracted.json !== JSON.stringify(expected)) {
        throw new Error(`DOM predicate failed for ${id}: ${JSON.stringify(extracted)}`);
      }
      const elapsed = performance.now() - began;
      if (warmup) {
        row.warmup_ms = elapsed;
        workPhase = true;
      } else {
        row.latencies_ms.push(elapsed);
      }
      sample();
    };

    await visit(site.links.home, 'warmup', true);
    for (let n = 0; n < job.pages; n++) {
      await visit(`${origin}/page?id=${n}&cell=${encodeURIComponent(job.cell)}`, String(n));
    }
    await new Promise(resolve => setTimeout(resolve, 25));
  } catch (error) {
    row.errors.push({name: error?.name || 'Error', message: String(error?.message || error)});
  } finally {
    sample();
    if (adapter) {
      try {
        await adapter.close();
      } catch (error) {
        row.errors.push({name: error?.name || 'Error', message: `close: ${String(error?.message || error)}`});
      }
    }
    sample();
    if (sampler) clearInterval(sampler);
    await fixture.close();

    row.requests = fixture.counts.get(job.cell) || 0;
    if (row.requests !== job.pages + 1) {
      row.errors.push({name: 'FixtureRequestError', message: 'warmup and main-page request count did not match the configured page count'});
    }
    row.main_cookie_checks = fixture.cookieChecks.get(job.cell) || [];
    row.cookie_success = row.main_cookie_checks.length === job.pages && row.main_cookie_checks.every(Boolean);
    if (!row.cookie_success) {
      row.errors.push({name: 'CookiePredicateError', message: 'not every main page received the warmup session cookie'});
    }
    if (row.latencies_ms.length !== job.pages) {
      row.errors.push({name: 'PagePredicateError', message: 'not every requested page completed the shared extraction predicate'});
    }
    const budget = evidence.budgets[`${job.tool}/${site.id}`] || {};
    row.evidence_requests = budget.requests || 0;
    row.evidence_bytes = budget.bytes_charged || 0;
    if (row.evidence_requests !== (job.pages + 1) * 2) {
      row.errors.push({name: 'EvidenceRequestError', message: 'expected one robots and one main request for warmup and every page'});
    }
    row.peak_total_rss_bytes = peakTotalRss || null;
    row.peak_work_rss_bytes = workPhase ? (peakWorkRss || null) : null;
    row.peak_pids = [...observedPids].sort((a, b) => a - b);
    row.evidence_directory = job.evidence;
    row.evidence_records = evidence.records.length;
    row.evidence_results = evidence.results.length;
    row.verification = verify(job.evidence);
    evidence.save('verification.json', row.verification);
    if (!row.verification.ok) {
      row.errors.push({name: 'EvidenceVerificationError', message: row.verification.errors.join(',')});
    }
  }
  return row;
}

function failedRow(job, message) {
  return {
    tool: job.tool, iteration: job.iteration, cell_id: job.cell, profile: 'pooled',
    startup_ms: null, warmup_ms: null, latencies_ms: [], requests: 0,
    evidence_requests: 0, evidence_bytes: 0, main_cookie_checks: [], cookie_success: false,
    runtime: null, peak_total_rss_bytes: null, peak_work_rss_bytes: null,
    errors: [{name: 'WorkerError', message}], evidence_directory: job.evidence,
  };
}

function signalWorkerTree(child, signal) {
  try {
    if (process.platform === 'win32') child.kill(signal);
    else process.kill(-child.pid, signal);
  } catch {
    try { child.kill(signal); } catch {}
  }
}

async function runWorker(job) {
  const started = performance.now();
  let child;
  try {
    child = spawn(process.execPath, [fileURLToPath(import.meta.url), `--worker=${JSON.stringify(job)}`], {
      stdio: ['ignore', 'pipe', 'pipe'], detached: process.platform !== 'win32',
    });
  } catch (error) {
    return failedRow(job, String(error?.message || error));
  }
  let stdout = '';
  let stderr = '';
  let timedOut = false;
  let forceKillTimer;
  child.stdout.on('data', chunk => { stdout = (stdout + chunk).slice(-2_000_000); });
  child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-16_000); });
  const exited = new Promise(resolve => {
    child.once('error', error => resolve({error: String(error)}));
    child.once('close', (code, signal) => resolve({code, signal}));
  });
  const timeout = setTimeout(() => {
    timedOut = true;
    signalWorkerTree(child, 'SIGTERM');
    forceKillTimer = setTimeout(() => signalWorkerTree(child, 'SIGKILL'), 5000);
  }, 60000);
  const status = await exited;
  clearTimeout(timeout);
  if (forceKillTimer) clearTimeout(forceKillTimer);
  if (timedOut) signalWorkerTree(child, 'SIGKILL');

  let row;
  try { row = JSON.parse(stdout.trim().split(/\r?\n/).at(-1)).row; } catch {}
  if (!row) {
    const reason = timedOut ? 'worker timeout after 60 seconds'
      : status.error || `worker exited ${status.code}/${status.signal}: ${stderr.slice(-2500)}`;
    row = failedRow(job, reason);
  }
  row.worker_elapsed_ms = performance.now() - started;
  if (timedOut) row.errors.push({name: 'WorkerTimeout', message: 'worker timeout after 60 seconds'});
  if (status.code !== 0 && !row.errors.length) {
    row.errors.push({name: 'WorkerError', message: `worker exit code ${status.code}`});
  }
  return row;
}

function summarize(rows, tools, pages) {
  const byTool = {};
  for (const tool of tools) {
    const cells = rows.filter(row => row.tool === tool);
    const successful = cells.filter(row => !row.errors.length && row.cookie_success
      && row.latencies_ms.length === pages);
    const latencies = successful.flatMap(row => row.latencies_ms);
    byTool[tool] = {
      cells: cells.length,
      successful_cells: successful.length,
      failed_cells: cells.length - successful.length,
      median_startup_ms: median(successful.map(row => row.startup_ms)),
      median_latency_ms: median(latencies),
      median_peak_total_rss_bytes: median(successful.map(row => row.peak_total_rss_bytes)),
      median_peak_work_rss_bytes: median(successful.map(row => row.peak_work_rss_bytes)),
      median_fixture_requests: median(successful.map(row => row.requests)),
      median_evidence_requests: median(successful.map(row => row.evidence_requests)),
      median_evidence_bytes: median(successful.map(row => row.evidence_bytes)),
      available: cells.some(row => row.startup_ms !== null),
    };
  }
  return byTool;
}

function checkpoint(output, configuration, environment, sources, versionsHash, rows) {
  const data = {
    schema_version: 1,
    updated_at: new Date().toISOString(),
    configuration,
    environment,
    source_sha256: sources,
    versions_sha256: versionsHash,
    completed_cells: rows.length,
    cells: rows,
  };
  const temporary = path.join(output, 'raw.checkpoint.tmp');
  fs.writeFileSync(temporary, `${JSON.stringify(data, null, 2)}\n`);
  fs.renameSync(temporary, path.join(output, 'raw.checkpoint.json'));
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  fs.mkdirSync(path.dirname(options.output), {recursive: true});
  fs.mkdirSync(options.output);
  const sources = {};
  for (const name of fs.readdirSync(here).filter(name => /\.(mjs|json|py)$/.test(name)
    && !name.endsWith('.test.mjs')).sort()) {
    sources[name] = crypto.createHash('sha256').update(fs.readFileSync(path.join(here, name))).digest('hex');
  }
  const versionsHash = crypto.createHash('sha256').update(fs.readFileSync(path.join(here, 'versions.json'))).digest('hex');
  const environment = {
    platform: process.platform, arch: process.arch, node: process.version,
    kernel: os.release(), total_memory_bytes: os.totalmem(),
  };
  const configuration = {
    tools: options.tools, iterations: options.iterations, pages_per_cell: options.pages,
    warmups_per_cell: 1, profile: {id: 'pooled', session_pool: true},
    lightpanda: {release: lightpandaRelease, profile: 'compat'},
    rotating_tool_order: true, worker_timeout_ms: 60000, sample_interval_ms: 20,
    fixture: 'private ephemeral 127.0.0.1 HTTP server in each worker',
    robots: 'each adapter navigation checks Allow; robots and document requests are included in evidence',
    fixture_observe_wait_ms: 100,
    capture_screenshots: false,
    latency_definition: 'ToolAdapter.goto through DOM extraction; includes the shared 100 ms fixture observation window',
    rss_definition: 'Linux /proc RSS sum of worker and all observed descendants sampled every 20 ms; '
      + 'worker root included, parent controller excluded; startup peak includes setup, warmup, work and teardown; '
      + 'work peak begins after warmup',
    request_budget: 'unchanged evidence limits: 25 requests and 8 MiB per tool/cell',
    success_predicate: 'same cookie receipt, title, embedded JSON payload, JS-created text and 150 DOM nodes for every page',
    external_sites: false,
  };
  const rows = [];
  const unavailable = new Set();
  for (let iteration = 1; iteration <= options.iterations; iteration++) {
    const offset = (iteration - 1) % options.tools.length;
    for (let index = 0; index < options.tools.length; index++) {
      const tool = options.tools[(index + offset) % options.tools.length];
      const cell = `i${iteration}-${tool}`;
      const evidence = path.join(options.output, 'cells', cell);
      const job = {tool, iteration, cell, pages: options.pages, evidence};
      fs.mkdirSync(path.dirname(evidence), {recursive: true});
      const row = unavailable.has(tool)
        ? failedRow(job, 'tool marked unavailable after earlier setup failure')
        : await runWorker(job);
      rows.push(row);
      if (row.startup_ms === null && row.errors.length) unavailable.add(tool);
      checkpoint(options.output, configuration, environment, sources, versionsHash, rows);
    }
  }

  const functionalSuccess = rows.length === options.iterations * options.tools.length
    && rows.every(row => !row.errors.length && row.cookie_success
      && row.latencies_ms.length === options.pages
      && row.evidence_requests <= 25 && row.evidence_bytes <= 8 * 1024 * 1024);
  const raw = {
    schema_version: 1, created_at: new Date().toISOString(), configuration, environment,
    source_sha256: sources, versions_sha256: versionsHash, cells: rows,
    functional_success: functionalSuccess,
  };
  const summary = {
    schema_version: 1, created_at: raw.created_at, configuration, environment,
    source_sha256: sources, versions_sha256: versionsHash,
    functional_success: functionalSuccess,
    by_tool: summarize(rows, options.tools, options.pages),
    unavailable_tools: [...unavailable],
  };
  fs.writeFileSync(path.join(options.output, 'raw.json'), `${JSON.stringify(raw, null, 2)}\n`, {flag: 'wx'});
  fs.writeFileSync(path.join(options.output, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`, {flag: 'wx'});
  console.log(JSON.stringify({output: options.output, functional_success: functionalSuccess, by_tool: summary.by_tool}, null, 2));
  if (!functionalSuccess) process.exitCode = 1;
}

const workerArgument = process.argv.find(argument => argument.startsWith('--worker='));
if (workerArgument) {
  runCell(JSON.parse(workerArgument.slice('--worker='.length)))
    .then(row => process.stdout.write(`${JSON.stringify({row})}\n`))
    .catch(error => {
      process.stdout.write(`${JSON.stringify({error: String(error?.stack || error)})}\n`);
      process.exitCode = 1;
    });
} else {
  main().catch(error => {
    console.error(`engine comparison failed: ${error.stack || error}`);
    process.exitCode = 1;
  });
}
