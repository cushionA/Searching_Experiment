import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Evidence,verify,safeError} from './evidence.mjs';
import {ToolAdapter} from './adapters.mjs';
import {siteIDs,DEFAULT_MAX_EVIDENCE_BYTES} from './search-load.mjs';
import {TOOL_NAMES,sha,snapshotSources} from './runtime.mjs';

export const DEFAULT_QUERIES=Object.freeze(['猫','日本語検索','気候変動','人工知能','東京 観光']);
export const DEFAULT_MAX_DURATION_MS=30*60*1000;
export const DEFAULT_MAX_PAGES_PER_SITE=100;
export const DEFAULT_PROVIDER_URL_TARGET=100;
export const DEFAULT_MAX_TOTAL_PAGES=500;
export const DEFAULT_MAX_UNIQUE_URLS=500;
const browserTools=new Set(TOOL_NAMES.filter(name=>!['wreq-js','impit'].includes(name)));

export function normalizeResultURL(value,baseURL='https://example.com/') {
  try {
    let url=new URL(value,baseURL);
    for(let i=0;i<3;i++) {
      let candidate=url.searchParams.get('url')||url.searchParams.get('uddg')||url.searchParams.get('u');
      if(!candidate&&url.pathname==='/url') candidate=url.searchParams.get('q');
      if(candidate&&!/^https?:/i.test(candidate)) {
        const wrapped=candidate;
        try {
          const payload=candidate.startsWith('a1')?candidate.slice(2):candidate;
          const decoded=Buffer.from(payload,'base64url').toString('utf8');
          candidate=decoded.match(/https?:\/\/[^\s\u0000"<>]+/i)?.[0]||null;
          if(candidate) candidate=decodeURIComponent(candidate);
        } catch {candidate=null;}
        if(!candidate&&/\/(?:goto|url|ck\/a|redirect)\/?$/i.test(url.pathname)) return null;
        if(!candidate) candidate=wrapped.startsWith('/')?new URL(wrapped,baseURL).href:null;
      }
      if(!candidate||!/^https?:/i.test(candidate)) break;
      url=new URL(candidate);
    }
    if(!['http:','https:'].includes(url.protocol)||url.username||url.password) return null;
    url.hash='';
    for(const key of [...url.searchParams.keys()]) if(/^utm_|^(?:gclid|fbclid|ved|ei|sa|source)$/i.test(key)) url.searchParams.delete(key);
    url.hostname=url.hostname.toLowerCase();
    if((url.protocol==='https:'&&url.port==='443')||(url.protocol==='http:'&&url.port==='80')) url.port='';
    return url.href;
  } catch {return null;}
}

export function validatePaginationManifest(value) {
  if(!value||value.schema!==1||!Array.isArray(value.queries)||value.queries.length!==5
    ||value.queries.some(q=>typeof q!=='string'||!q.trim()||q.trim()!==q||q.length>500)
    ||!Array.isArray(value.sites)||value.sites.length!==siteIDs.length) throw new Error('invalid_search_pagination_manifest');
  if(new Set(value.sites.map(x=>x?.id)).size!==siteIDs.length||siteIDs.some(id=>!value.sites.some(s=>s?.id===id))) throw new Error('invalid_site_set');
  const sites=value.sites.map(site=>{
    if(!site||!siteIDs.includes(site.id)||!browserTools.has(site.tool)||typeof site.origin!=='string'
      ||typeof site.search_url!=='string'||(site.search_url.match(/\{query\}/g)||[]).length!==1
      ||!Array.isArray(site.result_selectors)||!Array.isArray(site.next_selectors))
      throw new Error(`invalid_site_config:${site?.id||'unknown'}`);
    const origin=new URL(site.origin),sample=new URL(site.search_url.replace('{query}',encodeURIComponent('試験')));
    if(origin.protocol!=='https:'||origin.pathname!=='/'||origin.search||origin.hash||origin.username||origin.password||sample.origin!==origin.origin)
      throw new Error(`invalid_site_origin:${site.id}`);
    if(typeof site.timezoneId!=='string'||!site.timezoneId) throw new Error(`invalid_timezone:${site.id}`);
    try {new Intl.DateTimeFormat('en-US',{timeZone:site.timezoneId});} catch {throw new Error(`invalid_timezone:${site.id}`);}
    const params=site.max_results_params??{};
    if(!params||typeof params!=='object'||Array.isArray(params)||Object.entries(params).some(([k,v])=>!k||typeof v!=='string'))
      throw new Error(`invalid_max_results_params:${site.id}`);
    return {...site,origin:origin.origin,max_results_params:params,next_text:site.next_text??null};
  });
  const limits={results_per_site:value.results_per_site??DEFAULT_PROVIDER_URL_TARGET,max_pages_per_site:value.max_pages_per_site??DEFAULT_MAX_PAGES_PER_SITE,
    max_total_pages:value.max_total_pages??DEFAULT_MAX_TOTAL_PAGES,max_unique_urls:value.max_unique_urls??DEFAULT_MAX_UNIQUE_URLS,
    max_evidence_bytes:value.max_evidence_bytes??DEFAULT_MAX_EVIDENCE_BYTES,max_duration_ms:value.max_duration_ms??DEFAULT_MAX_DURATION_MS,
    target_interval_ms:value.target_interval_ms??1000};
  if(limits.results_per_site<1||limits.results_per_site>DEFAULT_PROVIDER_URL_TARGET||limits.max_pages_per_site<1||limits.max_pages_per_site>DEFAULT_MAX_PAGES_PER_SITE||limits.max_total_pages<1||limits.max_total_pages>DEFAULT_MAX_TOTAL_PAGES
    ||limits.max_unique_urls<1||limits.max_unique_urls>DEFAULT_MAX_UNIQUE_URLS||limits.max_evidence_bytes<1||limits.max_evidence_bytes>DEFAULT_MAX_EVIDENCE_BYTES
    ||limits.max_duration_ms<1||limits.max_duration_ms>DEFAULT_MAX_DURATION_MS||limits.target_interval_ms!==1000
    ||Object.values(limits).some(x=>!Number.isSafeInteger(x))) throw new Error('invalid_limits');
  return {schema:1,queries:[...value.queries],sites,results_per_site:limits.results_per_site,max_pages_per_site:limits.max_pages_per_site,max_total_pages:limits.max_total_pages,
    max_unique_urls:limits.max_unique_urls,max_evidence_bytes:limits.max_evidence_bytes,max_duration_ms:limits.max_duration_ms,
    target_interval_ms:limits.target_interval_ms,requested_max_results:value.requested_max_results??null,
    note:'requested display count and observed items are stored separately; observed maximum is not inferred from requested parameters'};
}

export function paginationPlan(manifest) {
  const m=validatePaginationManifest(manifest);
  return {sites:m.sites.map(({id,tool,origin,result_selectors,next_selectors,max_results_params,next_text,timezoneId})=>({site:id,tool,origin,
    result_selectors,next_selectors,max_results_params,next_text,timezoneId})),queries:m.queries,results_per_site:m.results_per_site,max_pages_per_site:m.max_pages_per_site,
    max_total_pages:m.max_total_pages,max_unique_urls:m.max_unique_urls,max_evidence_bytes:m.max_evidence_bytes,
    max_duration_ms:m.max_duration_ms,target_interval_ms:m.target_interval_ms,first_pass:'one initial observation per site; then finish each site before moving to the next; Google last',
    requested_max_results:m.requested_max_results,pagination_order:'complete each provider after the ten-site initial pass; Google last',
    additional_requests:{purpose:'resolve opaque Google /goto result links only',max_total:m.max_total_pages,max_redirects:0,
      timeout_ms_max:5000,max_parallel:2,measured_separately:true},
    external_network:'enabled only during run'};
}

export function pageEvaluator(config,requestedMax,url,navigate=true,clickNext=false,timeBudgetMs=DEFAULT_MAX_DURATION_MS) {
  return async browser=>{
    const started=Date.now();
    if(navigate) await browser.page.goto(url,{waitUntil:'domcontentloaded',timeout:Math.max(1,Math.min(30000,timeBudgetMs))});
    if(navigate&&config.result_selectors.length&&browser.page.waitForFunction) {
      await browser.page.waitForFunction(({selectors})=>selectors.some(selector=>{
        try {return document.querySelector(selector)!==null;} catch {return false;}
      })||/captcha|challenge|verify you are human|anubis|verifying your request\.\.\.|access denied|not yet available in your country|sign in to continue|log in to continue|ログインが必要|認証が必要/i.test(document.body?.innerText||''),
      {selectors:config.result_selectors},{timeout:Math.max(1,Math.min(5000,timeBudgetMs)),polling:100}).catch(()=>{});
    }
    const data=await browser.page.evaluate(async({resultSelectors,nextSelectors,nextText,clickNext,siteID,timeBudgetMs})=>{
      const extractItems=()=>{
        const selectors=resultSelectors.flatMap(selector=>{try{return [...document.querySelectorAll(selector)].map(node=>({selector,node}));}catch{return [];}});
        const seen=new Set(),items=[];
        for(const {selector,node} of selectors) {
          const anchors=[...(node.matches?.('a[href]')?[node]:[]),...node.querySelectorAll('a[href]')];
          for(const a of anchors) {
            const href=a.href||a.getAttribute('href'),title=(a.innerText||a.textContent||'').replace(/\s+/g,' ').trim();
            if(!href||!title||seen.has(href)||a.closest('nav,header,footer,[role="navigation"]')) continue;
            try {
              const candidate=new URL(href,location.href);
              const wrapped=['url','q','uddg','u'].some(key=>candidate.searchParams.has(key))||/\/(?:goto|url|ck\/a|redirect)\/?$/i.test(candidate.pathname);
              if(candidate.origin===location.origin&&!wrapped) continue;
            } catch {continue;}
            const context=(node.innerText||node.textContent||'').replace(/\s+/g,' ').trim();
            if(/sponsored|advertisement|広告|スポンサー/i.test(`${context.slice(0,100)} ${a.getAttribute('aria-label')||''}`)) continue;
            seen.add(href);items.push({href,title,selector,context:context.slice(0,500)});
          }
        }
        return items;
      };
      const chooseNext=()=>{
        for(const selector of nextSelectors) {
          try {for(const el of document.querySelectorAll(selector)) {
            const label=(el.innerText||el.getAttribute('aria-label')||'').trim();
            if(el.disabled||el.getAttribute('aria-disabled')==='true'||(nextText&&!label.includes(nextText))
              ||siteID==='brave'&&!/\bnext\b|次へ|次のページ/i.test(label)) continue;
            return {el,selector,label,href:el.href||el.getAttribute('href')||null};
          }} catch{}
        }
        return null;
      };
      let items=extractItems(),candidate=chooseNext(),click_progress=false,items_before_click=items.length;
      if(clickNext&&candidate&&!candidate.href) {
        candidate.el.click();
        await new Promise(resolve=>{
          const started=Date.now();
          const timer=setInterval(()=>{
            if(extractItems().length>items_before_click||Date.now()-started>=Math.min(5000,timeBudgetMs)){clearInterval(timer);resolve();}
          },100);
        });
        items=extractItems();click_progress=items.length>items_before_click;candidate=chooseNext();
      }
      const next=candidate?.href||null,click_available=!!candidate&&!candidate.href;
      const text=(document.body?.innerText||'').slice(0,4000),status=/captcha|challenge|verify you are human|ロボットではない|人間であること/i.test(text)?'challenge':
        /anubis|verifying your request\.\.\./i.test(text)?'verification_gate':siteID==='qwant'&&/not yet available in your country/i.test(text)?'country_unavailable':
        /\b403\b|access denied|request blocked/i.test(text)?'blocked':/\b429\b|too many requests|しばらくしてから/i.test(text)?'rate_limited':
        /sign in required|sign in to continue|log in to continue|ログインして続行|ログインしてください|ログインが必要|認証が必要/i.test(text)
          ||siteID==='kagi'&&/\bkagi\b.{0,60}\b(?:log[ -]?in|sign[ -]?in)\b|\b(?:log[ -]?in|sign[ -]?in)\b.{0,60}\bkagi\b/i.test(text)
          ?'auth_required':items.length?'content_observed':'empty';
      return {items,next,click_available,click_selector:candidate?.selector||null,click_label:candidate?.label||null,click_progress,items_before_click,
        text:text.slice(0,1000),status,extractor_configured:resultSelectors.length>0};
    },{resultSelectors:config.result_selectors,nextSelectors:config.next_selectors,nextText:config.next_text,clickNext,siteID:config.id,timeBudgetMs});
    return {...data,operation_started_at_ms:started};
  };
}

export function extractionProgress(previous,next) {
  if(!next) return {progress:false,reason:'next_missing'};
  if(previous&&normalizeResultURL(next,previous)===normalizeResultURL(previous,previous)) return {progress:false,reason:'next_same_url'};
  return {progress:true,reason:'next_available'};
}

export function collectPageURLs(items,baseURL,providerSeen,globalSeen,globalCap=DEFAULT_MAX_UNIQUE_URLS,providerCap=DEFAULT_PROVIDER_URL_TARGET) {
  const collected=[];
  for(const item of items||[]) {
    const url=normalizeResultURL(item.href,baseURL);
    if(!url) continue;
    const providerDuplicate=providerSeen.has(url),globalDuplicate=globalSeen.has(url);
    const accepted=!providerDuplicate&&providerSeen.size<providerCap;
    const globalAccepted=!globalDuplicate&&globalSeen.size<globalCap;
    if(accepted) providerSeen.add(url);
    if(globalAccepted) globalSeen.add(url);
    collected.push({...item,url,provider_duplicate:providerDuplicate,global_duplicate:globalDuplicate,
      accepted,global_accepted:globalAccepted});
  }
  return collected;
}

export async function resolveGoogleOpaqueURL({href,adapter,evidence,site,deadline,clock={now:()=>Date.now()},resolverTiming=null,
  sourceDomSha256=null,query=null,page=null}) {
  const original=new URL(href);
  if(site.id!=='google'||original.origin!==site.origin||original.pathname!=='/goto'||!original.searchParams.has('url')
    ||normalizeResultURL(href,site.origin+'/')) return {outcome:'not_applicable'};
  const previousContext=evidence.stageContext;
  evidence.stageContext={role:'result_link_resolution',client:site.tool,target:site.id,profile:'search-pagination',request_method:'GET',
    ...(sourceDomSha256?{source_dom_sha256:sourceDomSha256}:{}),...(query?{query}:{}),...(page?{page}:{})};
  let requestStartedAtMs=null,startIntervalMs=null;
  let record=null;
  const started=clock.now();
  try {
    const timeLeft=deadline-clock.now();
    if(timeLeft<=0) throw new Error('duration_limit');
    requestStartedAtMs=clock.now();
    startIntervalMs=resolverTiming?.last_started_at_ms===undefined?null:requestStartedAtMs-resolverTiming.last_started_at_ms;
    if(resolverTiming) {resolverTiming.last_started_at_ms=requestStartedAtMs;resolverTiming.start_intervals_ms.push(startIntervalMs);}
    record=evidence.reserve(`${site.tool}/${site.id}`,href,'result_redirect_get');
    record.request_method='GET';record.resource_type='result_link_resolution';record.role='result_link_resolution';
    const response=await adapter.browser.context.request.get(href,{maxRedirects:0,timeout:Math.max(1,Math.min(5000,timeLeft))});
    const status=response.status(),headers=await response.headers(),body=Buffer.from(await response.body());
    evidence.finish(record,status,headers,body);
    const location=headers.location||headers.Location||null;
    let resolvedURL=null,outcome='resolver_no_location';
    if(status>=300&&status<400&&location) {
      const normalized=normalizeResultURL(location,href);
      if(normalized&&new URL(normalized).origin!==site.origin) {resolvedURL=normalized;outcome='resolver_redirect_resolved';}
      else outcome='resolver_location_rejected';
    } else if(status===403) outcome='resolver_http_403';
    else if(status===429) outcome='resolver_http_429';
    else if(status>=300&&status<400) outcome='resolver_redirect_without_location';
    else if(status>=200&&status<300) outcome='resolver_not_redirected';
    const resolution={outcome,url:resolvedURL,record_index:record.index,http_status:status,location,
      body_sha256:record.body_sha256,elapsed_ms:clock.now()-started,started_at_ms:requestStartedAtMs,start_interval_ms:startIntervalMs};
    evidence.result({client:site.tool,target:site.id,profile:'search-pagination',role:'result_link_resolution',url:href,
      source_dom_sha256:sourceDomSha256,query,page,...resolution});
    return resolution;
  } catch(error) {
    if(record?.status==='interrupted') evidence.fail(record,error);
    const resolution={outcome:error.message==='duration_limit'?'resolver_duration_limit':'resolver_error',url:null,
      ...(record?{record_index:record.index}:{}),error:safeError(error),elapsed_ms:clock.now()-started,
      started_at_ms:requestStartedAtMs,start_interval_ms:startIntervalMs};
    evidence.result({client:site.tool,target:site.id,profile:'search-pagination',role:'result_link_resolution',url:href,
      source_dom_sha256:sourceDomSha256,query,page,...resolution});
    return resolution;
  } finally {evidence.stageContext=previousContext;}
}

function evidenceBytes(directory) {
  let total=0;
  const visit=p=>{for(const e of fs.readdirSync(p,{withFileTypes:true})){const f=path.join(p,e.name);if(e.isDirectory())visit(f);else if(e.isFile())total+=fs.statSync(f).size;}};
  visit(directory);return total;
}

export function verifySearchPagination(directory) {
  const evidenceVerification=verify(directory),errors=[...evidenceVerification.errors];
  const manifest=validatePaginationManifest(JSON.parse(fs.readFileSync(path.join(directory,'search-pagination.json'),'utf8')));
  const state=JSON.parse(fs.readFileSync(path.join(directory,'search-pagination-state.json'),'utf8'));
  const ledger=JSON.parse(fs.readFileSync(path.join(directory,'ledger.json'),'utf8'));
  if(!Array.isArray(state.pages)||state.pages.length!==state.total_pages||state.pages.length>manifest.max_total_pages) errors.push('pagination:page_count');
  if(!Array.isArray(state.unique_urls)||state.unique_urls.length!==state.global_unique_url_count||state.unique_urls.length>manifest.max_unique_urls) errors.push('pagination:global_unique_cap');
  for(const page of state.pages||[]) {
    if(!manifest.sites.some(site=>site.id===page.site)||page.page>manifest.max_pages_per_site||page.accepted_candidate_count!==page.extracted_urls.length) errors.push(`pagination:page:${page.site}/${page.page}`);
    if(page.start_interval_ms!==null&&page.start_interval_ms<manifest.target_interval_ms) errors.push(`pagination:interval:${page.site}/${page.page}`);
    if(page.dom_sha256&&!fs.existsSync(path.join(directory,'blobs',page.dom_sha256))) errors.push(`pagination:source_dom:${page.site}/${page.page}`);
  }
  for(const [site,summary] of Object.entries(state.sites||{})) if(summary.provider_unique_urls>manifest.results_per_site||summary.pages>manifest.max_pages_per_site) errors.push(`pagination:site_cap:${site}`);
  const resolverRecords=ledger.records.filter(record=>record.kind==='result_redirect_get');
  if(resolverRecords.length!==(state.link_resolution?.requests||0)||resolverRecords.length>manifest.max_total_pages) errors.push('pagination:resolver_count');
  for(const record of resolverRecords) {
    try {const url=new URL(record.url);if(url.origin!=='https://www.google.com'||url.pathname!=='/goto'||record.request_method!=='GET'
      ||record.role!=='result_link_resolution'||!record.source_dom_sha256) errors.push(`pagination:resolver_record:${record.index}`);}
    catch {errors.push(`pagination:resolver_record:${record.index}`);}
  }
  const resolverByIndex=new Map(resolverRecords.map(record=>[record.index,record]));
  for(const page of state.pages||[]) for(const item of page.extracted_urls||[]) if(item.resolution_record_index) {
    const record=resolverByIndex.get(item.resolution_record_index);
    if(!record||record.url!==item.href||record.source_dom_sha256!==page.dom_sha256||item.resolved_url!==item.url) errors.push(`pagination:resolver_link:${item.resolution_record_index}`);
  }
  return {...evidenceVerification,ok:!errors.length,errors,pagination_pages:state.pages?.length??0,unique_urls:state.global_unique_url_count??0,
    max_unique_urls:manifest.max_unique_urls,max_pages:manifest.max_total_pages};
}

async function execute({manifest:input,directory,fixture=false,adapterFactory=null,clock={now:()=>Date.now(),sleep:ms=>new Promise(r=>setTimeout(r,ms))}}) {
  const manifest=validatePaginationManifest(input),started=clock.now(),deadline=started+manifest.max_duration_ms;
  if(fs.existsSync(directory)) throw new Error('existing_run_cannot_be_overwritten');
  fs.mkdirSync(path.dirname(directory),{recursive:true});
  const evidence=new Evidence(directory,false,{policy:'browser_observation',authorization:'User-authorized bounded search pagination observation.'});
  evidence.save('search-pagination.json',manifest);evidence.save('source-hashes.json',snapshotSources(evidence));
  const state={schema:1,started_at:new Date(started).toISOString(),started_at_ms:started,deadline_at_ms:deadline,global_stop:null,
    pages:[],sites:{},unique_urls:[],timing:{target_interval_ms:1000,definition:'start-to-start target; measured values retained; no claim of achieved cadence'},
    link_resolution:{requests:0,limit:manifest.max_total_pages,elapsed_ms:0,wall_ms:0,requests_per_second:0,
      start_intervals_ms:[],outcomes:{},max_concurrency:2}};
  let bytesAtStart=evidenceBytes(directory),globalUnique=new Set(),totalPages=0,lastStart=null;
  const siteStates=new Map(manifest.sites.map(site=>[site.id,{config:site,queryIndex:0,pages:0,seen:new Set(),nextURL:null,stop:null,visited:new Set()}]));
  const ordered=[...manifest.sites.filter(s=>s.id!=='google'),...manifest.sites.filter(s=>s.id==='google')];
  let adapters=[];
  try {
    for(const site of ordered) {
      const adapter=adapterFactory?await adapterFactory({site,evidence}):await new ToolAdapter({tool:site.tool,site:{id:site.id,origins:[site.origin],links:{home:site.origin+'/'}},evidence,profile:{id:'search-pagination'},options:{captureScreenshots:false,observeMs:0},timezoneId:site.timezoneId}).open();
      adapters.push(adapter);siteStates.get(site.id).adapter=adapter;
      if(!fixture&&adapter.capabilities?.timezoneId!==true)throw new Error(`timezone_unsupported:${site.id}`);
    }
    const observe=async(s,phase)=>{
      if(state.global_stop||s.stop||s.queryIndex>=manifest.queries.length) return;
      if(clock.now()>=deadline){state.global_stop='duration_limit';return;}
      if(totalPages>=manifest.max_total_pages){state.global_stop='total_page_limit';return;}
      if(s.pages>=manifest.max_pages_per_site){s.stop='site_page_limit';return;}
      if(globalUnique.size>=manifest.max_unique_urls){state.global_stop='global_unique_url_limit';return;}
      if(s.seen.size>=manifest.results_per_site){s.stop='results_per_site_target';return;}
      if(evidenceBytes(directory)-bytesAtStart>=manifest.max_evidence_bytes){state.global_stop='evidence_limit';return;}
      const query=manifest.queries[s.queryIndex];
      const clickNext=s.nextAction==='click';
      const targetURL=clickNext?(s.adapter.currentURL||s.lastURL):s.nextURL||s.config.search_url.replace('{query}',encodeURIComponent(query));
      const parsed=new URL(targetURL);for(const [k,v] of Object.entries(s.config.max_results_params)) parsed.searchParams.set(k,v);
      if(!clickNext&&s.visited.has(parsed.href)){s.stop='next_same_url';return;} if(!clickNext)s.visited.add(parsed.href);
      const due=lastStart===null?clock.now():lastStart+manifest.target_interval_ms;
      await clock.sleep(Math.max(0,due-clock.now()));
      if(clock.now()>=deadline){state.global_stop='duration_limit';return;}
      const navStart=clock.now(),late=Math.max(0,navStart-due);lastStart=navStart;
      const result=await s.adapter.accessLink(parsed.href,{role:'pagination_page',operation:pageEvaluator(s.config,manifest.requested_max_results,parsed.href,!clickNext,clickNext,deadline-navStart)});
      const navFinished=clock.now(),op=result.operation||{},rawItems=Array.isArray(op.items)?op.items:[];
      const resolutions=new Map(),opaqueItems=s.config.id==='google'?rawItems.filter(item=>!normalizeResultURL(item.href,result.final_url||parsed.href)
        &&(()=>{try{const u=new URL(item.href,result.final_url||parsed.href);return u.origin===s.config.origin&&u.pathname==='/goto'&&u.searchParams.has('url');}catch{return false;}})()):[];
      const resolutionRemaining=Math.max(0,manifest.max_total_pages-state.link_resolution.requests);
      const toResolve=opaqueItems.slice(0,resolutionRemaining);
      if(opaqueItems.length>resolutionRemaining)s.resolverStop='resolver_request_limit';
      for(let i=0;i<toResolve.length;i+=2) {
        if(clock.now()>=deadline){s.resolverStop='resolver_duration_limit';break;}
        const resolverStarted=clock.now();
        const group=await Promise.all(toResolve.slice(i,i+2).map(item=>resolveGoogleOpaqueURL({href:item.href,adapter:s.adapter,evidence,site:s.config,deadline,clock,
          resolverTiming:state.link_resolution,sourceDomSha256:result.dom_sha256,query,page:s.pages+1})));
        state.link_resolution.wall_ms+=clock.now()-resolverStarted;
        for(let j=0;j<group.length;j++) {
          const resolution=group[j],item=toResolve[i+j];resolutions.set(item.href,resolution);
          if(resolution.record_index) {state.link_resolution.requests++;state.link_resolution.elapsed_ms+=resolution.elapsed_ms||0;}
          state.link_resolution.outcomes[resolution.outcome]=(state.link_resolution.outcomes[resolution.outcome]||0)+1;
          if(['resolver_http_403','resolver_http_429','resolver_duration_limit'].includes(resolution.outcome))s.resolverStop=resolution.outcome;
        }
        if(s.resolverStop)break;
      }
      const items=rawItems.flatMap(item=>{
        const resolution=resolutions.get(item.href);
        if(resolution) return resolution.url?[{...item,href:resolution.url,original_href:item.href,resolved_url:resolution.url,
          resolution_record_index:resolution.record_index,resolved_http_metadata:{status:resolution.http_status,location:resolution.location,
            outcome:resolution.outcome,body_sha256:resolution.body_sha256,elapsed_ms:resolution.elapsed_ms,
            started_at_ms:resolution.started_at_ms,start_interval_ms:resolution.start_interval_ms}}]:[];
        return normalizeResultURL(item.href,result.final_url||parsed.href)?[item]:[];
      });
      const uniqueBefore=s.seen.size;
      const normalized=collectPageURLs(items,result.final_url||parsed.href,s.seen,globalUnique,manifest.max_unique_urls,manifest.results_per_site)
        .map(item=>item.original_href?{...item,href:item.original_href}:item);
      const progress=clickNext?{progress:s.seen.size>uniqueBefore,reason:s.seen.size>uniqueBefore?'click_unique_url_increased':'click_no_unique_url_increase'}:
        op.next?extractionProgress(result.final_url||parsed.href,op.next):op.click_available?{progress:true,reason:'click_candidate_available_unverified'}:extractionProgress(result.final_url||parsed.href,null);
      const previousPage=state.pages.filter(page=>page.site===s.config.id).at(-1);
      const row={site:s.config.id,query,query_index:s.queryIndex,page:s.pages+1,url:parsed.href,final_url:result.final_url||null,
        started_at_ms:navStart,finished_at_ms:navFinished,elapsed_ms:navFinished-navStart,target_interval_ms:1000,late_ms:late,
        start_interval_ms:lastStart===navStart?(state.pages.at(-1)?navStart-state.pages.at(-1).started_at_ms:null):null,phase,
        page_event_timings:'unmeasured',outcome:result.outcome,http_status:result.http_status??null,dom_sha256:result.dom_sha256||null,
        requested_max_results:manifest.requested_max_results,observed_items_count:rawItems.length,accepted_candidate_count:normalized.length,extracted_urls:normalized,
        resolver_requests:[...resolutions.values()].filter(item=>item.record_index).length,
        resolver_elapsed_ms:[...resolutions.values()].reduce((sum,item)=>sum+(item.record_index?item.elapsed_ms||0:0),0),
        resolver_start_intervals_ms:[...resolutions.values()].filter(item=>item.record_index).map(item=>item.start_interval_ms),
        resolver_stop_reason:s.resolverStop||null,
        next_url:op.next||null,next_action:clickNext?'click':op.next?'navigate':op.click_available?'click':null,
        next_selector:op.click_selector||null,next_selector_confidence:s.config.id==='duckduckgo'?'candidate':'observed',
        next_progress:progress,stop_reason:null};
      if(['execution_error','adapter_error','unsupported_capability'].includes(result.outcome)||op.outcome==='operation_error') row.stop_reason=op.outcome||result.outcome;
      else if(['blocked','rate_limited','challenge','auth_required','verification_gate','country_unavailable'].includes(op.status)) row.stop_reason=op.status;
      else if(result.http_status===403) row.stop_reason='http_403';else if(result.http_status===429)row.stop_reason='http_429';
      else if(!op.extractor_configured)row.stop_reason='extractor_unconfigured';
      else if(!items.length)row.stop_reason='empty_page';else if(!progress.progress)row.stop_reason=progress.reason;
      else if(previousPage?.dom_sha256&&previousPage.dom_sha256===row.dom_sha256)row.stop_reason='next_dom_unchanged';
      if(s.resolverStop)row.stop_reason=s.resolverStop;
      evidence.result({client:s.config.tool,target:s.config.id,profile:'search-pagination',role:'search_pagination_page',outcome:row.stop_reason||result.outcome,
        query,page:row.page,requested_max_results:row.requested_max_results,observed_items_count:row.observed_items_count,
        extracted_urls:normalized,source_dom_sha256:row.dom_sha256,metadata_sha256:sha(JSON.stringify({site:row.site,query,page:row.page,url:row.url,final_url:row.final_url}))});
      state.pages.push(row);s.pages++;totalPages++;
      s.lastURL=row.final_url||row.url;
      if(state.pages.length===1||row.stop_reason) {
        try {
          const screenshot=await s.adapter.browser?.page?.screenshot?.({type:'png'});
          if(screenshot) evidence.result({client:s.config.tool,target:s.config.id,profile:'search-pagination',role:'search_pagination_screenshot',
            outcome:'screenshot_saved',source_dom_sha256:row.dom_sha256,screenshot_sha256:evidence.blob(screenshot),page:row.page,stop_reason:row.stop_reason});
        } catch(error) {evidence.result({client:s.config.tool,target:s.config.id,profile:'search-pagination',role:'search_pagination_screenshot',
          outcome:'screenshot_failed',source_dom_sha256:row.dom_sha256,error:safeError(error)});}
      }
      if(row.stop_reason) {if(row.stop_reason==='next_missing'||row.stop_reason==='next_same_url'||row.stop_reason==='empty_page') {s.queryIndex++;s.nextURL=null;s.nextAction=null;s.visited.clear();if(s.queryIndex<manifest.queries.length)s.stop=null;else s.stop=row.stop_reason;}else s.stop=row.stop_reason;}
      else if(op.next){s.nextURL=op.next;s.nextAction='navigate';}
      else if(op.click_available&&progress.progress){s.nextURL=null;s.nextAction='click';}
      else {s.queryIndex++;s.nextURL=null;s.nextAction=null;s.visited.clear();if(s.queryIndex>=manifest.queries.length)s.stop='next_missing';}
      if(s.seen.size>=manifest.results_per_site)s.stop='results_per_site_target';
      if(s.resolverStop)s.stop=s.resolverStop;
      state.unique_urls=[...globalUnique];state.sites[s.config.id]={query_index:s.queryIndex,pages:s.pages,provider_unique_urls:s.seen.size,stop:s.stop};
      evidence.save('search-pagination-state.json',state);evidence.flush(true);
      if(evidenceBytes(directory)-bytesAtStart>=manifest.max_evidence_bytes)state.global_stop='evidence_limit';
    };
    for(const site of ordered) await observe(siteStates.get(site.id),'initial_pass');
    for(const site of ordered) {
      const s=siteStates.get(site.id);
      while(!state.global_stop&&!s.stop&&s.queryIndex<manifest.queries.length&&clock.now()<deadline
        &&totalPages<manifest.max_total_pages&&globalUnique.size<manifest.max_unique_urls) await observe(s,'pagination');
    }
    for(const s of siteStates.values()) state.sites[s.config.id]={query_index:s.queryIndex,pages:s.pages,provider_unique_urls:s.seen.size,stop:s.stop};
    state.global_stop ||= globalUnique.size>=manifest.max_unique_urls?'global_unique_url_limit':totalPages>=manifest.max_total_pages?'total_page_limit':clock.now()>=deadline?'duration_limit':'all_sites_exhausted';
    state.link_resolution.requests_per_second=state.link_resolution.wall_ms>0?1000*state.link_resolution.requests/state.link_resolution.wall_ms:0;
    state.finished_at=new Date(clock.now()).toISOString();state.total_pages=totalPages;state.global_unique_url_count=globalUnique.size;
    state.provider_unique_url_counts=Object.fromEntries([...siteStates].map(([id,s])=>[id,s.seen.size]));
    state.saved_evidence_bytes=evidenceBytes(directory);evidence.save('search-pagination-state.json',state);evidence.flush(true);
    const verification=verifySearchPagination(directory);evidence.save('verification.json',verification);return {directory,state,verification};
  } catch(error) {state.global_stop||='execution_error';state.error=safeError(error);state.finished_at=new Date(clock.now()).toISOString();evidence.save('search-pagination-state.json',state);evidence.flush(true);throw error;}
  finally {for(const adapter of adapters.reverse()) await adapter.close?.();}
}

export async function runSearchPagination(options) {
  const directory=path.resolve(options.directory),lockPath=directory+'.lock';fs.mkdirSync(path.dirname(directory),{recursive:true});
  const lock=fs.openSync(lockPath,'wx');
  try {fs.writeSync(lock,JSON.stringify({pid:process.pid,started_at:new Date().toISOString()}));return await execute({...options,directory});}
  finally {fs.closeSync(lock);fs.unlinkSync(lockPath);}
}

export function parsePaginationCLI(args) {
  const [mode,...rest]=args;
  if(mode==='plan'&&rest.length===1)return {mode,manifestPath:rest[0]};
  if(mode==='run'&&rest.length===2)return {mode,manifestPath:rest[0],directory:rest[1]};
  if(mode==='verify'&&rest.length===1)return {mode,directory:rest[0]};
  throw new Error('Usage: search-pagination.mjs plan MANIFEST | run MANIFEST RUN_DIRECTORY | verify RUN_DIRECTORY');
}

async function main() {
  const options=parsePaginationCLI(process.argv.slice(2));
  if(options.mode==='verify') {const result=verifySearchPagination(path.resolve(options.directory));console.log(JSON.stringify(result,null,2));if(!result.ok)process.exitCode=1;return;}
  const manifest=validatePaginationManifest(JSON.parse(fs.readFileSync(path.resolve(options.manifestPath),'utf8')));
  if(options.mode==='plan'){console.log(JSON.stringify(paginationPlan(manifest),null,2));return;}
  const result=await runSearchPagination({...options,manifest});
  const summary={directory:result.directory,total_pages:result.state.total_pages,unique_urls:result.state.global_unique_url_count,
    sites:result.state.sites,timing:result.state.timing,verification:result.verification};
  console.log(JSON.stringify(summary,null,2));if(!result.verification.ok)process.exitCode=1;
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) main().catch(error=>{console.error(JSON.stringify(safeError(error)));process.exitCode=1;});
