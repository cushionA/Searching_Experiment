#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {openBrowser} from './runner.mjs';
import {Evidence, verify} from './evidence.mjs';
import {snapshotSources} from './runtime.mjs';

const here=path.dirname(fileURLToPath(import.meta.url));
const availableTools=['rebrowser-lightpanda','patchright','obscura','obscura-stealth','obscura-no-render','obscura-patched'];
const defaultTools=availableTools.slice(0,5);
const pagesDefault=8, iterationsDefault=5, iterationsLimit=20, requestLimit=25, byteLimit=8*1024*1024;
const lightpanda={release:'1.0.0',profile:'compat'};
const pageSize=Number(os.constants?.PAGE_SIZE)||4096;

function parseArgs(argv) {
  let output,iterations=iterationsDefault,pages=pagesDefault,tools=defaultTools.slice();
  for(const arg of argv) {
    const match=/^--(iterations|pages)=(\d+)$/.exec(arg);
    if(match) {
      const value=Number(match[2]);
      if(!Number.isSafeInteger(value)||value<1)throw new Error(`invalid ${match[1]}`);
      if(match[1]==='iterations')iterations=value;else pages=value;
    } else if(arg.startsWith('--tools=')) {
      tools=arg.slice('--tools='.length).split(',');
      if(!tools.length||tools.some(tool=>!availableTools.includes(tool))||new Set(tools).size!==tools.length)
        throw new Error(`invalid tools; choose unique names from ${availableTools.join(',')}`);
    } else if(arg.startsWith('--'))throw new Error(`unknown option: ${arg}`);
    else if(output)throw new Error('provide one output directory');else output=arg;
  }
  if(!output)throw new Error('usage: node engine-core-benchmark.mjs NEW_OUTPUT_DIRECTORY [--iterations=5] [--pages=8] [--tools=name,name]');
  if(iterations>iterationsLimit)throw new Error(`iterations limited to ${iterationsLimit}`);
  if(pages>11)throw new Error('pages limited to 11 by the unchanged 25-request evidence budget');
  return {output:path.resolve(output),iterations,pages,tools};
}
function median(values) {
  const xs=values.filter(Number.isFinite).slice().sort((a,b)=>a-b);
  if(!xs.length)return null;const mid=Math.floor(xs.length/2);return xs.length%2?xs[mid]:(xs[mid-1]+xs[mid])/2;
}
function procStat(pid) {
  try {
    const raw=fs.readFileSync(`/proc/${pid}/stat`,'utf8'),close=raw.lastIndexOf(')');
    const fields=raw.slice(close+2).trim().split(/\s+/);
    return {pid:Number(pid),ppid:Number(fields[1]),start:fields[19],rss:Number(fields[21])*pageSize};
  } catch{return null;}
}
function treeSnapshot(rootPid,seen) {
  if(process.platform!=='linux')return {bytes:null,pssBytes:null,pids:[]};
  const processes=[];
  try {for(const name of fs.readdirSync('/proc'))if(/^\d+$/.test(name)){const item=procStat(name);if(item)processes.push(item);}}catch{}
  const byPid=new Map(processes.map(item=>[item.pid,item])),root=byPid.get(rootPid);
  const identities=new Set(root?[`${root.pid}:${root.start}`]:[]);
  for(const id of seen){const [pid,start]=id.split(':');if(byPid.get(Number(pid))?.start===start)identities.add(id);}
  let changed=true;
  while(changed){changed=false;for(const item of processes){const id=`${item.pid}:${item.start}`;
    if(identities.has(id))continue;const parent=byPid.get(item.ppid);
    if(parent&&identities.has(`${parent.pid}:${parent.start}`)){identities.add(id);changed=true;}}}
  let bytes=0,pssBytes=0,pssAvailable=true;const pids=[];
  for(const id of identities){seen.add(id);const [pid,start]=id.split(':'),item=byPid.get(Number(pid));
    if(item?.start===start){bytes+=item.rss;pids.push(item.pid);
      try {const rollup=fs.readFileSync(`/proc/${item.pid}/smaps_rollup`,'utf8');const match=/^Pss:\s+(\d+)\s+kB$/m.exec(rollup);
        if(match)pssBytes+=Number(match[1])*1024;else pssAvailable=false;
      } catch {pssAvailable=false;}
    }}
  return {bytes,pssBytes:pssAvailable?pssBytes:null,pids};
}

function makeFixture(evidence,key,cell) {
  const counts=new Map(),cookieChecks=[];
  const server=http.createServer((request,response)=>{
    const url=new URL(request.url,'http://127.0.0.1'),token=url.searchParams.get('cell')||cell;
    counts.set(token,(counts.get(token)||0)+1);
    const id=url.pathname==='/home'?'warmup':url.searchParams.get('id')||'missing';
    let status=200,headers={'content-type':'text/html; charset=utf-8','cache-control':'no-store'},body;
    if(request.method!=='GET') {status=405;headers={'content-type':'text/plain'};body=Buffer.from('method not allowed');}
    else if(url.pathname!=='/home'&&url.pathname!=='/page') {status=404;headers={'content-type':'text/plain'};body=Buffer.from('not found');}
    else {
      if(id==='warmup')headers['set-cookie']='benchmark_session=present; Path=/; SameSite=Lax';
      else cookieChecks.push(/(?:^|;\s*)benchmark_session=present(?:;|$)/.test(request.headers.cookie||''));
      const expected={id,title:`Engine fixture ${id}`,expected:`expected-${id}`,values:[id,'json-data',42]};
      const nodes=Array.from({length:150},(_,n)=>`<div class="load" data-n="${n}">node-${n}</div>`).join('');
      body=Buffer.from(`<!doctype html><html><head><meta charset="utf-8"><title>${expected.title}</title></head><body>
        <main id="fixture" data-page="${id}"><script id="payload" type="application/json">${JSON.stringify(expected)}</script>
        ${nodes}<div id="generated"></div></main><script>const root=document.querySelector('#fixture');const p=document.createElement('p');
        p.textContent='generated-'+root.getAttribute('data-page');document.querySelector('#generated').appendChild(p);</script></body></html>`);
    }
    const recordUrl=new URL(request.url,`http://${request.headers.host}`).href;
    const record=evidence.reserve(key,recordUrl,'fixture_get');
    evidence.finish(record,status,headers,body);
    response.writeHead(status,headers);response.end(body);
  });
  return {counts,cookieChecks,async start(){await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});return `http://127.0.0.1:${server.address().port}`;},
    async close(){if(server.listening)await new Promise(resolve=>server.close(resolve));}};
}
function extractPage(page) {
  return page.evaluate(()=>({title:document.title,page:document.querySelector('#fixture')?.getAttribute('data-page')||null,
    json:document.querySelector('#payload')?.textContent||null,generated:document.querySelector('#generated p')?.textContent||null,
    load_nodes:document.querySelectorAll('.load').length}));
}
function failedRow(job,message) {
  return {tool:job.tool,iteration:job.iteration,cell_id:job.cell,startup_ms:null,warmup_ms:null,latencies_ms:[],
    median_latency_ms:null,requests:null,evidence_requests:null,evidence_bytes:null,main_cookie_checks:[],cookie_success:false,
    peak_total_rss_sum_bytes:null,peak_work_rss_sum_bytes:null,peak_total_pss_bytes:null,peak_work_pss_bytes:null,
    peak_pids:[],runtime:null,errors:[{name:'WorkerError',message}],evidence_directory:job.evidence};
}

async function runCell(job) {
  const evidence=new Evidence(job.evidence),key=`${job.tool}/${job.cell}`;
  evidence.save('sources.json',snapshotSources(evidence));
  const fixture=makeFixture(evidence,key,job.cell),seenProcesses=new Set(),observedPids=new Set();
  const row={tool:job.tool,iteration:job.iteration,cell_id:job.cell,startup_ms:null,warmup_ms:null,latencies_ms:[],
    median_latency_ms:null,requests:0,evidence_requests:0,evidence_bytes:0,main_cookie_checks:[],cookie_success:false,
    peak_total_rss_sum_bytes:null,peak_work_rss_sum_bytes:null,peak_total_pss_bytes:null,peak_work_pss_bytes:null,
    peak_pids:[],runtime:null,errors:[],evidence_directory:job.evidence};
  let browser,sampler,workPhase=false,peakTotal=0,peakWork=0,peakTotalPss=0,peakWorkPss=0,pssSamples=0,origin;
  const sample=()=>{const current=treeSnapshot(process.pid,seenProcesses);if(current.bytes===null)return;
    peakTotal=Math.max(peakTotal,current.bytes);if(workPhase)peakWork=Math.max(peakWork,current.bytes);
    if(current.pssBytes!==null){pssSamples++;peakTotalPss=Math.max(peakTotalPss,current.pssBytes);if(workPhase)peakWorkPss=Math.max(peakWorkPss,current.pssBytes);}
    for(const pid of current.pids)observedPids.add(pid);};
  try {
    origin=await fixture.start();
    const started=performance.now();
    sampler=setInterval(sample,50);sample();
    browser=await openBrowser(job.tool,{fixture:true,...(job.tool==='rebrowser-lightpanda'?{lightpanda}:{})});
    row.startup_ms=performance.now()-started;row.runtime=browser.runtime||null;
    sample();
    const visit=async(url,id,warmup=false)=>{
      const began=performance.now();
      await browser.page.goto(url,{waitUntil:'load',timeout:30000});
      const extracted=await extractPage(browser.page);
      const expected={id,title:`Engine fixture ${id}`,expected:`expected-${id}`,values:[id,'json-data',42]};
      if(extracted.page!==id||extracted.title!==expected.title||extracted.generated!==`generated-${id}`
        ||extracted.load_nodes!==150||extracted.json!==JSON.stringify(expected))
        throw new Error(`shared DOM predicate failed for ${id}: ${JSON.stringify(extracted)}`);
      const elapsed=performance.now()-began;
      if(warmup){row.warmup_ms=elapsed;workPhase=true;}else row.latencies_ms.push(elapsed);
      sample();
    };
    await visit(`${origin}/home?cell=${encodeURIComponent(job.cell)}`,'warmup',true);
    for(let n=0;n<job.pages;n++)await visit(`${origin}/page?id=${n}&cell=${encodeURIComponent(job.cell)}`,String(n));
    await new Promise(resolve=>setTimeout(resolve,25));
  } catch(error) {row.errors.push({name:error?.name||'Error',message:String(error?.message||error)});}
  finally {
    sample();
    if(browser)try{await browser.close();}catch(error){row.errors.push({name:error?.name||'Error',message:`close: ${String(error?.message||error)}`});}
    sample();if(sampler)clearInterval(sampler);await fixture.close();
    row.requests=fixture.counts.get(job.cell)||0;
    row.main_cookie_checks=fixture.cookieChecks.slice();
    row.cookie_success=row.main_cookie_checks.length===job.pages&&row.main_cookie_checks.every(Boolean);
    if(row.requests>requestLimit)row.errors.push({name:'RequestBudgetError',message:`fixture GETs ${row.requests} exceeded ${requestLimit}`});
    if(!row.cookie_success)row.errors.push({name:'CookiePredicateError',message:'not every page received the warmup cookie'});
    if(row.latencies_ms.length!==job.pages)row.errors.push({name:'PagePredicateError',message:'not every page completed the DOM predicate'});
    const budget=evidence.budgets[key]||{};row.evidence_requests=budget.requests||0;row.evidence_bytes=budget.bytes_charged||0;
    row.peak_total_rss_sum_bytes=peakTotal||null;row.peak_work_rss_sum_bytes=workPhase?(peakWork||null):null;
    row.peak_total_pss_bytes=pssSamples?peakTotalPss:null;row.peak_work_pss_bytes=workPhase&&pssSamples?peakWorkPss:null;
    row.peak_pids=[...observedPids].sort((a,b)=>a-b);row.evidence_records=evidence.records.length;row.evidence_results=evidence.results.length;
    row.verification=verify(job.evidence);evidence.save('verification.json',row.verification);
    if(!row.verification.ok)row.errors.push({name:'EvidenceVerificationError',message:row.verification.errors.join(',')});
    row.median_latency_ms=row.errors.length?null:median(row.latencies_ms);
    if(row.errors.length){row.startup_ms=null;row.warmup_ms=null;row.peak_total_rss_sum_bytes=null;row.peak_work_rss_sum_bytes=null;
      row.peak_total_pss_bytes=null;row.peak_work_pss_bytes=null;}
  }
  return row;
}
function signalTree(child,signal){try{if(process.platform==='win32')child.kill(signal);else process.kill(-child.pid,signal);}catch{try{child.kill(signal);}catch{}}}
async function runWorker(job) {
  let child;const started=performance.now();
  try{child=spawn(process.execPath,[fileURLToPath(import.meta.url),`--worker=${JSON.stringify(job)}`],{stdio:['ignore','pipe','pipe'],detached:process.platform!=='win32'});}
  catch(error){return failedRow(job,String(error?.message||error));}
  let stdout='',stderr='',timedOut=false,killTimer;
  child.stdout.on('data',chunk=>{stdout=(stdout+chunk).slice(-2_000_000);});child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-16_000);});
  const exited=new Promise(resolve=>{child.once('error',error=>resolve({error:String(error)}));child.once('close',(code,signal)=>resolve({code,signal}));});
  const timeout=setTimeout(()=>{timedOut=true;signalTree(child,'SIGTERM');killTimer=setTimeout(()=>signalTree(child,'SIGKILL'),5000);},60000);
  const status=await exited;clearTimeout(timeout);if(killTimer)clearTimeout(killTimer);if(timedOut)signalTree(child,'SIGKILL');
  let row;try{row=JSON.parse(stdout.trim().split(/\r?\n/).at(-1)).row;}catch{}
  if(!row)row=failedRow(job,timedOut?'worker timeout after 60 seconds':status.error||`worker exited ${status.code}/${status.signal}: ${stderr.slice(-2500)}`);
  row.worker_elapsed_ms=performance.now()-started;
  if(timedOut)row.errors.push({name:'WorkerTimeout',message:'worker timeout after 60 seconds'});
  if(status.code!==0&&!row.errors.length)row.errors.push({name:'WorkerError',message:`worker exit code ${status.code}`});
  if(row.errors.length){row.median_latency_ms=null;row.startup_ms=null;row.warmup_ms=null;row.peak_total_rss_sum_bytes=null;row.peak_work_rss_sum_bytes=null;
    row.peak_total_pss_bytes=null;row.peak_work_pss_bytes=null;}
  return row;
}
function summarize(rows,tools) {
  const byTool={};
  for(const tool of tools){const cells=rows.filter(row=>row.tool===tool),good=cells.filter(row=>!row.errors.length);
    byTool[tool]={cells:cells.length,successful_cells:good.length,failed_cells:cells.length-good.length,
      median_startup_ms:median(good.map(row=>row.startup_ms)),median_warmup_ms:median(good.map(row=>row.warmup_ms)),
      median_page_latency_ms:median(good.flatMap(row=>row.latencies_ms)),
      median_peak_total_rss_sum_bytes:median(good.map(row=>row.peak_total_rss_sum_bytes)),median_peak_work_rss_sum_bytes:median(good.map(row=>row.peak_work_rss_sum_bytes)),
      median_peak_total_pss_bytes:median(good.map(row=>row.peak_total_pss_bytes)),median_peak_work_pss_bytes:median(good.map(row=>row.peak_work_pss_bytes)),
      median_fixture_gets:median(good.map(row=>row.requests)),
      available:cells.some(row=>row.runtime!==null)};}
  return byTool;
}
function checkpoint(output,configuration,environment,sources,rows) {
  const data={schema_version:1,updated_at:new Date().toISOString(),configuration,environment,source_sha256:sources,completed_cells:rows.length,cells:rows};
  const temp=path.join(output,'raw.checkpoint.tmp');fs.writeFileSync(temp,`${JSON.stringify(data,null,2)}\n`);fs.renameSync(temp,path.join(output,'raw.checkpoint.json'));
}
async function main() {
    const options=parseArgs(process.argv.slice(2));fs.mkdirSync(path.dirname(options.output),{recursive:true});fs.mkdirSync(options.output);
  const sources={};for(const name of fs.readdirSync(here).filter(name=>/\.(mjs|json|py)$/.test(name)&&!name.endsWith('.test.mjs')).sort())
    sources[name]=crypto.createHash('sha256').update(fs.readFileSync(path.join(here,name))).digest('hex');
  const configuration={tools:options.tools,iterations:options.iterations,pages_per_cell:options.pages,warmups_per_cell:1,rotating_tool_order:true,
    worker_timeout_ms:60000,sample_interval_ms:50,lightpanda,fixture:'private ephemeral 127.0.0.1 server per independent Node worker',
    external_network:false,external_assets:false,evidence:'each fixture GET reserved and completed through Evidence; one per-tool/cell budget, max 25 requests and 8 MiB',
    pool_mode:false,session:'one browser context per cell; warmup Set-Cookie and common main-page receipt predicate',
    latency:'page.goto(load) plus identical title/JSON/JS-node-count extraction; no artificial observation sleep',
    rss:'worker and every observed descendant /proc RSS sum, includes shared-page double counting; parent controller excluded; work peak begins after warmup',
    pss:'worker and every observed descendant PSS from /proc/PID/smaps_rollup sampled every 50ms; parent controller excluded; unavailable or permission denied is null; work peak begins after warmup',
    sampling:'50ms process-tree RSS/PSS reads add shared measurement overhead to all arms'};
  const environment={platform:process.platform,arch:process.arch,node:process.version,kernel:os.release(),total_memory_bytes:os.totalmem()};
  const rows=[];
  for(let iteration=1;iteration<=options.iterations;iteration++){
    const offset=(iteration-1)%options.tools.length;
    for(let index=0;index<options.tools.length;index++){
      const tool=options.tools[(index+offset)%options.tools.length],cell=`i${iteration}-${tool}`,evidence=path.join(options.output,'cells',cell);
      fs.mkdirSync(path.dirname(evidence),{recursive:true});
      const row=await runWorker({tool,iteration,cell,pages:options.pages,evidence});rows.push(row);
      checkpoint(options.output,configuration,environment,sources,rows);
    }
  }
  const success=rows.length===options.iterations*options.tools.length&&rows.every(row=>!row.errors.length&&row.requests<=requestLimit&&row.evidence_bytes<=byteLimit);
  const raw={schema_version:1,created_at:new Date().toISOString(),configuration,environment,source_sha256:sources,cells:rows,functional_success:success};
  const summary={schema_version:1,created_at:raw.created_at,configuration,environment,source_sha256:sources,functional_success:success,by_tool:summarize(rows,options.tools)};
  fs.writeFileSync(path.join(options.output,'raw.json'),`${JSON.stringify(raw,null,2)}\n`,{flag:'wx'});
  fs.writeFileSync(path.join(options.output,'summary.json'),`${JSON.stringify(summary,null,2)}\n`,{flag:'wx'});
  console.log(JSON.stringify({output:options.output,functional_success:success,by_tool:summary.by_tool},null,2));
  if(!success)process.exitCode=1;
}

const workerArgument=process.argv.find(argument=>argument.startsWith('--worker='));
if(workerArgument)runCell(JSON.parse(workerArgument.slice('--worker='.length))).then(row=>process.stdout.write(`${JSON.stringify({row})}\n`))
  .catch(error=>{process.stdout.write(`${JSON.stringify({error:String(error?.stack||error)})}\n`);process.exitCode=1;});
else main().catch(error=>{console.error(`engine core benchmark failed: ${error.stack||error}`);process.exitCode=1;});
