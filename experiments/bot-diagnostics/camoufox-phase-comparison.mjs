// Phase attribution, not an external-site benchmark. Run inside --network none.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {execFileSync,spawn} from 'node:child_process';
import {Evidence,verify,safeError} from './evidence.mjs';
import {snapshotSources,repo,sleep} from './runtime.mjs';
import {prepare} from './manifest.mjs';
import {ToolAdapter} from './adapters.mjs';
import {openCamoufox} from './camoufox-runtime.mjs';
import {buildCamoufoxLaunchOptions,openCamoufoxFourplay,createCamoufoxLaunchHook} from './camoufox-fourplay-runtime.mjs';
import {prepareExtension} from './fourplay-native-runtime.mjs';
import {openFourplay} from './fourplay-runtime.mjs';

const output=path.resolve(process.argv[2]||'');
if(!process.argv[2]||fs.existsSync(output)) throw new Error('Supply a new output directory');
fs.mkdirSync(output,{recursive:true});
const write=(name,value)=>fs.writeFileSync(path.join(output,name),JSON.stringify(value,null,2)+'\n');
const requests=[];
const server=http.createServer((req,res)=>{
  requests.push({url:req.url,at:new Date().toISOString()});
  res.setHeader('cache-control','no-store');
  if(req.url==='/slow.svg') {
    setTimeout(()=>{res.setHeader('content-type','image/svg+xml');res.end('<svg xmlns="http://www.w3.org/2000/svg"/>');},500);return;
  }
  res.writeHead(req.url.startsWith('/error')?503:200,{'content-type':'text/html'});
  res.end('<!doctype html><title>Phase fixture</title><p id="result">fixture</p><p id="ready">pending</p><img src="/slow.svg" onload="document.querySelector(\'#ready\').textContent=\'loaded\'">');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
const rows=[];
const orders=[['hybrid','playwright-reuse','playwright-fresh'],['playwright-fresh','playwright-reuse','hybrid'],
  ['playwright-reuse','hybrid','playwright-fresh'],['playwright-fresh','hybrid','playwright-reuse']];
const require=createRequire(path.join(repo,'.deps/camoufox/package.json'));
const {firefox}=require('playwright-core');
let activePhases=[];
function instrument(page) {
  for(const method of ['goto','content','evaluate']) {
    const original=page[method].bind(page);
    page[method]=async(...args)=>{
      const label=method==='evaluate'&&args[0].toString().includes('window.stop')?'stop':method;
      const start=performance.now();
      try {return await original(...(method==='goto'?[args[0],{...args[1],waitUntil:'load'}]:args));}
      finally {activePhases.push({phase:label,ms:performance.now()-start});}
    };
  }
}
async function extract(page,mode) {
  const started=performance.now();
  const value=mode==='batch'
    ?await page.evaluate(()=>[document.title,document.querySelector('#result').textContent,document.querySelector('#ready').textContent])
    :[await page.evaluate(()=>document.title),await page.evaluate(()=>document.querySelector('#result').textContent),await page.evaluate(()=>document.querySelector('#ready').textContent)];
  if(JSON.stringify(value)!==JSON.stringify(['Phase fixture','fixture','loaded'])) throw new Error('extraction mismatch');
  return {mode,ms:performance.now()-started};
}
function evidenceFor(name,tool,url=origin+'/probe?q=fixture') {
  const evidence=new Evidence(path.join(output,name),false,{policy:'browser_observation',authorization:'User-authorized isolated local fixture phase attribution'});
  const manifest=prepare({schema:1,tools:[tool],sites:[{id:'fixture',origins:[origin],links:{home:url,targets:[url]}}]},{fixture:true});
  evidence.save('scenario.json',manifest);evidence.save('sources.json',snapshotSources(evidence));
  return {evidence,manifest};
}
try {
  const config=await buildCamoufoxLaunchOptions();
  write('conditions.json',{commit:execFileSync('git',['rev-parse','HEAD'],{cwd:repo,encoding:'utf8'}).trim(),
    tracked_diff:execFileSync('git',['diff','HEAD','--stat'],{cwd:repo,encoding:'utf8'}).trim(),
    environment:'same Cloud Docker image; --network none; DISPLAY :99; 1280x720x24',node:process.version,
    origin,orders,pages_per_session:3,cold:'fresh profile, first navigation; NOT cold OS disk/page cache',warm:'subsequent navigation in same profile; cache-control no-store',
    camoufox:config.metadata,generated_args:config.generated.args,firefox_user_prefs:config.generated.firefoxUserPrefs,
    observe_ms:100,navigation_timeout_ms:25000,fixture_resource_delay_ms:500,
    extraction_order:'separate,batch,batch,separate on warm #1; reversed on warm #2; same DOM and 3 values',
    limitations:['same parent Node process; dependencies preloaded','hybrid proxy relay vs Playwright direct loopback','Playwright fresh tab changes creation/close policy only within Playwright','startup markers differ by protocol; not pure process spawn time','nested evaluate timing inside native content must not be added twice']});
  write('fingerprint.json',Object.fromEntries(Object.entries(config.generated.env).filter(([k])=>/^CAMOU_CONFIG_\d+$/.test(k))));
  for(let block=0;block<orders.length;block++) for(const arm of orders[block]) {
    const tool=arm==='hybrid'?'camoufox-fourplay':'camoufox';
    const {evidence,manifest}=evidenceFor(`${block+1}-${arm}`,tool);
    const row={block:block+1,arm,started_at:new Date().toISOString(),pages:[]};
    const adapter=new ToolAdapter({tool,site:manifest.sites[0],evidence,fixture:true,profile:manifest.profiles[0],options:{observeMs:100,captureScreenshots:false},clientOpener:async()=>({close:async()=>{}}),
      browserOpener:async()=>arm==='hybrid'
        ?openCamoufoxFourplay({fixture:true,loadLaunchOptions:async()=>async()=>config.generated})
        :openCamoufox({fixture:true,headful:true,loadDependencies:async()=>({firefox,launchOptions:async()=>config.generated})})});
    try {
      const start=performance.now();await adapter.open();row.open_ms=performance.now()-start;
      row.startup=adapter.browser.runtime.startup_timing_ms;
      instrument(adapter.browser.page);
      for(let pageIndex=0;pageIndex<3;pageIndex++) {
        activePhases=[];
        const start=performance.now();let prepareTabMs=0;
        if(arm==='playwright-fresh') {
          const before=performance.now(),old=adapter.browser.page;
          adapter.browser.page=await adapter.browser.context.newPage();
          await old.close();instrument(adapter.browser.page);prepareTabMs=performance.now()-before;
        }
        const result=await adapter.homepage(manifest.sites[0].links.home);
        const observationMs=performance.now()-start;
        const phases=[...activePhases];
        const markers=await adapter.browser.page.evaluate(()=>({ready:document.querySelector('#ready')?.textContent,
          navigation:performance.getEntriesByType('navigation')[0]?.toJSON(),viewport:{width:innerWidth,height:innerHeight},ua:navigator.userAgent}));
        if(result.http_status!==200||markers.ready!=='loaded') throw new Error('navigation or DOM readiness failed');
        const extraction=[];
        if(pageIndex>0) for(const mode of pageIndex===1?['separate','batch','batch','separate']:['batch','separate','separate','batch']) extraction.push(await extract(adapter.browser.page,mode));
        row.pages.push({phase:pageIndex===0?'cold':'warm',index:pageIndex,status:result.http_status,observation_ms:observationMs,prepare_tab_ms:prepareTabMs,
          phases,native_navigation:adapter.browser.page.lastNavigationTiming,markers,extraction});
      }
    } catch(error) {row.error=safeError(error);} finally {await adapter.close().catch(error=>{row.close_error=safeError(error);});}
    row.verification=verify(evidence.directory);evidence.save('measurement.json',row);rows.push(row);write('results.json',rows);
    console.log(JSON.stringify({block:block+1,arm,pages:row.pages.length,error:row.error}));
  }
  // Existing HTTP bridge: observe as a separate compatibility check, not a matched timing arm.
  const bridge={started_at:new Date().toISOString(),pages:[]};let service,child,browser;
  const profile=fs.mkdtempSync('/tmp/phase-bridge-');
  try {
    const password=crypto.randomBytes(24).toString('hex');
    fs.writeFileSync('/run/fourplay-password.txt',password,{mode:0o600});
    process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD=password;
    process.env.BOT_DIAGNOSTICS_FOURPLAY_URL='http://127.0.0.1:3000';
    const log=fs.openSync(path.join(output,'bridge-process.log'),'wx');
    service=spawn(process.execPath,[path.join(repo,'experiments/fourget-selfhost/fourplay/server.cjs')],{detached:true,stdio:['ignore',log,log],env:{...process.env,NODE_PATH:path.join(repo,'.deps/fourplay/node_modules')}});fs.closeSync(log);
    const extension=prepareExtension(process.env.BOT_DIAGNOSTICS_CAMOUFOX_FOURPLAY_EXTENSION,path.join(profile,'extension'),3030,password);
    child=createCamoufoxLaunchHook({launchOptions:config})({profile,extension:extension.path});
    const started=performance.now();let healthy=false;
    for(let i=0;i<100;i++) {
      try {healthy=(await(await fetch('http://127.0.0.1:3000/health',{signal:AbortSignal.timeout(500)})).json()).browser_connected===true;}catch{}
      if(healthy) break;if(service.exitCode!==null) throw new Error('bridge_process_exited:'+service.exitCode);await sleep(100);
    }
    if(!healthy) throw new Error('bridge_connection_timeout_10s');
    bridge.launch_connect_and_poll_ms=performance.now()-started;
    const {evidence,manifest}=evidenceFor('bridge','4play');
    const adapter=new ToolAdapter({tool:'4play',site:manifest.sites[0],evidence,fixture:true,profile:manifest.profiles[0],options:{observeMs:100,captureScreenshots:false},browserOpener:async()=>browser=await openFourplay({fixture:true,headful:true})});
    try {
      await adapter.open();
      for(const pathname of ['/probe?q=fixture','/error?q=fixture','/probe?q=fixture','/error?q=fixture']) {
        const started=performance.now();const result=await adapter.homepage(origin+pathname);
        bridge.pages.push({url:origin+pathname,status:result.http_status,outcome:result.outcome,ms:performance.now()-started,errors:result.bridge_errors});
      }
    } finally {await adapter.close();browser=null;}
    bridge.verification=verify(evidence.directory);
  } catch(error) {bridge.error=safeError(error);}
  finally {
    await browser?.close().catch(()=>{});
    for(const proc of [child,service]) if(proc?.pid) {try{process.kill(-proc.pid,'SIGTERM');}catch{}}
    await sleep(300);
    for(const proc of [child,service]) if(proc?.pid) {try{process.kill(-proc.pid,'SIGKILL');}catch{}}
    fs.rmSync(profile,{recursive:true,force:true});fs.rmSync('/run/fourplay-password.txt',{force:true});delete process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD;
    write('bridge.json',bridge);
  }
  if(rows.some(row=>row.error||row.pages.length!==3||!row.verification.ok)) process.exitCode=1;
} catch(error) {write('setup-error.json',safeError(error));process.exitCode=1;}
finally {write('fixture-requests.json',requests);await new Promise(resolve=>server.close(resolve));}
