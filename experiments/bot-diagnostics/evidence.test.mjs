import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Evidence,verify} from './evidence.mjs';

function makeRun(options={}) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'bot-evidence-test-'));
  const directory=path.join(root,'run');
  const evidence=new Evidence(directory,false,{policy:'browser_observation',authorization:'offline evidence batching test',...options});
  const counts={ledger:0,results:0};
  const save=evidence.save.bind(evidence);
  evidence.save=(name,value)=>{if(name==='ledger.json')counts.ledger++;if(name==='results.json')counts.results++;return save(name,value);};
  return {root,directory,evidence,counts,close:()=>fs.rmSync(root,{recursive:true,force:true})};
}
function sample(evidence,index) {
  const record=evidence.reserve('fixture/site','http://127.0.0.1:4000/search?q='+index,'main_document');
  evidence.finish(record,200,{'content-type':'text/html'},Buffer.from(`<title>${index}</title>`));
  evidence.result({client:'fixture',target:'site',http_status:200,index});
}

test('timed coalescing reduces disk flushes and interval expiry persists both files',async()=>{
  const run=makeRun({flush_interval_ms:15});
  try {
    for(let index=0;index<40;index++) sample(run.evidence,index);
    assert.equal(run.counts.ledger,0);
    assert.equal(run.counts.results,0);
    await new Promise(resolve=>setTimeout(resolve,40));
    assert.equal(run.counts.ledger,1);
    assert.equal(run.counts.results,1);
    assert.equal(JSON.parse(fs.readFileSync(path.join(run.directory,'ledger.json'),'utf8')).records.length,40);
    assert.equal(JSON.parse(fs.readFileSync(path.join(run.directory,'results.json'),'utf8')).length,40);
    assert.equal(verify(run.directory).ok,true);
  } finally {run.close();}
});

test('force flush cancels the timer and persists complete ledger/results for verify',()=>{
  const run=makeRun({flush_interval_ms:10000});
  try {
    sample(run.evidence,1);
    assert.equal(run.counts.ledger,0);
    run.evidence.flush(true);
    assert.equal(run.counts.ledger,1);
    assert.equal(run.counts.results,1);
    const ledger=JSON.parse(fs.readFileSync(path.join(run.directory,'ledger.json'),'utf8'));
    const results=JSON.parse(fs.readFileSync(path.join(run.directory,'results.json'),'utf8'));
    assert.equal(ledger.records[0].status,200);
    assert.equal(results.length,1);
    assert.equal(verify(run.directory).ok,true);
  } finally {run.close();}
});

test('flush interval zero retains immediate write behavior',()=>{
  const run=makeRun();
  try {
    sample(run.evidence,1);
    assert.equal(run.counts.ledger,3);
    assert.equal(run.counts.results,3);
    assert.equal(verify(run.directory).ok,true);
  } finally {run.close();}
});
