import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import {prepare,execute} from './framework.mjs';

const requests=[];
const normal='<title>Fixture page</title><div id="normal-content">Normal content</div>';
const server=http.createServer((req,res)=>{
  const cookie=req.headers.cookie||'';
  requests.push({path:req.url,session:cookie.includes('lab_session=present'),cleared:cookie.includes('cleared=1')});
  if(req.url==='/robots.txt'){res.end('User-agent: *\nAllow: /\nDisallow: /forbidden');return;}
  if(req.url==='/favicon.ico'){res.writeHead(404);res.end();return;}
  if(req.url==='/'||req.url==='/network-action'){res.setHeader('Set-Cookie','lab_session=present; Path=/; SameSite=Lax');}
  else if(!cookie.includes('lab_session=present')){res.writeHead(403);res.end('<title>Access Denied</title>Missing session');return;}
  if(req.url==='/unlock'){res.setHeader('Set-Cookie','cleared=1; Path=/; SameSite=Lax');res.end(normal);return;}
  if(req.url==='/challenge'&&!cookie.includes('cleared=1')){
    res.writeHead(403,{'Content-Type':'text/html'});
    res.end('<title>Security Check</title><p>Verify that you are human</p><button id="solve" onclick="location.href=\'/unlock\'">Continue</button>');return;
  }
  if(req.url==='/redirect'){res.writeHead(302,{Location:'/inside'});res.end();return;}
  if(req.url==='/network-action'){
    res.end(normal+'<button id="forbidden" onclick="location.href=\'/forbidden\'">Blocked path</button>');return;
  }
  res.setHeader('Content-Type','text/html; charset=utf-8');
  res.end(normal+'<input id="query"><button id="apply" onclick="document.querySelector(\'#result\').textContent=\'applied\'">Apply</button><p id="result">pending</p><button id="hover" onmouseover="this.setAttribute(\'data-over\',\'true\')">Hover</button>');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
const root=path.resolve(process.argv[2]||'.lab-output/framework-options-smoke');
const base={schema:1,tools:['wreq-js','impit','patchright','rebrowser-lightpanda','playwright-baseline'],
  limits:{requests_per_tool_site:25,body_bytes_per_tool_site:8388608},options:{selectors:true,recoverSimple:true,seed:17},
  recovery_plan:{max_attempts:1,return_home_after_success:true,wait_seconds:0,retry_original_target:true}};
const site={id:'fixture',origins:[origin],links:{home:origin+'/',targets:[origin+'/challenge',origin+'/redirect']},params:{},selectors:{primary:'#query'},
  operations:[{op:'fill',selector:'#query',value:'Test'},
    {op:'click',selector:'#apply',expect:{kind:'text',selector:'#result',value:'applied'}},
    {op:'hover',selector:'#hover',expect:{kind:'attribute',selector:'#hover',name:'data-over',value:'true'}}],
  recovery:{kind:'simple_button',selector:'#solve',success:{kind:'present',selector:'#normal-content'}}};
const reports=[];
try {
  for(const humanlike of [false,true]) {
    const manifest=prepare({...base,profiles:[{id:humanlike?'humanlike':'baseline',humanlike,extensions:[]}],sites:[site]},{fixture:true});
    const directory=path.join(root,humanlike?'humanlike':'native');
    const result=await execute({manifest,directory,fixture:true});
    assert.equal(result.verification.ok,true);
    const outcomes=JSON.parse(fs.readFileSync(path.join(directory,'pipeline-results.json')));
    for(const tool of ['patchright','rebrowser-lightpanda','playwright-baseline']) {
      const outcome=outcomes.find(o=>o.tool===tool);
      assert.equal(outcome.state,'navigation_completed',JSON.stringify(outcome));
      assert.equal(outcome.events.find(e=>e.role==='selectorProbe').result.outcome,'operation_confirmed',JSON.stringify(outcome));
      assert.equal(outcome.events.find(e=>e.role==='recoverSimpleChallenge').result.outcome,'recovery_confirmed');
      assert.equal(outcome.events.filter(e=>e.role==='returnHome').length,1);
      const challengeVisits=outcome.events.filter(e=>e.role==='target'&&e.result.url===origin+'/challenge');
      assert.equal(challengeVisits.length,2);assert.equal(challengeVisits.at(-1).result.http_status,200);
    }
    const ledgerBefore=fs.readFileSync(path.join(directory,'ledger.json'),'utf8');
    await execute({manifest,directory,fixture:true,resume:true});
    assert.equal(fs.readFileSync(path.join(directory,'ledger.json'),'utf8'),ledgerBefore,'completed cells do not refetch on resume');
    reports.push(result);
  }
  const extensionManifest=prepare({...base,options:{selectors:false,recoverSimple:false},
    profiles:[{id:'baseline',extensions:[]}],sites:[{...site,operations:[],links:{home:origin+'/',targets:[origin+'/inside']}}]}, {fixture:true,extensions:true});
  // Separate local fixture case: check extension availability without resetting any measured real-site budget.
  extensionManifest.profiles=extensionManifest.profiles.filter(p=>p.id==='extension');
  const directory=path.join(root,'extension');
  const result=await execute({manifest:extensionManifest,directory,fixture:true});
  assert.equal(result.verification.ok,true);
  const outcomes=JSON.parse(fs.readFileSync(path.join(directory,'pipeline-results.json')));
  for(const tool of ['patchright','playwright-baseline']) {
    const outcome=outcomes.find(o=>o.tool===tool);
    assert.equal(outcome.state,'navigation_completed',JSON.stringify(outcome));
    assert.equal(outcome.events.find(e=>e.role==='extensionProbe').result.outcome,'extension_loaded');
  }
  for(const tool of ['wreq-js','impit','rebrowser-lightpanda']) assert.equal(outcomes.find(o=>o.tool===tool).state,'unsupported_capability');
  reports.push(result);
  const robotManifest=prepare({...base,tools:['patchright','rebrowser-lightpanda','playwright-baseline'],
    profiles:[{id:'baseline',extensions:[]}],sites:[{...site,id:'fixture-robots',recovery:null,
      links:{home:origin+'/network-action',targets:[]},
      operations:[{op:'click',selector:'#forbidden',expect:{kind:'url',value:origin+'/forbidden'}}]}]},{fixture:true});
  const robotDirectory=path.join(root,'robots-operation');
  const robotsResult=await execute({manifest:robotManifest,directory:robotDirectory,fixture:true});
  assert.equal(robotsResult.verification.ok,true);
  const robotOutcomes=JSON.parse(fs.readFileSync(path.join(robotDirectory,'pipeline-results.json')));
  for(const outcome of robotOutcomes){
    const operation=outcome.events.find(e=>e.role==='selectorProbe').result;
    assert.notEqual(operation.outcome,'operation_confirmed');
    assert.ok(operation.observation.blocked_requests.some(r=>r.url===origin+'/forbidden'&&r.reason==='robots_denied'),JSON.stringify(outcome));
  }
  assert.equal(requests.some(r=>r.path==='/forbidden'),false,'disallowed action URL never reaches the server');
  reports.push(robotsResult);
  fs.writeFileSync(path.join(root,'fixture-requests.json'),JSON.stringify(requests,null,2)+'\n');
  fs.writeFileSync(path.join(root,'checks.json'),JSON.stringify({fixture_only:true,
    checks:['native fill/click/hover effects','humanlike native inputs','simple challenge confirmation','one homepage revisit','original target retried once',
      'cookie persistence','extension actually loaded','unsupported capability recorded','completed cells not refetched on resume','robots applies to native clicks'],reports},null,2)+'\n');
  console.log(JSON.stringify({fixture_only:true,reports},null,2));
} finally {await new Promise(resolve=>server.close(resolve));}
