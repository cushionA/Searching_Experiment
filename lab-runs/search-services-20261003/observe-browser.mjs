/** Continue requested search checks using native headful browser observation. */
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Evidence,verify,safeError} from '../../experiments/bot-diagnostics/evidence.mjs';
import {ToolAdapter} from '../../experiments/bot-diagnostics/adapters.mjs';
import {snapshotSources} from '../../experiments/bot-diagnostics/runtime.mjs';
const root=path.dirname(fileURLToPath(import.meta.url));
const group=process.argv[2];
if(!['general','meta','ai-api','niche'].includes(group)) throw new Error('Specify saved run group');
const directory=path.join(root,group);
const planName=process.argv[3]||'browser-observation-plan.json';
if(!/^[a-z0-9-]+\.json$/.test(planName))throw new Error('Invalid plan filename');
const plan=JSON.parse(fs.readFileSync(path.join(root,planName)));
const resultName=plan.result_filename||'browser-observation-results.json';
const selected=plan.sites.filter(site=>site.group===group);
const lockPath=directory+'.lock',lock=fs.openSync(lockPath,'wx');
fs.writeSync(lock,JSON.stringify({pid:process.pid,started_at:new Date().toISOString(),purpose:'browser_observation'}));
const cleanURL=url=>{
  if(!url) return url;
  try {const u=new URL(url);for(const k of [...u.searchParams.keys()]) if(/token|__cf_/i.test(k))u.searchParams.delete(k);return u.href;}
  catch{return url;}
};
let adapter;
try {
  const evidence=new Evidence(directory,true,{policy:'browser_observation',authorization:plan.authorization});
  const scenario=JSON.parse(fs.readFileSync(path.join(directory,'scenario.json')));
  const invocations=JSON.parse(fs.readFileSync(path.join(directory,'framework-invocations.json')));
  evidence.save('framework-invocations.json',[...invocations,{started_at:new Date().toISOString(),purpose:'Normal headful browser search observation',
    authorization:plan.authorization,policy:evidence.policy,headless:false,plan_sha256:evidence.blob(fs.readFileSync(path.join(root,planName))),
    source:evidence.blob(fs.readFileSync(fileURLToPath(import.meta.url))),sources:snapshotSources(evidence),
    legacy_constraints_enforced:false,query_policy:'One proposed query per unfinished service; all responses kept as observed.'}]);
  const completed=[];
  if(fs.existsSync(path.join(directory,resultName))) throw new Error('Observation batch already saved; do not repeat completed searches');
  evidence.save(resultName,completed);
  for(const cell of selected) {
    const original=scenario.sites.find(site=>site.id===cell.id);
    if(!original) throw new Error('Unknown original site');
    const site={...original,origins:[...new Set([...original.origins,...(cell.known_origins||[])])]};
    const saved={id:site.id,group,policy:evidence.policy,started_at:new Date().toISOString(),events:[],
      accounting_before:{...evidence.budgets[`patchright/${site.id}`]}};
    try {
      adapter=new ToolAdapter({tool:'patchright',site,evidence,profile:{id:'browser-observation',humanlike:false,extensions:[]},options:{headful:true}});
      await adapter.open();
      const result=await adapter.accessLink(cell.url,{role:cell.role});
      saved.events.push(result);
      console.log(JSON.stringify({id:site.id,role:cell.role,outcome:result.outcome,http_status:result.http_status,
        final_url:cleanURL(result.final_url),title:result.title,visible_text_prefix:result.visible_text_prefix?.slice(0,260)}));
    } catch(error) {saved.error=safeError(error);console.log(JSON.stringify({id:site.id,error:saved.error}));}
    finally {
      await adapter?.close();adapter=null;
      saved.finished_at=new Date().toISOString();saved.accounting_after={...evidence.budgets[`patchright/${site.id}`]};
      completed.push(saved);evidence.save(resultName,completed);
    }
  }
  const checked=verify(directory);evidence.save('verification.json',checked);
  console.log(JSON.stringify({group,verification:checked,services:completed.length}));
  if(!checked.ok)process.exitCode=1;
} finally {await adapter?.close();fs.closeSync(lock);fs.unlinkSync(lockPath);}
