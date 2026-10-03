#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {openBrowser} from './runner.mjs';
import {deps, repo, sleep, snapshotSources} from './runtime.mjs';
import {ToolAdapter} from './adapters.mjs';
import {Evidence,verify} from './evidence.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const profiles = ['baseline', 'compat', 'probe'];
const sessionProfiles = ['compat', 'pooled'];
const release = '1.0.0';
const pageSize = 4096;

function parseArgs(argv) {
  let outputDirectory;
  let iterations = 5;
  let pages = 10;
  let sessionPool = false;
  for (const arg of argv) {
    if (arg === '--session-pool') { sessionPool = true; continue; }
    const match = /^--(iterations|pages)=(\d+)$/.exec(arg);
    if (match) {
      const n = Number(match[2]);
      if (!Number.isSafeInteger(n) || n < 1) throw new Error(`Invalid ${match[1]}: ${match[2]}`);
      if (match[1] === 'iterations') iterations = n;
      else pages = n;
    } else if (arg.startsWith('--')) throw new Error(`Unknown option: ${arg}`);
    else if (outputDirectory) throw new Error('Provide exactly one outputDirectory');
    else outputDirectory = arg;
  }
  if (!outputDirectory) throw new Error('Usage: node lightpanda-benchmark.mjs outputDirectory [--iterations=N] [--pages=N] [--session-pool]');
  if(sessionPool && pages>11) throw new Error('session-pool comparison supports at most 11 pages plus warmup within the shared 25-request budget (robots included)');
  return {outputDirectory: path.resolve(outputDirectory), iterations, pages, sessionPool};
}

function sha256(data) { return crypto.createHash('sha256').update(data).digest('hex'); }
async function fileInfo(filename) {
  try {
    const stat = fs.statSync(filename);
    const digest = crypto.createHash('sha256');
    await new Promise((resolve, reject) => {
      const stream = fs.createReadStream(filename);
      stream.on('data', chunk => digest.update(chunk));
      stream.once('error', reject);
      stream.once('end', resolve);
    });
    return {path: filename, sha256:digest.digest('hex'), bytes:stat.size};
  } catch (error) { return {path: filename, unavailable: String(error.message || error)}; }
}
function median(values) {
  const xs = values.filter(Number.isFinite).slice().sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}
function rssBytes(pid) {
  if (!pid || process.platform !== 'linux') return null;
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    const close = stat.lastIndexOf(')');
    const fields = stat.slice(close + 1).trim().split(/\s+/);
    const residentPages = Number(fields[21]); // field 24 (RSS), after pid/comm.
    return Number.isFinite(residentPages) && residentPages >= 0 ? residentPages * pageSize : null;
  } catch { return null; }
}

function makeFixture() {
  const counts = new Map();
  const cookieChecks = new Map();
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const token = url.searchParams.get('cell') || 'unattributed';
    counts.set(token, (counts.get(token) || 0) + 1);
    if (url.pathname === '/robots.txt') { res.writeHead(200, {'content-type':'text/plain'}); res.end('User-agent: *\nAllow: /\n'); return; }
    if (url.pathname !== '/page' && url.pathname !== '/warmup') {
      res.writeHead(404); res.end('not found'); return;
    }
    const id = url.pathname === '/warmup' ? 'warmup' : (url.searchParams.get('id') || 'missing');
    if (url.pathname === '/warmup') res.setHeader('set-cookie', 'benchmark_session=present; Path=/; SameSite=Lax');
    else cookieChecks.set(token, (cookieChecks.get(token) || []).concat(/(?:^|;\s*)benchmark_session=present(?:;|$)/.test(req.headers.cookie || '')));
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>Local benchmark ${id}</title></head><body>
      <main id="fixture" data-page="${id}"></main>
      <script>
        window.workload = {ready:false, api:{table:typeof HTMLTableElement.prototype.insertRow, cell:typeof HTMLTableRowElement.prototype.insertCell}};
        const root=document.querySelector('#fixture');
        const heading=document.createElement('h1'); heading.textContent='Local fixture ${id}'; root.appendChild(heading);
        const table=document.createElement('table'); table.id='work'; root.appendChild(table);
        const body=document.createElement('tbody'); table.appendChild(body);
        const row=document.createElement('tr'); body.appendChild(row);
        for(const text of ['alpha-${id}','beta']) { const cell=document.createElement('td'); cell.textContent=text; row.appendChild(cell); }
        const generated=document.createElement('tr'); body.appendChild(generated);
        for(const text of ['generated-${id}','tail']) { const cell=document.createElement('td'); cell.textContent=text; generated.appendChild(cell); }
        window.workload.ready=true;
      </script></body></html>`;
    res.writeHead(200, {'content-type':'text/html; charset=utf-8', 'cache-control':'no-store'});
    res.end(html);
  });
  return {
    counts, cookieChecks,
    async start() { await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); }); return `http://127.0.0.1:${server.address().port}`; },
    async close() { if (server.listening) await new Promise(resolve => server.close(resolve)); },
  };
}

async function runCell(profile, iteration, origin, pageCount) {
  const cellId = `i${iteration}-${profile}`;
  const row = {iteration, profile, cell_id:cellId, startup_ms:null, warmup_ms:null, navigations:[], requests:0,
    browser_pid:null, peak_browser_rss_bytes:null, peak_node_rss_bytes:null, runtime:null, errors:[]};
  const samples = [];
  let sampling = true;
  const sample = () => {
    if (!sampling) return;
    const browserRss = rssBytes(row.browser_pid);
    if (browserRss !== null) {
      const nodeRss = process.memoryUsage().rss;
      samples.push({browserRss, nodeRss, totalRss:browserRss + nodeRss, at:Date.now()});
    }
  };
  let opened;
  const startupStart = performance.now();
  try {
    opened = await openBrowser('rebrowser-lightpanda', {lightpanda:{release, profile}});
    activeOpened = opened;
    row.startup_ms = performance.now() - startupStart;
    row.browser_pid = opened.child?.pid ?? null;
    row.runtime = opened.runtime || null;
    sample();
    const timer = setInterval(sample, 10);
    try {
      const gotoExtract = async (url, expectedId, warmup = false) => {
        const start = performance.now();
        await opened.page.goto(url, {waitUntil:'load', timeout:30000});
        const gotoMs = performance.now() - start;
        const extracted = await opened.page.evaluate(() => {
          const root = document.querySelector('#fixture');
          const rows = Array.from(document.querySelectorAll('#work tr'), tr => Array.from(tr.querySelectorAll('td,th'), cell => cell.textContent));
          return {title:document.title, page:root?.getAttribute('data-page') ?? null,
            heading:document.querySelector('h1')?.textContent ?? null, rows,
            workload:window.workload || null,
            probe:document.documentElement.getAttribute('data-lightpanda-probe')};
        });
        const valid = extracted.page === expectedId && extracted.heading === `Local fixture ${expectedId}` &&
          extracted.rows[0]?.[0] === `alpha-${expectedId}` && extracted.rows[0]?.[1] === 'beta' &&
          extracted.rows[1]?.[0] === `generated-${expectedId}` && extracted.rows[1]?.[1] === 'tail' &&
          extracted.workload?.ready === true;
        if (!valid) throw new Error(`DOM extraction validation failed for ${expectedId}: ${JSON.stringify(extracted)}`);
        if (profile === 'probe' && extracted.probe !== 'loaded') throw new Error('probe marker was not loaded');
        const totalMs = performance.now() - start;
        if (!warmup) row.navigations.push({page:expectedId, total_ms:totalMs, goto_ms:gotoMs,
          extracted:{title:extracted.title, page:extracted.page, row_count:extracted.rows.length,
            workload_api:extracted.workload?.api, workload_error:extracted.workload?.error || null,
            probe:extracted.probe}});
        else row.warmup_ms = {total_ms:totalMs, goto_ms:gotoMs};
      };
      const warmStart = `${origin}/warmup?cell=${encodeURIComponent(cellId)}`;
      await gotoExtract(warmStart, 'warmup', true);
      for (let p = 0; p < pageCount; p++) {
        await gotoExtract(`${origin}/page?id=${p}&cell=${encodeURIComponent(cellId)}`, String(p));
        sample();
      }
      // Allow the 10 ms sampler to observe the final browser working set before close.
      await sleep(12);
    } finally { clearInterval(timer); }
  } catch (error) {
    row.errors.push({name:error?.name || 'Error', message:String(error?.message || error)});
  } finally {
    sample();
    sampling = false;
    row.peak_browser_rss_bytes = samples.length ? Math.max(...samples.map(x => x.browserRss)) : null;
    row.peak_node_rss_bytes = samples.length ? Math.max(...samples.map(x => x.nodeRss)) : process.memoryUsage().rss;
    row.peak_total_rss_bytes = samples.length ? Math.max(...samples.map(x => x.totalRss)) : null;
    if (opened) {
      try { await opened.close(); }
      catch (error) { row.errors.push({name:error?.name || 'Error', message:`close: ${String(error?.message || error)}`}); }
      if (activeOpened === opened) activeOpened = null;
    }
    row.requests = fixtureCounts.get(cellId) || 0;
  }
  return row;
}

async function runSessionCell(profile, iteration, origin, pageCount, evidenceDirectory) {
  const cellId=`i${iteration}-${profile}`;
  const row={iteration,profile,cell_id:cellId,startup_ms:null,warmup_ms:null,navigations:[],requests:0,
    browser_pid:null,peak_browser_rss_bytes:null,peak_node_rss_bytes:null,runtime:null,errors:[],evidence_directory:evidenceDirectory,
    main_cookie_checks:[]};
  const samples=[];let sampling=true,adapter;
  const sample=()=>{if(!sampling)return;const browserRss=rssBytes(row.browser_pid);if(browserRss!==null){const nodeRss=process.memoryUsage().rss;samples.push({browserRss,nodeRss,totalRss:browserRss+nodeRss,at:Date.now(),contexts:adapter.browser.browser.browserContexts().length,pid:adapter.browser.child.pid});}};
  const evidence=new Evidence(evidenceDirectory);
  evidence.save('sources.json',snapshotSources(evidence));
  const site={id:'fixture',origins:[origin],links:{home:`${origin}/warmup?cell=${encodeURIComponent(cellId)}`,targets:[]}};
  const armProfile={id:profile,lightpanda:{release,profile:'compat'},...(profile==='pooled'?{session_pool:{max_session_uses:20}}:{})};
  const started=performance.now();
  try {
    adapter=new ToolAdapter({tool:'rebrowser-lightpanda',site,evidence,fixture:true,profile:armProfile});
    await adapter.open();
    activeOpened=adapter;
    row.startup_ms=performance.now()-started;row.browser_pid=adapter.browser?.child?.pid??null;row.runtime=adapter.browser?.runtime||null;sample();
    row.child_context_count=adapter.browser?.browser?.browserContexts?.().length??null;
    row.child_context_count_includes_default_blank=true;
    const timer=setInterval(sample,10);
    try {
      const visit=async(url,id,warmup=false)=>{
        const begin=performance.now();
        const outcome=await adapter.goto(url);
        if(outcome.outcome!=='content_observed') throw new Error(`expected content_observed for ${id}, got ${outcome.outcome}`);
        const extracted=await adapter.browser.page.evaluate(()=>{
          const root=document.querySelector('#fixture');const rows=Array.from(document.querySelectorAll('#work tr'),tr=>Array.from(tr.querySelectorAll('td,th'),cell=>cell.textContent));
          return {title:document.title,page:root?.getAttribute('data-page')??null,heading:document.querySelector('h1')?.textContent??null,rows,workload:window.workload||null,probe:document.documentElement.getAttribute('data-lightpanda-probe')};
        });
        const valid=extracted.page===id&&extracted.heading===`Local fixture ${id}`&&extracted.rows[0]?.[0]===`alpha-${id}`&&extracted.rows[0]?.[1]==='beta'&&extracted.rows[1]?.[0]===`generated-${id}`&&extracted.rows[1]?.[1]==='tail'&&extracted.workload?.ready===true;
        if(!valid)throw new Error(`DOM extraction validation failed for ${id}: ${JSON.stringify(extracted)}`);
        const elapsed=performance.now()-begin;
        if(warmup)row.warmup_ms={total_ms:elapsed};else row.navigations.push({page:id,total_ms:elapsed,goto_ms:null,extracted:{title:extracted.title,page:extracted.page,row_count:extracted.rows.length,workload_api:extracted.workload?.api,probe:extracted.probe}});
      };
      await visit(site.links.home,'warmup',true);
      for(let p=0;p<pageCount;p++){await visit(`${origin}/page?id=${p}&cell=${encodeURIComponent(cellId)}`,String(p));sample();}
      await sleep(12);
    } finally {clearInterval(timer);}
  } catch(error) {row.errors.push({name:error?.name||'Error',message:String(error?.message||error)});}
  finally {
    sample();sampling=false;
    row.peak_browser_rss_bytes=samples.length?Math.max(...samples.map(x=>x.browserRss)):null;
    row.peak_node_rss_bytes=samples.length?Math.max(...samples.map(x=>x.nodeRss)):process.memoryUsage().rss;
    row.peak_total_rss_bytes=samples.length?Math.max(...samples.map(x=>x.totalRss)):null;
    if(adapter)try{await adapter.close();}catch(error){row.errors.push({name:error?.name||'Error',message:`close: ${String(error?.message||error)}`});}
    if(activeOpened===adapter)activeOpened=null;
    row.requests=Array.from(fixtureCounts.values()).reduce((sum,n)=>sum+n,0);
    row.main_cookie_checks=activeFixture?.cookieChecks?.get(cellId)||[];
    row.main_cookie_checks_all_present=row.main_cookie_checks.length===pageCount&&row.main_cookie_checks.every(Boolean);
    row.evidence_record_count=evidence.records.length;row.evidence_result_count=evidence.results.length;
    row.pool_state=profile==='pooled'?evidence.sessionPolicyState?.sites?.[`rebrowser-lightpanda/fixture`]:null;
    row.same_browser_pid=samples.length>0&&samples.every(sample=>sample.pid===row.browser_pid);
    row.peak_browser_context_count=samples.length?Math.max(...samples.map(sample=>sample.contexts)):null;
    row.verification=verify(evidence.directory);evidence.save('verification.json',row.verification);
    if(!row.verification.ok || !row.same_browser_pid || row.peak_browser_context_count!==2) row.errors.push({name:'SessionInvariantError',message:'evidence verification or single active browser context invariant failed'});
  }
  return row;
}

// Assigned for the active local fixture to keep each run cell's observed HTTP count.
let fixtureCounts = new Map();
let activeOpened = null;
let activeFixture = null;

function aggregate(rows, expectedCellCount, expectedPages, profileSet, sessionMode=false) {
  const byProfile = {};
  for (const profile of profileSet) {
    const cells = rows.filter(row => row.profile === profile);
    byProfile[profile] = {
      cells:cells.length,
      successful_cells:cells.filter(row => row.errors.length === 0 && row.navigations.length > 0).length,
      worker_elapsed_ms_median:median(cells.map(row => row.worker_elapsed_ms)),
      startup_ms_median:median(cells.map(row => row.startup_ms)),
      navigation_total_ms_median:median(cells.flatMap(row => row.navigations.map(nav => nav.total_ms))),
      navigation_goto_ms_median:median(cells.flatMap(row => row.navigations.map(nav => nav.goto_ms))),
      peak_browser_rss_bytes_median:median(cells.map(row => row.peak_browser_rss_bytes)),
      peak_node_rss_bytes_median:median(cells.map(row => row.peak_node_rss_bytes)),
      peak_total_rss_bytes_median:median(cells.map(row => row.peak_total_rss_bytes)),
      requests_per_cell_median:median(cells.map(row => row.requests)),
    };
  }
  const baseline = byProfile[sessionMode?'compat':'baseline'];
  const allCellsComplete = rows.length === expectedCellCount && rows.every(row => row.errors.length === 0 && row.navigations.length === expectedPages && (!sessionMode || row.main_cookie_checks_all_present));
  const comparisons = {};
  for (const profile of profileSet.slice(1)) {
    const current = byProfile[profile];
    const increase = (value, reference) => Number.isFinite(value) && Number.isFinite(reference) && reference > 0 ? value / reference - 1 : null;
    const memoryIncrease = increase(current.peak_total_rss_bytes_median, baseline.peak_total_rss_bytes_median);
    const latencyIncrease = increase(current.navigation_total_ms_median, baseline.navigation_total_ms_median);
    comparisons[profile] = {
      total_peak_rss_increase_fraction:memoryIncrease,
      navigation_latency_increase_fraction:latencyIncrease,
      memory_gate:{limit_fraction:0.10, result:!allCellsComplete || memoryIncrease === null ? 'insufficient_data' : memoryIncrease <= 0.10 ? 'within_guide' : 'over_guide'},
      latency_gate:{limit_fraction:0.20, result:!allCellsComplete || latencyIncrease === null ? 'insufficient_data' : latencyIncrease <= 0.20 ? 'within_guide' : 'over_guide'},
    };
  }
  return {all_cells_complete:allCellsComplete, by_profile:byProfile, relative_to_baseline:comparisons,
    interpretation:'10% total process memory and 20% latency are indicative gates, not statistical claims. Each cell runs in a separate Node worker, so browser and worker RSS are sampled together and the fixture server is included in that worker. Small iteration counts, short fixture pages, 10 ms /proc sampling, host load, and OS scheduling can materially affect these medians.'};
}

async function runWorkerCell(job) {
  const started = performance.now();
  let child;
  try {
    child = spawn(process.execPath, [fileURLToPath(import.meta.url), `--worker=${JSON.stringify(job)}`], {
      stdio:['ignore','pipe','pipe'], detached:process.platform !== 'win32',
    });
  } catch (error) {
    return failedWorkerRow(job, `worker spawn failed: ${String(error?.message || error)}`, performance.now() - started);
  }
  let stdout = '', stderr = '', timedOut = false, timeoutKill = null;
  const append = (current, chunk, cap) => (current + chunk.toString()).slice(-cap);
  child.stdout.on('data', chunk => { stdout = append(stdout, chunk, 1024 * 1024); });
  child.stderr.on('data', chunk => { stderr = append(stderr, chunk, 16 * 1024); });
  const exited = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code, signal) => resolve({code, signal}));
  });
  const timer = setTimeout(() => {
    timedOut = true;
    child.kill('SIGTERM');
    timeoutKill = setTimeout(() => {
      try { if (process.platform === 'win32') child.kill('SIGKILL'); else process.kill(-child.pid, 'SIGKILL'); }
      catch { child.kill('SIGKILL'); }
    }, 5000);
  }, 60000);
  let result;
  try { result = await exited; }
  catch (error) { result = {code:-1, signal:null, spawnError:String(error?.message || error)}; }
  finally {
    clearTimeout(timer); if (timeoutKill) clearTimeout(timeoutKill);
    // If SIGTERM arrived during browser setup, the worker may not yet own an
    // activeOpened handle. Reap its process group even after the worker exits.
    if(timedOut && process.platform!=='win32') {try {process.kill(-child.pid,'SIGKILL');} catch {}}
  }
  const elapsed = performance.now() - started;
  let payload;
  try { payload = JSON.parse(stdout.trim().split(/\r?\n/).at(-1)); } catch { payload = null; }
  let row = payload?.row;
  if (!row) row = failedWorkerRow(job, timedOut ? 'worker timed out after 60 seconds' :
    payload?.error || result.spawnError || `worker exited code=${result.code} signal=${result.signal}: ${stderr.trim()}`, elapsed);
  row.worker_elapsed_ms = elapsed;
  if (timedOut && !row.errors.some(error => /timed out/.test(error.message))) {
    row.errors.push({name:'WorkerTimeout', message:'worker timed out after 60 seconds'});
  }
  if (result.code !== 0 && !row.errors.length) row.errors.push({name:'WorkerError', message:`worker exited code=${result.code} signal=${result.signal}`});
  return row;
}

function failedWorkerRow(job, message, elapsed) {
  return {iteration:job.iteration, profile:job.profile, cell_id:`i${job.iteration}-${job.profile}`,
    worker_elapsed_ms:elapsed, startup_ms:null, warmup_ms:null, navigations:[], requests:0,
    browser_pid:null, peak_browser_rss_bytes:null, peak_node_rss_bytes:null, peak_total_rss_bytes:null,
    runtime:null, errors:[{name:'WorkerError', message}]};
}

async function workerMain(job) {
  const profileSet=job?.sessionPool?sessionProfiles:profiles;
  if (!job || !profileSet.includes(job.profile) || !Number.isSafeInteger(job.iteration) || job.iteration < 1 ||
      !Number.isSafeInteger(job.pages) || job.pages < 1) throw new Error('invalid benchmark worker job');
  process.on('SIGTERM', async () => {
    try { await activeOpened?.close(); } catch {}
    try { await activeFixture?.close(); } catch {}
    process.exit(143);
  });
  const fixture = makeFixture();
  activeFixture = fixture;
  const origin = await fixture.start();
  fixtureCounts = fixture.counts;
  let row;
  try {
    row=job.sessionPool?await runSessionCell(job.profile,job.iteration,origin,job.pages,job.evidenceDirectory):await runCell(job.profile,job.iteration,origin,job.pages);
  }
  finally { await fixture.close(); activeFixture = null; }
  process.stdout.write(`${JSON.stringify({row})}\n`);
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  fs.mkdirSync(path.dirname(opts.outputDirectory), {recursive:true});
  fs.mkdirSync(opts.outputDirectory);

  const rows = [];
  const profileSet=opts.sessionPool?sessionProfiles:profiles;
  for (let iteration = 0; iteration < opts.iterations; iteration++) {
    const offset = iteration % profileSet.length;
    const order = profileSet.map((_, index) => profileSet[(index + offset) % profileSet.length]);
    for (const profile of order) {
      const evidenceDirectory=opts.sessionPool?path.join(opts.outputDirectory,'cells',`i${iteration+1}-${profile}`):undefined;
      if(evidenceDirectory)fs.mkdirSync(path.dirname(evidenceDirectory),{recursive:true});
      rows.push(await runWorkerCell({profile, iteration:iteration + 1, pages:opts.pages,sessionPool:opts.sessionPool,evidenceDirectory}));
    }
  }

  const binaryPath = rows.find(row => row.runtime?.executable)?.runtime.executable || path.join(repo, '.deps/lightpanda');
  const binary = await fileInfo(binaryPath);
  const sourceFiles = ['lightpanda-benchmark.mjs','runner.mjs','runtime.mjs','lightpanda-runtime.mjs','lightpanda-shims.mjs','adapters.mjs','evidence.mjs','session-manager.mjs','session-pool.mjs','session-policy.mjs','versions.json','config.json','package-lock.json'];
  const sourceEntries = await Promise.all(sourceFiles.map(async name => [name, await fileInfo(path.join(here, name))]));
  const sourceHashes = Object.fromEntries(sourceEntries);
  const allSucceeded = rows.length === opts.iterations * profileSet.length && rows.every(row => row.errors.length === 0 && row.navigations.length === opts.pages && (!opts.sessionPool || row.main_cookie_checks_all_present));
  const raw = {
    schema_version:1, created_at:new Date().toISOString(), configuration:{release, profiles:profileSet, session_pool_comparison:opts.sessionPool, comparison_baseline:opts.sessionPool?'compat':'baseline', iterations:opts.iterations, pages_per_cell:opts.pages,
      warmups_per_cell:1, browser_per_cell:true, node_worker_per_cell:true, worker_timeout_ms:60000,
      profile_order:'rotates by iteration', sample_interval_ms:10,
      fixture_origin:'one ephemeral 127.0.0.1 server per worker', external_network:'not used',
      ...(opts.sessionPool?{arms:{compat:{tool_adapter:true,evidence:true,lightpanda:{release:'1.0.0',profile:'compat'},session_pool:false},pooled:{tool_adapter:true,evidence:true,lightpanda:{release:'1.0.0',profile:'compat'},session_pool:{max_session_uses:20}}},fixture_observe_wait_ms:100,included_in_navigation_latency:true,session_cookie:'warmup sets benchmark_session=present; fixture records boolean receipt for each main page',evidence:'each arm ledger/results/blobs saved under cells/iN-profile'}:{}),
      process_memory:'after browser setup through warmup and navigations: Node worker RSS + single Lightpanda process RSS sampled together; startup/teardown transients and parent controller excluded; shared pages may be counted twice'},
    environment:{platform:process.platform, arch:process.arch, node:process.version, kernel:os.release(), total_memory_bytes:os.totalmem()},
    sources:sourceHashes, binary, cells:rows, functional_success:allSucceeded,
  };
  const totals = aggregate(rows, opts.iterations * profileSet.length, opts.pages, profileSet, opts.sessionPool);
  const summary = {schema_version:1, created_at:raw.created_at, configuration:raw.configuration, environment:raw.environment,
    sources:raw.sources, binary, functional_success:allSucceeded, ...totals};
  fs.writeFileSync(path.join(opts.outputDirectory, 'raw.json'), `${JSON.stringify(raw, null, 2)}\n`, {flag:'wx'});
  fs.writeFileSync(path.join(opts.outputDirectory, 'summary.json'), `${JSON.stringify(summary, null, 2)}\n`, {flag:'wx'});
  console.log(JSON.stringify({outputDirectory:opts.outputDirectory, functional_success:allSucceeded, summary:summary.by_profile}, null, 2));
  if (!allSucceeded) process.exitCode = 1;
}

const workerArg = process.argv.find(arg => arg.startsWith('--worker='));
if (workerArg) {
  workerMain(JSON.parse(workerArg.slice('--worker='.length))).catch(error => {
    process.stdout.write(`${JSON.stringify({error:String(error?.stack || error)})}\n`);
    process.exitCode = 1;
  });
} else {
  main().catch(error => { console.error(`lightpanda benchmark failed: ${error.stack || error}`); process.exitCode = 1; });
}
