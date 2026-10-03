/** Continue one observed image-order challenge; coordinates supplied after screenshot inspection. */
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import {fileURLToPath} from 'node:url';
import {Evidence,verify,safeError} from '../../experiments/bot-diagnostics/evidence.mjs';
import {ToolAdapter} from '../../experiments/bot-diagnostics/adapters.mjs';
import {performSteps} from '../../experiments/bot-diagnostics/operations.mjs';
import {snapshotSources} from '../../experiments/bot-diagnostics/runtime.mjs';
const root=path.dirname(fileURLToPath(import.meta.url)),directory=path.join(root,'general');
const plan=JSON.parse(fs.readFileSync(path.join(root,'browser-observation-plan.json')));
const site=JSON.parse(fs.readFileSync(path.join(directory,'scenario.json'))).sites.find(s=>s.id==='yandex');
const url=plan.sites.find(s=>s.id==='yandex').url;
const lockPath=directory+'.lock',lock=fs.openSync(lockPath,'wx');fs.writeSync(lock,JSON.stringify({pid:process.pid,purpose:'one_visual_challenge'}));
const e=new Evidence(directory,true,{policy:'browser_observation',authorization:plan.authorization});
const saved={id:'yandex',started_at:new Date().toISOString(),events:[],visual_attempts:0};
e.save('framework-invocations.json',[...JSON.parse(fs.readFileSync(path.join(directory,'framework-invocations.json'))),{
  purpose:'One image-order challenge on the same query, screenshot inspected before coordinates',started_at:new Date().toISOString(),authorization:plan.authorization,
  source:e.blob(fs.readFileSync(fileURLToPath(import.meta.url))),sources:snapshotSources(e),headless:false}]);
const adapter=new ToolAdapter({tool:'patchright',site,evidence:e,profile:{id:'visual-check',humanlike:false,extensions:[]},options:{headful:true}});
const input=readline.createInterface({input:process.stdin,output:process.stdout});
const answer=new Promise(resolve=>input.once('line',line=>resolve(JSON.parse(line))));
try {
  await adapter.open();
  saved.events.push(await adapter.accessLink(url,{role:'target'}));
  if(await adapter.browser.page.locator('#js-button').count()===1) {
    saved.events.push(await adapter.accessLink(url,{role:'target',operation:browser=>performSteps(browser,[{op:'click',selector:'#js-button',expect:{kind:'present',selector:'.AdvancedCaptcha'}}])}));
  }
  e.save('yandex-visual-results.json',saved);
  const last=saved.events.at(-1);
  console.log(JSON.stringify({phase:'awaiting_visual_coordinates',screenshot:path.join(directory,last.screenshot||''),dom_sha256:last.dom_sha256,visible_text_prefix:last.visible_text_prefix?.slice(0,300)}));
  const coordinates=await answer;
  if(!Array.isArray(coordinates.points)||coordinates.points.length!==6||coordinates.points.some(p=>!Array.isArray(p)||p.length!==2||p.some(x=>!Number.isFinite(x)))) throw new Error('six screen-coordinate points required');
  saved.visual_attempts=1;saved.coordinate_plan=coordinates;
  saved.events.push(await adapter.accessLink(url,{role:'target',operation:async browser=>{
    for(const [x,y] of coordinates.points)await browser.page.mouse.click(x,y);
    const submit=browser.page.locator('button[type="submit"]');
    if(await submit.count()!==1)throw new Error('Submit selector is not unique');
    await submit.click({timeout:7000});
    return {outcome:'visual_sequence_submitted',clicks:6,submit_clicks:1,success_requires_saved_search_body:true};
  }}));
  saved.finished_at=new Date().toISOString();e.save('yandex-visual-results.json',saved);
  const result=saved.events.at(-1);console.log(JSON.stringify({phase:'completed',outcome:result.outcome,http_status:result.http_status,title:result.title,visible_text_prefix:result.visible_text_prefix?.slice(0,500),screenshot:result.screenshot,verification:verify(directory)}));
} catch(error) {saved.error=safeError(error);e.save('yandex-visual-results.json',saved);console.log(JSON.stringify({error:saved.error}));process.exitCode=1;}
finally {input.close();await adapter.close();e.save('verification.json',verify(directory));fs.closeSync(lock);fs.unlinkSync(lockPath);}
