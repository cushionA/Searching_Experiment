import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createHash} from 'node:crypto';
import {repo, here, state, require, proxy, sleep, limits} from './runtime.mjs';

async function hashFile(filename) {
  const hasher = createHash('sha256');
  for await (const chunk of fs.createReadStream(filename)) hasher.update(chunk);
  return hasher.digest('hex');
}

const variants = {
  obscura: ['default', 'ca649bbd80167e457e7ebf615c5155b7d4fd1984c5ad2bd755253ee1a133e34d',
    'c4a6c447c97368691b58226f79dfd082061a6f1ede3b670d7c62c6da8a8b62c2'],
  'obscura-stealth': ['stealth', '777542f2e3ab9a426e59f8797a442ad591464daca3ac151b42b9df683d90b803',
    'd0c00df34107aecf3fa130f8d5156309ae3f97be072bb1395287a7634370adc0'],
  'obscura-no-render': ['no-render', 'ee2e8721ce4e589f127e06640334831bc99c78e62b68f2b473c7426fd55db364',
    'e2b9fe6bfd6557de73f46083806730be2d4dfeffa6ae97a088aeb3c10feb2f47'],
};

export async function openObscura(name, {fixture = false} = {}) {
  const patched = name === 'obscura-patched';
  if (!patched && !Object.hasOwn(variants,name)) throw new Error('invalid_obscura_variant');
  const directory = patched ? path.join(repo,'.deps/obscura-patched')
    : path.join(repo,'.deps/obscura-0.2.3',variants[name][0]);
  const build = patched ? JSON.parse(fs.readFileSync(path.join(directory,'build.json'),'utf8')) : null;
  if (patched && (build.upstream_commit !== '1a3169da276d7720732c7b20535474942917fb83'
    || build.patch_sha256 !== await hashFile(path.join(here,'patches/obscura-0.2.3-interception.patch'))
    || build.features !== 'no-default-features')) throw new Error('Obscura custom build provenance mismatch');
  const [variant,...hashes] = patched ? ['patched-no-render',build.binaries.obscura,
    build.binaries['obscura-worker']] : variants[name];
  for (const [i,filename] of ['obscura','obscura-worker'].entries()) {
    if (await hashFile(path.join(directory,filename)) !== hashes[i]) throw new Error(`Obscura hash mismatch: ${filename}`);
  }
  fs.mkdirSync(state,{recursive:true});
  const storage = fs.mkdtempSync(path.join(state,'obscura-'));
  const portServer = http.createServer();
  await new Promise((resolve,reject)=>{portServer.once('error',reject);portServer.listen(0,'127.0.0.1',resolve);});
  const port = portServer.address().port;
  await new Promise(resolve=>portServer.close(resolve));
  const executable = path.join(directory,'obscura');
  const args = ['serve','--host','127.0.0.1','--port',String(port),'--workers','1',
    '--max-connections','1','--storage-dir',storage,'--quiet',
    ...(fixture?['--allow-private-network']:[]),
    ...(proxy && !fixture?['--proxy',proxy]:[]),...(variant==='stealth'?['--stealth']:[])];
  const child = spawn(executable,args,{env:{...process.env},stdio:['ignore','ignore','pipe']});
  const log = [];
  let browser,spawnError,closed=false;
  child.on('error',error=>{spawnError=error;});
  child.stderr.on('data',chunk=>{log.push(chunk.toString().slice(-2000));if(log.length>20)log.shift();});
  const close = async()=>{
    if(closed) return;
    closed=true;
    try {await browser?.close();}
    finally {
      if(child.exitCode===null && child.signalCode===null && !spawnError) {
        const exited=once(child,'exit');
        child.kill('SIGTERM');
        const timer=setTimeout(()=>child.kill('SIGKILL'),2000);
        try {await exited;} finally {clearTimeout(timer);}
      }
      fs.rmSync(storage,{recursive:true,force:true});
    }
  };
  try {
    // Wait using HTTP discovery, which does not consume the single CDP slot.
    for(let attempt=0;attempt<100;attempt++) {
      if(spawnError) throw spawnError;
      if(child.exitCode!==null || child.signalCode!==null) throw new Error('Obscura exited: '+log.join('').slice(-1200));
      try {
        const response=await fetch(`http://127.0.0.1:${port}/json/version`,{signal:AbortSignal.timeout(500)});
        if(response.ok) break;
      } catch {}
      if(attempt===99) throw new Error('Obscura CDP startup timeout');
      await sleep(100);
    }
    // Obscura replies to Page.navigate after loading, unlike Chromium's early
    // acknowledgement. Keep the CDP deadline outside the navigation window.
    const protocolTimeout=Math.max(12000,limits.navigation_timeout_ms+limits.observe_ms+5000);
    if (patched) browser=await require('puppeteer-core').connect({browserWSEndpoint:`ws://127.0.0.1:${port}`,
      defaultViewport:null,protocolTimeout});
    else browser=await require('playwright').chromium.connectOverCDP(`ws://127.0.0.1:${port}`,{timeout:12000});
    const probe=patched ? await browser.createBrowserContext() : await browser.newContext();
    const probePage=await probe.newPage();
    // Obscura 0.2.3 ignores Playwright's context userAgent option. Report the
    // observed native value instead of claiming that an override was applied.
    const ua=await probePage.evaluate(()=>navigator.userAgent);
    await probe.close();
    const newContext=async(cookies=[])=>{
      const context=patched ? await browser.createBrowserContext()
        : await browser.newContext({userAgent:ua,serviceWorkers:'block',ignoreHTTPSErrors:false});
      try {
        if(cookies.length) {
          if(patched) await context.setCookie(...cookies);
          else await context.addCookies(cookies);
        }
        const page=await context.newPage();
        return {context,page};
      } catch(error) {await context.close();throw error;}
    };
    const handle={browser,...await newContext(),ua,kind:patched?'puppeteer':'playwright',child,log,close,
      runtime:{executable,version:patched?build.reported_version:'0.2.3',
        ...(patched?{upstream_tag:'v0.2.3',upstream_commit:build.upstream_commit,patch_sha256:build.patch_sha256}:{}),
        cdp_reported_version:await browser.version(),variant,sha256:hashes[0],
        worker_sha256:hashes[1],driver:patched?'puppeteer-core@24.8.1':'playwright.connectOverCDP',stealth:variant==='stealth',
        screenshots:!patched&&variant!=='no-render',budgeted_navigation:patched,user_agent_override:false,
        ...(patched?{interception_scope:'unmodified GET continue/fail; every HTTP redirect',
          redirect_chain_reporting:false,document_request_limit_per_observation:limits.max_redirects+1,
          async_cdp_wait_with_interception:false,injected_scripts:[],protocol_timeout_ms:protocolTimeout}:{}),
        workers:1,max_connections:1}};
    handle.replaceContext=async(cookies=[])=>{
      await handle.context.close();
      Object.assign(handle,await newContext(cookies));
    };
    return handle;
  } catch(error) {await close();throw error;}
}
