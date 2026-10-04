import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Evidence,verify,safeError} from '../../experiments/bot-diagnostics/evidence.mjs';
import {ToolAdapter} from '../../experiments/bot-diagnostics/adapters.mjs';
import {snapshotSources,sha,proxy} from '../../experiments/bot-diagnostics/runtime.mjs';
import {pageEvaluator} from '../../experiments/bot-diagnostics/search-pagination.mjs';
import {openBrowser} from '../../experiments/bot-diagnostics/runner.mjs';

const directory=path.resolve(process.argv[2]||'');
if(!process.argv[2]||fs.existsSync(directory)) throw new Error('new_run_directory_required');
const proxyNames=['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'];
if(proxyNames.some(name=>Boolean(process.env[name]))||proxy) throw new Error('proxy_present; stopped_without_unsetting');
if(process.platform==='linux'&&fs.existsSync('/etc/codex/network-policy.json')) throw new Error('network_policy_present; stopped_without_modification');

const query='東京都大田区 池上本門寺 松濤園 公開日';
const googleOrigin='https://www.google.com';
const searchURL=`${googleOrigin}/search?q=${encodeURIComponent(query)}`;
const site={id:'google-direct',origins:['https://api.ipify.org',googleOrigin],links:{home:googleOrigin+'/'} };
const evidence=new Evidence(directory,false,{policy:'browser_observation',authorization:'User-authorized direct-route comparison: one ipify page and at most two Google SERP pages; no proxy configured; maximum three top-level navigations.'});
evidence.save('direct-route.json',{checked_at:new Date().toISOString(),proxy_environment_variables_present:false,runtime_proxy_configured:false,
  network_policy_file_present:false,route:'Chromium native browser with fresh non-persistent context; no proxy option configured',
  browser_mode:'headless; WSL Xvfb could not bind its unix listener because the shared WSL X11 socket directory is not writable',
  limits:{top_level_navigation_limit:3,duration_limit_ms:180000,evidence_limit_bytes:67108864},
  comparison:{prior_google_exit:'35.189.135.33',query,timezone:'Asia/Tokyo',publisher_navigation:false}});
evidence.save('sources.json',snapshotSources(evidence));
const sourceBytes=fs.readFileSync(fileURLToPath(import.meta.url));
evidence.save('scenario-source.json',{source_path:fileURLToPath(import.meta.url),source_blob_sha256:evidence.blob(sourceBytes)});
const siteConfig={...site,result_selectors:['a[jsname="UWckNb"]:has(h3)'],next_selectors:['a#pnnext'],next_text:null,id:'google'};
const adapter=await new ToolAdapter({tool:'patchright',site,evidence,profile:{id:'google-direct-fresh'},
  options:{captureScreenshots:false,observeMs:0},timezoneId:'Asia/Tokyo',
  browserOpener:(name,options)=>openBrowser(name,{...options,headful:false}),
  clientOpener:async()=>({close:async()=>{}})}).open();
if(adapter.browser?.runtime?.headless!==true) throw new Error('headless_browser_not_confirmed');
const started=Date.now(),deadline=started+180000,rows=[];
let previousNavigationStart=null,stopReason=null,ipExitDiffers=null;
const byteCount=()=>fs.readdirSync(path.join(directory,'blobs'),{withFileTypes:true}).filter(x=>x.isFile())
  .reduce((sum,x)=>sum+fs.statSync(path.join(directory,'blobs',x.name)).size,0);
const startNavigation=async()=>{
  if(Date.now()>=deadline) throw new Error('duration_limit');
  if(previousNavigationStart!==null) await new Promise(resolve=>setTimeout(resolve,Math.max(0,previousNavigationStart+1000-Date.now())));
  previousNavigationStart=Date.now();return previousNavigationStart;
};
const mainHeaders=[];
const onResponse=async response=>{try{if(response.request().isNavigationRequest()) mainHeaders.push({url:response.url(),status:response.status(),names:Object.keys(await response.allHeaders()).map(x=>x.toLowerCase()).sort()});}catch{}};
adapter.browser.page.on('response',onResponse);
try {
  const ipStart=await startNavigation();
  const ipResult=await adapter.accessLink('https://api.ipify.org?format=json',{role:'direct_route_identity',operation:async browser=>{
    const response=await browser.page.goto('https://api.ipify.org?format=json',{waitUntil:'domcontentloaded',timeout:10000});
    let ip=null;try{ip=JSON.parse(await browser.page.locator('body').innerText({timeout:2000})).ip;}catch{}
    if(typeof ip==='string') ipExitDiffers=ip!=='35.189.135.33';
    return {http_status:response?.status()??null,ip_observed:typeof ip==='string',different_from_prior_google_exit:ipExitDiffers};
  }});
  const ipRecord=evidence.records.find(item=>item.kind==='main_document');
  evidence.save('ipify-check.json',{started_at_ms:ipStart,http_status:ipResult.http_status??null,outcome:ipResult.operation?.outcome||ipResult.outcome,
    different_from_prior_google_exit:ipExitDiffers,raw_ip_body_sha256:ipRecord?.body_sha256||null,
    note:'Raw response is retained as an evidence blob; address value omitted from this summary.'});
  if([401,403,429].includes(ipResult.http_status)||ipResult.outcome==='challenge_observed') stopReason='ipify_refusal_or_challenge';
  if(byteCount()>67108864) stopReason='evidence_limit';
  let currentURL=searchURL,priorDomSha=null;
  for(let page=1;page<=2&&!stopReason;page++) {
    const navigationStart=await startNavigation(),headerOffset=mainHeaders.length;
    const result=await adapter.accessLink(currentURL,{role:'search_pagination_page',operation:pageEvaluator(siteConfig,10,currentURL,true,false,
      Math.max(1,Math.min(10000,deadline-Date.now())))});
    const operation=result.operation||{},domSha=result.dom_sha256||null;
    const record=evidence.records.find(item=>item.index===result.main_record);
    const html=domSha?fs.readFileSync(path.join(directory,'blobs',domSha),'utf8'):'';
    const googleGate=/captcha|challenge|verify you are human|unusual traffic|automated queries|ロボットではない|人間であること|異常なトラフィック|自動化されたクエリ/i.test(operation.text||'');
    let challengeUrl=false;
    try {const final=new URL(result.final_url||currentURL);challengeUrl=/\/(?:sorry|challenge|captcha)(?:\/|$)/i.test(final.pathname)||/(?:captcha|challenge|sorry)/i.test(final.searchParams.get('q')||'');} catch {}
    const recaptchaScripts=[...html.matchAll(/<script\b[^>]*\bsrc=["']([^"']*(?:recaptcha|google\.com\/recaptcha)[^"']*)["'][^>]*>/gi)].map(match=>match[1]).slice(0,20);
    const response=mainHeaders.slice(headerOffset).filter(item=>item.url===result.final_url||item.url===currentURL).at(-1)||null;
    const row={page,url:currentURL,final_url:result.final_url||null,http_status:result.http_status??record?.response_status??null,
      outcome:result.outcome||null,observed_result_card_count:Array.isArray(operation.items)?operation.items.length:0,
      result_titles:(operation.items||[]).slice(0,10).map(item=>item.title),next_url:operation.next||null,dom_sha256:domSha,
      dom_changed_from_previous:priorDomSha===null?null:priorDomSha!==domSha,google_gate_observed:googleGate,challenge_url_observed:challengeUrl,
      captcha_or_recaptcha_scripts:recaptchaScripts,main_document_header_names:response?.names||[],
      main_document_response_header_values_kept:record?.headers||record?.response_headers||{},started_at_ms:navigationStart,
      operation_status:operation.status||null,stop_reason:null};
    const refusal=[401,403,429].includes(row.http_status)||['challenge','blocked','rate_limited','verification_gate','auth_required'].includes(operation.status)||googleGate||challengeUrl;
    if(refusal){row.stop_reason='refusal_or_challenge';stopReason=row.stop_reason;}
    else if(page===2&&domSha===priorDomSha){row.stop_reason='next_dom_unchanged';stopReason=row.stop_reason;}
    else if(page===1&&!operation.next){row.stop_reason='next_link_missing';stopReason=row.stop_reason;}
    else if(byteCount()>67108864){row.stop_reason='evidence_limit';stopReason=row.stop_reason;}
    rows.push(row);
    evidence.result({client:'patchright',target:'google',profile:'google-direct-fresh',role:'search_pagination_page',outcome:row.stop_reason||row.outcome,
      page,query,query_sha256:sha(query),http_status:row.http_status,observed_result_card_count:row.observed_result_card_count,
      result_titles:row.result_titles,next_url:row.next_url,dom_sha256:domSha,dom_changed_from_previous:row.dom_changed_from_previous,
      google_gate_observed:googleGate,challenge_url_observed:challengeUrl,captcha_or_recaptcha_scripts:recaptchaScripts,main_document_header_names:row.main_document_header_names,
      final_url:row.final_url,operation_status:row.operation_status});
    priorDomSha=domSha;
    currentURL=operation.next||'';
    if(!currentURL) break;
  }
} catch(error) {
  evidence.save('execution-error.json',{error:safeError(error),pages:rows.length,finished_at:new Date().toISOString()});
  stopReason||='execution_error';
} finally {
  adapter.browser.page.off('response',onResponse);await adapter.close();evidence.flush(true);
}
evidence.save('direct-check.json',{query,page_count:rows.length,pages:rows,ipify_different_from_prior_google_exit:ipExitDiffers,
  stop_reason:stopReason||'authorized_page_cap_or_next_unavailable',started_at:new Date(started).toISOString(),finished_at:new Date().toISOString(),
  saved_evidence_bytes:byteCount()});
const verification=verify(directory);evidence.save('verification.json',verification);
if(!verification.ok) throw new Error(JSON.stringify(verification.errors));
console.log(JSON.stringify({ok:true,pages:rows.length,ipify_different_from_prior_google_exit:ipExitDiffers,stop_reason:stopReason,
  verification:{records:verification.records,results:verification.results,ok:verification.ok},evidence_directory:directory}));
