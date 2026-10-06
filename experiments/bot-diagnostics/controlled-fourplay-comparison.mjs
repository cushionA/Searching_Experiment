// Cloud-local fixture only. No public targets, authentication or challenge actions.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {Evidence, verify, safeError} from './evidence.mjs';
import {snapshotSources, repo} from './runtime.mjs';
import {prepare} from './manifest.mjs';
import {ToolAdapter} from './adapters.mjs';
import {openFourplayNative} from './fourplay-native-runtime.mjs';
import {openCamoufoxFourplay, buildCamoufoxLaunchOptions} from './camoufox-fourplay-runtime.mjs';
import {openCamoufox} from './camoufox-runtime.mjs';

const output = path.resolve(process.argv[2] || '');
if (!process.argv[2] || fs.existsSync(output)) throw new Error('Supply a new output directory');
fs.mkdirSync(output, {recursive:true});
const requests=[];
const server=http.createServer((req,res)=>{
  requests.push({url:req.url,at:new Date().toISOString(),cookie:req.headers.cookie||null});
  if(req.url==='/slow.svg') {
    setTimeout(()=>{res.writeHead(200,{'content-type':'image/svg+xml'});res.end('<svg xmlns="http://www.w3.org/2000/svg"/>');},500);return;
  }
  const status=req.url.startsWith('/error')?503:200;
  res.writeHead(status,{'content-type':'text/html','set-cookie':'fixture_session=present; Path=/; SameSite=Lax','cache-control':'no-store'});
  res.end('<!doctype html><title>Controlled fixture</title><p id="result">query=fixture; status='+status+'</p><p id="ready">pending</p><img src="/slow.svg" onload="document.querySelector(\'#ready\').textContent=\'loaded\'">');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
const write=(name,value)=>fs.writeFileSync(path.join(output,name),JSON.stringify(value,null,2)+'\n');
const arms=['firefox-fourplay','camoufox-fourplay','camoufox-load','camoufox-domcontentloaded'];
const rows=[];
try {
  const config=await buildCamoufoxLaunchOptions();
  const require=createRequire(path.join(repo,'.deps/camoufox/package.json'));
  const {firefox}=require('playwright-core');
  write('conditions.json',{commit:execFileSync('git',['rev-parse','HEAD'],{cwd:repo,encoding:'utf8'}).trim(),
    dirty:execFileSync('git',['status','--porcelain'],{cwd:repo,encoding:'utf8'}).trim(),
    environment:'Cloud Docker; --network none; loopback fixture only',node:process.version,display:process.env.DISPLAY,
    camoufox:config.metadata,generated_args:config.generated.args,firefox_user_prefs:config.generated.firefoxUserPrefs,
    fixed_fingerprint:true,arms,order:[arms,[...arms].reverse()],navigation_timeout_ms:25000,observe_ms:100,
    query:'fixture',catalog:['/ok?q=fixture','/error?q=fixture'],origin,
    arm_semantics:'firefox-fourplay uses native runtime, NOT existing 4play HTTP bridge; Camoufox load vs domcontentloaded changes only wait gate'});
  // Preserve the exact generated non-secret fingerprint for replay, not environment variables.
  write('fingerprint.json',Object.fromEntries(Object.entries(config.generated.env).filter(([k])=>/^CAMOU_CONFIG_\d+$/.test(k))));
  for(let repeat=0;repeat<2;repeat++) for(const arm of repeat?[...arms].reverse():arms) {
    const directory=path.join(output,`${repeat+1}-${arm}`);
    const evidence=new Evidence(directory,false,{policy:'browser_observation',authorization:'User-authorized controlled local fixture comparison'});
    const tool=arm==='firefox-fourplay'?'4play':arm==='camoufox-fourplay'?arm:'camoufox';
    const manifest=prepare({schema:1,tools:[tool],sites:[{id:'fixture',origins:[origin],links:{home:origin+'/ok?q=fixture',targets:[origin+'/error?q=fixture']}}]},{fixture:true});
    evidence.save('scenario.json',manifest); evidence.save('sources.json',snapshotSources(evidence));
    const started=performance.now();
    const row={repeat:repeat+1,arm,started_at:new Date().toISOString(),pages:[]};
    const adapter=new ToolAdapter({tool,site:manifest.sites[0],evidence,fixture:true,profile:manifest.profiles[0],options:{observeMs:100,captureScreenshots:false},
      clientOpener:async()=>({close:async()=>{}}),
      browserOpener:async()=>{
        if(arm==='firefox-fourplay') return openFourplayNative({fixture:true,executable:'/usr/bin/firefox-esr',extension:process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION});
        if(arm==='camoufox-fourplay') return openCamoufoxFourplay({fixture:true,loadLaunchOptions:async()=>async()=>config.generated});
        const browser=await openCamoufox({fixture:true,headful:true,loadDependencies:async()=>({firefox,launchOptions:async()=>config.generated})});
        const goto=browser.page.goto.bind(browser.page);
        browser.page.goto=(url,options)=>goto(url,{...options,waitUntil:arm==='camoufox-load'?'load':'domcontentloaded'});
        return browser;
      }});
    try {
      await adapter.open(); row.launch_ms=performance.now()-started;
      for(const url of [manifest.sites[0].links.home,...manifest.sites[0].links.targets]) {
        const start=performance.now();
        const result=await adapter.homepage(url);
        const markers=await adapter.browser.page.evaluate(()=>({ready:document.querySelector('#ready')?.textContent,result:document.querySelector('#result')?.textContent,webdriver:navigator.webdriver,ua:navigator.userAgent}));
        row.pages.push({url,status:result.http_status,outcome:result.outcome,elapsed_ms:performance.now()-start,markers});
      }
    } catch(error) {row.error=safeError(error);} finally {await adapter.close().catch(error=>{row.close_error=safeError(error);});}
    row.total_ms=performance.now()-started;row.verification=verify(directory);
    evidence.save('measurement.json',row); rows.push(row);write('results.json',rows);
    console.log(JSON.stringify({arm,repeat:repeat+1,error:row.error,pages:row.pages.length}));
  }
} catch(error) {write('setup-error.json',safeError(error));process.exitCode=1;}
finally {write('fixture-requests.json',requests);await new Promise(resolve=>server.close(resolve));}
