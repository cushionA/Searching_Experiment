import test from 'node:test';
import assert from 'node:assert/strict';
import {waitForDOMStability} from './dom-stability.mjs';
const snapshot=text=>({url:'http://fixture/',title:'fixture',text,links:[{href:'http://example.test/',text:'query'}]});
function clock(read) {let time=0;return {read,now:()=>time,sleep:async ms=>{time+=ms;}};}
test('late DOM resets stability timer and preserves relevant candidates',async()=>{
 let n=0;const result=await waitForDOMStability(null,{timeoutMs:1000,stableMs:200,pollMs:100,minTextChars:5,terms:['query']},clock(async()=>snapshot(++n<3?'query pending':'query ready')));
 assert.equal(result.outcome,'ready');assert.equal(result.elapsed_ms,400);assert.equal(result.samples.length,5);
});
test('challenge stops immediately without extra reads or actions',async()=>{
 const result=await waitForDOMStability(null,{timeoutMs:1000,stableMs:200,pollMs:100,minTextChars:5,terms:['query']},clock(async()=>snapshot('Verify that you are human')));
 assert.equal(result.outcome,'stopped_challenge_observed');assert.equal(result.elapsed_ms,0);assert.equal(result.samples.length,1);
});
test('stable irrelevant content times out instead of declaring success',async()=>{
 const result=await waitForDOMStability(null,{timeoutMs:500,stableMs:200,pollMs:100,minTextChars:5,terms:['missing']},clock(async()=>snapshot('query ready')));
 assert.equal(result.outcome,'timeout');assert.equal(result.elapsed_ms,500);
});
test('a stalled browser read is bounded by the deadline',async()=>{
 const start=performance.now();const result=await waitForDOMStability(null,{timeoutMs:20,stableMs:5,pollMs:5,minTextChars:0}, {read:()=>new Promise(()=>{})});
 assert.equal(result.outcome,'timeout');assert.ok(performance.now()-start<1000);
});
test('generic policy without query terms can accept stable meaningful text',async()=>{
 const result=await waitForDOMStability(null,{timeoutMs:500,stableMs:200,pollMs:100,minTextChars:5},clock(async()=>snapshot('plain text')));
 assert.equal(result.outcome,'ready');assert.equal(result.elapsed_ms,200);
});
