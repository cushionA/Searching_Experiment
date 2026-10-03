#!/usr/bin/env node
// Local-only regression harness for Obscura's outbound request interception.
// Usage: node obscura-interception-smoke.mjs NEW_DIR --binary=/absolute/path/to/obscura [--driver=puppeteer|playwright]
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {once} from 'node:events';
import {fileURLToPath, pathToFileURL} from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const argv = process.argv.slice(2);
if (!argv[0] || argv[0].startsWith('--')) throw new Error('Usage: node obscura-interception-smoke.mjs NEW_DIR --binary=/absolute/path [--driver=puppeteer|playwright]');
const output = path.resolve(argv.shift());
let binary, driver = 'puppeteer';
for (const arg of argv) {
  if (arg.startsWith('--binary=')) binary = path.resolve(arg.slice(9));
  else if (arg.startsWith('--driver=')) driver = arg.slice(9);
  else throw new Error(`Unknown argument: ${arg}`);
}
if (!binary || !path.isAbsolute(binary) || !fs.statSync(binary).isFile()) throw new Error('--binary must name an existing absolute executable file');
if (!['puppeteer','playwright'].includes(driver)) throw new Error('--driver must be puppeteer or playwright');
if (fs.existsSync(output)) throw new Error(`Output directory already exists: ${output}`);
fs.mkdirSync(output,{recursive:true});
const startedAt = new Date().toISOString();
async function sha256File(file) { const hash=createHash('sha256'); for await (const chunk of fs.createReadStream(file)) hash.update(chunk); return hash.digest('hex'); }
const inputHash = await sha256File(fileURLToPath(import.meta.url));
const binaryHash = await sha256File(binary);
const trace = {started_at:startedAt,inputs:{output,binary,driver},source_sha256:inputHash,binary_sha256:binaryHash,events:[],server_requests:[],assertions:[],errors:[]};
const now = () => Number(process.hrtime.bigint()) / 1e6;
function event(type, data={}) { const row={type,at_ms:now(),...data}; trace.events.push(row); return row; }
function check(name, fn) {
  try { fn(); trace.assertions.push({name,ok:true}); }
  catch (error) { trace.assertions.push({name,ok:false,error:String(error?.stack||error)}); throw error; }
}
const temp = fs.mkdtempSync(path.join(os.tmpdir(),'obscura-interception-smoke-'));
let serverA,serverB,child,browser,activeContext;
let binaryStderr='';
const serverHits = new Map();
let activeCase = null;
function makeServer(label, handler) {
  const s=http.createServer((req,res)=>{
    const hit={case:activeCase,server:label,method:req.method,url:req.url,at_ms:now(),headers:{...req.headers}};
    const originalEnd=res.end; res.end=function(chunk,...args){ if(chunk!==undefined&&chunk!==null) hit.response_body=Buffer.isBuffer(chunk)?chunk.toString('utf8'):String(chunk); else hit.response_body=''; return originalEnd.call(this,chunk,...args); };
    trace.server_requests.push(hit); const key=`${label} ${req.method} ${req.url}`;
    serverHits.set(key,(serverHits.get(key)||0)+1);
    handler(req,res,hit);
  });
  return s;
}
async function listen(s) { await new Promise((resolve,reject)=>{s.once('error',reject);s.listen(0,'127.0.0.1',resolve);}); }
async function closeServer(s) { if(s?.listening) await new Promise(resolve=>s.close(resolve)); }
const pageHTML = (foreign) => `<!doctype html><title>fixture</title><div id="result">loaded</div><link rel="stylesheet" href="/style.css"><script src="/asset.js"></script><script type="module" src="/module-entry.js"></script><img src="${foreign}/pixel.png"><script>fetch('/late').then(r=>r.text()).then(t=>document.body.dataset.late=t); fetch('${foreign}/forbidden-fetch').catch(()=>{}); fetch('/post',{method:'POST',body:'x'}).catch(()=>{}); fetch('${foreign}/cross-get').catch(()=>{});</script>`;
async function setupServers() {
  serverA=makeServer('primary',(req,res)=>{
    const u=new URL(req.url,'http://fixture');
    if(u.pathname==='/redirect'){res.writeHead(302,{location:'/hop'});res.end();}
    else if(u.pathname==='/foreign-redirect'){res.writeHead(302,{location:`${foreignBase}/redirected`});res.end();}
    else if(u.pathname==='/fetch-case'){res.setHeader('content-type','text/html');res.end('<!doctype html><script>fetch("/fetch-redirect").catch(()=>{})</script>fetch');}
    else if(u.pathname==='/page-a'||u.pathname==='/page-b'){res.setHeader('content-type','text/html');res.end(`<!doctype html><title>${u.pathname.slice(1)}</title><body>${u.pathname.slice(1)}</body>`);}
    else if(u.pathname==='/delayed-result'){res.end('delayed-A-ok');}
    else if(u.pathname==='/blocked-result'){res.end('should-never-reach-server');}
    else if(u.pathname==='/fetch-redirect'){res.writeHead(302,{location:'/fetch-final'});res.end();}
    else if(u.pathname==='/fetch-final'){res.end('should be denied');}
    else if(u.pathname==='/hop'){res.writeHead(302,{location:'/page'});res.end();}
    else if(u.pathname==='/page'){res.setHeader('content-type','text/html');res.end(pageHTML(foreignBase));}
    else if(u.pathname==='/asset.js'){res.setHeader('content-type','text/javascript');res.end("document.documentElement.dataset.script='yes';");}
    else if(u.pathname==='/module-entry.js'){res.setHeader('content-type','text/javascript');res.end("import {value} from '/module-dep.js'; document.documentElement.dataset.module=value;");}
    else if(u.pathname==='/module-dep.js'){res.setHeader('content-type','text/javascript');res.end("export const value='module-ok';");}
    else if(u.pathname==='/style.css'){res.setHeader('content-type','text/css');res.end('body{color:rgb(1,2,3)}');}
    else if(u.pathname==='/late'){res.end('late-ok');}
    else if(u.pathname==='/cookie'){res.setHeader('content-type','text/html');res.end('<script>document.cookie="smoke=present; path=/"</script>cookie');}
    else if(u.pathname==='/cookie-check'){res.setHeader('content-type','text/plain');res.end('fresh context check');}
    else {res.end('ok');}
  });
  serverB=makeServer('secondary',(_req,res)=>{res.setHeader('access-control-allow-origin','*');res.setHeader('access-control-allow-methods','GET,POST,OPTIONS');res.setHeader('access-control-allow-headers','*');res.end('foreign');});
  await listen(serverA); await listen(serverB);
  primaryBase=`http://127.0.0.1:${serverA.address().port}`;
  foreignBase=`http://127.0.0.1:${serverB.address().port}`;
}
let primaryBase,foreignBase;
function hit(label, method, url, caseName=activeCase) { return trace.server_requests.find(x=>x.case===caseName&&x.server===label&&x.method===method&&x.url===url); }
function allHits(label, caseName=activeCase) { return trace.server_requests.filter(x=>x.case===caseName&&x.server===label); }
async function launch() {
  const reserve=http.createServer(); await listen(reserve); const port=reserve.address().port; await closeServer(reserve);
  const storage=path.join(temp,'storage'); fs.mkdirSync(storage);
  const args=['serve','--host','127.0.0.1','--port',String(port),'--workers','1','--max-connections','1','--storage-dir',storage,'--quiet','--allow-private-network'];
  child=spawn(binary,args,{env:{...process.env},stdio:['ignore','ignore','pipe']});
  let logs=''; child.stderr.on('data',b=>{logs=(logs+b.toString()).slice(-12000);binaryStderr=logs;});
  child.on('error',err=>trace.errors.push({where:'binary_spawn',error:String(err)}));
  let version;
  for(let i=0;i<100;i++){
    if(child.exitCode!==null||child.signalCode!==null) throw new Error(`Obscura exited during startup: ${logs}`);
    try {const r=await fetch(`http://127.0.0.1:${port}/json/version`,{signal:AbortSignal.timeout(350)});if(r.ok){version=await r.json();break;}}catch{}
    await new Promise(r=>setTimeout(r,100));
  }
  if(!version) throw new Error(`Obscura CDP startup timeout: ${logs}`);
  const require=createRequire(path.join(repo,'package.json'));
  if(driver==='puppeteer'){
    const pptr=createRequire(path.join(repo,'.deps/bot-diagnostics/package.json'))('puppeteer-core');
    browser=await pptr.connect({browserWSEndpoint:version.webSocketDebuggerUrl,defaultViewport:null,protocolTimeout:10000});
  } else {
    const {chromium}=require(path.join(repo,'.deps/bot-diagnostics/node_modules/playwright'));
    browser=await chromium.connectOverCDP(version.webSocketDebuggerUrl,{timeout:10000});
  }
  trace.cdp_version=version.Browser||null;
}
async function newContext() {
  if(driver==='puppeteer') { activeContext=await browser.createBrowserContext(); const page=await activeContext.newPage(); return page; }
  activeContext=await browser.newContext({serviceWorkers:'block'}); return await activeContext.newPage();
}
async function disposeContext() { try {await activeContext?.close();}finally{activeContext=null;} }
// Install a single policy for a test case and save every callback, decision, and
// callback/receipt timestamp. Puppeteer uses its supported interception API;
// Playwright mode uses CDP Fetch directly and records every request-stage event.
async function installPolicy(page, name, decide, {gateUrl=null,gateMs=0}={}) {
  const pending=[];
  const handle=async req=>{
    const u=driver==='puppeteer'?req.url():req.request.url;
    const method=driver==='puppeteer'?req.method():req.request.method;
    const rawResource=driver==='puppeteer'?req.resourceType():req.resourceType;
    const resource=String(rawResource||'other').toLowerCase();
    const row=event('request_callback',{case:name,url:u,method,resource_type:resource,driver});
    try {
      let action=await decide({url:u,method,resource,row});
      if(gateUrl===u) { await new Promise(r=>setTimeout(r,gateMs)); event('decision_gate_released',{case:name,url:u,delay_ms:gateMs}); }
      if(!action) action={allow:false,reason:'policy_default_deny'};
      row.decision=action.allow?'continue':'abort'; row.reason=action.reason||null;
      if(driver==='puppeteer') { if(action.allow) await req.continue(); else await req.abort('blockedbyclient'); }
      else { const cdp=page.__smokeCdp; if(action.allow) await cdp.send('Fetch.continueRequest',{requestId:req.requestId}); else await cdp.send('Fetch.failRequest',{requestId:req.requestId,errorReason:'BlockedByClient'}); }
    } catch(error) { row.callback_error=String(error?.stack||error); trace.errors.push({case:name,where:'callback',error:row.callback_error}); try { if(driver==='puppeteer') await req.abort('failed'); else await page.__smokeCdp.send('Fetch.failRequest',{requestId:req.requestId,errorReason:'Failed'}); } catch{} }
  };
  if(driver==='puppeteer') { await page.setRequestInterception(true); page.on('request',req=>{const p=handle(req);pending.push(p);}); }
  else { const cdp=await activeContext.newCDPSession(page); page.__smokeCdp=cdp; cdp.on('Fetch.requestPaused',ev=>{const p=handle(ev);pending.push(p);}); await cdp.send('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]}); }
  return async()=>{await Promise.allSettled(pending);};
}
const settle = ms => new Promise(r=>setTimeout(r,ms));
try {
  await setupServers(); await launch();
  trace.fixture={primary_origin:primaryBase,secondary_origin:foreignBase,servers:['127.0.0.1 only'],max_browser_connections:1,workers:1};
  // 1. A held, denied main-frame navigation must never reach the fixture server.
  {
    activeCase='blocked-navigation';
    const page=await newContext(); const finish=await installPolicy(page,'blocked-navigation',()=>({allow:false,reason:'all_blocked'}),{gateUrl:`${primaryBase}/blocked`,gateMs:150});
    try {await page.goto(`${primaryBase}/blocked`,{waitUntil:'domcontentloaded',timeout:4000});}catch(error){event('navigation_rejected',{case:'blocked-navigation',error:String(error)});}
    await finish(); await settle(100);
    check('blocked navigation callback is recorded',()=>assert(trace.events.some(e=>e.type==='request_callback'&&e.case==='blocked-navigation'&&e.url===`${primaryBase}/blocked`)));
    check('blocked primary navigation has zero server receipts',()=>assert.equal(hit('primary','GET','/blocked'),undefined));
    check('held navigation callback precedes any receipt',()=>{const cb=trace.events.find(e=>e.type==='request_callback'&&e.case==='blocked-navigation'&&e.url===`${primaryBase}/blocked`);const receipt=hit('primary','GET','/blocked');assert(cb&&(!receipt||cb.at_ms<receipt.at_ms));});
    await disposeContext();
  }
  // 2. Redirect chain callbacks and server receipts must have identical order.
  {
    activeCase='redirect-chain';
    const page=await newContext(); const finish=await installPolicy(page,'redirect-chain',({url,method})=>method==='GET'&&url.startsWith(primaryBase+'/')?{allow:true,reason:'fixture_origin_get'}:{allow:false,reason:'deny_non_get_or_foreign'});
    await page.goto(`${primaryBase}/redirect`,{waitUntil:'domcontentloaded',timeout:5000}); await finish();
    const expected=['/redirect','/hop','/page'];
    check('redirect chain server hit order',()=>assert.deepEqual(allHits('primary').filter(h=>expected.includes(new URL(h.url,primaryBase).pathname)).map(h=>new URL(h.url,primaryBase).pathname),expected));
    check('redirect chain callbacks match server hit order',()=>{const urls=trace.events.filter(e=>e.type==='request_callback'&&e.case==='redirect-chain'&&e.method==='GET').map(e=>new URL(e.url).pathname).filter(p=>expected.includes(p));assert.deepEqual(urls,expected);});
    for(const p of expected) check(`redirect callback before ${p} receipt`,()=>{const cb=trace.events.find(e=>e.type==='request_callback'&&e.case==='redirect-chain'&&new URL(e.url).pathname===p);const h=hit('primary','GET',p);assert(cb&&h&&cb.at_ms<h.at_ms);});
    await disposeContext();
  }
  // A same-origin redirect to a second local origin must be stopped before its receipt.
  {
    activeCase='cross-origin-redirect';
    const page=await newContext(); const finish=await installPolicy(page,'cross-origin-redirect',({url,method})=>method==='GET'&&url.startsWith(primaryBase+'/')?{allow:true,reason:'primary_only'}:{allow:false,reason:'foreign_redirect_denied'});
    await page.goto(`${primaryBase}/foreign-redirect`,{waitUntil:'domcontentloaded',timeout:4000}).catch(e=>event('case_navigation_error',{case:'cross-origin-redirect',error:String(e)}));
    await finish(); await settle(50);
    check('cross-origin redirect landing callback is denied',()=>assert(trace.events.some(e=>e.type==='request_callback'&&e.case==='cross-origin-redirect'&&e.url===`${foreignBase}/redirected`&&e.decision==='abort')));
    check('cross-origin redirect has no secondary receipt',()=>assert.equal(allHits('secondary').length,0));
    await disposeContext();
  }
  // 3. Exercise static resources, late fetch, POST, image and cross-origin GET.
  {
    activeCase='resource-policy';
    const page=await newContext(); const allowed=new Set([`${primaryBase}/page`,`${primaryBase}/asset.js`,`${primaryBase}/module-entry.js`,`${primaryBase}/module-dep.js`,`${primaryBase}/style.css`,`${primaryBase}/late`]);
    const finish=await installPolicy(page,'resource-policy',({url,method,resource})=>{
      if(method!=='GET') return {allow:false,reason:'get_only'};
      if(resource==='image') return {allow:false,reason:'deny_image'};
      if(url.startsWith(foreignBase)) return {allow:false,reason:'deny_foreign_origin'};
      return allowed.has(url)?{allow:true,reason:'fixture_allowlist'}:{allow:false,reason:'not_allowlisted'};
    });
    await page.goto(`${primaryBase}/page`,{waitUntil:'load',timeout:6000}).catch(e=>event('case_navigation_error',{case:'resource-policy',error:String(e)}));
    if(driver==='playwright') await page.waitForFunction('document.body?.dataset?.late === "late-ok"',undefined,{timeout:3000}).catch(()=>{});
    else await page.waitForFunction(()=>document.body?.dataset?.late==='late-ok',{timeout:3000}).catch(()=>{});
    await finish(); await settle(100);
    const caseEvents=trace.events.filter(e=>e.type==='request_callback'&&e.case==='resource-policy');
    for(const p of ['/page','/asset.js','/module-entry.js','/module-dep.js','/style.css','/late']) {
      check(`allowed resource callback+receipt ${p}`,()=>{const cb=caseEvents.find(e=>new URL(e.url).pathname===p&&e.method==='GET');const h=hit('primary','GET',p);assert(cb&&h&&cb.at_ms<h.at_ms);});
    }
    const scriptValue=await page.evaluate(()=>document.documentElement.dataset.script||null).catch(()=>null);
    check('external classic script applied DOM marker',()=>assert.equal(scriptValue,'yes'));
    const moduleValue=await page.evaluate(()=>document.documentElement.dataset.module||null).catch(()=>null);
    check('module dependency loaded and applied DOM marker',()=>assert.equal(moduleValue,'module-ok'));
    check('primary HTML body matches controlled source exactly',()=>assert.equal(hit('primary','GET','/page').response_body,pageHTML(foreignBase)));
    check('classic script response body matches expected source exactly',()=>assert.equal(hit('primary','GET','/asset.js').response_body,"document.documentElement.dataset.script='yes';"));
    check('image never reaches secondary server; any requested image is denied',()=>{const images=caseEvents.filter(e=>String(e.resource_type).toLowerCase()==='image');assert(images.every(c=>c.decision==='abort'));assert.equal(allHits('secondary').filter(h=>h.url==='/pixel.png').length,0);event('image_observation',{case:'resource-policy',status:images.length?'intercepted_and_denied':'not_requested_by_engine',callback_count:images.length});});
    check('foreign fetch callback is denied and secondary origin gets no requests',()=>{assert(caseEvents.some(e=>e.url===`${foreignBase}/forbidden-fetch`&&e.decision==='abort'));assert(caseEvents.some(e=>e.url===`${foreignBase}/cross-get`&&e.decision==='abort'));assert.equal(allHits('secondary').length,0);});
    check('POST callback is denied and server receives nothing',()=>{assert(caseEvents.some(e=>e.url===`${primaryBase}/post`&&e.method==='POST'&&e.decision==='abort'));assert.equal(hit('primary','POST','/post'),undefined);});
    await disposeContext();
  }
  // 4. Request budget counts actual allowed server requests, including redirects.
  {
    activeCase='two-request-budget';
    const page=await newContext(); let remaining=2;
    const finish=await installPolicy(page,'two-request-budget',({url})=>{
      if(url.startsWith(primaryBase+'/')&&remaining>0){remaining--;return {allow:true,reason:'budget_remaining'};}
      return {allow:false,reason:'budget_exhausted_or_foreign'};
    });
    await page.goto(`${primaryBase}/redirect`,{waitUntil:'domcontentloaded',timeout:5000}).catch(e=>event('budget_navigation_error',{error:String(e)}));
    await finish(); await settle(100);
    check('budget spends exactly two receipts on redirect and hop',()=>assert.deepEqual(allHits('primary').map(h=>new URL(h.url,primaryBase).pathname),['/redirect','/hop']));
    check('budget denies page after two allowed receipts',()=>assert(trace.events.some(e=>e.type==='request_callback'&&e.case==='two-request-budget'&&new URL(e.url).pathname==='/page'&&e.decision==='abort')));
    await disposeContext();
  }
  // Redirected fetch consumes the remaining budget; its landing stays blocked.
  {
    activeCase='fetch-redirect-budget'; let remaining=2;
    const page=await newContext(); const finish=await installPolicy(page,'fetch-redirect-budget',({url,method})=>{
      if(method==='GET'&&url.startsWith(primaryBase+'/')&&remaining>0){remaining--;return {allow:true,reason:'two_request_fetch_budget'};}
      return {allow:false,reason:'fetch_budget_exhausted'};
    });
    await page.goto(`${primaryBase}/fetch-case`,{waitUntil:'load',timeout:4000});
    for(let i=0;i<60&&!trace.events.some(e=>e.type==='request_callback'&&e.case==='fetch-redirect-budget'&&new URL(e.url).pathname==='/fetch-final');i++) await settle(25);
    await finish();
    check('fetch redirect budget spends exactly two receipts',()=>assert.deepEqual(allHits('primary').map(h=>new URL(h.url,primaryBase).pathname),['/fetch-case','/fetch-redirect']));
    check('fetch redirect landing callback is denied',()=>assert(trace.events.some(e=>e.type==='request_callback'&&e.case==='fetch-redirect-budget'&&new URL(e.url).pathname==='/fetch-final'&&e.decision==='abort')));
    await disposeContext();
  }
  // file:// must be rejected while interception is active, before canary bytes reach the page.
  {
    activeCase='file-uri-deny';
    const canaryPath=path.join(temp,'file-uri-canary.html');
    const canary='OBSCURA_FILE_URI_CANARY_7d9b';
    fs.writeFileSync(canaryPath,`<!doctype html><title>local canary</title><body>${canary}</body>`);
    const fileUrl=pathToFileURL(canaryPath).href;
    trace.fixture.file_uri_canary={sha256:createHash('sha256').update(canary).digest('hex'),url:fileUrl};
    const page=await newContext(); const finish=await installPolicy(page,'file-uri-deny',()=>({allow:false,reason:'deny_all_file_uri'}));
    let navigationError;
    try {await page.goto(fileUrl,{waitUntil:'domcontentloaded',timeout:3000});}
    catch(error){navigationError=error;event('file_navigation_rejected',{case:'file-uri-deny',error:String(error)});}
    await finish();
    const content=await page.content().catch(()=>'' );
    check('file URI navigation is rejected',()=>assert(navigationError));
    check('local canary content never reaches the page DOM',()=>assert(!content.includes(canary)));
    await disposeContext();
  }
  // Two pages in one context exercise late request routing to the owning page/session.
  {
    activeCase='two-page-delayed-routing';
    const pageA=await newContext();
    const pageB=await activeContext.newPage();
    const finishA=await installPolicy(pageA,'two-page-page-A',({url,method})=>method==='GET'&&[`${primaryBase}/page-a`,`${primaryBase}/delayed-result`].includes(url)?{allow:true,reason:'page_A_policy'}:{allow:false,reason:'page_A_deny'});
    const finishB=await installPolicy(pageB,'two-page-page-B',({url,method})=>method==='GET'&&url===`${primaryBase}/page-b`?{allow:true,reason:'page_B_policy'}:{allow:false,reason:'page_B_deny'});
    await Promise.all([
      pageA.goto(`${primaryBase}/page-a`,{waitUntil:'domcontentloaded',timeout:4000}),
      pageB.goto(`${primaryBase}/page-b`,{waitUntil:'domcontentloaded',timeout:4000}),
    ]);
    // Start delayed fetches only after both page navigations have completed.
    await Promise.all([
      pageA.evaluate(()=>setTimeout(()=>fetch('/delayed-result').then(r=>r.text()).then(t=>document.body.dataset.delayed=t).catch(()=>document.body.dataset.delayed='failed'),400)),
      pageB.evaluate(()=>setTimeout(()=>fetch('/blocked-result').then(r=>r.text()).then(t=>document.body.dataset.blocked=t).catch(()=>document.body.dataset.blocked='denied'),400)),
    ]);
    // Use Node-side polling with synchronous DOM reads. Awaiting a page-side
    // waitForFunction can monopolize Runtime.awaitPromise while this engine
    // needs to dispatch the Fetch pause for the background requests.
    let markers={a:null,b:null}; const pollDeadline=Date.now()+3000;
    while(Date.now()<pollDeadline && (markers.a!=='delayed-A-ok'||markers.b!=='denied')) {
      [markers.a,markers.b]=await Promise.all([
        pageA.evaluate(()=>document.body?.dataset?.delayed||null),
        pageB.evaluate(()=>document.body?.dataset?.blocked||null),
      ]);
      if(markers.a!=='delayed-A-ok'||markers.b!=='denied') await settle(50);
    }
    event('two_page_markers',{case:'two-page-delayed-routing',markers});
    await Promise.all([finishA(),finishB()]); await settle(50);
    const aCallbacks=trace.events.filter(e=>e.type==='request_callback'&&e.case==='two-page-page-A');
    const bCallbacks=trace.events.filter(e=>e.type==='request_callback'&&e.case==='two-page-page-B');
    check('page A owns its delayed fetch callback and allows it',()=>assert(aCallbacks.some(e=>e.url===`${primaryBase}/delayed-result`&&e.decision==='continue')));
    check('page B owns its delayed fetch callback and blocks it',()=>assert(bCallbacks.some(e=>e.url===`${primaryBase}/blocked-result`&&e.decision==='abort')));
    check('delayed fetch receipts match page-specific policies',()=>{assert.equal(hit('primary','GET','/delayed-result','two-page-delayed-routing')?.response_body,'delayed-A-ok');assert.equal(hit('primary','GET','/blocked-result','two-page-delayed-routing'),undefined);});
    check('page A callback stream contains no page B URL',()=>assert(!aCallbacks.some(e=>e.url===`${primaryBase}/blocked-result`)));
    check('page B callback stream contains no page A URL',()=>assert(!bCallbacks.some(e=>e.url===`${primaryBase}/delayed-result`)));
    await disposeContext();
  }
  // 5. Context replacement discards cookies and starts with fresh state.
  {
    activeCase='cookie-create';
    let page=await newContext(); const finish1=await installPolicy(page,'cookie-create',({url})=>url===`${primaryBase}/cookie`?{allow:true}:{allow:false});
    await page.goto(`${primaryBase}/cookie`,{waitUntil:'load',timeout:4000}); await finish1();
    let cookie=''; try{cookie=await page.evaluate(()=>document.cookie);}catch{}
    check('cookie was created in first context',()=>assert.match(cookie,/smoke=present/));
    await disposeContext(); activeCase='cookie-replacement'; page=await newContext(); const finish2=await installPolicy(page,'cookie-replacement',({url})=>url===`${primaryBase}/cookie-check`?{allow:true,reason:'same_origin_cookie_probe'}:{allow:false});
    await page.goto(`${primaryBase}/cookie-check`,{waitUntil:'load',timeout:4000});
    const fresh=await page.evaluate(()=>document.cookie); await finish2();
    const probeHit=hit('primary','GET','/cookie-check','cookie-replacement');
    check('replacement context has no prior cookie',()=>assert.doesNotMatch(fresh,/smoke=present/));
    check('replacement context sends no cookie header to same-origin probe',()=>assert(probeHit&&(!probeHit.headers.cookie||!probeHit.headers.cookie.includes('smoke=present'))));
    await disposeContext();
  }
  trace.outcome='passed';
} catch(error) {
  trace.outcome='failed'; trace.errors.push({where:'harness',error:String(error?.stack||error)});
} finally {
  try { await disposeContext(); } catch(error){trace.errors.push({where:'context_cleanup',error:String(error)});}
  try { await browser?.close(); } catch(error){trace.errors.push({where:'browser_cleanup',error:String(error)});}
  if(child && child.exitCode===null && child.signalCode===null){const exited=once(child,'exit');child.kill('SIGTERM');const timer=setTimeout(()=>child.kill('SIGKILL'),2000);try{await exited;}catch{}finally{clearTimeout(timer);}}
  await closeServer(serverA); await closeServer(serverB);
  fs.rmSync(temp,{recursive:true,force:true});
  trace.binary_stderr=binaryStderr;
  trace.finished_at=new Date().toISOString();
  fs.writeFileSync(path.join(output,'evidence.json'),JSON.stringify(trace,null,2)+'\n');
}
const failed=trace.outcome!=='passed'||trace.assertions.some(a=>!a.ok);
console.log(JSON.stringify({directory:output,outcome:trace.outcome,assertions:trace.assertions.length,failed:trace.assertions.filter(a=>!a.ok).map(a=>a.name),errors:trace.errors},null,2));
if(failed) process.exitCode=1;
