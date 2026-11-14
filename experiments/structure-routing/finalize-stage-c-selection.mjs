import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

// Offline-only finalizer for an interrupted Stage C selection revision.
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const runDir=path.join(root,'lab-runs/structure-routing-20261115');
const require=createRequire(path.join(root,'.deps/bot-diagnostics/package.json'));
const {chromium}=require('playwright');
const argv=process.argv.slice(2);
const option=(name,fallback)=>argv.includes(name)?argv[argv.indexOf(name)+1]:fallback;
const revision=option('--revision','v4');
if(revision!=='v4') throw new Error('This offline finalizer is restricted to v4 partial evidence');
const interruptionFile=path.join(runDir,'stage-c-v4-interruption.json');
const interruption=JSON.parse(fs.readFileSync(interruptionFile,'utf8'));
if(interruption.revision!=='v4'||interruption.reserved_documents!==58) throw new Error('unexpected v4 interruption reservation');
const manifestFile=path.join(runDir,'stage-c-independent-manifest-v4.json');
const ledgerFile=path.join(runDir,'stage-c-selection-ledger-v4.json');
if(fs.existsSync(manifestFile)||fs.existsSync(ledgerFile)) throw new Error('v4 freeze already exists; refusing overwrite');
const evidenceRoot=path.join(runDir,'artifacts/stage-c-selection-v4');
const targets=fs.readFileSync(path.join(runDir,'targets.jsonl'),'utf8').trim().split(/\r?\n/).map(JSON.parse);
const siteTargets=['01','08'].map(id=>targets.find(item=>item.id===id));
if(siteTargets.some(item=>!item?.practice)) throw new Error('authorized practice target missing');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const blocked=/\/(?:login|logout|register|cart|checkout|account|api|privacy|terms)(?:[/?#]|$)/i;
const allowed=(value,hosts)=>{try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)&&hosts.includes(u.hostname.toLowerCase());}catch{return false;}};
const sameDoc=(a,b)=>{const x=new URL(a),y=new URL(b);return x.origin===y.origin&&x.pathname===y.pathname&&x.search===y.search;};
const fileHash=file=>sha(fs.readFileSync(file));
const pageNoFromText=value=>{const m=String(value||'').match(/(?:page\s*)?(\d+)(?:\s+of\s+\d+)?/i);return m?Number(m[1]):null;};
const pageNoFromHref=(raw,label='')=>{
  try{
    const u=new URL(raw,'https://offline.invalid/');
    for(const key of ['page','p','page_num','pageNumber']) if(u.searchParams.has(key)){const n=Number(u.searchParams.get(key));if(Number.isInteger(n)&&n>0)return n;}
    let m=u.pathname.match(/(?:^|\/)page[-_/](\d+)(?:\.html?)?\/?$/i);if(m)return Number(m[1]);
  }catch{}
  const n=String(label||'').trim().match(/^\d+$/);return n?Number(n[0]):null;
};

const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'/usr/bin/chromium'});
const context=await browser.newContext({javaScriptEnabled:false,serviceWorkers:'block'});
await context.setOffline(true);
await context.route('**/*',route=>route.abort());
const page=await context.newPage();
const sites=[];
try{
  for(const target of siteTargets){
    const dir=path.join(evidenceRoot,target.id);
    const files=fs.readdirSync(dir).filter(name=>/^doc-\d+\.dom\.html$/.test(name)).sort((a,b)=>Number(a.match(/\d+/)[0])-Number(b.match(/\d+/)[0]));
    if(!files.length) throw new Error(`no_saved_dom:${target.id}`);
    const docs=[];
    for(const domFile of files){
      const match=domFile.match(/^(doc-\d+)\.dom\.html$/);
      const sourceFile=`${match[1]}.source.html`;
      const domPath=path.join(dir,domFile),sourcePath=path.join(dir,sourceFile);
      if(!fs.existsSync(sourcePath)||fs.statSync(sourcePath).size===0||fs.statSync(domPath).size===0) throw new Error(`incomplete_saved_document:${target.id}:${match[1]}`);
      const html=fs.readFileSync(domPath,'utf8');
      const analysis=await page.evaluate(markup=>{
        const d=new DOMParser().parseFromString(markup,'text/html');
        const current=d.querySelector('[aria-current="page"]');
        const currentText=current?.textContent||d.querySelector('.current,[class~="current"]')?.textContent||'';
        const currentNumber=(()=>{const a=current?.getAttribute('aria-label')||currentText;const m=a.match(/(?:page\s*)?(\d+)(?:\s+of\s+\d+)?/i);return m?Number(m[1]):null;})();
        const anchors=[...d.querySelectorAll('a[href]')].map(a=>{
          const excluded=a.closest('header,nav,footer,aside,[role="navigation"],[role="banner"],[role="contentinfo"],.navbar,.footer');
          const main=a.closest('main,article,[role="main"]');
          const item=a.closest('li,article,[class*="product" i],[class*="item" i],[class*="card" i]');
          const repeated=Boolean(item&&item.parentElement&&[...item.parentElement.children].filter(n=>n.tagName===item.tagName&&(!item.className||n.className===item.className)).length>=2);
          return {href:a.getAttribute('href'),text:(a.innerText||a.textContent||'').replace(/\s+/g,' ').trim(),aria:a.getAttribute('aria-label')||'',
            alt:a.querySelector('img[alt]')?.getAttribute('alt')||'',title:a.getAttribute('title')||'',excluded:Boolean(excluded),in_main:Boolean(main),repeated};
        });
        return {currentNumber,anchors,title:d.title||''};
      },html);
      docs.push({domFile,sourceFile,domPath,sourcePath,domSha256:fileHash(domPath),sourceSha256:fileHash(sourcePath),analysis,url:null});
    }
    const urlQueues=new Map();
    const addUrl=(n,url,kind)=>{
      if(!Number.isInteger(n)||n<1)return;
      const list=urlQueues.get(n)||[];
      if(!list.some(item=>item.url===url))list.push({url,kind});
      urlQueues.set(n,list);
    };
    const assigned=new Set();
    const first=docs[0]; first.url=target.start_url; assigned.add(first);
    addUrl(first.analysis.currentNumber||1,target.start_url,'authorized_seed');
    let progress=true;
    while(progress){
      progress=false;
      // Explicit current-page anchors are observed URL evidence.
      for(const doc of docs){
        if(doc.url)continue;
        const active=doc.analysis.anchors.find(a=>/page/i.test(a.aria)&&/current/i.test(a.aria));
        if(active?.href){
          const href=new URL(active.href,target.start_url).href;
          const n=doc.analysis.currentNumber||pageNoFromText(active.aria)||pageNoFromHref(href,active.text);
          if(n){doc.url=href;assigned.add(doc);addUrl(n,href,'observed_current_page_link');progress=true;}
        }
      }
      // Replay only pagination links in the saved documents; no network access is possible.
      for(const doc of docs){
        if(!doc.url)continue;
        const current=doc.analysis.currentNumber||1;
        for(const a of doc.analysis.anchors){
          const label=(a.text||a.aria||'').trim();
          const isNext=/^(next|forward|older|次へ|次|›|»|→)$/i.test(label)||/\bnext\b|forward/i.test(a.aria);
          const isPrev=/^(previous|prev|back|前へ|‹|«|←)$/i.test(label)||/\bprevious page\b|\bback\b/i.test(a.aria);
          const explicit=pageNoFromHref(a.href,label);
          const targetNo=explicit||(isNext?current+1:isPrev?current-1:null);
          if(!targetNo)continue;
          let href;try{href=new URL(a.href,doc.url).href;}catch{continue;}
          if(!allowed(href,target.allowed_hosts))continue;
          addUrl(targetNo,href,'observed_pagination_link');
        }
      }
      // Assign the next saved observation for each page number using a URL directly observed in a pager.
      for(const doc of docs){
        if(doc.url)continue;
        const n=doc.analysis.currentNumber;
        if(!n)continue;
        const queue=urlQueues.get(n)||[];
        const candidate=queue.find(item=>!assigned.has(item.url));
        if(candidate){doc.url=candidate.url;assigned.add(doc);progress=true;}
      }
    }
    const unresolved=docs.filter(doc=>!doc.url);
    if(unresolved.length) throw new Error(`could_not_reconstruct_saved_list_url:${target.id}:${unresolved.map(x=>x.domFile).join(',')}`);
    const candidates=new Map();
    for(const doc of docs){
      let savedHtml=fs.readFileSync(doc.domPath,'utf8');
      const baseTag=`<base href="${doc.url.replace(/&/g,'&amp;').replace(/\"/g,'&quot;')}">`;
      if(/<head\b[^>]*>/i.test(savedHtml)) savedHtml=savedHtml.replace(/<head\b[^>]*>/i,match=>`${match}${baseTag}`);
      else savedHtml=`<head>${baseTag}</head>${savedHtml}`;
      await page.setContent(savedHtml,{waitUntil:'domcontentloaded'});
      const anchors=await page.locator('a[href]').evaluateAll(nodes=>nodes.map(a=>{
        const excluded=a.closest('header,nav,footer,aside,[role="navigation"],[role="banner"],[role="contentinfo"],.navbar,.footer');
        const main=a.closest('main,article,[role="main"]');
        const item=a.closest('li,article,[class*="product" i],[class*="item" i],[class*="card" i]');
        const repeated=Boolean(item&&item.parentElement&&[...item.parentElement.children].filter(n=>n.tagName===item.tagName&&(!item.className||n.className===item.className)).length>=2);
        return {href:a.href,text:(a.innerText||a.textContent||'').replace(/\s+/g,' ').trim(),aria:a.getAttribute('aria-label')||'',alt:a.querySelector('img[alt]')?.getAttribute('alt')||'',title:a.getAttribute('title')||'',excluded:Boolean(excluded),in_main:Boolean(main),repeated};
      }));
      for(const raw of anchors){
        let u;try{u=new URL(raw.href,doc.url);}catch{continue;}
        if(!allowed(u.href,target.allowed_hosts)||blocked.test(u.pathname)||sameDoc(u.href,doc.url))continue;
        const label=(raw.text||raw.aria||raw.title||raw.alt||'').replace(/\s+/g,' ').trim();
        const pagination=/^(next|forward|older|previous|prev|back|次へ|次|前へ|›|»|→|‹|«|←|\d+)$/i.test(label)||/page[-/=?]\d+/i.test(`${u.pathname}${u.search}`)||/\bpage\s+\d+\b/i.test(raw.aria);
        const detailish=!raw.excluded&&(raw.in_main||raw.repeated)&&Boolean(label)&&(raw.repeated||(raw.in_main&&u.pathname.split('/').filter(Boolean).length>=2)||(raw.in_main&&/\.(?:html?|php)(?:$|\?)/i.test(u.pathname)));
        const isList=/\/(?:products|category|catalogue)\/?$/i.test(u.pathname)||/\/page[-_]\d+(?:\.html?)?\/?$/i.test(u.pathname);
        if(!(detailish&&!isList)||pagination)continue;
        if(!candidates.has(u.href))candidates.set(u.href,{url:u.href,title_proxy:label.slice(0,240),source_url:doc.url,
          source_document:doc.domFile,source_html:doc.sourceFile,label_status:'unverified_proxy'});
      }
    }
    const selected=[...candidates.values()].slice(0,100).map((item,index)=>({rank:index+1,...item}));
    sites.push({id:target.id,start_url:target.start_url,allowed_hosts:target.allowed_hosts,observed_list_documents:docs.length,
      charged_document_reservation:null,targets:selected,stopped:selected.length<100?'fewer_than_100_unique_targets_in_preserved_lists':null,
      source_documents:docs.map(doc=>({dom_file:doc.domFile,source_file:doc.sourceFile,source_url:doc.url,dom_sha256:doc.domSha256,source_sha256:doc.sourceSha256}))});
  }
}finally{await context.close();await browser.close();}
if(sites.some(site=>site.targets.length<100)) throw new Error(`insufficient_independent_targets:${sites.map(site=>`${site.id}:${site.targets.length}`).join(',')}`);
const siteLowerBounds=Object.fromEntries(sites.map(site=>[site.id,site.observed_list_documents+1]));
const currentLowerBound=Object.values(siteLowerBounds).reduce((sum,n)=>sum+n,0); // At least one document request per saved page plus one robots request per site.
const manifest={schema:'stage-c-independent-targets-v1',revision:'v4',finalization:'offline_from_preserved_independent_list_observations',network_layer:'cloud-linux-chromium151-proxy-20261115',
  proxy_context_enabled:true,frozen_at:new Date().toISOString(),source:'offline pagination replay over preserved authorized public-list observations',
  selection_documents:58,observed_list_documents:sites.reduce((sum,site)=>sum+site.observed_list_documents,0),prior_selection_documents:62,
  prior_selection_attempts:[
    {revision:'v1',documents:0,requests:2},
    {revision:'v2',record_type:'interrupted_reservation',documents:60,exact_documents:null,requests:null,exact_requests:null,reason:'Interrupted before freeze after guard teardown discrepancy detected elsewhere',partial_evidence_preserved:'artifacts/stage-c-selection-v2'},
    {revision:'v3',documents:2,requests:4}
  ],charged_selection_documents_total:120,total_cap:400,independent_targets:sites.map(site=>({id:site.id,start_url:site.start_url,
    targets:site.targets.map(({source_html,...item})=>item),stopped:site.stopped}))};
const ledger={stage:'C',revision:'v4',offline_finalization:true,started_at:interruption.at,finished_at:new Date().toISOString(),
  network_layer:'cloud-linux-chromium151-proxy-20261115',proxy_context_enabled:true,total_documents:58,document_attempts_exact:null,
  unlogged_document_reservation:58,observed_list_documents:11,exact_requests:null,requests_known_lower_bound:currentLowerBound,
  current_selection_requests:null,current_selection_requests_known_lower_bound:currentLowerBound,total_selection_requests:null,
  total_selection_requests_known_lower_bound:2+4+currentLowerBound,prior_selection_documents:62,charged_selection_documents_total:120,
  prior_attempts:manifest.prior_selection_attempts,sites:sites.map(site=>({id:site.id,start_url:site.start_url,observed_list_documents:site.observed_list_documents,
    document_attempts_exact:null,charged_document_reservation:null,network:{requests:null,requests_known_lower_bound:siteLowerBounds[site.id],
      robots_attempts_lower_bound:1,observed_document_responses_lower_bound:site.observed_list_documents},
    candidates:site.targets,source_documents:site.source_documents,stopped:site.stopped})),interruption_record:'stage-c-v4-interruption.json'};
const manifestBytes=JSON.stringify(manifest,null,2)+'\n';
const ledgerBytes=JSON.stringify({...ledger,manifest_sha256:sha(manifestBytes)},null,2)+'\n';
fs.writeFileSync(manifestFile,manifestBytes,{flag:'wx'});
fs.writeFileSync(ledgerFile,ledgerBytes,{flag:'wx'});
console.log(JSON.stringify({manifest:manifestFile,ledger:ledgerFile,manifest_sha256:sha(manifestBytes),selection_documents:58,
  observed_list_documents:11,request_count_exact:null,request_count_known_lower_bound:currentLowerBound,
  targets:sites.map(site=>({id:site.id,count:site.targets.length})),bulk_document_budget:280,bulk_document_budget_per_site:140}));
