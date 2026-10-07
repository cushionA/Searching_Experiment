// Fixed authorized subset, sequential navigation, no authentication/challenge actions.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {execFileSync} from 'node:child_process';
import {Evidence,verify,safeError} from './evidence.mjs';
import {snapshotSources,repo,sha,sleep} from './runtime.mjs';
import {prepare} from './manifest.mjs';
import {ToolAdapter} from './adapters.mjs';
import {buildCamoufoxLaunchOptions,openCamoufoxFourplay} from './camoufox-fourplay-runtime.mjs';
import {readDOMSnapshot,summarizeDOM} from './dom-stability.mjs';

const [directory,mode]=process.argv.slice(2),fixture=mode==='--fixture';
if(!directory||!['--fixture','--public'].includes(mode)) throw new Error('Usage: hybrid-wait-comparison.mjs NEW_DIRECTORY --fixture|--public');
const output=path.resolve(directory);if(fs.existsSync(output)) throw new Error('Output exists');fs.mkdirSync(output,{recursive:true});
const write=(name,value)=>fs.writeFileSync(path.join(output,name),JSON.stringify(value,null,2)+'\n');
const query='早稲田大学 教員',terms=['早稲田','教員'];
const policy={timeoutMs:5000,stableMs:750,pollMs:250,minTextChars:200,terms};
const catalogPath=path.join(repo,'experiments/bot-diagnostics/search-targets.json');
const catalog=JSON.parse(fs.readFileSync(catalogPath));
const requests=[];let server;
let sites=catalog.sites.filter(site=>['bing','brave'].includes(site.id));
if(fixture) {
 server=http.createServer((req,res)=>{
  requests.push({url:req.url,at:new Date().toISOString()});res.setHeader('content-type','text/html; charset=utf-8');res.setHeader('cache-control','no-store');
  res.end('<!doctype html><title>Delayed query fixture</title><p id="result">pending</p><script>setTimeout(()=>{document.querySelector("#result").textContent="早稲田大学 教員の研究内容。".repeat(30);let a=document.createElement("a");a.href="http://example.test/teacher";a.textContent="早稲田大学 教員";document.body.append(a)},900)</script>');
 });await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin=`http://127.0.0.1:${server.address().port}`;
 sites=sites.map(site=>({...site,origins:[origin],search_url_templates:[origin+'/'+site.id+'?q={query}']}));
}
const plan=[['bing','baseline'],['brave','dom-stable'],['bing','dom-stable'],['brave','baseline']];
const schedule=[...plan.map(([site,arm])=>({repeat:1,site,arm})),...[...plan].reverse().map(([site,arm])=>({repeat:2,site,arm}))];
const stopped=new Map(),lastStarted=new Map(),rows=[];
try {
 const config=await buildCamoufoxLaunchOptions();
 write('conditions.json',{commit:execFileSync('git',['rev-parse','HEAD'],{cwd:repo,encoding:'utf8'}).trim(),tracked_diff:execFileSync('git',['diff','HEAD','--stat'],{cwd:repo,encoding:'utf8'}).trim(),
  fixture,environment:'Cloud Docker headful Xvfb :99, same image/binary/host/proxy in both arms',query,terms,catalog_revision:catalog.revision,catalog_sha256:sha(fs.readFileSync(catalogPath)),
  schedule,max_top_level_navigations:8,min_start_interval_per_site_ms:fixture?0:20000,observe_ms:fixture?100:6000,navigation_timeout_ms:25000,
  baseline:'native4play load-complete gate + existing observation delay',added:'same baseline then bounded read-only meaningful DOM stability wait before evidence capture',policy,
  stop_policy:'challenge/access-denied/rate-limit: close session and skip this site for the rest of this run; no retries',
  camoufox:config.metadata,network:fixture?'Docker network none':'existing managed proxy, configured CA, normal passive browser traffic; no background-route claim',
  metrics:'DOM text/link quantities and query-token matching are proxies, not semantic accuracy or bypass success'});
 write('catalog.json',catalog);write('fingerprint.json',Object.fromEntries(Object.entries(config.generated.env).filter(([k])=>/^CAMOU_CONFIG_\d+$/.test(k))));
 for(const cell of schedule) {
  if(stopped.has(cell.site)) {rows.push({...cell,skipped:true,reason:stopped.get(cell.site)});write('results.json',rows);continue;}
  const cooldown=fixture?0:20000-(Date.now()-(lastStarted.get(cell.site)||0));if(cooldown>0) await sleep(cooldown);
  const site=sites.find(site=>site.id===cell.site),url=site.search_url_templates[0].replace('{query}',encodeURIComponent(query));
  const manifest=prepare({schema:1,tools:['camoufox-fourplay'],sites:[{id:site.id,origins:site.origins,links:{home:url,targets:[]}}]},{fixture});
  const evidence=new Evidence(path.join(output,`${cell.repeat}-${cell.site}-${cell.arm}`),false,{policy:'browser_observation',authorization:'User explicitly requested added-wait hybrid crawling on a small existing public subset; no CAPTCHA/authentication actions'});
  evidence.save('scenario.json',manifest);evidence.save('sources.json',snapshotSources(evidence));
  const row={...cell,url,started_at:new Date().toISOString()};
  const adapter=new ToolAdapter({tool:'camoufox-fourplay',site:manifest.sites[0],evidence,fixture,profile:manifest.profiles[0],
   options:{observeMs:fixture?100:6000,captureScreenshots:false,...(cell.arm==='dom-stable'?{domWait:policy}:{})},
   browserOpener:async()=>openCamoufoxFourplay({fixture,loadLaunchOptions:async()=>async()=>config.generated})});
  try {
   const openStarted=performance.now();await adapter.open();row.open_ms=performance.now()-openStarted;
   lastStarted.set(cell.site,Date.now());const start=performance.now();const result=await adapter.goto(url);row.elapsed_ms=performance.now()-start;
   row.status=result.http_status;row.outcome=result.outcome;row.dom_wait=result.dom_wait;row.navigation_error=result.navigation_error;
   row.dom_sha256=result.dom_sha256;row.metrics=summarizeDOM(await readDOMSnapshot(adapter.browser.page),terms);
   row.semantic_quality='not_graded';row.responses=evidence.records.length;row.bytes=evidence.records.reduce((n,r)=>n+(r.bytes_charged||0),0);
   if(['challenge_observed','access_denied_observed','rate_limited_observed'].includes(result.outcome)||/^stopped_/.test(result.dom_wait?.outcome||'')) stopped.set(cell.site,result.outcome||result.dom_wait.outcome);
  } catch(error) {row.error=safeError(error);stopped.set(cell.site,'execution_error; bounded run does not retry');}
  finally {await adapter.close().catch(error=>{row.close_error=safeError(error);});}
  row.verification=verify(evidence.directory);evidence.save('measurement.json',row);rows.push(row);write('results.json',rows);
  console.log(JSON.stringify({site:row.site,arm:row.arm,repeat:row.repeat,status:row.status,outcome:row.outcome,wait:row.dom_wait?.outcome,error:row.error}));
 }
} catch(error) {write('setup-error.json',safeError(error));process.exitCode=1;}
finally {if(server) await new Promise(resolve=>server.close(resolve));write('fixture-requests.json',requests);}
