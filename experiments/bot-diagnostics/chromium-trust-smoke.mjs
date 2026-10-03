import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import https from 'node:https';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {openBrowser} from './runner.mjs';
import {Evidence,verify,safeError} from './evidence.mjs';
import {snapshotSources} from './runtime.mjs';

if(process.argv.length!==3) throw new Error('Usage: node chromium-trust-smoke.mjs NEW_DIRECTORY');
const directory=path.resolve(process.argv[2]);
const evidence=new Evidence(directory);
evidence.save('sources.json',snapshotSources(evidence));
evidence.flush();
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'chromium-tls-check-'));
let server,browser,requests=0;
try {
  const key=path.join(temporary,'key.pem'),cert=path.join(temporary,'cert.pem');
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,
    '-out',cert,'-days','1','-subj','/CN=localhost',
    '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'});
  server=https.createServer({key:fs.readFileSync(key),cert:fs.readFileSync(cert)},(_req,res)=>{
    requests++;res.end('untrusted certificate must prevent this response');
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  // Run the real external-browser preflight, but connect only to this local
  // negative-control server. No test CA is imported into the trust store.
  browser=await openBrowser('patchright');
  const url=`https://127.0.0.1:${server.address().port}/`;
  const record=evidence.reserve('patchright/untrusted-local-certificate',url,'tls_negative_control');
  let rejection;
  try {await browser.page.goto(url,{timeout:10000,waitUntil:'load'});}
  catch(error) {rejection=error;}
  if(rejection) evidence.fail(record,rejection);
  else evidence.fail(record,new Error('unexpected_trust_of_self_signed_certificate'));
  assert.match(rejection?.message||'',/ERR_CERT_AUTHORITY_INVALID/);
  assert.equal(requests,0);
  evidence.result({client:'patchright',outcome:'tls_negative_control_passed',
    runtime:browser.runtime,certificate_verification:true,untrusted_certificate_rejected:true,
    server_http_requests:requests,certificate_imports:0,error:safeError(rejection)});
} finally {
  try {await browser?.close();}
  finally {
    if(server?.listening) await new Promise(resolve=>server.close(resolve));
    fs.rmSync(temporary,{recursive:true,force:true});
  }
}
const verification=verify(directory);
evidence.save('verification.json',verification);
assert.equal(verification.ok,true);
console.log(JSON.stringify({directory,verification,result:evidence.results.at(-1)},null,2));
