import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import vm from 'node:vm';
const {openDocumentTab,pageNavigationExpression,waitForBlankTab}=createRequire(import.meta.url)('../fourget-selfhost/fourplay/tab-navigation.cjs');

const target='https://example.test/a?x="quoted"&next=%2F#part';
const container={id:'private-7'};
const blank=(id=4,owner=container,status='complete',url='about:blank')=>({id,container:owner,url,status});
const ok={status:true,result:[{frameId:0,result:true}]};

test('waits for the same tab and container to reach completed about:blank',async()=>{
  let time=0,calls=0;
  const observed=await waitForBlankTab({id:4},{container,timeoutMs:300,pollMs:100,
    now:()=>time,sleep:async ms=>{time+=ms;},getTabs:async()=>++calls===1?[blank(4,container,'loading')]:[blank()]});
  assert.equal(observed.id,4);assert.equal(calls,2);
});

test('missing completion and a same-id tab in another container fail finitely',async()=>{
  let time=0;
  await assert.rejects(waitForBlankTab({id:4},{container,timeoutMs:200,pollMs:100,now:()=>time,
    sleep:async ms=>{time+=ms;},getTabs:async()=>[blank(4,container,'loading')]}),/blank_tab_timeout/);
  await assert.rejects(waitForBlankTab({id:4},{container,timeoutMs:200,getTabs:async()=>[blank(4,{id:'other'})]}),/tab_container_mismatch/);
});

test('a stalled tab-list call is bounded and wrong tab or URL observations time out',async()=>{
  const started=performance.now();
  await assert.rejects(waitForBlankTab({id:4},{container,timeoutMs:25,getTabs:()=>new Promise(()=>{})}),/blank_tab_timeout/);
  assert.ok(performance.now()-started<1000);
  for (const tabs of [[blank(4,container,'complete','https://example.test/')],[blank(5)]]) {
    let time=0,calls=0;
    await assert.rejects(waitForBlankTab({id:4},{container,timeoutMs:200,pollMs:100,now:()=>time,
      sleep:async ms=>{time+=ms;},getTabs:async()=>{calls++;return tabs;}}),/blank_tab_timeout/);
    assert.equal(calls,2);
  }
});

test('binds before polling and runs a safely serialized inline page script from the isolated caller',async()=>{
  const events=[],opened={id:9,container};
  const returned=await openDocumentTab({container,url:target,tabOpen:async(...args)=>{
    events.push(['open',...args]);return opened;
  },getTabs:async()=>{events.push(['poll']);return [blank(9)];},onTab:async tab=>{events.push(['bind',tab]);},inject:async(tab,expression,isolated)=>{
    events.push(['inject',tab,expression,isolated]);return ok;
  }});
  assert.equal(returned,opened);
  assert.deepEqual(events.map(event=>event[0]),['open','bind','poll','inject']);
  assert.deepEqual(events[0],['open','about:blank',false,container]);
  assert.equal(events[3][1],opened);assert.equal(events[3][3],true);
  assert.equal(events[3][2],pageNavigationExpression(new URL(target).href));
  let navigatedTo=null,removed=false;
  const document={documentElement:{appendChild(script){vm.runInNewContext(script.textContent,{location:{assign:value=>{navigatedTo=value;}}});}},
    createElement(name){assert.equal(name,'script');return {textContent:'',remove(){removed=true;}};}};
  assert.equal(vm.runInNewContext(events[3][2],{document}),true);
  assert.equal(navigatedTo,new URL(target).href);assert.equal(removed,true);
});

test('rejects malformed, non-HTTP, and credentialed target URLs',async()=>{
  for (const url of ['not a URL','file:///tmp/page','https://user:secret@example.test/']) {
    await assert.rejects(openDocumentTab({container,url,tabOpen:async()=>{throw new Error('should_not_open');}}),/invalid_navigation_url/);
  }
});

test('rejects failed navigation injection',async()=>{
  await assert.rejects(openDocumentTab({container,url:target,tabOpen:async()=>({id:4}),
    getTabs:async()=>[blank()],inject:async()=>({status:false,result:null})}),/navigation_injection_failed/);
});
