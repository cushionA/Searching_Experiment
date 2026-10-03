/** One observed verification control per service, in one normal headful session. */
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Evidence,verify,safeError} from '../../experiments/bot-diagnostics/evidence.mjs';
import {ToolAdapter} from '../../experiments/bot-diagnostics/adapters.mjs';
import {performSteps,inspect} from '../../experiments/bot-diagnostics/operations.mjs';
import {snapshotSources} from '../../experiments/bot-diagnostics/runtime.mjs';
const root=path.dirname(fileURLToPath(import.meta.url)),directory=path.join(root,'general');
const plan=JSON.parse(fs.readFileSync(path.join(root,'browser-observation-plan.json')));
const specs=[{id:'brave',selector:'button.kind--filled'},{id:'yandex',selector:'#js-button'}];
const scenario=JSON.parse(fs.readFileSync(path.join(directory,'scenario.json')));
const lockPath=directory+'.lock',lock=fs.openSync(lockPath,'wx');
fs.writeSync(lock,JSON.stringify({pid:process.pid,started_at:new Date().toISOString(),purpose:'simple_challenge_observation'}));
let adapter;
try {
  const e=new Evidence(directory,true,{policy:'browser_observation',authorization:plan.authorization});
  const invocations=JSON.parse(fs.readFileSync(path.join(directory,'framework-invocations.json')));
  e.save('framework-invocations.json',[...invocations,{started_at:new Date().toISOString(),purpose:'One native verification-control click on observed Brave/Yandex challenge',
    authorization:plan.authorization,selectors:specs,source:e.blob(fs.readFileSync(fileURLToPath(import.meta.url))),sources:snapshotSources(e),headless:false}]);
  const completed=[];
  if(fs.existsSync(path.join(directory,'simple-challenge-results.json'))) throw new Error('Do not repeat saved challenge checks');
  e.save('simple-challenge-results.json',completed);
  for(const spec of specs) {
    const site=scenario.sites.find(s=>s.id===spec.id),cell=plan.sites.find(s=>s.id===spec.id);
    const saved={id:site.id,policy:'browser_observation',selector:spec.selector,started_at:new Date().toISOString(),events:[]};
    try {
      adapter=new ToolAdapter({tool:'patchright',site,evidence:e,profile:{id:'challenge-check',humanlike:false,extensions:[]},options:{headful:true}});
      await adapter.open();
      const before=await adapter.accessLink(cell.url,{role:'target'});saved.events.push(before);
      saved.selector_before=await inspect(adapter.browser.page,spec.selector);
      if(saved.selector_before.count===1) {
        const after=await adapter.accessLink(cell.url,{role:'target',operation:browser=>performSteps(browser,[{op:'click',selector:spec.selector,expect:{kind:'present',selector:'a[href*="waseda."]'}}])});
        saved.events.push(after);
        console.log(JSON.stringify({id:site.id,control:spec.selector,operation:after.operation?.outcome,http_status:after.http_status,outcome:after.outcome,title:after.title,visible_text_prefix:after.visible_text_prefix?.slice(0,400),screenshot:after.screenshot}));
      } else console.log(JSON.stringify({id:site.id,control:spec.selector,match_count:saved.selector_before.count,outcome:before.outcome,http_status:before.http_status}));
    } catch(error) {saved.error=safeError(error);console.log(JSON.stringify({id:site.id,error:saved.error}));}
    finally {await adapter?.close();adapter=null;saved.finished_at=new Date().toISOString();completed.push(saved);e.save('simple-challenge-results.json',completed);}
  }
  const checked=verify(directory);e.save('verification.json',checked);console.log(JSON.stringify({verification:checked}));if(!checked.ok)process.exitCode=1;
} finally {await adapter?.close();fs.closeSync(lock);fs.unlinkSync(lockPath);}
