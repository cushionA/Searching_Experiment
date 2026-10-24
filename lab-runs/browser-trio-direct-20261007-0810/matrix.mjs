import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {pathToFileURL,fileURLToPath} from 'node:url';

const file=fileURLToPath(import.meta.url),root=path.dirname(file);
const source='/mnt/c/Users/tatuk/.codex/worktrees/camoufox-fourplay/SearchEngine';
const api=name=>import(pathToFileURL(path.join(source,'experiments/bot-diagnostics',name)).href);
const mode=process.argv[2]||'prepare',tool=process.argv[3];
const tools=['camoufox','camoufox-fourplay','4play'];
const [{Evidence,verify,safeError},{prepare},{ToolAdapter},{runScenario},{browserSite},
  {openFourplayNative},{openCamoufoxFourplay,buildCamoufoxLaunchOptions},{openCamoufox},{snapshotSources,proxy}]=await Promise.all([
  api('evidence.mjs'),api('manifest.mjs'),api('adapters.mjs'),api('scenario.mjs'),api('runner.mjs'),
  api('fourplay-native-runtime.mjs'),api('camoufox-fourplay-runtime.mjs'),api('camoufox-runtime.mjs'),api('runtime.mjs')
]);
assert.equal(proxy,undefined);
for(const name of ['HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy','ALL_PROXY','all_proxy']) assert.ok(!process.env[name]);
const sdkRoot=process.env.BOT_DIAGNOSTICS_CAMOUFOX_DEPS;
const templatePath=path.join(root,'camoufox-launch-template.json');
const sensor='https://joshinweb.jp/CIbl-mRCKq8eHv1cyg/QEcESVLcm2pGLriaub/Lw9CD1Y_SwE/SR/IpPwtCYwc';
const original=JSON.parse(fs.readFileSync('/mnt/c/Users/tatuk/Desktop/SearchEngine/lab-runs/4play-camoufox-20261007/hybrid/blocking-manifest.json'));
const site={...original.sites.find(site=>site.id==='joshin'),session_initialization:{kind:'sensor_revisit',sensor_url:sensor}};
const config={schema:1,tools,sites:[site],profiles:[{id:'baseline',humanlike:false,extensions:[]}],
  execution_policy:'browser_observation',recovery_plan:{max_attempts:0,return_home_after_success:false,wait_seconds:0,retry_original_target:false}};
const write=(name,value)=>fs.writeFileSync(path.join(root,name),JSON.stringify(value,null,2)+'\n');
const makeOptions=()=>({...JSON.parse(fs.readFileSync(templatePath)).generated,env:{...process.env,...JSON.parse(fs.readFileSync(templatePath)).generated.env}});
if(mode==='prepare') {
  const built=await buildCamoufoxLaunchOptions();
  const environment=Object.fromEntries(Object.entries(built.generated.env||{}).filter(([key])=>/^CAMOU_CONFIG_\d+$|^FONTCONFIG_(?:PATH|FILE)$/.test(key)));
  write('camoufox-launch-template.json',{generated:{...built.generated,env:environment},metadata:built.metadata});
  const manifest=prepare(config,{headful:true,executionPolicy:'browser_observation'});
  manifest.options.observeMs=6000;
  write('manifest.json',manifest);
  write('conditions.json',{schema:1,tools,proxy:null,proxy_environment_cleared:true,browser_proxy_type:0,headful:true,display:process.env.DISPLAY,
    source_checkout:source,source_base_commit:'8b9ec94',source_local_scenario_changes:true,
    residential_isp_verified:false,actual_exit_ip_measured:false,
    sites:['joshin'],sensor_url:sensor,sensor_rule:'same observed document 403 + configured script GET 200 + same sensor POST 201; one same-session revisit of denied final URL',
    camoufox_shared_fingerprint:built.metadata,observe_ms:6000,extra_delay_ms:0,selectors:false,challenge_solving:false,
    concurrency:'all three tools run concurrently; separate processes/profiles/containers/ports',
    source_entry:'shared ToolAdapter + runScenario + browserSite; native opener injected for standalone 4play; direct observation does not invoke legacy proxy-only framework CLI',
    authorization:'User confirmed camoufox, assembled Camoufox+4play, standalone 4play; Google excluded; same-session sensor initialization in scenario',
    started_at:new Date().toISOString()});
  console.log(JSON.stringify({prepared:true,tools,sites:['joshin'],shared_camoufox_config:built.metadata.config_sha256}));
} else if(mode==='matrix-fixture'||mode==='matrix-run') {
  const childMode=mode==='matrix-fixture'?'fixture':'run';
  const progress={started_at:new Date().toISOString(),mode:childMode,concurrency:3,children:[]};
  write(mode+'.json',progress);
  const children=tools.map(engine=>new Promise(resolve=>{
    const child=spawn(process.execPath,[file,childMode,engine],{env:{...process.env,BOT_DIAGNOSTICS_STATE:'/tmp/searchengine-trio-direct-20261007-0810/'+engine},
      stdio:['ignore','pipe','pipe']});
    const item={tool:engine,pid:child.pid,started_at:new Date().toISOString(),exit_code:null};
    progress.children.push(item);write(mode+'.json',progress);
    child.stdout.on('data',chunk=>process.stdout.write(chunk));
    let error='';
    child.stderr.on('data',chunk=>{error=(error+chunk.toString()).slice(-6000);});
    child.on('error',failure=>{item.error=safeError(failure);});
    child.on('close',code=>{
      item.exit_code=code;item.finished_at=new Date().toISOString();
      if(error) item.error=safeError(new Error(error));
      write(mode+'.json',progress);resolve(item);
    });
  }));
  const completed=await Promise.all(children);
  progress.finished_at=new Date().toISOString();write(mode+'.json',progress);
  if(completed.some(item=>item.exit_code!==0)) process.exitCode=1;
} else {
  assert.ok(['fixture','run'].includes(mode));assert.ok(tools.includes(tool));
  let server,adapter;
  let activeSite=site;
  if(mode==='fixture') {
    server=http.createServer((request,response)=>{
      if(request.url==='/sensor'&&request.method==='POST') {response.writeHead(201,{'Set-Cookie':'scenario_initialized=1; Path=/; SameSite=Lax'});response.end('initialized');return;}
      if(request.url==='/sensor') {response.setHeader('Content-Type','application/javascript');response.end('fetch("/sensor",{method:"POST",credentials:"same-origin"});');return;}
      response.setHeader('Content-Type','text/html; charset=utf-8');
      if(!String(request.headers.cookie||'').includes('scenario_initialized=1')) {
        response.writeHead(403);response.end('<!doctype html><title>Access Denied</title><p>Access Denied</p><script src="/sensor"></script>');return;
      }
      response.end('<!doctype html><title>Scenario ready</title><p id="ready">pending</p><script>document.querySelector("#ready").textContent="JavaScript ready"</script>');
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const origin='http://127.0.0.1:'+server.address().port;
    activeSite={id:'fixture',origins:[origin],links:{home:origin+'/',targets:[origin+'/inside']},selectors:{},params:{},
      session_initialization:{kind:'sensor_revisit',sensor_url:origin+'/sensor'}};
  }
  class DirectEvidence extends Evidence {
    reserve(...args) {
      const record=super.reserve(...args);
      if(!['127.0.0.1','localhost'].includes(new URL(record.url).hostname)) record.route='workstation_direct';
      this.flush();return record;
    }
  }
  const directory=path.join(process.env.BOT_DIAGNOSTICS_EVIDENCE_ROOT||root,mode,tool);
  const evidence=new DirectEvidence(directory,false,{policy:'browser_observation',authorization:'User authorized three-tool direct Joshin comparison; deterministic loopback initialization fixture'});
  const manifest=prepare({...config,tools:[tool],sites:[activeSite]},{fixture:mode==='fixture',headful:true,executionPolicy:'browser_observation'});
  manifest.options.observeMs=6000;
  manifest.options.captureScreenshots=tool==='camoufox';
  evidence.save('scenario.json',manifest);
  evidence.save('invocation.json',{tool,mode,started_at:new Date().toISOString(),proxy:null,
    source_hashes:snapshotSources(evidence),driver_sha256:evidence.blob(fs.readFileSync(file)),
    launch_template_sha256:evidence.blob(fs.readFileSync(templatePath))});
  const noProxy='user_pref("network.proxy.type", 0);\nuser_pref("network.proxy.autoconfig_url", "");\n';
  const browserOpener=async(name,options)=>{
    const fixture=mode==='fixture';
    if(name==='4play') return openFourplayNative({...options,port:3035,
      launch:({profile,executable,extension})=>{
        if(!fixture) fs.appendFileSync(path.join(profile,'user.js'),noProxy);
        return spawn(path.join(process.env.BOT_DIAGNOSTICS_FOURPLAY_DEPS,'node_modules','.bin','web-ext'),
          ['run','--source-dir',extension,'--firefox',executable,'--firefox-profile',profile,'--keep-profile-changes','--no-reload','--no-input'],
          {stdio:'ignore',detached:true,env:{...process.env}});
      }});
    if(name==='camoufox-fourplay') return openCamoufoxFourplay({...options,port:3036,loadLaunchOptions:async()=>async()=>makeOptions(),
      spawnProcess:(command,args,options)=>{
        if(!fixture) fs.appendFileSync(path.join(args[args.indexOf('--firefox-profile')+1],'user.js'),noProxy);
        return spawn(command,args,options);
      }});
    const localRequire=createRequire(path.join(sdkRoot,'package.json'));
    return openCamoufox({...options,loadDependencies:async()=>({
      firefox:localRequire('playwright-core').firefox,
      launchOptions:async()=>{
        const generated=makeOptions();
        if(!fixture) generated.firefoxUserPrefs={...generated.firefoxUserPrefs,'network.proxy.type':0,'network.proxy.autoconfig_url':''};
        return generated;
      }})});
  };
  const siteObserver=async(evidence,name,target,session)=>{
    await browserSite(evidence,name==='4play'?'fourplay-native':name,target,session);
    const result=evidence.results.at(-1);result.client=name;evidence.flush();
    console.log(JSON.stringify({tool:name,site:activeSite.id,role:evidence.stageContext?.role,http_status:result.http_status,outcome:result.outcome,final_url:result.final_url}));
  };
  try {
    adapter=new ToolAdapter({tool,site:manifest.sites[0],evidence,fixture:mode==='fixture',profile:manifest.profiles[0],
      options:manifest.options,browserOpener,siteObserver,clientOpener:async()=>({close:async()=>{}})});
    await adapter.open();
    const outcome=await runScenario({adapter,site:manifest.sites[0],options:manifest.options,recoveryPlan:manifest.recovery_plan,profile:manifest.profiles[0]});
    evidence.save('pipeline-results.json',[outcome]);
    if(mode==='fixture') {
      assert.equal(outcome.state,'navigation_completed');
      assert.ok(evidence.results.some(row=>row.http_status===403));
      assert.ok(evidence.results.some(row=>row.role==='session_initialization_revisit'&&row.http_status===200));
      const final=evidence.results.at(-1);
      assert.equal(final.http_status,200);
      assert.ok(fs.readFileSync(path.join(directory,'blobs',final.dom_sha256),'utf8').includes('JavaScript ready'));
    }
  } catch(error) {
    evidence.save('driver-failure.json',safeError(error));process.exitCode=1;
    console.log(JSON.stringify({tool,error:safeError(error)}));
  } finally {
    try {await adapter?.close();} finally {if(server) await new Promise(resolve=>server.close(resolve));evidence.flush(true);}
  }
  const verification=verify(directory);evidence.save('verification.json',verification);
  assert.equal(verification.ok,true,JSON.stringify(verification));
}
