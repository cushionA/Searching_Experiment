/** Explicit continuation of the six robots-unavailable cells; shared original budgets. */
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Evidence, verify, safeError} from '../../experiments/bot-diagnostics/evidence.mjs';
import {ToolAdapter} from '../../experiments/bot-diagnostics/adapters.mjs';
import {snapshotSources} from '../../experiments/bot-diagnostics/runtime.mjs';

const root=path.dirname(fileURLToPath(import.meta.url));
const group=process.argv[2];
if(!['meta','ai-api'].includes(group)) throw new Error('Specify meta or ai-api');
const directory=path.join(root,group);
const plan=JSON.parse(fs.readFileSync(path.join(root,'robots-retry-plan.json')));
const selected=plan.sites.filter(site=>site.group===group);
const lockPath=directory+'.lock';
const lock=fs.openSync(lockPath,'wx');
fs.writeSync(lock,JSON.stringify({pid:process.pid,started_at:new Date().toISOString(),purpose:'robots_retry'}));
const displayURL=url=>{
  if(!url) return url;
  try {
    const parsed=new URL(url);
    for(const key of [...parsed.searchParams.keys()]) if(/token|__cf_/i.test(key)) parsed.searchParams.delete(key);
    return parsed.href;
  } catch { return url; }
};
let adapter;
try {
  const evidence=new Evidence(directory,true);
  const scenario=JSON.parse(fs.readFileSync(path.join(directory,'scenario.json')));
  const invocation={started_at:new Date().toISOString(),purpose:'User-requested retry of six robots-unavailable services',
    authorization:plan.authorization,plan_sha256:evidence.blob(fs.readFileSync(path.join(root,'robots-retry-plan.json'))),
    source:evidence.blob(fs.readFileSync(fileURLToPath(import.meta.url))),sources:snapshotSources(evidence),
    headless:false,budget_policy:'append to original per-site key; no budget reset; explicit deny stays blocked'};
  const historyFile=path.join(directory,'framework-invocations.json');
  const history=JSON.parse(fs.readFileSync(historyFile));
  evidence.save('framework-invocations.json',[...history,invocation]);
  const finished=[];
  evidence.save('robots-retry-results.json',finished);
  for(const cell of selected) {
    const original=scenario.sites.find(site=>site.id===cell.id);
    if(!original) throw new Error('Unknown existing cell');
    const site={...original,origins:[...new Set([...original.origins,...cell.redirect_origins])]};
    const before={...(evidence.budgets[`patchright/${site.id}`]||{})};
    const saved={id:site.id,group,started_at:new Date().toISOString(),budget_before:before,events:[]};
    const record=async(url,role)=>{
      const result=await adapter.accessLink(url,{role});
      saved.events.push(result);
      evidence.save('robots-retry-results.json',[...finished,saved]);
      console.log(JSON.stringify({id:site.id,role,url,outcome:result.outcome,http_status:result.http_status,
        final_url:displayURL(result.final_url),robots_outcome:result.robots_outcome,title:result.title,
        visible_text_prefix:result.visible_text_prefix?.slice(0,200)}));
      return result;
    };
    try {
      adapter=new ToolAdapter({tool:'patchright',site,evidence,profile:scenario.profiles[0],
        options:{...scenario.options,headful:true,robotsRedirectOrigins:cell.redirect_origins,robotsUnavailableProbe:true}});
      await adapter.open();
      const home=await record(cell.home,'homepage');
      if(cell.canonical_home && cell.canonical_home!==cell.home && home.outcome!=='robots_denied') {
        // The new origin is a saved redirect destination in this continuation plan.
        await record(cell.canonical_home,'homepage');
      }
      if(cell.query_url) await record(cell.query_url,'target');
    } catch(error) {
      saved.error=safeError(error);
      console.log(JSON.stringify({id:site.id,error:saved.error}));
    } finally {
      await adapter?.close(); adapter=null;
      saved.finished_at=new Date().toISOString();
      saved.budget_after={...(evidence.budgets[`patchright/${site.id}`]||{})};
      finished.push(saved);
      evidence.save('robots-retry-results.json',finished);
    }
  }
  const checked=verify(directory);
  evidence.save('verification.json',checked);
  console.log(JSON.stringify({group,verification:checked,sites:finished.map(x=>x.id)}));
  if(!checked.ok) process.exitCode=1;
} finally {
  await adapter?.close();
  fs.closeSync(lock); fs.unlinkSync(lockPath);
}
