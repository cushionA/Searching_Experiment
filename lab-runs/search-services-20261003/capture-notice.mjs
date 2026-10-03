/** One observed official notice, charged to the existing Yahoo Kids cell. */
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Evidence, verify} from '../../experiments/bot-diagnostics/evidence.mjs';
import {ToolAdapter} from '../../experiments/bot-diagnostics/adapters.mjs';
import {snapshotSources} from '../../experiments/bot-diagnostics/runtime.mjs';

const root=path.dirname(fileURLToPath(import.meta.url));
const directory=path.join(root,'meta');
const lockPath=directory+'.lock';
const lock=fs.openSync(lockPath,'wx');
fs.writeSync(lock,JSON.stringify({pid:process.pid,started_at:new Date().toISOString()}));
let adapter;
try {
  const evidence=new Evidence(directory,true);
  const scenario=JSON.parse(fs.readFileSync(path.join(directory,'scenario.json')));
  const site=scenario.sites.find(s=>s.id==='yahoo-kids');
  const url='https://kids.yahoo.co.jp/info/archives/20260928_1.html';
  const invocation={started_at:new Date().toISOString(),purpose:'Read service-ending notice linked on observed official homepage',
    role:'service_notice',url,headless:false,sources:snapshotSources(evidence),
    source:evidence.blob(fs.readFileSync(fileURLToPath(import.meta.url))),budget_policy:'same existing patchright/yahoo-kids budget; no search query'};
  const historyFile=path.join(directory,'framework-invocations.json');
  evidence.save('framework-invocations.json',[...JSON.parse(fs.readFileSync(historyFile)),invocation]);
  adapter=new ToolAdapter({tool:'patchright',site,evidence,profile:scenario.profiles[0],options:scenario.options});
  await adapter.open();
  const result=await adapter.accessLink(url,{role:'service_notice'});
  evidence.save('service-notice.json',result);
  await adapter.close();adapter=null;
  const checked=verify(directory);
  evidence.save('verification.json',checked);
  console.log(JSON.stringify({outcome:result.outcome,http_status:result.http_status,title:result.title,visible_text_prefix:result.visible_text_prefix,verification:checked}));
} finally {
  await adapter?.close();
  fs.closeSync(lock);fs.unlinkSync(lockPath);
}
