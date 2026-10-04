import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Evidence} from './evidence.mjs';
import {siteIDs} from './search-load.mjs';
import {collectPageURLs,extractionProgress,normalizeResultURL,pageEvaluator,resolveGoogleOpaqueURL,runSearchPagination,validatePaginationManifest} from './search-pagination.mjs';

function manifest(overrides={}) {
  return {schema:1,queries:['q1','q2','q3','q4','q5'],results_per_site:100,max_pages_per_site:100,max_total_pages:500,
    max_unique_urls:500,max_evidence_bytes:1024*1024,max_duration_ms:30*60*1000,target_interval_ms:1000,
    requested_max_results:'maximum_available; no configurable count observed in prior DOM',
    sites:siteIDs.map(id=>({id,tool:'patchright',origin:`https://${id}.example`,search_url:`https://${id}.example/search?q={query}`,
      result_selectors:['.result'],next_selectors:['a.next'],max_results_params:{},timezoneId:'Asia/Tokyo'})),...overrides};
}

function tempRun(){const root=fs.mkdtempSync(path.join(os.tmpdir(),'search-pagination-'));return {root,directory:path.join(root,'run'),close:()=>fs.rmSync(root,{recursive:true,force:true})};}
function fakeClock(){let now=1_800_000_000_000;return {now:()=>now,sleep:async ms=>{now+=ms;}};}
function adapterFactory({clock,pages=()=>({items:[],next:null,status:'empty'}),calls=[],request=null}={}) {
  return async({site,evidence})=>({site,capabilities:{timezoneId:true},browser:{context:{request}},async accessLink(url,{operation}){
    calls.push({site:site.id,url,at:clock.now()});
    const browser={page:{goto:async target=>{this.current=target;},evaluate:async(_fn,args)=>pages({site,url,query:new URL(url).searchParams.get('q'),
      clickNext:!!args?.clickNext,index:calls.filter(x=>x.site===site.id).length})}};
    const operationResult={extractor_configured:true,...await operation(browser)};
    const body=Buffer.from(JSON.stringify(operationResult));const dom_sha256=evidence.blob(body);
    const result={client:site.tool,target:site.id,url,final_url:url,role:'pagination_page',outcome:'content_observed',http_status:200,dom_sha256,operation:operationResult};
    evidence.result(result);return result;
  },async close(){}});
}

test('URL normalization unwraps observed redirect forms and collection deduplicates against site and global caps',()=>{
  assert.equal(normalizeResultURL('https://www.google.com/goto?url=https%3A%2F%2Fexample.com%2Fa%3Futm_source%3Dx%23frag'),
    'https://example.com/a');
  const target='https://example.com/opaque';
  const payload=Buffer.concat([Buffer.from([8,1,18,target.length]),Buffer.from(target)]).toString('base64url');
  assert.equal(normalizeResultURL(`https://www.google.com/goto?url=${payload}`),target);
  const opaque=Buffer.concat([Buffer.from([8,1,18,99,1]),Buffer.alloc(98,0x41)]).toString('base64url');
  assert.equal(normalizeResultURL(`https://www.google.com/goto?url=${opaque}`),null);
  assert.equal(normalizeResultURL('https://www.google.com/goto?url=opaque-unresolvable'),null);
  const site=new Set(),global=new Set();
  const got=collectPageURLs([{href:'https://a.example/1',title:'one'},{href:'https://a.example/1',title:'duplicate'},
    {href:'https://b.example/2',title:'two'}],'https://search.example/',site,global,1);
  assert.deepEqual(got.map(x=>x.accepted),[true,false,true]);
  assert.deepEqual(got.map(x=>x.global_accepted),[true,false,false]);
  assert.equal(site.size,2);assert.equal(global.size,1);
  const providerLimited=new Set();
  collectPageURLs([{href:'https://a.example/1'},{href:'https://a.example/2'}],'https://search.example/',providerLimited,new Set(),500,1);
  assert.equal(providerLimited.size,1);
  assert.deepEqual(extractionProgress('https://x.example/page/1','https://x.example/page/1'),{progress:false,reason:'next_same_url'});
  assert.deepEqual(extractionProgress('https://x.example/page/1',null),{progress:false,reason:'next_missing'});
});

test('fixture SERP card and next anchor are extracted by the native operation callback',async()=>{
  const resultAnchor={href:'https://publisher.example/story',innerText:'Fixture result',textContent:'Fixture result',
    getAttribute:()=>null,closest:()=>null};
  const nextAnchor={href:'https://search.example/page/2',innerText:'Next',textContent:'Next',disabled:false,getAttribute:()=>null};
  const card={innerText:'Fixture result snippet',textContent:'Fixture result snippet',matches:()=>false,
    querySelectorAll:()=>[resultAnchor]};
  const documentFixture={body:{innerText:'Fixture results include entry-1234039.html and ref 142913 plus an unrelated ブロック keyword'},
    querySelectorAll:selector=>selector==='.result-card'?[card]:selector==='a.next'?[nextAnchor]:[]};
  const priorDocument=globalThis.document,priorLocation=globalThis.location;
  globalThis.document=documentFixture;globalThis.location={href:'https://search.example/page/1',origin:'https://search.example'};
  try {
    let waitOptions=null;
    const operation=pageEvaluator({id:'fixture',result_selectors:['.result-card'],next_selectors:['a.next'],next_text:'Next'},null,
      'https://search.example/page/1');
    const observed=await operation({page:{goto:async(_url,options)=>assert.ok(options.timeout<=30000),
      waitForFunction:async(_fn,_arg,options)=>{waitOptions=options;},evaluate:async(fn,args)=>fn(args)}});
    assert.equal(observed.items[0].href,'https://publisher.example/story');
    assert.equal(observed.next,'https://search.example/page/2');
    assert.equal(observed.extractor_configured,true);
    assert.equal(observed.status,'content_observed');
    assert.equal(waitOptions.timeout,5000);
  } finally {
    if(priorDocument===undefined)delete globalThis.document;else globalThis.document=priorDocument;
    if(priorLocation===undefined)delete globalThis.location;else globalThis.location=priorLocation;
  }
});

test('Google opaque resolver records same-origin GET without following publisher redirects',async()=>{
  for(const fixture of [
    {status:302,headers:{location:'https://www.instagram.com/p/example/'},expected:'resolver_redirect_resolved',url:'https://www.instagram.com/p/example/'},
    {status:200,headers:{},expected:'resolver_not_redirected',url:null},
    {status:302,headers:{},expected:'resolver_redirect_without_location',url:null},
    {status:429,headers:{},expected:'resolver_http_429',url:null},
  ]) {
    const temp=tempRun(),evidence=new Evidence(temp.directory,false,{policy:'browser_observation',authorization:'Offline Google redirect resolver fixture.'});
    const visited=[],opaque='https://www.google.com/goto?url=CAESopaque';
    const adapter={browser:{context:{request:{get:async(url,options)=>{
      visited.push(url);assert.equal(options.maxRedirects,0);assert.ok(options.timeout<=5000);
      return {status:()=>fixture.status,headers:async()=>fixture.headers,body:async()=>Buffer.alloc(237,65)};
    }}}}};
    try {
      const resolution=await resolveGoogleOpaqueURL({href:opaque,adapter,evidence,site:{id:'google',tool:'patchright',origin:'https://www.google.com'},
        deadline:Date.now()+10000});
      evidence.flush(true);
      assert.equal(resolution.outcome,fixture.expected);assert.equal(resolution.url,fixture.url);
      assert.deepEqual(visited,[opaque]);
      assert.equal(evidence.records.length,1);assert.equal(evidence.records[0].kind,'result_redirect_get');
      assert.equal(evidence.records[0].request_method,'GET');assert.equal(evidence.records[0].status,fixture.status);
      assert.equal(fs.statSync(path.join(temp.directory,'blobs',evidence.records[0].body_sha256)).size,237);
    } finally {temp.close();}
  }
});

test('run retains the original opaque href while exposing its verified normalized URL',async()=>{
  const temp=tempRun(),clock=fakeClock(),visited=[];
  const request={get:async(url,options)=>{
    visited.push(url);assert.equal(options.maxRedirects,0);
    return {status:()=>302,headers:async()=>({location:'https://www.instagram.com/p/example/'}),body:async()=>Buffer.alloc(237,65)};
  }};
  const pageFactory=({site})=>site.id==='google'
    ?{items:[{href:'https://www.google.com/goto?url=CAESopaque',title:'Publisher result'}],next:null,status:'content_observed'}
    :{items:[],next:null,status:'empty'};
  try {
    const input=manifest({results_per_site:1,max_total_pages:30,max_unique_urls:30});
    input.sites=input.sites.map(site=>site.id==='google'?{...site,origin:'https://www.google.com',search_url:'https://www.google.com/search?q={query}'}:site);
    const result=await runSearchPagination({manifest:input,directory:temp.directory,
      fixture:true,clock,adapterFactory:adapterFactory({clock,request,pages:pageFactory})});
    const page=result.state.pages.find(row=>row.site==='google');
    assert.equal(page.extracted_urls[0].href,'https://www.google.com/goto?url=CAESopaque');
    assert.equal(page.extracted_urls[0].url,'https://www.instagram.com/p/example/');
    assert.equal(page.extracted_urls[0].resolved_url,page.extracted_urls[0].url);
    assert.equal(page.extracted_urls[0].resolved_http_metadata.status,302);
    assert.equal(result.state.link_resolution.requests,1);
    assert.equal(result.verification.ok,true);
    assert.deepEqual(visited,['https://www.google.com/goto?url=CAESopaque']);
  } finally {temp.close();}
});

test('pagination observes all sites first, then completes each provider with global one-second spacing',async()=>{
  const temp=tempRun(),clock=fakeClock(),calls=[];
  try {
    const result=await runSearchPagination({manifest:manifest({results_per_site:3,max_total_pages:30,max_unique_urls:30}),directory:temp.directory,
      fixture:true,clock,adapterFactory:adapterFactory({clock,calls,pages:({site,query,index})=>({items:[{href:`https://${site.id}.example/${query}/${index}`,title:'result'}],next:null,status:'content_observed'})})});
    assert.equal(result.state.pages.length,30);
    assert.deepEqual(calls.slice(0,10).map(x=>x.site),[...siteIDs.filter(id=>id!=='google'),'google']);
    assert.deepEqual(calls.slice(10,14).map(x=>x.site),['yahoo-japan','yahoo-japan','bing','bing']);
    assert.ok(result.state.pages.slice(0,10).every(row=>row.phase==='initial_pass'));
    assert.ok(result.state.pages.slice(10).every(row=>row.phase==='pagination'));
    assert.ok(result.state.pages.slice(1).every((row,i)=>row.start_interval_ms>=1000));
    assert.equal(result.state.pages[0].requested_max_results,'maximum_available; no configurable count observed in prior DOM');
    assert.equal(result.state.pages[0].observed_items_count,1);
    assert.equal(result.state.pages[0].next_progress.reason,'next_missing');
    assert.ok(result.state.pages.every(row=>row.dom_sha256));
  } finally {temp.close();}
});

test('empty and blocked pages stop or advance with explicit reasons and unchanged next DOM is recorded',async()=>{
  const temp=tempRun(),clock=fakeClock();
  try {
    const result=await runSearchPagination({manifest:manifest({results_per_site:1,max_total_pages:30,max_unique_urls:30}),directory:temp.directory,
      fixture:true,clock,adapterFactory:adapterFactory({clock,pages:({site,index,url})=>site.id==='google'
        ?{items:[{href:'https://result.example/1',title:'result'}],next:index===1?url:null,status:'content_observed'}
        :site.id==='bing'?{items:[],next:null,status:'blocked'}:{items:[],next:null,status:'empty'}})});
    const google=result.state.pages.filter(x=>x.site==='google');
    assert.equal(google.length,1);assert.equal(google[0].next_progress.reason,'next_same_url');
    assert.equal(google[0].stop_reason,'next_same_url');
    assert.equal(result.state.pages.find(x=>x.site==='bing').stop_reason,'blocked');
    assert.equal(result.state.sites.bing.pages,1);
    assert.equal(result.state.sites.google.pages,1);
  } finally {temp.close();}
});

test('candidate More Results controls count as progress only after unique organic URLs increase',async()=>{
  const temp=tempRun(),clock=fakeClock();
  try {
    const pageFactory=({site,query,clickNext})=>{
      if(site.id!=='duckduckgo') return {items:[],next:null,status:'empty'};
      return {items:clickNext?[{href:'https://result.example/1',title:'one'},{href:'https://result.example/2',title:'two'}]:[{href:'https://result.example/1',title:'one'}],
        next:null,click_available:true,click_progress:clickNext,status:'content_observed'};
    };
    const result=await runSearchPagination({manifest:manifest({results_per_site:2,max_total_pages:30,max_unique_urls:30}),directory:temp.directory,
      fixture:true,clock,adapterFactory:adapterFactory({clock,pages:pageFactory})});
    const pages=result.state.pages.filter(row=>row.site==='duckduckgo');
    assert.equal(pages.length,2);
    assert.equal(pages[0].next_progress.reason,'click_candidate_available_unverified');
    assert.equal(pages[1].next_action,'click');
    assert.equal(pages[1].next_progress.reason,'click_unique_url_increased');
    assert.equal(pages[1].next_selector_confidence,'candidate');
  } finally {temp.close();}
});

test('unconfigured sites stop only after block and gate classifiers get first chance',async()=>{
  const temp=tempRun(),clock=fakeClock();
  try {
    const configured=manifest({sites:manifest().sites.map((site,index)=>index===0?{...site,result_selectors:[],next_selectors:[]}:site),
      max_total_pages:30,max_unique_urls:30});
    const pageFactory=({site})=>site.id==='yahoo-japan'
      ?{items:[],next:null,status:'challenge',extractor_configured:false}
      :{items:[],next:null,status:'empty'};
    const result=await runSearchPagination({manifest:configured,directory:temp.directory,fixture:true,clock,
      adapterFactory:adapterFactory({clock,pages:pageFactory})});
    assert.equal(result.state.pages.find(row=>row.site==='yahoo-japan').stop_reason,'challenge');
  } finally {temp.close();}
});

test('manifest validates fixed targets, timezone, and bounded page/evidence limits',()=>{
  assert.equal(validatePaginationManifest(manifest()).sites.length,10);
  assert.throws(()=>validatePaginationManifest(manifest({results_per_site:101})),/invalid_limits/);
  assert.throws(()=>validatePaginationManifest(manifest({sites:manifest().sites.map((site,i)=>i?site:{...site,timezoneId:'Invalid/Zone'})})),/invalid_timezone/);
});
