import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Evidence, verify, safeError} from './evidence.mjs';
import {ToolAdapter} from './adapters.mjs';
import {TOOL_NAMES, sha, snapshotSources} from './runtime.mjs';

export const stages = Object.freeze([
  {id:'qps-0.2', qps:0.2, max_searches:100},
  {id:'qps-0.5', qps:0.5, max_searches:300},
  {id:'qps-1', qps:1, max_searches:600},
]);
export const siteIDs = Object.freeze(['google','yahoo-japan','bing','kagi','startpage','duckduckgo','brave','ecosia','qwant','yandex']);
export const DEFAULT_MAX_EVIDENCE_BYTES = 1024 ** 3;
export const DEFAULT_MAX_DURATION_MS = 6_600_000;
const MAX_DURATION_MS = DEFAULT_MAX_DURATION_MS;
const browserTools = new Set(TOOL_NAMES.filter(name=>!['wreq-js','impit'].includes(name)));

export function validateLoadManifest(value, {fixture=false}={}) {
  if(!value || value.schema!==1 || !Array.isArray(value.queries) || !value.queries.length
    || value.queries.some(query=>typeof query!=='string'||!query.trim()||query.length>500)
    || !Array.isArray(value.sites) || value.sites.length!==siteIDs.length) throw new Error('invalid_search_load_manifest');
  if(value.queries.some(query=>query.trim()!==query) || new Set(value.sites.map(site=>site?.id)).size!==siteIDs.length
    || siteIDs.some(id=>!value.sites.some(site=>site?.id===id))) throw new Error('invalid_query_or_site_set');
  const sites=value.sites.map(site=>{
    if(!site || !siteIDs.includes(site.id) || !browserTools.has(site.tool) || typeof site.origin!=='string'
      || typeof site.search_url!=='string' || !site.search_url.includes('{query}')
      || (site.search_url.match(/\{query\}/g)||[]).length!==1) throw new Error(`invalid_site_config:${site?.id||'unknown'}`);
    if(site.timezoneId!==undefined) {
      if(typeof site.timezoneId!=='string'||!site.timezoneId) throw new Error(`invalid_timezone:${site.id}`);
      try {new Intl.DateTimeFormat('en-US',{timeZone:site.timezoneId});} catch {throw new Error(`invalid_timezone:${site.id}`);}
    }
    const origin=new URL(site.origin), sample=new URL(site.search_url.replace('{query}',encodeURIComponent('試験')));
    const local=origin.protocol==='http:' && ['127.0.0.1','localhost'].includes(origin.hostname);
    if((!fixture && origin.protocol!=='https:') || (fixture && origin.protocol!=='https:'&&!local)
      || origin.username || origin.password || origin.pathname!=='/' || origin.search || origin.hash
      || sample.origin!==origin.origin || sample.username || sample.password || sample.hash) throw new Error(`invalid_site_origin:${site.id}`);
    return {id:site.id,tool:site.tool,origin:origin.origin,search_url:site.search_url,
      ...(site.timezoneId?{timezoneId:site.timezoneId}:{})};
  });
  if(value.max_evidence_bytes!==undefined && (!Number.isSafeInteger(value.max_evidence_bytes)
    || value.max_evidence_bytes<1 || value.max_evidence_bytes>DEFAULT_MAX_EVIDENCE_BYTES)) throw new Error('invalid_evidence_limit');
  if(value.max_duration_ms!==undefined && (!Number.isSafeInteger(value.max_duration_ms)
    || value.max_duration_ms<1 || value.max_duration_ms>MAX_DURATION_MS)) throw new Error('invalid_duration_limit');
  const stagePlan=value.stages??stages;
  if(!Array.isArray(stagePlan)||stagePlan.length!==stages.length||stagePlan.some((stage,index)=>!stage
    ||stage.id!==stages[index].id||stage.qps!==stages[index].qps||!Number.isSafeInteger(stage.max_searches)
    ||stage.max_searches<1||stage.max_searches>stages[index].max_searches)) throw new Error('invalid_stage_limits');
  if(value.observe_ms!==undefined&&(!Number.isInteger(value.observe_ms)||value.observe_ms<0||value.observe_ms>6000))
    throw new Error('invalid_observe_ms');
  if(value.flush_interval_ms!==undefined&&(!Number.isSafeInteger(value.flush_interval_ms)||value.flush_interval_ms<0
    ||value.flush_interval_ms>60000)) throw new Error('invalid_flush_interval_ms');
  return {schema:1,queries:[...value.queries],sites,max_evidence_bytes:value.max_evidence_bytes??DEFAULT_MAX_EVIDENCE_BYTES,
    max_duration_ms:value.max_duration_ms??DEFAULT_MAX_DURATION_MS,stages:stagePlan.map(stage=>({...stage})),
    observe_ms:value.observe_ms??6000,flush_interval_ms:value.flush_interval_ms??1000};
}

export function loadPlan(manifest) {
  return {sites:manifest.sites.map(site=>({site:site.id,tool:site.tool,stages:manifest.stages.map(stage=>({...stage}))})),
    queries_per_site:manifest.stages.reduce((sum,stage)=>sum+stage.max_searches,0),
    max_total_searches:manifest.sites.length*manifest.stages.reduce((sum,stage)=>sum+stage.max_searches,0),
    max_evidence_bytes:manifest.max_evidence_bytes,max_duration_ms:manifest.max_duration_ms,
    observe_ms:manifest.observe_ms,flush_interval_ms:manifest.flush_interval_ms,
    rate_note:'Per-site QPS values are upper bounds. Achieved rates depend on navigation latency and the global run deadline.'};
}

function writeJSON(filename,value) {
  fs.writeFileSync(filename+'.tmp',JSON.stringify(value,null,2)+'\n');
  fs.renameSync(filename+'.tmp',filename);
}
function blockReason(result,siteID) {
  if([403,429].includes(result.http_status)) return `http_${result.http_status}`;
  if(['execution_error','adapter_error','unsupported_capability'].includes(result.outcome))
    return result.outcome;
  const text=`${result.title||''} ${result.visible_text_prefix||''} ${result.navigation_error?.message||''} ${result.error?.message||''}`;
  if(/anubis|verifying your request\.\.\./i.test(text)) return 'verification_gate';
  if(siteID==='qwant'&&/not yet available in your country/i.test(text)) return 'country_unavailable';
  if(result.outcome==='challenge_observed'||/challenge|captcha|人間であること|ロボットではない/i.test(text)) return 'challenge';
  if(/auth(?:entication)?[_ -]?required|sign in required|ログインが必要|認証が必要/i.test(text)) return 'auth_required';
  if(siteID==='kagi'&&/\bkagi\b.{0,50}\b(?:log[ -]?in|sign[ -]?in)\b|\b(?:log[ -]?in|sign[ -]?in)\b.{0,50}\bkagi\b/i.test(text)) return 'auth_required';
  if(/sign in to continue|log in to continue|ログインして続行|ログインしてください/i.test(text)) return 'auth_required';
  if(/timeout|timed out|navigation timeout/i.test(text)) return 'timeout';
  if(result.outcome==='navigation_unverified') return 'navigation_unverified';
  return null;
}

function hrefObservation(evidence,result,origin) {
  if(!result?.dom_sha256) return {dom:'unavailable',href_count:0,external_href_count:0,candidates:[]};
  const filename=path.join(evidence.directory,'blobs',result.dom_sha256);
  if(!fs.existsSync(filename)) return {dom:'missing',href_count:0,external_href_count:0,candidates:[]};
  const html=fs.readFileSync(filename,'utf8'),matches=html.matchAll(/<a\b[^>]*\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi);
  const candidates=[];
  let hrefCount=0,externalCount=0;
  for(const match of matches) {
    const value=(match[1]??match[2]??match[3]??'').replace(/&amp;/gi,'&');
    let url;
    try {url=new URL(value,result.final_url||'https://invalid.local/');} catch {continue;}
    if(!['http:','https:'].includes(url.protocol)) continue;
    hrefCount++;
    const external=url.origin!==origin;
    if(external) externalCount++;
    if(candidates.length<30) candidates.push({href:url.href,external});
  }
  return {dom:'observed',href_count:hrefCount,external_href_count:externalCount,candidates};
}

function savedEvidenceBytes(directory) {
  let total=0;
  const visit=current=>{
    for(const entry of fs.readdirSync(current,{withFileTypes:true})) {
      const filename=path.join(current,entry.name);
      if(entry.isDirectory()) visit(filename);
      else if(entry.isFile()) total+=fs.statSync(filename).size;
    }
  };
  visit(directory);return total;
}
async function captureNativeScreenshot(adapter,evidence,site,tag) {
  if(!adapter.browser?.page?.screenshot) return null;
  try {
    const bytes=await adapter.browser.page.screenshot({type:'png'});
    const screenshot_sha256=evidence.blob(bytes);
    evidence.result({client:site.tool,target:site.id,profile:'search-load',role:'search_load_screenshot',tag,
      outcome:'screenshot_saved',screenshot_sha256});
    return screenshot_sha256;
  } catch(error) {
    evidence.result({client:site.tool,target:site.id,profile:'search-load',role:'search_load_screenshot',tag,
      outcome:'screenshot_failed',error:safeError(error)});
    return null;
  }
}

async function executeSearchLoad({manifest:input,directory,resume=false,fixture=false,adapterFactory=null,stagePlan=null,
  evidenceFactory=null,beforeNavigation=null,clock={now:()=>Date.now(),sleep:ms=>new Promise(resolve=>setTimeout(resolve,ms))}}) {
  const manifest=validateLoadManifest(input,{fixture});
  stagePlan ||= manifest.stages;
  if(!Array.isArray(stagePlan)||!stagePlan.length||stagePlan.some(stage=>!stage.id||!Number.isFinite(stage.qps)||stage.qps<=0
    ||!Number.isSafeInteger(stage.max_searches)||stage.max_searches<=0)) throw new Error('invalid_stage_plan');
  const totalPerSite=stagePlan.reduce((sum,stage)=>sum+stage.max_searches,0);
  const manifestText=JSON.stringify({manifest,stagePlan});
  const manifestPath=path.join(directory,'search-load.json'),statePath=path.join(directory,'search-load-state.json');
  if(!resume && fs.existsSync(directory)) throw new Error('existing_run_cannot_be_overwritten');
  fs.mkdirSync(path.dirname(directory),{recursive:true});
  if(resume && (!fs.existsSync(manifestPath)||!fs.existsSync(statePath)
    ||JSON.parse(fs.readFileSync(manifestPath,'utf8'))!==manifestText
    ||JSON.parse(fs.readFileSync(statePath,'utf8')).manifest_sha256!==sha(manifestText))) {
    throw new Error('resume_conditions_mismatch');
  }
  const evidence=evidenceFactory?await evidenceFactory(directory,resume):new Evidence(directory,resume,{
    policy:'browser_observation',authorization:'User-authorized bounded search-load observation; ordinary browser_observation policy.',
    flush_interval_ms:manifest.flush_interval_ms});
  if(!resume) {
    writeJSON(manifestPath,manifestText);
    writeJSON(statePath,{schema:1,manifest_sha256:sha(manifestText),started_at_ms:clock.now(),
      deadline_at_ms:clock.now()+manifest.max_duration_ms,global_stop:null,
      flush_interval_ms:manifest.flush_interval_ms,
      resume_session_note:'A resumed process retains query, rate, evidence, and deadline state; the browser session is newly opened.',sites:{}});
    evidence.save('source-hashes.json',snapshotSources(evidence));
    evidence.flush();
  } else {
    for(const record of evidence.records) if(record.status==='interrupted')
      evidence.fail(record,new Error('previous_process_interrupted; retained request and evidence accounting'));
  }
  const state=JSON.parse(fs.readFileSync(statePath,'utf8'));
  for(const site of manifest.sites) state.sites[site.id] ||= {next_stage:0,next_index:0,query_count:0,stopped:null,measurements:[]};
  if(resume) for(const site of manifest.sites) {
    const siteState=state.sites[site.id],attempt=siteState.inflight;
    if(!attempt) continue;
    siteState.measurements.push({stage:attempt.stage,index:attempt.index,query_index:attempt.query_index,query:attempt.query,
      qps:stagePlan[attempt.stage_index].qps,started_at:new Date(attempt.started_clock_ms).toISOString(),
      started_clock_ms:attempt.started_clock_ms,finished_at:null,latency_ms:null,search_navigation_count:1,
      network_request_records:0,main_document_request_records:0,background_resource_records:0,
      outcome:'interrupted_attempt_no_retry',stop_reason:'interrupted_attempt_no_retry',search_results_status:'dom_unavailable',
      hrefs:{dom:'unavailable',href_count:0,external_href_count:0,candidates:[]}});
    siteState.stopped={stage:attempt.stage,index:attempt.index,reason:'interrupted_attempt_no_retry'};
    delete siteState.inflight;
  }
  if(resume) writeJSON(statePath,state);
  state.scheduler_cursor=Number.isInteger(state.scheduler_cursor)?state.scheduler_cursor:0;
  writeJSON(statePath,state);
  const sessions=new Map(manifest.sites.map(site=>[site.id,{adapter:null,closed:false}]));
  const closeSite=async site=>{
    const session=sessions.get(site.id);
    if(session?.adapter&&!session.closed) {session.closed=true;await session.adapter.close?.();}
  };
  const siteConfigFor=site=>({id:site.id,origins:[site.origin],links:{home:site.origin+'/',targets:[]},selectors:{},
    ...(site.timezoneId?{timezoneId:site.timezoneId}:{})});
  const setupSite=async site=>{
    const session=sessions.get(site.id),siteState=state.sites[site.id];
    if(session.adapter||siteState.stopped) return;
    const siteConfig=siteConfigFor(site);
    const adapter=adapterFactory?await adapterFactory({site,manifest,evidence,fixture}):new ToolAdapter({tool:site.tool,
      site:siteConfig,evidence,fixture,timezoneId:site.timezoneId,options:{captureScreenshots:false,observeMs:manifest.observe_ms}});
    session.adapter=adapter;
    let timer;
    try {
      const remaining=state.deadline_at_ms-clock.now();
      if(remaining<=0) throw new Error('max_duration_before_adapter_setup');
      const opening=Promise.resolve().then(()=>adapter.open());opening.catch(()=>{});
      const opened=await Promise.race([opening.then(value=>({value})),new Promise(resolve=>{
        timer=setTimeout(()=>resolve({deadline:true}),remaining);
      })]);
      if(opened.deadline) {
        state.global_stop={reason:'max_duration_during_adapter_setup',at_ms:clock.now()};
        siteState.stopped={reason:'max_duration_during_adapter_setup'};
        opening.finally(()=>closeSite(site)).catch(()=>{});
        return;
      }
      if(site.timezoneId&&adapter.capabilities?.timezoneId!==true) {
        siteState.stopped={reason:'unsupported_timezone',requested:site.timezoneId,
          observed:adapter.browser?.runtime?.timezone?.observed??null};
        evidence.result({client:site.tool,target:site.id,role:'search_load_preflight',outcome:'unsupported_capability',
          capability:'timezoneId',requested:site.timezoneId,observed:siteState.stopped.observed});
        evidence.flush(true);
        await closeSite(site);
      }
    } catch(error) {
      siteState.stopped={reason:'adapter_setup_failed',error:safeError(error)};
      evidence.result({client:site.tool,target:site.id,role:'search_load_preflight',outcome:'adapter_error',error:safeError(error)});
      evidence.flush(true);
      await closeSite(site);
    } finally {
      clearTimeout(timer);
    }
    writeJSON(statePath,state);
  };
  const findNextStage=siteState=>{
    let stageIndex=siteState.next_stage,index=siteState.next_index;
    while(stageIndex<stagePlan.length&&index>=stagePlan[stageIndex].max_searches) {stageIndex++;index=0;}
    siteState.next_stage=stageIndex;siteState.next_index=index;
    return stageIndex<stagePlan.length?{stageIndex,index,stage:stagePlan[stageIndex]}:null;
  };
  try {
    while(!state.global_stop) {
      const active=manifest.sites.map((site,index)=>({site,index,state:state.sites[site.id],session:sessions.get(site.id)}))
        .filter(item=>!item.state.stopped&&findNextStage(item.state));
      if(!active.length) break;
      const now=clock.now();
      if(now>=state.deadline_at_ms) {state.global_stop={reason:'max_duration',at_ms:now};break;}
      const unstarted=active.filter(item=>item.state.query_count===0);
      let selected;
      if(unstarted.length) {
        state.first_wave_complete=false;
        selected=unstarted.sort((a,b)=>(a.index-state.scheduler_cursor+manifest.sites.length)%manifest.sites.length
          -(b.index-state.scheduler_cursor+manifest.sites.length)%manifest.sites.length)[0];
      } else {
        state.first_wave_complete=true;
        selected=active.sort((a,b)=>a.index-b.index)[0];
      }
      const next=findNextStage(selected.state),last=selected.state.measurements.at(-1)?.started_clock_ms;
      const earliest=last===undefined?now:last+1000/next.stage.qps;
      if(earliest>now) await clock.sleep(Math.min(earliest-now,state.deadline_at_ms-now));
      if(clock.now()>=state.deadline_at_ms) {state.global_stop={reason:'max_duration',at_ms:clock.now()};break;}
      await setupSite(selected.site);
      if(state.global_stop) break;
      if(selected.state.stopped||!selected.session.adapter) continue;
      const {stageIndex,index,stage}=findNextStage(selected.state);
      const savedBytes=savedEvidenceBytes(evidence.directory);
      if(savedBytes>=manifest.max_evidence_bytes) {
        selected.state.stopped={stage:stage.id,index,reason:'saved_evidence_limit_before_navigation',saved_evidence_bytes:savedBytes};
        await closeSite(selected.site);writeJSON(statePath,state);continue;
      }
      if(clock.now()>=state.deadline_at_ms) {state.global_stop={reason:'max_duration',at_ms:clock.now()};break;}
      const previousStart=selected.state.measurements.at(-1)?.started_clock_ms??null;
      const queryIndex=selected.state.query_count,started=clock.now(),query=manifest.queries[queryIndex%manifest.queries.length];
      selected.state.inflight={stage:stage.id,stage_index:stageIndex,index,query_index:queryIndex,query,started_clock_ms:started};
      selected.state.query_count++;
      selected.state.next_stage=stageIndex;selected.state.next_index=index+1;
      if(selected.state.next_index>=stage.max_searches) {selected.state.next_stage++;selected.state.next_index=0;}
      writeJSON(statePath,state);
      await beforeNavigation?.({site:selected.site,stage,index,query});
      const url=selected.site.search_url.replace('{query}',encodeURIComponent(query));
      const recordsBefore=evidence.records.length;
      let result,deadlineTimer;
      const navigation=Promise.resolve().then(()=>selected.session.adapter.goto(url));navigation.catch(()=>{});
      try {
        const cutoff=new Promise(resolve=>{deadlineTimer=setTimeout(()=>resolve({deadline:true}),Math.max(0,state.deadline_at_ms-clock.now()));});
        const completed=await Promise.race([navigation.then(value=>({value})),cutoff]);
        if(completed.deadline) {result={outcome:'run_deadline_reached'};state.global_stop={reason:'max_duration_during_navigation',at_ms:clock.now()};}
        else result=completed.value;
      } catch(error) {result={outcome:'execution_error',error:safeError(error)};}
      finally {clearTimeout(deadlineTimer);}
      const finished=clock.now(),reason=blockReason(result||{},selected.site.id),timedOut=state.global_stop?.reason==='max_duration_during_navigation';
      const stopReason=timedOut?'max_duration':reason,hrefs=hrefObservation(evidence,result,selected.site.origin);
      const screenshot=(!timedOut&&(selected.state.measurements.length===0||stopReason))
        ?await captureNativeScreenshot(selected.session.adapter,evidence,selected.site,`${stage.id}-${index}`):null;
      evidence.flush(true);
      const requestRecords=evidence.records.slice(recordsBefore),mainDocuments=requestRecords.filter(record=>record.kind==='main_document').length;
      const observation={stage:stage.id,qps:stage.qps,index,query_index:queryIndex,query,
        started_at:new Date(started).toISOString(),started_clock_ms:started,finished_at:new Date(finished).toISOString(),
        latency_ms:Math.max(0,finished-started),http_status:result?.http_status??null,outcome:result?.outcome??'missing_result',
        search_navigation_count:1,blocked_requests:result?.blocked_requests?.length??0,network_request_records:requestRecords.length,
        main_document_request_records:mainDocuments,background_resource_records:requestRecords.length-mainDocuments,
        main_record:result?.main_record??null,search_results_status:hrefs.external_href_count
          ?'external_href_candidates_observed_unverified':'no_external_href_candidates_observed',hrefs,
        screenshot_sha256:screenshot,stop_reason:stopReason};
      if(previousStart!==null) observation.start_interval_ms=started-previousStart;
      selected.state.measurements.push(observation);delete selected.state.inflight;
      if(stopReason) selected.state.stopped={stage:stage.id,index,reason:stopReason};
      state.scheduler_cursor=(selected.index+1)%manifest.sites.length;
      if(stopReason) await closeSite(selected.site);
      writeJSON(statePath,state);
      if(state.global_stop) break;
    }
  } finally {
    try {for(const site of manifest.sites) await closeSite(site);}
    finally {evidence.flush(true);}
  }
  if(state.global_stop) for(const site of manifest.sites) {
    const siteState=state.sites[site.id];
    if(!siteState.stopped) siteState.stopped={reason:siteState.query_count?'max_duration':'not_run_before_max_duration'};
  }
  const outcomes=manifest.sites.map(site=>{
    const siteState=state.sites[site.id];
    return {site:site.id,queries:siteState.query_count,stopped:siteState.stopped,completed_stages:siteState.next_stage,
      first_search_observed:siteState.query_count>0};
  });
  state.rate_note='Per-site QPS values are upper bounds. Achieved rates can be lower because of navigation latency, site stops, or the global run deadline.';
  state.flush_interval_ms=manifest.flush_interval_ms;
  state.observation_note=`observe_ms=${manifest.observe_ms} is the DOM observation window after navigation; it does not confirm SERP completeness or search accuracy.`;
  const observedBytes=evidence.records.reduce((sum,record)=>sum+(record.bytes_charged||0),0);
  const storedBytes=savedEvidenceBytes(evidence.directory);
  state.observed_response_body_bytes=observedBytes;
  state.saved_evidence_bytes=storedBytes;
  state.saved_evidence_limit=manifest.max_evidence_bytes;
  state.duration_limit_ms=manifest.max_duration_ms;
  state.limit_note='Saved evidence bytes are checked between navigations; one navigation can exceed the limit. Response-body bytes are reported separately and do not represent all network traffic; WebSocket frames are not recorded.';
  evidence.save('search-load-state.json',state);
  evidence.flush(true);
  return {directory,sites:outcomes,verification:verify(directory)};
}

export async function runSearchLoad(options) {
  const directory=path.resolve(options.directory),lockPath=directory+'.lock';
  fs.mkdirSync(path.dirname(directory),{recursive:true});
  const lock=fs.openSync(lockPath,'wx');
  try {
    fs.writeSync(lock,JSON.stringify({pid:process.pid,started_at:new Date().toISOString()}));
    return await executeSearchLoad({...options,directory});
  } finally {fs.closeSync(lock);fs.unlinkSync(lockPath);}
}

export function parseLoadCLI(args) {
  const [mode,...rest]=args;
  if(!['plan','run','verify'].includes(mode)) throw new Error('Usage: search-load.mjs plan MANIFEST | run MANIFEST RUN_DIRECTORY [--resume] [--fixture] | verify RUN_DIRECTORY');
  if(mode==='plan' && (rest.length===1||(rest.length===2&&rest[1]==='--fixture')))
    return {mode,manifestPath:rest[0],fixture:rest.includes('--fixture')};
  if(mode==='verify' && rest.length===1) return {mode,directory:rest[0]};
  if(mode==='run' && rest.length>=2 && rest.length<=4 && rest.slice(2).every(arg=>['--resume','--fixture'].includes(arg)))
    return {mode,manifestPath:rest[0],directory:rest[1],resume:rest.includes('--resume'),fixture:rest.includes('--fixture')};
  throw new Error('invalid_cli_arguments');
}

async function main() {
  const options=parseLoadCLI(process.argv.slice(2));
  if(options.mode==='verify') {const result=verify(path.resolve(options.directory));console.log(JSON.stringify(result,null,2));if(!result.ok)process.exitCode=1;return;}
  const input=JSON.parse(fs.readFileSync(path.resolve(options.manifestPath),'utf8'));
  const manifest=validateLoadManifest(input,{fixture:options.fixture});
  if(options.mode==='plan') {console.log(JSON.stringify(loadPlan(manifest),null,2));return;}
  const result=await runSearchLoad({...options,manifest,directory:path.resolve(options.directory)});
  console.log(JSON.stringify(result,null,2));
  if(!result.verification.ok) process.exitCode=1;
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  main().catch(error=>{console.error(JSON.stringify(safeError(error)));process.exitCode=1;});
}
