// A/B validation of the blank-tab gate only. Requires Docker --network none.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import {execFileSync,spawn} from 'node:child_process';
import {Evidence,verify,safeError} from './evidence.mjs';
import {snapshotSources,repo,sleep,sha} from './runtime.mjs';
import {prepare} from './manifest.mjs';
import {ToolAdapter} from './adapters.mjs';
import {openFourplay} from './fourplay-runtime.mjs';
import {prepareExtension} from './fourplay-native-runtime.mjs';
import {buildCamoufoxLaunchOptions,createCamoufoxLaunchHook} from './camoufox-fourplay-runtime.mjs';
const output=path.resolve(process.argv[2]||'');
if(!process.argv[2]||fs.existsSync(output)) throw new Error('Supply a new output directory');
fs.mkdirSync(output,{recursive:true});
const write=(name,value)=>fs.writeFileSync(path.join(output,name),JSON.stringify(value,null,2)+'\n');
const baselineCommit='ada0ff2615a3fcba132f2fde1bbd0a912d3fe897';
const baseline=execFileSync('git',['show',baselineCommit+':experiments/fourget-selfhost/fourplay/server.cjs'],{cwd:repo});
const fixed=fs.readFileSync(path.join(repo,'experiments/fourget-selfhost/fourplay/server.cjs'));
const helper=fs.readFileSync(path.join(repo,'experiments/fourget-selfhost/fourplay/navigation-gate.cjs'));
const requests=[],rows=[];
const server=http.createServer((req,res)=>{
 requests.push({url:req.url,at:new Date().toISOString()});res.setHeader('cache-control','no-store');
 if(req.url==='/redirect') {res.writeHead(302,{location:'/slow'});res.end();return;}
 if(req.url==='/image.svg') {setTimeout(()=>{res.writeHead(200,{'content-type':'image/svg+xml'});res.end('<svg xmlns="http://www.w3.org/2000/svg"/>');},500);return;}
 if(req.url==='/never.svg') {res.writeHead(200,{'content-type':'image/svg+xml'});res.write('<svg xmlns="http://www.w3.org/2000/svg">');return;}
 res.writeHead(req.url==='/error'?503:200,{'content-type':'text/html; charset=utf-8'});
 res.end('<!doctype html><title>Bridge gate fixture</title><p id="ready">pending</p><img src="'+(req.url==='/never'?'/never.svg':'/image.svg')+'" onload="document.querySelector(\'#ready\').textContent=\'loaded\'">');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
try {
 const config=await buildCamoufoxLaunchOptions();
 const order=['legacy','fixed','fixed','legacy'];
 write('conditions.json',{commit:execFileSync('git',['rev-parse','HEAD'],{cwd:repo,encoding:'utf8'}).trim(),baselineCommit,
  tracked_diff:execFileSync('git',['diff','HEAD','--stat'],{cwd:repo,encoding:'utf8'}).trim(),
  source_sha256:{legacy:sha(baseline),fixed:sha(fixed),helper:sha(helper)},environment:'Cloud Docker --network none; identical image, Camoufox binary/config, Xvfb :99, fresh profiles',
  order,cases:['/slow','/error','/redirect','/never'],origin,observe_ms:100,gate_deadline_ms:18000,camoufox:config.metadata,
  interpretation:'Only bridge navigation gate/error scoping/metadata changes. No public sites. Compatibility/correctness probe, not a performance ranking.'});
 write('fingerprint.json',Object.fromEntries(Object.entries(config.generated.env).filter(([k])=>/^CAMOU_CONFIG_\d+$/.test(k))));
 for(let index=0;index<order.length;index++) {
  const arm=order[index],row={index:index+1,arm,started_at:new Date().toISOString(),pages:[]};
  const evidence=new Evidence(path.join(output,`${index+1}-${arm}`),false,{policy:'browser_observation',authorization:'Authorized isolated HTTP bridge initial-blank regression fixture'});
  evidence.save('sources.json',snapshotSources(evidence));
  evidence.save('bridge-sources.json',{legacy:evidence.blob(baseline),fixed:evidence.blob(fixed),helper:evidence.blob(helper)});
  const manifest=prepare({schema:1,tools:['4play'],sites:[{id:'fixture',origins:[origin],links:{home:origin+'/slow',targets:[origin+'/error',origin+'/redirect',origin+'/never']}}]},{fixture:true});evidence.save('scenario.json',manifest);
  const profile=fs.mkdtempSync('/tmp/bridge-gate-');let service,child,adapter,lastNavigation=null;
  try {
   const password=crypto.randomBytes(24).toString('hex');fs.writeFileSync('/run/fourplay-password.txt',password,{mode:0o600});
   process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD=password;process.env.BOT_DIAGNOSTICS_FOURPLAY_URL='http://127.0.0.1:3000';
   fs.writeFileSync(path.join(profile,'server.cjs'),arm==='legacy'?baseline:fixed);fs.writeFileSync(path.join(profile,'navigation-gate.cjs'),helper);
   const log=fs.openSync(path.join(output,`${index+1}-${arm}.log`),'wx');
   service=spawn(process.execPath,['--require',path.join(repo,'experiments/bot-diagnostics/fourplay-bridge-trace.cjs'),path.join(profile,'server.cjs')],
    {detached:true,stdio:['ignore',log,log],env:{...process.env,NODE_PATH:path.join(repo,'.deps/fourplay/node_modules'),PHASE_BRIDGE_TRACE:path.join(output,`${index+1}-${arm}-trace.jsonl`)}});fs.closeSync(log);
   const extension=prepareExtension(process.env.BOT_DIAGNOSTICS_CAMOUFOX_FOURPLAY_EXTENSION,path.join(profile,'extension'),3030,password);
   child=createCamoufoxLaunchHook({launchOptions:config})({profile,extension:extension.path});
   let ready=false;
   for(let attempt=0;attempt<100;attempt++) {
    try {ready=(await(await fetch('http://127.0.0.1:3000/health',{signal:AbortSignal.timeout(500)})).json()).browser_connected===true;}catch{}
    if(ready)break;if(service.exitCode!==null)throw new Error('bridge exited');await sleep(100);
   }
   if(!ready)throw new Error('bridge connection deadline');
   adapter=new ToolAdapter({tool:'4play',site:manifest.sites[0],evidence,fixture:true,profile:manifest.profiles[0],options:{observeMs:100,captureScreenshots:false},browserOpener:async()=>{
    const browser=await openFourplay({fixture:true,headful:true});const navigate=browser.navigate;
    browser.navigate=async(...args)=>{const result=await navigate(...args);lastNavigation=result.navigation||null;return result;};return browser;
   }});
   await adapter.open();
   for(const pathname of ['/slow','/error','/redirect','/never']) {
    const start=performance.now();const result=await adapter.goto(origin+pathname);
    const dom=result.dom_sha256?fs.readFileSync(path.join(evidence.directory,'blobs',result.dom_sha256),'utf8'):'';
    row.pages.push({pathname,elapsed_ms:performance.now()-start,status:result.http_status,outcome:result.outcome,
     dom_ready:dom.includes('<p id="ready">loaded</p>'),navigation:lastNavigation,errors:result.bridge_errors,dom_sha256:result.dom_sha256});
   }
  } catch(error) {row.error=safeError(error);}
  finally {
   await adapter?.close().catch(error=>{row.close_error=safeError(error);});
   for(const proc of [child,service])if(proc?.pid){try{process.kill(-proc.pid,'SIGTERM');}catch{}}
   await sleep(300);for(const proc of [child,service])if(proc?.pid){try{process.kill(-proc.pid,'SIGKILL');}catch{}}
   fs.rmSync(profile,{recursive:true,force:true});fs.rmSync('/run/fourplay-password.txt',{force:true});delete process.env.BOT_DIAGNOSTICS_FOURPLAY_PASSWORD;
  }
  row.verification=verify(evidence.directory);evidence.save('measurement.json',row);rows.push(row);write('results.json',rows);
  console.log(JSON.stringify({arm,index:index+1,error:row.error,pages:row.pages.map(p=>({path:p.pathname,ready:p.dom_ready,navigation:p.navigation?.outcome,ms:Math.round(p.elapsed_ms)}))}));
 }
 if(rows.some(row=>row.error||!row.verification.ok||row.pages.length!==4||row.arm==='fixed'&&row.pages.some(p=>p.pathname==='/never'?p.navigation?.outcome!=='timeout'||p.dom_ready||p.elapsed_ms>25000:!p.dom_ready||p.navigation?.outcome!=='complete'||p.status!==(p.pathname==='/error'?503:200))))process.exitCode=1;
} catch(error) {write('setup-error.json',safeError(error));process.exitCode=1;}
finally {write('fixture-requests.json',requests);await new Promise(resolve=>server.close(resolve));}
