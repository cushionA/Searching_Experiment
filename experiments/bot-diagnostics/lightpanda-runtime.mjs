import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {spawn, execFileSync} from 'node:child_process';
import {once} from 'node:events';
import {createHash} from 'node:crypto';
import {deps, repo, state, require, config, limits, proxy, sleep} from './runtime.mjs';
import {installTableCompatibility, installObservationProbe} from './lightpanda-shims.mjs';

export const STABLE_LIGHTPANDA = Object.freeze({
  version:'1.0.0', sha256:'aa5a4b8ed53d1e38b3c73f5b2647d0a84a82e6744557f45f9a9c85858aa031c3',
});
export function validateLightpanda(options) {
  if (!options || typeof options !== 'object' || Array.isArray(options)
    || Object.keys(options).some(key=>!['release','profile'].includes(key))
    || options.release !== '1.0.0' || !['baseline','compat','probe'].includes(options.profile)) {
    throw new Error('invalid_lightpanda_profile');
  }
  return options;
}

/** Opt-in release/profile. The old nightly path remains the default for existing runs. */
export async function openLightpanda(options = null, {identification = true,timezoneId} = {}) {
  if (options !== null) validateLightpanda(options);
  const stable = options !== null;
  const executable = stable ? path.join(repo,'.deps/lightpanda') : path.join(deps,'lightpanda');
  // The executable is large: hashing it as one Buffer overwhelms the driver RSS.
  const hasher = createHash('sha256');
  for await (const chunk of fs.createReadStream(executable)) hasher.update(chunk);
  const digest = hasher.digest('hex');
  const expected = stable ? STABLE_LIGHTPANDA.sha256
    : JSON.parse(fs.readFileSync(new URL('./versions.json',import.meta.url))).lightpanda_sha256;
  if (digest !== expected) throw new Error('Lightpanda hash mismatch');
  const profile = options?.profile || 'baseline';
  const fixMode = process.env.REBROWSER_PATCHES_RUNTIME_FIX_MODE || 'addBinding';
  if (!['0','addBinding','alwaysIsolated','enableDisable'].includes(fixMode)) throw new Error('invalid_rebrowser_runtime_fix');
  fs.mkdirSync(state,{recursive:true});
  const data = fs.mkdtempSync(path.join(state,'lightpanda-'));
  const server = http.createServer();
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const port = server.address().port;
  await new Promise(resolve=>server.close(resolve));
  const args = ['serve','--host','127.0.0.1','--port',String(port),
    ...(proxy ? ['--http-proxy',proxy] : []),...(identification ? ['--user-agent-suffix',config.identification] : []),
    '--http-max-response-size',String(limits.bytes_per_response),'--http-timeout',String(limits.navigation_timeout_ms),
    '--http-max-concurrent','1','--log-level','warn','--ca-cert',process.env.SSL_CERT_FILE || '/etc/ssl/certs/ca-certificates.crt'];
  const child = spawn(executable,args,{env:{...process.env,XDG_DATA_HOME:data,
    LIGHTPANDA_DISABLE_TELEMETRY:'true',LIGHTPANDA_DISABLE_CORE_DUMP:'true'},stdio:['ignore','ignore','pipe']});
  const log = [];
  let spawnError, browser, closed = false;
  child.on('error',error=>{spawnError=error;});
  child.stderr.on('data',chunk=>{log.push(chunk.toString().slice(-2000));if(log.length>20)log.shift();});
  const close = async()=>{
    if (closed) return;
    closed = true;
    try { await browser?.disconnect(); }
    finally {
      if (child.exitCode === null && child.signalCode === null && !spawnError) {
        const exited = once(child,'exit');
        child.kill('SIGTERM');
        const killTimer = setTimeout(()=>child.kill('SIGKILL'),2000);
        try { await exited; } finally { clearTimeout(killTimer); }
      }
      fs.rmSync(data,{recursive:true,force:true});
    }
  };
  try {
    const puppeteer = require('rebrowser-puppeteer-core');
    for(let i=0;i<30;i++) {
      if(spawnError) throw spawnError;
      if(child.exitCode!==null || child.signalCode!==null) throw new Error('Lightpanda exited: '+log.join('').slice(-1200));
      try {browser=await puppeteer.connect({browserWSEndpoint:`ws://127.0.0.1:${port}`,defaultViewport:null,protocolTimeout:12000});break;}
      catch(error){if(i===29)throw error;await sleep(100);}
    }
    const newContext = async()=>{
      const context=await browser.createBrowserContext();
      try {
        const page=await context.newPage();
        if(timezoneId) try {await page.emulateTimezone(timezoneId);} catch {}
        if(profile!=='baseline') await page.evaluateOnNewDocument(installTableCompatibility);
        if(profile==='probe') await page.evaluateOnNewDocument(installObservationProbe);
        return {context,page};
      } catch(error) {await context.close();throw error;}
    };
    const {context,page}=await newContext();
    const ua = await page.evaluate(()=>navigator.userAgent);
    const handle={browser,context,page,ua,kind:'puppeteer',child,log,close,
      runtime:{executable,version:execFileSync(executable,['version'],{encoding:'utf8'}).trim(),sha256:digest,
        profile,rebrowser_runtime_fix:fixMode,extensions:false,
        injected_scripts:profile==='baseline'?[]:profile==='compat'?['table-insertion']:['table-insertion','observation-marker']}};
    handle.replaceContext=async(cookies=[])=>{
      await handle.context.close();
      Object.assign(handle,await newContext());
      if(cookies.length) await handle.context.setCookie(...cookies);
    };
    return handle;
  } catch(error) {await close();throw error;}
}
