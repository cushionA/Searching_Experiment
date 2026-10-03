import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Evidence,verify} from './evidence.mjs';
import {SessionManager,waitForSessionCooldown} from './session-manager.mjs';
import {ToolAdapter} from './adapters.mjs';
import {prepare} from './manifest.mjs';
import {browserSite} from './runner.mjs';

function fixture(t) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'session-manager-test-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const evidence=new Evidence(path.join(root,'run'));evidence.flush();
  return {evidence,key:'rebrowser-lightpanda/fixture',policy:true};
}
function policyEvidence(t,policy) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'session-manager-open-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const evidence=new Evidence(path.join(root,'run'),false,{policy,authorization:'fixture regression test'});
  return {evidence,root};
}
test('retry and rotation counters and origin cooldown survive restart without saving cookies',async t=>{
  const args=fixture(t),url='https://example.com/';
  const manager=new SessionManager(args);manager.select();
  manager.snapshot([{name:'private',value:'sensitive-cookie'}]);
  const record=args.evidence.reserve(args.key,url,'main_document');
  args.evidence.finish(record,403,{},Buffer.from('denied'));
  const result={outcome:'access_denied_observed',http_status:403};
  const decision=manager.observe(result,url,{});
  assert.equal(manager.session.cookies.length,0);
  assert.equal(manager.retryAvailable(url,decision),true);
  manager.chargeRetry(url);assert.equal(manager.select().replace,true);
  const resumed=new Evidence(args.evidence.directory,true);
  const next=new SessionManager({...args,evidence:resumed});next.select();
  assert.equal(next.shared.retries,1);assert.equal(next.shared.rotations,1);
  assert.equal(next.retryAvailable(url,decision),false);
  assert.equal((await waitForSessionCooldown(resumed,url,0)).outcome,'session_cooldown_deferred');
  const saved=fs.readFileSync(path.join(resumed.directory,'session-policy-state.json'),'utf8');
  assert.ok(!saved.includes('sensitive-cookie'));
  assert.equal(verify(resumed.directory).ok,true);
});
test('429 preserves the session and does not truncate a long Retry-After',async t=>{
  const manager=new SessionManager(fixture(t));const {session}=manager.select();
  manager.snapshot([{name:'continuity',value:'one'}]);
  const result={outcome:'challenge_observed',http_status:429};
  manager.observe(result,'https://example.com/',{'retry-after':'3600'});
  assert.equal(manager.select().session.id,session.id);
  assert.equal(session.cookies.length,1);
  assert.equal(session.uses,1);
  assert.ok(result.session_pool.backoff_ms>=3600000);
  assert.equal((await waitForSessionCooldown(manager.evidence,'https://example.com/',0)).outcome,'session_cooldown_deferred');
});
test('cookies and metadata are bounded while retry admission uses the original request budget',t=>{
  const args=fixture(t),manager=new SessionManager(args);manager.select();
  args.evidence.budgets[args.key]={requests:24,bytes_charged:0};
  assert.equal(manager.retryAvailable('https://example.com/',{action:'retire'}),false);
  args.evidence.budgets[args.key]={requests:22,bytes_charged:0};
  assert.equal(manager.retryAvailable('https://example.com/target',{action:'retire'},4),false);
});
test('pool profile validation rejects wrong tools and inherits for humanlike input',()=>{
  const manifest=JSON.parse(fs.readFileSync(new URL('./lightpanda-sites.json',import.meta.url)));
  manifest.profiles[0].session_pool=true;
  const prepared=prepare(manifest,{humanlike:true});
  assert.equal(prepared.profiles.at(-1).session_pool.max_pool_size,3);
  manifest.tools=['impit'];
  assert.throws(()=>prepare(manifest),/session_pool_requires_browser_without_extensions/);
  manifest.tools=['patchright'];delete manifest.profiles[0].lightpanda;
  assert.equal(prepare(manifest).profiles[0].session_pool.max_pool_size,3);
});
test('unsupported budget interception stops before any browser or robots request',async t=>{
  const {evidence}=fixture(t);
  await browserSite(evidence,'obscura',{name:'fixture',url:'https://example.com/'},{
    browser:{runtime:{budgeted_navigation:false}},
    robots:{get:()=>{throw new Error('must not request');}},
  });
  assert.equal(evidence.records.length,0);
  assert.equal(evidence.results.at(-1).outcome,'execution_error');
  assert.match(evidence.results.at(-1).error.message,/unsupported_capability:budgeted_navigation/);
});
test('native operations never retry or replay after a block',async t=>{
  const args=fixture(t),url='http://127.0.0.1/';
  const adapter=new ToolAdapter({tool:'rebrowser-lightpanda',site:{id:'fixture',origins:['http://127.0.0.1'],links:{home:url}},
    evidence:args.evidence,fixture:true,profile:{id:'pool',session_pool:true}});
  adapter.sessions.select();let calls=0;
  adapter.accessLinkOnce=async()=>{calls++;return {outcome:'access_denied_observed',http_status:403};};
  const result=await adapter.accessLink(url,{operation:()=>{}});
  assert.equal(calls,1);assert.equal(result.session_pool.action,'retire');
  assert.equal(adapter.sessions.shared.retries,0);
});
test('different profiles cannot reset the site retry allowance',t=>{
  const args=fixture(t);
  const first=new SessionManager(args);first.select();first.chargeRetry('https://example.com/a');
  const second=new SessionManager(args);second.select();second.chargeRetry('https://example.com/b');
  const third=new SessionManager(args);third.select();
  assert.equal(third.retryAvailable('https://example.com/c',{action:'retire'}),false);
  assert.equal(third.shared.retries,2);
});
test('browser is closed even if another client fails during teardown',async t=>{
  const args=fixture(t);let closed=false;
  const adapter=new ToolAdapter({tool:'rebrowser-lightpanda',site:{id:'fixture'},evidence:args.evidence});
  adapter.robots={close:async()=>{throw new Error('robot client close failed');}};
  adapter.browser={close:async()=>{closed=true;}};
  await assert.rejects(adapter.close(),/robot client close failed/);assert.equal(closed,true);
});
test('open propagates fixture, Lightpanda and headful settings, initializes the pool, and gates navigation by policy',async t=>{
  const profile={id:'compat-pool',lightpanda:{release:'1.0.0',profile:'compat'},session_pool:{max_pool_size:2}};
  const observed=policyEvidence(t,'browser_observation');
  let browserArgs,clientArgs;
  const adapter=new ToolAdapter({tool:'rebrowser-lightpanda',site:{id:'fixture'},evidence:observed.evidence,
    fixture:true,profile,options:{headful:false},
    browserOpener:async(tool,options)=>{
      browserArgs={tool,options};
      return {ua:'fixture-agent',runtime:{budgeted_navigation:false,headless:false,display:':fixture',viewport:{width:1280,height:720}},
        replaceContext:async()=>{},close:async()=>{}};
    },
    clientOpener:async(...args)=>{clientArgs=args;return {close:async()=>{}};}});
  await adapter.open();
  assert.equal(browserArgs.tool,'rebrowser-lightpanda');
  assert.deepEqual(browserArgs.options.lightpanda,profile.lightpanda);
  assert.equal(browserArgs.options.fixture,true);
  assert.equal(browserArgs.options.headful,true);
  assert.deepEqual(clientArgs,['rebrowser-lightpanda','fixture-agent',true]);
  assert.equal(adapter.sessions.pool.state().length,1);
  assert.ok(adapter.sessions.pool.session);
  await adapter.close();

  const grounding=policyEvidence(t,'grounding');
  const rejected=new ToolAdapter({tool:'rebrowser-lightpanda',site:{id:'fixture'},evidence:grounding.evidence,
    fixture:true,profile,options:{headful:true},
    browserOpener:async()=>({ua:'fixture-agent',runtime:{budgeted_navigation:false},replaceContext:async()=>{},close:async()=>{}}),
    clientOpener:async()=>({close:async()=>{}})});
  await assert.rejects(rejected.open(),/unsupported_capability:budgeted_navigation/);

  const replacementCheck=policyEvidence(t,'grounding');
  const noReplacement=new ToolAdapter({tool:'rebrowser-lightpanda',site:{id:'fixture'},evidence:replacementCheck.evidence,
    fixture:true,profile,options:{},
    browserOpener:async()=>({ua:'fixture-agent',runtime:{budgeted_navigation:true},close:async()=>{}}),
    clientOpener:async()=>({close:async()=>{}})});
  await assert.rejects(noReplacement.open(),/unsupported_capability:session_context_replacement/);
});
