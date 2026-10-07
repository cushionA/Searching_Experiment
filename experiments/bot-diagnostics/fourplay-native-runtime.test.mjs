import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import vm from 'node:vm';
import {EventEmitter} from 'node:events';

const root=fs.mkdtempSync(path.join(os.tmpdir(),'fourplay-runtime-test-'));
process.env.BOT_DIAGNOSTICS_STATE=path.join(root,'state');
process.env.BOT_DIAGNOSTICS_FIREFOX=path.join(root,'firefox');
process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION=path.join(root,'upstream','ext');
process.env.DISPLAY='fixture:0';
fs.writeFileSync(process.env.BOT_DIAGNOSTICS_FIREFOX,'fixture');
fs.mkdirSync(process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION,{recursive:true});
fs.writeFileSync(path.join(process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION,'bg.js'),
  'async function ws_init(){\n\tvar config = await browser.storage.local.get();\n\t\n\ttry{\n\t\tglobal_ws = await ws_connect_timeout(config.ws_url, config.ws_timeout);\n\t}\n}\n');
fs.writeFileSync(path.join(root,'upstream-commit.txt'),'fixture-upstream-commit\n');
const {openFourplayNative,startFixtureRelay,initializeFourplayServer}=await import('./fourplay-native-runtime.mjs');

test('native controller binds loopback and chooses a fresh unlogged session token',async()=>{
  let initArgs;
  initializeFourplayServer({init:(...args)=>{initArgs=args;}},{port:32001,password:'fresh-token',commandTimeout:2000});
  assert.deepEqual(initArgs,[{port:32001,host:'127.0.0.1'},'fresh-token',2000]);
  const tokens=[];
  for(let index=0;index<2;index++) {
    const server=fixtureServer();
    const browser=await openFourplayNative({fixture:true,serverFactory:options=>{tokens.push(options.password);return server;},
      launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
    await browser.close();
  }
  assert.notEqual(tokens[0],tokens[1]);
  assert.ok(tokens.every(token=>/^[a-f0-9]{48}$/.test(token)));
});

test('fixture traces redact the extension WebSocket authentication path',async t=>{
  const server=fixtureServer();
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,
    launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  server.event.emit('web_request',{id:-1,container:'firefox-default',type:'websocket',method:'GET',
    url:'ws://127.0.0.1:3030/private-fixture-token?secret=fixture'});
  assert.equal(browser.runtime.fixture_event_trace.at(-1).url,'ws://127.0.0.1:3030/[redacted]');
  assert.equal(JSON.stringify(browser.runtime.fixture_event_trace).includes('private-fixture-token'),false);
});

function fixtureServer({emitNavigationEvents=false,httpErrorStatus=null,redirectTo=null,completeNavigation=true,
  injectionFailureAt=null,domReadyBeforeResponse=false,responseDelayMs=25}={}) {
  const event=new EventEmitter(), calls=[],tabs=new Map(),navigations=[];
  let pageUrl='about:blank',nextTabId=2,navigationAttempts=0;
  const emitNavigation=(tabId,url,{status=httpErrorStatus||200,body='native response'}={})=>{
    if(emitNavigationEvents) event.emit('web_request',{id:1,container:'container-1',url:'http://127.0.0.1:8080/favicon.ico',type:'other',method:'GET',headers:[]});
    event.emit('web_request',{id:tabId,container:'container-1',url,type:'main_frame',method:'GET',headers:[]});
    if(status>=400) event.emit('dom_load_fail',{id:tabId,container:'container-1',url,error:`HTTP/2 ${status} Forbidden`});
    const response=()=>event.emit('web_response',{id:tabId,container:'container-1',url,type:'main_frame',method:'GET',status,headers:[],body:Buffer.from(body)});
    if(domReadyBeforeResponse) {
      event.emit('dom_ready',{id:tabId,container:'container-1',url,status:'complete'});
      setTimeout(response,responseDelayMs);
    } else response();
  };
  const server={event,calls,navigations,stopped:false,async stop(){server.stopped=true;},async call(method,...args){
    calls.push({method,args});
    if(method==='get_ua') return 'Mozilla/5.0 Firefox fixture';
    if(method==='close_all_tabs') return {id:1,url:'about:blank'};
    if(method==='delete_all_containers'||method==='web_response_whitelist') return 1;
    if(method==='container_create') return {id:'container-1'};
    if(method==='tab_open'){
      pageUrl=args[0];
      const tab={id:nextTabId++,url:'about:blank',status:'complete',container:'container-1'};
      tabs.set(tab.id,tab);
      // The blank document completes before navigation is injected. It must never
      // satisfy the later target-document wait.
      event.emit('dom_ready',{id:tab.id,container:tab.container,url:'about:blank',status:'complete'});
      return tab;
    }
    if(method==='get_tab_list') return [...tabs.values()].map(tab=>({...tab}));
    if(method==='tab_close') { tabs.delete(args[0]); return true; }
    if(method==='container_attach_proxy') return true;
    if(method==='tab_inject_js') {
      const tabId=typeof args[0]==='object'?args[0]?.id:args[0];
      const source=args[1];
      if(source.includes('fixture evaluation failure')) return {status:'fixture evaluation failure',result:null};
      let requestedURL=null,injectionFailed=false;
      const pageContext=vm.createContext({location:{assign:value=>{requestedURL=String(value);}}});
      const document={
        documentElement:{appendChild:script=>{
          navigationAttempts++;
          if(navigationAttempts===injectionFailureAt) { injectionFailed=true; return; }
          vm.runInContext(script.textContent,pageContext);
        }},
        createElement:name=>({tagName:name.toUpperCase(),textContent:'',remove(){this.removed=true;}}),
      };
      try { vm.runInNewContext(source,{document}); } catch {}
      if(injectionFailed) return {status:'fixture_navigation_injection_failed',result:null};
      if(requestedURL!==null) {
        navigations.push({tabId,url:requestedURL,isolated:args[2]===true});
        if(completeNavigation) {
          const finalURL=redirectTo || requestedURL;
          if(redirectTo) {
            emitNavigation(tabId,requestedURL,{status:302,body:'redirect'});
            emitNavigation(tabId,finalURL,{status:200,body:'redirect target'});
          } else emitNavigation(tabId,requestedURL);
          pageUrl=finalURL;
          tabs.set(tabId,{id:tabId,url:finalURL,status:'complete',container:'container-1'});
          event.emit('dom_ready',{id:tabId,container:'container-1',url:finalURL,status:'complete'});
        }
        return {status:true,result:[{frameId:0,result:true}]};
      }
      if(source.includes('document.documentElement?.outerHTML')) return {status:true,result:[{frameId:0,result:'<html><body><input id="q"></body></html>'}]};
      if(source.includes(',"inputValue",[]]')) return {status:true,result:[{frameId:0,result:'query'}]};
      if(source.includes(',"count",[]]')) return {status:true,result:[{frameId:0,result:1}]};
      return {status:true,result:[{frameId:0,result:'native-result'}]};
    }
    throw new Error(`unexpected method ${method}`);
  }};
  queueMicrotask(()=>event.emit('server_ready',{}));
  return server;
}

test('adapts official request/response events and evaluates in the extension isolated world',async t=>{
  const server=fixtureServer();
  let sessionPassword;
  const browser=await openFourplayNative({fixture:true,serverFactory:options=>{sessionPassword=options.password;return server;},
    launch:({extension})=>{assert.equal(fs.existsSync(path.join(extension,'bg.js')),true);queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  assert.match(fs.readFileSync(path.join(browser.runtime.extension_copy||path.join(process.env.BOT_DIAGNOSTICS_STATE,fs.readdirSync(process.env.BOT_DIAGNOSTICS_STATE)[0],'extension'),'bg.js'),'utf8'),new RegExp(`127\\.0\\.0\\.1:3030/${sessionPassword}`));
  assert.match(fs.readFileSync(path.join(process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION,'bg.js'),'utf8'),/var config = await browser\.storage\.local\.get\(\);/);
  const firefoxPrefs=fs.readFileSync(path.join(process.env.BOT_DIAGNOSTICS_STATE,fs.readdirSync(process.env.BOT_DIAGNOSTICS_STATE)[0],'user.js'),'utf8');
  assert.match(firefoxPrefs,/network\.proxy\.failover_direct", false/);
  await browser.page.goto('http://127.0.0.1:8080/fixture');
  assert.equal(browser.kind,'fourplay-native');
  assert.equal(await browser.page.evaluate(()=>42),'native-result');
  assert.equal(await browser.page.locator('#q').count(),1);
  assert.equal(await browser.page.locator('#q').inputValue(),'query');
  const observed=[];
  browser.context.on('request',request=>observed.push(request));
  browser.context.on('response',async response=>observed.push({status:response.status(),body:(await response.body()).toString()}));
  server.event.emit('web_request',{id:2,container:'container-1',url:'http://127.0.0.1:8080/fixture',status:0,origin:'http://127.0.0.1:8080',type:'main_frame',method:'GET',headers:['accept: text/html']});
  server.event.emit('web_request',{id:99,container:'container-1',url:'http://127.0.0.1:8080/other-tab',status:0,origin:'http://127.0.0.1:8080',type:'main_frame',method:'GET',headers:[]});
  server.event.emit('web_request',{id:2,container:'another-container',url:'http://127.0.0.1:8080/other-container',status:0,origin:'http://127.0.0.1:8080',type:'main_frame',method:'GET',headers:[]});
  server.event.emit('web_response',{id:2,container:'container-1',url:'http://127.0.0.1:8080/fixture',status:207,origin:'http://127.0.0.1:8080',type:'main_frame',method:'GET',headers:['content-type: text/html'],body:Buffer.from('real body')});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(observed[0].id,2);
  assert.equal(observed[0].frame(),browser.page.mainFrame());
  assert.equal(observed[0].method(),'GET');
  assert.equal(observed[0].isNavigationRequest(),true);
  assert.deepEqual(observed[1],{status:207,body:'real body'});
  assert.equal(browser.runtime.screenshots,false);
  assert.equal(browser.runtime.selector_operations,false);
  assert.equal(browser.runtime.budgeted_navigation,false);
  assert.equal(browser.runtime.fourplay_upstream_commit,'fixture-upstream-commit');
  await browser.page.goto('http://127.0.0.1:8080/second');
  for(const timing of [browser.runtime.startup_timing_ms,browser.page.lastNavigationTiming]) {
    assert.ok(Object.values(timing).every(value=>Number.isFinite(value)&&value>=0));
    assert.ok(timing.total_ms>=0);
  }
  assert.equal(server.calls.filter(call=>call.method==='tab_close'&&call.args[0]===2).length,1);
  assert.equal(browser.runtime.headless,false);
});

test('surfaces native evaluation errors and terminates server/profile on close',async()=>{
  const server=fixtureServer();
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  await browser.page.goto('http://127.0.0.1:8080/fixture');
  const profile=fs.readdirSync(process.env.BOT_DIAGNOSTICS_STATE).map(name=>path.join(process.env.BOT_DIAGNOSTICS_STATE,name))[0];
  await assert.rejects(browser.page.evaluate(()=>{throw new Error('fixture evaluation failure');}),/fourplay_evaluate_failed/);
  await browser.close();
  assert.equal(server.stopped,true);
  assert.equal(fs.existsSync(profile),false);
});

test('failed navigation closes only the new blank tab and restores the previous page',async t=>{
  const server=fixtureServer({injectionFailureAt:2});
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,
    launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  await browser.page.goto('http://127.0.0.1:8080/first');
  const firstTab=server.navigations[0].tabId;
  assert.equal(browser.page.url(),'http://127.0.0.1:8080/first');

  await assert.rejects(browser.page.goto('http://127.0.0.1:8080/injection-fails'),/navigation_injection_failed/);
  const closes=()=>server.calls.filter(call=>call.method==='tab_close').map(call=>call.args[0]);
  assert.deepEqual(closes(),[3]);
  assert.equal(browser.page.url(),'http://127.0.0.1:8080/first');
  assert.equal(await browser.page.evaluate(()=>42),'native-result');
  assert.equal(server.calls.filter(call=>call.method==='tab_inject_js'&&
    (typeof call.args[0]==='object'?call.args[0]?.id:call.args[0])===firstTab).length,2);

  await browser.page.goto('http://127.0.0.1:8080/third');
  assert.equal(browser.page.url(),'http://127.0.0.1:8080/third');
  assert.deepEqual(closes(),[3,firstTab]);
});

test('ignores stale favicon traffic while binding navigation events to the target main frame',async t=>{
  const server=fixtureServer({emitNavigationEvents:true});
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,
    launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  const requests=[]; browser.context.on('request',request=>requests.push(request));
  const responses=[]; browser.context.on('response',response=>responses.push({status:response.status(),body:response.body()}));
  await browser.page.goto('http://127.0.0.1:8080/redirect-target');
  assert.equal(requests.length,1);
  assert.equal(requests[0].url(),'http://127.0.0.1:8080/redirect-target');
  assert.equal(requests[0].isNavigationRequest(),true);
  assert.equal(requests[0].frame(),browser.page.mainFrame());
  assert.equal(responses.length,1);
  assert.equal(responses[0].status,200);
  assert.equal((await responses[0].body).toString(),'native response');
  assert.equal(server.calls.some(call=>call.method==='tab_open'&&call.args[1]===false),true);
  assert.equal(browser.runtime.viewport,null);
  assert.ok(browser.runtime.observation_limits.includes('viewport_not_measured'));
});

test('HTTP rejection notifications remain native responses and do not fail DOM navigation',async t=>{
  const server=fixtureServer({emitNavigationEvents:true,httpErrorStatus:403});
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,
    launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  const responses=[], failures=[];
  browser.context.on('response',response=>responses.push(response));
  browser.context.on('requestfailed',request=>failures.push(request));
  await browser.page.goto('http://127.0.0.1:8080/rejected');
  assert.equal(responses[0].status(),403);
  assert.equal((await responses[0].body()).toString(),'native response');
  assert.equal(responses[0].request().method(),'GET');
  assert.equal(responses[0].url(),'http://127.0.0.1:8080/rejected');
  assert.equal(browser.page.url(),'http://127.0.0.1:8080/rejected');
  server.event.emit('dom_load_fail',{id:2,url:'http://127.0.0.1:8080/favicon.ico',error:'HTTP/2 404 Not Found'});
  assert.equal(failures.length,0);
});

test('initial about:blank completion cannot satisfy a target navigation wait',async t=>{
  const server=fixtureServer({completeNavigation:false});
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,
    launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  await assert.rejects(browser.page.goto('http://127.0.0.1:8080/never',{timeout:100}),/fourplay_dom_ready_timeout/);
  assert.equal(server.calls.filter(call=>call.method==='tab_open'&&call.args[0]==='about:blank').length,1);
  assert.deepEqual(server.navigations,[{tabId:2,url:'http://127.0.0.1:8080/never',isolated:true}]);
  assert.equal(browser.page.url(),'about:blank');
});

test('redirect completion waits for a matching final main-frame response',async t=>{
  const server=fixtureServer({emitNavigationEvents:true,redirectTo:'http://127.0.0.1:8080/final'});
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,
    launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  const responses=[];browser.context.on('response',response=>responses.push({url:response.url(),status:response.status()}));
  await browser.page.goto('http://127.0.0.1:8080/redirect-start');
  assert.equal(browser.page.url(),'http://127.0.0.1:8080/final');
  assert.deepEqual(responses,[
    {url:'http://127.0.0.1:8080/redirect-start',status:302},
    {url:'http://127.0.0.1:8080/final',status:200},
  ]);
});

test('DOM completion before the response waits for the matching main-frame response',async t=>{
  const server=fixtureServer({emitNavigationEvents:true,domReadyBeforeResponse:true,responseDelayMs:40});
  const browser=await openFourplayNative({fixture:true,serverFactory:()=>server,
    launch:()=>{queueMicrotask(()=>server.event.emit('browser_connect',{}));return {kill(){}};},connectTimeout:500});
  t.after(()=>browser.close());
  let settled=false;
  const navigation=browser.page.goto('http://127.0.0.1:8080/dom-first',{timeout:500}).then(()=>{settled=true;});
  await new Promise(resolve=>setTimeout(resolve,5));
  assert.equal(settled,false);
  await navigation;
  assert.equal(settled,true);
  assert.equal(browser.page.url(),'http://127.0.0.1:8080/dom-first');
});

test('fixture relay forwards loopback HTTP and rejects external and CONNECT traffic',async t=>{
  const origin=http.createServer((request,response)=>{response.writeHead(200,{'content-type':'text/plain'});response.end('fixture');});
  await new Promise(resolve=>origin.listen(0,'127.0.0.1',resolve));
  const relay=await startFixtureRelay();
  t.after(async()=>{await relay.close();await new Promise(resolve=>origin.close(resolve));});
  const request=(target)=>new Promise((resolve,reject)=>{
    const req=http.request({host:'127.0.0.1',port:relay.port,path:target,method:'GET'},response=>{
      let body='';response.setEncoding('utf8');response.on('data',chunk=>body+=chunk);response.on('end',()=>resolve({status:response.statusCode,body}));
    }); req.on('error',reject);req.end();
  });
  assert.deepEqual(await request(`http://127.0.0.1:${origin.address().port}/fixture`),{status:200,body:'fixture'});
  assert.equal((await request('http://example.com/')).status,403);
  const connectStatus=await new Promise((resolve,reject)=>{
    const req=http.request({host:'127.0.0.1',port:relay.port,method:'CONNECT',path:'example.com:443'});
    req.on('connect',(response,socket)=>{socket.destroy();resolve(response.statusCode);});req.on('error',reject);req.end();
  });
  assert.equal(connectStatus,403);
});

test.after(()=>fs.rmSync(root,{recursive:true,force:true}));
