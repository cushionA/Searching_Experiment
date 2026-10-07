// Headful, loopback-only integration check for native 4play tab navigation.
// Usage: node experiments/bot-diagnostics/fourplay-navigation-fixture.mjs NEW_DIR --native|--hybrid
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import {openFourplayNative} from './fourplay-native-runtime.mjs';
import {openCamoufoxFourplay} from './camoufox-fourplay-runtime.mjs';

const output=path.resolve(process.argv[2]||'');
const mode=process.argv[3];
if(!process.argv[2]||!['--native','--hybrid'].includes(mode)||fs.existsSync(output))
  throw new Error('usage: fourplay-navigation-fixture.mjs NEW_OUTPUT_DIRECTORY --native|--hybrid (directory must not exist)');
fs.mkdirSync(output,{recursive:true});
const requests=[], responses=[], failures=[];
const html=(title,body)=>`<!doctype html><meta charset="utf-8"><title>${title}</title><main id="fixture">${body}</main>`;
const pages={
  '/start':html('Start',`<p id="marker">start-page</p><a id="to-b" href="/b">Continue to B</a>`),
  '/redirect-target':html('Redirect target',`<p id="marker">redirect-target-dom-complete</p><a id="to-b" href="/b">Continue to B</a>`),
  '/b':html('Page B',`<p id="marker">page-b</p><a id="to-c" href="/c">Continue to C</a>`),
  '/c':html('Page C',`<p id="marker">page-c</p>`),
  '/denied-get':html('GET denied',`<p id="marker">fixture-get-403-body</p>`),
  '/denied-post':html('POST denied',`<p id="marker">fixture-post-403-body</p>`),
};
const server=http.createServer((req,res)=>{
  const chunks=[]; req.on('data',chunk=>chunks.push(chunk));
  req.on('end',()=>{
    const item={method:req.method,url:req.url,referer:req.headers.referer||null,
      sec_fetch_site:req.headers['sec-fetch-site']||null,sec_fetch_mode:req.headers['sec-fetch-mode']||null,
      cookie_names:String(req.headers.cookie||'').split(';').map(v=>v.trim().split('=')[0]).filter(Boolean),
      body_bytes:Buffer.concat(chunks).length};
    requests.push(item);
    if(req.url==='/start') {res.writeHead(302,{location:'/redirect-target','cache-control':'no-store'});res.end();return;}
    const status=req.url==='/denied-get'||req.url==='/denied-post'?403:200;
    const key=req.url.split('?')[0];
    res.writeHead(status,{'content-type':'text/html; charset=utf-8','cache-control':'no-store',
      ...(key==='/redirect-target'?{'set-cookie':'fixture_session=loopback; Path=/; SameSite=Lax'}:{})});
    res.end(pages[key]||html('Not found','<p id="marker">not-found</p>'));
  });
});
await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
const origin=`http://127.0.0.1:${server.address().port}`;
let browser, summary=null;
const save=(name,value)=>fs.writeFileSync(path.join(output,name),JSON.stringify(value,null,2)+'\n');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label,timeoutMs=10000){
  const end=Date.now()+timeoutMs;let last;
  while(Date.now()<end){try{last=await check();if(last)return last;}catch{}await wait(100);}
  throw new Error(`fixture_timeout:${label}`);
}
function safeHeaders(headers={}){
  const get=name=>{const key=Object.keys(headers).find(k=>k.toLowerCase()===name);return key?headers[key]:null;};
  return {referer:get('referer')||null,sec_fetch_site:get('sec-fetch-site')||null,
    sec_fetch_mode:get('sec-fetch-mode')||null,
    cookie_names:String(get('cookie')||'').split(';').map(v=>v.trim().split('=')[0]).filter(Boolean)};
}
async function main(){
  browser=mode==='--native'
    ? await openFourplayNative({fixture:true})
    : await openCamoufoxFourplay({fixture:true});
  const {page}=browser;
  page.on('request',request=>requests.push({source:'browser',method:request.method(),url:request.url(),
      resource_type:request.resourceType(),navigation:request.isNavigationRequest(),tab_id:page._tabId,
      container_id:browser.container?.id??null,...safeHeaders(request.headers?.())}));
  page.on('response',async response=>{
      try {const body=await response.body();responses.push({url:response.url(),status:response.status(),
        method:response.request().method(),resource_type:response.request().resourceType(),
        navigation:response.request().isNavigationRequest(),body_bytes:body.length,
        body_sha256:crypto.createHash('sha256').update(body).digest('hex'),
        body_excerpt:body.toString('utf8').slice(0,400)});}catch(error){responses.push({url:response.url(),status:response.status(),body_error:String(error.message)});}
    });
  page.on('requestfailed',request=>failures.push({method:request.method(),url:request.url(),
    failure:request.failure?.()||null}));
  await page.goto(origin+'/start',{timeout:20000});
  const redirect=await page.evaluate(()=>({url:location.href,title:document.title,marker:document.querySelector('#marker')?.textContent,
    ready:document.readyState,html:document.documentElement.outerHTML}));
  if(redirect.url!==origin+'/redirect-target'||redirect.marker!=='redirect-target-dom-complete'||redirect.ready!=='complete')
    throw new Error('redirect_target_dom_incomplete');
  await until(()=>responses.some(r=>r.url===origin+'/redirect-target'&&r.status===200&&r.navigation&&r.method==='GET'),
    'requested main_frame response');
  const redirectResponse=responses.find(r=>r.url===origin+'/redirect-target'&&r.status===200&&r.navigation&&r.method==='GET');
  const tabId=page._tabId;
  await page.evaluate(()=>document.querySelector('#to-b').click());
  await until(async()=>page.url()===origin+'/b'&&await page.evaluate(()=>document.querySelector('#marker')?.textContent)==='page-b','anchor-B');
  await page.evaluate(()=>document.querySelector('#to-c').click());
  await until(async()=>page.url()===origin+'/c'&&await page.evaluate(()=>document.querySelector('#marker')?.textContent)==='page-c','anchor-C');
  const browserB=requests.find(x=>x.source==='browser'&&x.url===origin+'/b'&&x.navigation);
  const browserC=requests.find(x=>x.source==='browser'&&x.url===origin+'/c'&&x.navigation);
  const chain={tab_id_before:tabId,tab_id_after:page._tabId,container_id:browser.container?.id??null,url:page.url(),
    b:{referer:browserB?.referer??null,tab_id:browserB?.tab_id??null,container_id:browserB?.container_id??null},
    c:{referer:browserC?.referer??null,tab_id:browserC?.tab_id??null,container_id:browserC?.container_id??null},
    cookie_names:browserC?.cookie_names||[]};
  if(tabId!==page._tabId) throw new Error('same_tab_id_changed');
  if(!browserB||!browserC||browserB.referer!==origin+'/redirect-target'||browserC.referer!==origin+'/b')
    throw new Error('same_tab_referer_chain_incomplete');
  if(!chain.container_id||browserB.container_id!==chain.container_id||browserC.container_id!==chain.container_id||
    browserB.tab_id!==tabId||browserC.tab_id!==tabId) throw new Error('same_tab_container_continuity_failed');
  if(!chain.cookie_names.includes('fixture_session')) throw new Error('fixture_cookie_missing_on_C');
  // Exercise ordinary browser navigation for both status pages. A 403 must arrive as a response.
  await page.goto(origin+'/denied-get',{timeout:20000});
  const deniedGet=await page.evaluate(()=>({url:location.href,marker:document.querySelector('#marker')?.textContent,ready:document.readyState}));
  if(deniedGet.marker!=='fixture-get-403-body'||deniedGet.ready!=='complete') throw new Error('GET_403_DOM_body_missing');
  await page.goto(origin+'/start',{timeout:20000});
  await page.evaluate(url=>{const f=document.createElement('form');f.method='POST';f.action=url;document.body.append(f);f.submit();},origin+'/denied-post');
  await until(async()=>page.url()===origin+'/denied-post'&&await page.evaluate(()=>document.querySelector('#marker')?.textContent)==='fixture-post-403-body','POST-403');
  await until(()=>responses.some(r=>r.url===origin+'/denied-get'&&r.status===403)&&responses.some(r=>r.url===origin+'/denied-post'&&r.status===403),'403 responses');
  const statusChecks={get:{...deniedGet,response:responses.find(r=>r.url===origin+'/denied-get'&&r.status===403)||null},
    post:{url:page.url(),marker:await page.evaluate(()=>document.querySelector('#marker')?.textContent),
      response:responses.find(r=>r.url===origin+'/denied-post'&&r.status===403)||null}};
  for(const [name,entry,method] of [['GET',statusChecks.get,'GET'],['POST',statusChecks.post,'POST']]){
    if(entry.response?.status!==403||entry.response?.method!==method||!entry.response.body_sha256)
      throw new Error(`${name}_403_not_recorded_as_response`);
    if(failures.some(item=>item.url===entry.response.url&&item.method===method))
      throw new Error(`${name}_403_reported_as_network_failure`);
  }
  summary={ok:true,schema:1,mode:mode.slice(2),fixture_origin:origin,fixture:true,network_scope:'127.0.0.1 loopback fixture only',
    initial_navigation:{requested:origin+'/start',redirect_target:redirect.url,title:redirect.title,marker:redirect.marker,
      ready_state:redirect.ready,dom_sha256:crypto.createHash('sha256').update(redirect.html).digest('hex'),response:redirectResponse},
    same_tab_anchor_chain:chain,status_checks:statusChecks,
    browser_runtime:browser.runtime||null,assertions:{redirect_target_dom_complete:true,requested_main_frame_response:true,same_tab_A_to_B_to_C:true,
      referer_and_cookie_observed_on_chain:true,container_and_tab_continuity:true,
      get_403_response:true,post_403_response:true}};
  console.log(JSON.stringify({output,mode:summary.mode,assertions:summary.assertions}));
}
try {await main();}
catch(error){summary={ok:false,schema:1,mode:mode.slice(2),fixture_origin:origin,error:{name:error.name,message:error.message,stack:error.stack}};process.exitCode=1;}
finally {
  if(summary)save('summary.json',summary);
  save('browser-requests.json',requests);save('browser-responses.json',responses);save('browser-request-failures.json',failures);
  save('fixture-http-requests.json',requests.filter(x=>!x.source));
  try{await browser?.close?.();}catch{}await new Promise(resolve=>server.close(resolve));
}
