import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Evidence,verify} from './runner.mjs';
import {ToolAdapter} from './adapters.mjs';

const here=path.dirname(fileURLToPath(import.meta.url));
const output=process.argv[2]&&path.resolve(process.argv[2]);
if(!output) throw new Error('Usage: node experiments/bot-diagnostics/fourplay-smoke.mjs NEW_OUTPUT_DIRECTORY');
if(fs.existsSync(output)) throw new Error('output_directory_already_exists');

const origin='http://127.0.0.1:3000';
const home=`${origin}/fixture`;
const next=`${origin}/fixture/next`;
const evidence=new Evidence(output,false,{policy:'browser_observation',authorization:'Local 4play fixture smoke test'});
const adapter=new ToolAdapter({tool:'4play',fixture:true,evidence,
  site:{id:'fourplay-fixture',origins:[origin],links:{home,targets:[next]}},
  profile:{id:'baseline',extensions:[]},
  options:{captureScreenshots:false,observeMs:100}});

function verifyNavigation(url,result) {
  assert.equal(result.outcome,'content_observed',`${url}: ${result.outcome}`);
  assert.equal(result.http_status,200,`${url}: expected HTTP 200`);
  assert.equal(result.final_url,url);
  assert.ok(Number.isInteger(result.main_record),`${url}: main document response was not captured`);
  const record=evidence.records.find(item=>item.index===result.main_record);
  assert.equal(record?.status,200);
  assert.equal(record?.resource_type,'main_frame');
  assert.ok(record?.body_sha256,`${url}: response body was not saved`);
  assert.ok(result.dom_sha256,`${url}: DOM was not saved`);
  const dom=fs.readFileSync(path.join(evidence.directory,'blobs',result.dom_sha256),'utf8');
  assert.match(dom,/javascript executed/);
}

try {
  await adapter.open();
  verifyNavigation(home,await adapter.goto(home));
  verifyNavigation(next,await adapter.followLink(next));
} finally {
  await adapter.close();
}
evidence.flush(true);
const verification=verify(output);
assert.equal(verification.ok,true,JSON.stringify(verification));
evidence.save('verification.json',verification);
console.log(JSON.stringify({output,verification,limitations:['response_headers_not_exposed',
  'request_and_failed_request_coverage_unavailable','referrer_not_preserved_between_tabs']},null,2));
