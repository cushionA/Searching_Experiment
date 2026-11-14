import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {installDocumentGuard} from './document-guard.mjs';
import {environmentContextOptions} from './environment.mjs';

// Stage C target selection is independent of candidate rules, artifacts, and results.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const runDir = path.join(root, 'lab-runs/structure-routing-20261115');
const require = createRequire(path.join(root, '.deps/bot-diagnostics/package.json'));
const {chromium} = require('playwright');
const robotsParser = require('robots-parser');
const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const mode = option('--mode', 'select');
const revision = option('--revision', 'v1');
if (!/^v[1-9][0-9]*$/.test(revision)) throw new Error('revision must look like v1 or v2');
const suffix = revision === 'v1' ? '' : `-${revision}`;
const manifestFile = path.join(runDir, `stage-c-independent-manifest${suffix}.json`);
const ledgerFile = path.join(runDir, `stage-c-selection-ledger${suffix}.json`);
const evidenceRoot = path.join(runDir, 'artifacts', `stage-c-selection${suffix}`);
const targets = fs.readFileSync(path.join(runDir, 'targets.jsonl'), 'utf8').trim().split(/\r?\n/).map(JSON.parse);
const selectedTargets = ['01', '08'].map(id => targets.find(x => x.id === id));
if (selectedTargets.some(x => !x?.practice)) throw new Error('authorized practice targets missing');
const MAX_PER_SITE = 30;
const MAX_TARGETS_PER_SITE = 100;
const DOC_BUDGET = 60;
const CUMULATIVE_SELECTION_CAP = 120;
const interruptedFile=path.join(runDir,'interrupted-executions.json');
function collectPriorSelectionAttempts(currentRevision){
  const attempts=[];
  for(let version=1;version<Number(currentRevision.slice(1));version++){
    const priorPath=path.join(runDir,`stage-c-selection-ledger${version===1?'':`-v${version}`}.json`);
    if(fs.existsSync(priorPath)){
      const prior=JSON.parse(fs.readFileSync(priorPath,'utf8'));
      attempts.push({revision:`v${version}`,documents:Number(prior.total_documents)||0,requests:(prior.sites||[]).reduce((sum,s)=>sum+(Number(s.network?.requests)||0),0),site_outcomes:(prior.sites||[]).map(s=>({id:s.id,stopped:s.stopped})),ledger:priorPath});
    }
  }
  if(fs.existsSync(interruptedFile)){
    const interrupted=JSON.parse(fs.readFileSync(interruptedFile,'utf8'));
    const stage=interrupted.stage_c;
    if(stage?.revision&&Number(stage.revision.slice(1))<Number(currentRevision.slice(1))){
      const reservation=Number(stage.unlogged_document_reservation);
      attempts.push({revision:stage.revision,record_type:'interrupted_reservation',documents:Number.isFinite(reservation)?reservation:0,requests:null,
        exact_documents:stage.exact_documents??null,exact_requests:stage.exact_requests??null,reason:stage.reason,partial_evidence_preserved:stage.partial_evidence_preserved,record:interruptedFile});
    }
  }
  return attempts;
}
function requestAccounting(attempts){
  const unknown=attempts.filter(item=>item.requests===null||item.requests===undefined).length;
  const known=attempts.reduce((sum,item)=>sum+(Number(item.requests)||0),0);
  return {known_lower_bound:known,unknown_attempts:unknown,exact:unknown?null:known};
}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const safeUrl = (raw, hosts) => {
  try { const u = new URL(raw); return ['http:', 'https:'].includes(u.protocol) && hosts.includes(u.hostname.toLowerCase()); }
  catch { return false; }
};
const blockedPath = /\/(?:login|logout|register|cart|checkout|account|api|privacy|terms)(?:[/?#]|$)/i;
const sameDocument = (a, b) => {
  const x = new URL(a); const y = new URL(b);
  return x.origin === y.origin && x.pathname === y.pathname && x.search === y.search;
};

if (mode === 'select') {
  if (fs.existsSync(manifestFile) || fs.existsSync(ledgerFile)) throw new Error('Stage C selection is already frozen');
  fs.mkdirSync(evidenceRoot, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH || undefined});
  const priorAttempts=collectPriorSelectionAttempts(revision);
  const priorDocuments=priorAttempts.reduce((sum,item)=>sum+item.documents,0);
  const availableSelectionDocuments=Math.max(0,Math.min(DOC_BUDGET,CUMULATIVE_SELECTION_CAP-priorDocuments,400-priorDocuments));
  const proxyEnabled=process.env.STRUCTURE_USE_ENV_PROXY==='true';
  const networkLayer=proxyEnabled?'cloud-linux-chromium151-proxy-20261115':'cloud-linux-chromium151-direct';
  const ledger = {stage:'C', revision, selection_only:true, network_layer:networkLayer, proxy_context_enabled:proxyEnabled, created_at:new Date().toISOString(), doc_budget:availableSelectionDocuments,
    per_site_max:MAX_PER_SITE, total_documents:0, prior_attempts:priorAttempts, sites:[], policy:'public list discovery; target labels are unverified title/path proxies'};
  try {
    for (const target of selectedTargets) {
      const hosts = target.allowed_hosts;
      const siteDir = path.join(evidenceRoot, target.id);
      fs.mkdirSync(siteDir, {recursive:true});
      const context = await browser.newContext({...environmentContextOptions(),locale:'ja-JP', timezoneId:'Asia/Tokyo', viewport:{width:1365,height:900}, serviceWorkers:'block'});
      const page = await context.newPage();
      const client = await context.newCDPSession(page);
      await client.send('Network.enable');
      await client.send('Network.setCacheDisabled', {cacheDisabled:true});
      const robots = new Map();
      const siteLimit=Math.min(MAX_PER_SITE,Math.max(0,availableSelectionDocuments-ledger.total_documents));
      const site = {id:target.id,start_url:target.start_url,allowed_hosts:hosts,documents:[],candidates:[],stopped:null,
        network:{requests:0,robots_attempts:0,redirect_requests:0,xhr_fetch:0,bytes:0,statuses:{}}};
      const bodyTasks=[];
      page.on('request', request => {
        site.network.requests++;
        if (request.redirectedFrom()) site.network.redirect_requests++;
        if (['xhr','fetch'].includes(request.resourceType())) site.network.xhr_fetch++;
      });
      page.on('response', response => {
        const key=String(response.status()); site.network.statuses[key]=(site.network.statuses[key]||0)+1;
        bodyTasks.push(response.body().then(body=>{site.network.bytes+=body.byteLength;}).catch(()=>{}));
      });
      const getRobots = async url => {
        const targetUrl=new URL(url); const origin=targetUrl.origin;
        if (robots.has(origin)) return robots.get(origin);
        let robotsUrl=`${origin}/robots.txt`; const seen=new Set(); let response=null; let body='';
        for(let redirects=0;redirects<=5;redirects++){
          if(seen.has(robotsUrl)) throw new Error('robots_redirect_loop');
          seen.add(robotsUrl); site.network.robots_attempts++; site.network.requests++;
          response=await context.request.get(robotsUrl,{timeout:15000,maxRedirects:0});
          const status=response.status(); const key=String(status); site.network.statuses[key]=(site.network.statuses[key]||0)+1;
          const responseBody=await response.body(); site.network.bytes+=responseBody.byteLength;
          if([401,403,429].includes(status)) throw new Error(`robots_or_access_refusal:${status}`);
          if(status>=300&&status<400){
            const location=response.headers().location;
            if(!location) throw new Error('robots_redirect_without_location');
            const next=new URL(location,robotsUrl).href;
            if(!safeUrl(next,hosts)) throw new Error('robots_redirect_outside_allowed_hosts');
            site.network.redirect_requests++; site.network.redirect_responses=(site.network.redirect_responses||0)+1;
            robotsUrl=next; continue;
          }
          body=responseBody.toString('utf8'); break;
        }
        if(!response) throw new Error('robots_request_failed');
        const parser=response.status()===200?robotsParser(`${origin}/robots.txt`,body):robotsParser(`${origin}/robots.txt`,'');
        robots.set(origin,parser); return parser;
      };
      let lastDocumentAt=0;
      let intervalChain=Promise.resolve();
      const guard=await installDocumentGuard(page,{limit:siteLimit,beforeRequest:async info=>{
        if (info.method!=='GET'||!safeUrl(info.url,hosts)) return false;
        let parser;
        try { parser=await getRobots(info.url); } catch { return false; }
        if (parser.isAllowed(info.url,'*')===false) return false;
        let release;
        const previous=intervalChain;
        intervalChain=new Promise(resolve=>{release=resolve;});
        await previous;
        try {
          const delay=Math.max(0,1000-(Date.now()-lastDocumentAt));
          if (delay) await wait(delay);
          lastDocumentAt=Date.now();
          return true;
        } finally { release(); }
      }});
      const deadline=Date.now()+10*60*1000;
      const listUrl=url=>{const p=new URL(url).pathname;return /\/(?:products|category|catalogue)\/?$/i.test(p)||/\/page[-_]\d+(?:\.html?)?\/?$/i.test(p);};
      const extract=async()=>page.locator('a[href]').evaluateAll(nodes=>nodes.map(a=>{
        const excluded=a.closest('header,nav,footer,aside,[role="navigation"],[role="banner"],[role="contentinfo"],.navbar,.footer');
        const main=a.closest('main,article,[role="main"]');
        const item=a.closest('li,article,[class*="product" i],[class*="item" i],[class*="card" i]');
        const repeated=Boolean(item&&item.parentElement&&[...item.parentElement.children].filter(n=>n.tagName===item.tagName&&(!item.className||n.className===item.className)).length>=2);
        return {href:a.href,text:(a.innerText||a.textContent||'').replace(/\s+/g,' ').trim(),aria:a.getAttribute('aria-label')||'',
          alt:a.querySelector('img[alt]')?.getAttribute('alt')||'',title:a.getAttribute('title')||'',excluded:Boolean(excluded),
          in_main:Boolean(main),repeated};
      }));
      const scoreLink=(raw,current)=>{
        let u; try {u=new URL(raw.href);} catch {return null;}
        if (!safeUrl(u.href,hosts)||blockedPath.test(u.pathname)||sameDocument(u.href,current)) return null;
        const label=(raw.text||raw.aria||raw.title||raw.alt||'').replace(/\s+/g,' ').trim();
        const pagination=/^(next|older|次へ|次|›|»|→|\d+)$/i.test(label)||/page[-/=?]\d+/i.test(`${u.pathname}${u.search}`);
        const contentArea=raw.in_main||raw.repeated;
        const detailish=!raw.excluded&&contentArea&&Boolean(label)&&(raw.repeated||(raw.in_main&&u.pathname.split('/').filter(Boolean).length>=2)||(raw.in_main&&/\.(?:html?|php)(?:$|\?)/i.test(u.pathname)));
        return {url:u.href,title_proxy:label.slice(0,240),path:u.pathname,pagination,detailish,source_url:current};
      };
      try {
        const parser=await getRobots(target.start_url);
        if (parser.isAllowed(target.start_url,'*')===false) throw new Error('robots_disallows_entry');
        const queue=[target.start_url],seenLists=new Set(),candidateMap=new Map();
        while(queue.length&&guard.counters.reserved<siteLimit&&candidateMap.size<MAX_TARGETS_PER_SITE){
          if(Date.now()>deadline) throw new Error('site_time_budget_10m');
          const url=queue.shift(); if(seenLists.has(url)) continue; seenLists.add(url);
          const index=site.documents.length+1; const response=await page.goto(url,{waitUntil:'domcontentloaded',timeout:45000});
          const source=response?await response.body().catch(()=>Buffer.alloc(0)):Buffer.alloc(0);
          const rendered=await page.content().catch(()=>'<html></html>');
          const finalUrl=page.url(); const title=await page.title().catch(()=> '');
          const entry={requested_url:url,final_url:finalUrl,status:response?.status()??null,title,source_bytes:source.byteLength,
            source_file:`doc-${String(index).padStart(2,'0')}.source.html`,dom_file:`doc-${String(index).padStart(2,'0')}.dom.html`};
          fs.writeFileSync(path.join(siteDir,entry.source_file),source);
          fs.writeFileSync(path.join(siteDir,entry.dom_file),rendered,'utf8');
          site.documents.push(entry);
          if(!response||[401,403,429].includes(response.status())) throw new Error(`access_refused:${response?.status()??'navigation_error'}`);
          for(const raw of await extract()){
            const c=scoreLink(raw,finalUrl); if(!c) continue;
            if(c.pagination&&listUrl(c.url)&&!seenLists.has(c.url)){if(!queue.includes(c.url))queue.push(c.url);}
            else if(c.detailish&&!listUrl(c.url)&&!candidateMap.has(c.url)) candidateMap.set(c.url,c);
          }
          site.candidates=[...candidateMap.values()].slice(0,MAX_TARGETS_PER_SITE);
        }
        if(queue.length&&guard.counters.reserved>=siteLimit) site.stopped='selection_document_cap';
        if(candidateMap.size<MAX_TARGETS_PER_SITE) site.stopped ||= 'fewer_public_list_candidates_observed';
      }catch(error){site.stopped=String(error.message||error);}
      await guard.seal();
      await Promise.allSettled(bodyTasks);
      site.guard={...guard.counters};
      site.network.document_attempts=guard.counters.reserved;
      site.network.document_denied=guard.counters.denied;
      ledger.total_documents+=guard.counters.reserved;
      await guard.cleanup(); await context.close(); ledger.sites.push(site);
      if(ledger.total_documents>=availableSelectionDocuments) break;
    }
  }finally{await browser.close();}
  const priorRequestAccounting=requestAccounting(priorAttempts);
  const currentSelectionRequests=ledger.sites.reduce((sum,site)=>sum+(Number(site.network?.requests)||0),0);
  const totalRequestAccounting=requestAccounting([...priorAttempts,{revision,requests:currentSelectionRequests}]);
  const manifest={schema:'stage-c-independent-targets-v1',revision,network_layer:networkLayer,proxy_context_enabled:proxyEnabled,frozen_at:new Date().toISOString(),source:'public-list discovery on authorized practice targets',
    selection_documents:ledger.total_documents,prior_selection_documents:priorDocuments,total_cap:400,independent_targets:ledger.sites.map(site=>({id:site.id,start_url:site.start_url,
      targets:site.candidates.slice(0,MAX_TARGETS_PER_SITE).map((x,i)=>({rank:i+1,url:x.url,title_proxy:x.title_proxy,source_url:x.source_url,label_status:'unverified_proxy'})),stopped:site.stopped}))};
  const bytes=JSON.stringify(manifest,null,2)+'\n'; fs.writeFileSync(manifestFile,bytes); fs.writeFileSync(ledgerFile,JSON.stringify({...ledger,prior_selection_requests:priorRequestAccounting.exact,prior_selection_requests_known_lower_bound:priorRequestAccounting.known_lower_bound,prior_selection_request_unknown_attempts:priorRequestAccounting.unknown_attempts,current_selection_requests:currentSelectionRequests,total_selection_requests:totalRequestAccounting.exact,total_selection_requests_known_lower_bound:totalRequestAccounting.known_lower_bound,manifest_sha256:sha(bytes)},null,2)+'\n');
  console.log(JSON.stringify({manifest:manifestFile,sha256:sha(bytes),selection_documents:ledger.total_documents,targets:manifest.independent_targets.map(s=>({id:s.id,count:s.targets.length}))}));
}else if(mode==='score'){
  const bytes=fs.readFileSync(manifestFile); const manifest=JSON.parse(bytes); const actualHash=sha(bytes);
  const expected=option('--manifest-sha256',''); if(expected&&expected!==actualHash) throw new Error('frozen manifest hash mismatch');
  const evaluation=[];
  const priorLedgers=collectPriorSelectionAttempts(revision);
  for(const site of manifest.independent_targets){
    const bulkDir=path.join(runDir,'artifacts',site.id,'C1','bulk'); const resultPath=path.join(bulkDir,'result.json');
    const result=JSON.parse(fs.readFileSync(resultPath,'utf8'));
    if(!Number.isFinite(Date.parse(result.started_at))) throw new Error(`bulk_started_at_missing:${site.id}`);
    if(Date.parse(result.started_at)<Date.parse(manifest.frozen_at)) throw new Error(`bulk_started_before_manifest_freeze:${site.id}`);
    const frozen=new Set(site.targets.map(x=>new URL(x.url).href)); const observations=result.observations||[];
    const reached=new Set(), useful=new Set(), successMilestones=[],attemptMilestones=[];
    let priorSuccess={docs:0,requests:0,ms:0},priorAttempt={docs:0,requests:0,ms:0},priorAttemptReached=0;
    const costs=o=>({docs:Number(o.document_attempts)||0,requests:Number(o.requests)||0,ms:Number(o.cumulative_ms)||Number(o.elapsed_ms)||0});
    for(const [i,o] of observations.entries()){
      let intended,actual; try{intended=new URL(o.requested_url).href;actual=new URL(o.url).href;}catch{continue;}
      const source=o.source_html?path.join(bulkDir,o.source_html):''; const dom=o.rendered_dom?path.join(bulkDir,o.rendered_dom):'';
      const filesOkay=Boolean(source&&dom&&fs.existsSync(source)&&fs.statSync(source).size>0&&fs.existsSync(dom)&&fs.statSync(dom).size>0);
      const valid=filesOkay&&o.valid_html===true&&o.status===200&&intended===actual&&!o.navigation_error&&frozen.has(intended);
      if(valid){reached.add(intended);useful.add(intended);}
      const c=costs(o),n=i+1;
      if([10,30,100].includes(n)){
        attemptMilestones.push({attempts:n,cumulative_independent_reached:reached.size,incremental_independent_reached:reached.size-priorAttemptReached,
          cumulative_document_cost:c.docs,cumulative_request_cost:c.requests,cumulative_time_ms:c.ms,
          incremental_document_cost:c.docs-priorAttempt.docs,incremental_request_cost:c.requests-priorAttempt.requests,incremental_time_ms:c.ms-priorAttempt.ms});
        priorAttempt=c; priorAttemptReached=reached.size;
      }
      for(const targetN of [10,30,100]) if(reached.size>=targetN&&!successMilestones.some(x=>x.independent_targets===targetN)){
        const successMilestone={independent_targets:targetN,cumulative_document_cost:c.docs,cumulative_request_cost:c.requests,cumulative_time_ms:c.ms,
        incremental_document_cost:c.docs-priorSuccess.docs,incremental_request_cost:c.requests-priorSuccess.requests,incremental_time_ms:c.ms-priorSuccess.ms};
        successMilestones.push(successMilestone); priorSuccess=c;
      }
    }
    for(const n of [10,30,100]) if(!attemptMilestones.some(x=>x.attempts===n)) attemptMilestones.push({attempts:n,unmet:true,observed_attempts:observations.length,cumulative_independent_reached:reached.size,cumulative_document_cost:null,cumulative_request_cost:null,cumulative_time_ms:null,incremental_document_cost:null,incremental_request_cost:null,incremental_time_ms:null});
    for(const targetN of [10,30,100]) if(reached.size<targetN) successMilestones.push({independent_targets:targetN,unmet:true,cumulative_document_cost:null,cumulative_request_cost:null,cumulative_time_ms:null,incremental_document_cost:null,incremental_request_cost:null,incremental_time_ms:null});
    const misses=[...frozen].filter(url=>!reached.has(url));
    evaluation.push({id:site.id,frozen_target_count:frozen.size,manifest_sha256:actualHash,attempts:observations.length,
      bulk_document_attempts:result.document_attempts??observations.length,useful_html_documents:useful.size,independent_targets_reached:reached.size,
      unreached_urls:misses,success_milestones:successMilestones,attempt_milestones:attemptMilestones,bulk_ledger:resultPath});
  }
  const bulkDocuments=evaluation.reduce((sum,site)=>sum+site.bulk_document_attempts,0);
  const priorSelectionDocuments=priorLedgers.reduce((sum,item)=>sum+item.documents,0);
  const totalDocuments=priorSelectionDocuments+manifest.selection_documents+bulkDocuments;
  const priorRequestAccounting=requestAccounting(priorLedgers);
  const currentSelectionRequests=JSON.parse(fs.readFileSync(ledgerFile,'utf8')).sites.reduce((sum,s)=>sum+(Number(s.network?.requests)||0),0);
  const totalRequestAccounting=requestAccounting([...priorLedgers,{revision,requests:currentSelectionRequests}]);
  if(Number(manifest.prior_selection_documents)!==priorSelectionDocuments) throw new Error('prior_selection_document_accounting_mismatch');
  if(priorSelectionDocuments+manifest.selection_documents>CUMULATIVE_SELECTION_CAP) throw new Error(`Cumulative Stage C selection cap exceeded: ${priorSelectionDocuments+manifest.selection_documents}/${CUMULATIVE_SELECTION_CAP}`);
  if(totalDocuments>400) throw new Error(`Stage C document cap exceeded: ${totalDocuments}/400`);
  const out={schema:`stage-c-score-${revision}`,revision,network_layer:manifest.network_layer||'unknown',proxy_context_enabled:manifest.proxy_context_enabled??false,scored_at:new Date().toISOString(),manifest_sha256:actualHash,selection_documents:manifest.selection_documents,
    prior_selection_documents:priorSelectionDocuments,bulk_documents:bulkDocuments,total_documents:totalDocuments,
    prior_selection_requests:priorRequestAccounting.exact,prior_selection_requests_known_lower_bound:priorRequestAccounting.known_lower_bound,prior_selection_request_unknown_attempts:priorRequestAccounting.unknown_attempts,current_selection_requests:currentSelectionRequests,total_selection_requests:totalRequestAccounting.exact,total_selection_requests_known_lower_bound:totalRequestAccounting.known_lower_bound,
    prior_selection_attempts:priorLedgers,cumulative_selection_cap:CUMULATIVE_SELECTION_CAP,bulk_document_budget:400-totalDocuments+bulkDocuments,bulk_document_budget_per_site:Math.floor((400-totalDocuments+bulkDocuments)/2),stage_c_total_cap:400,sites:evaluation};
  const output=path.join(runDir,`stage-c-score${suffix}.json`); fs.writeFileSync(output,JSON.stringify(out,null,2)+'\n');
  console.log(JSON.stringify({output,manifest_sha256:actualHash,total_documents:totalDocuments,sites:evaluation.map(x=>({id:x.id,targets:x.frozen_target_count,reached:x.independent_targets_reached}))}));
}else throw new Error('Use --mode select or --mode score');
