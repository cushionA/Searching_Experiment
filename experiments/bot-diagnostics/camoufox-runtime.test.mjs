import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'camoufox-runtime-test-'));
process.env.BOT_DIAGNOSTICS_CAMOUFOX_DEPS = path.join(root, 'deps');
process.env.BOT_DIAGNOSTICS_STATE = path.join(root, 'state');
process.env.HTTPS_PROXY = 'http://proxy.invalid:8080';
const browserDirectory = path.join(process.env.BOT_DIAGNOSTICS_CAMOUFOX_DEPS, 'browser');
fs.mkdirSync(browserDirectory, {recursive:true});
const browserExecutable = path.join(browserDirectory, 'camoufox-bin');
fs.writeFileSync(browserExecutable, '#!/bin/sh\nexit 0\n', {mode:0o755});
fs.writeFileSync(path.join(browserDirectory, 'version.json'), JSON.stringify({version:'152.0.4-beta.30'}));
fs.writeFileSync(path.join(process.env.BOT_DIAGNOSTICS_CAMOUFOX_DEPS, 'browser-runtime.json'), JSON.stringify({
  package:'camoufox-js@0.12.0',playwright_core:'1.60.0',browser_version:'152.0.4-beta.30',
}));

const {openCamoufox} = await import('./camoufox-runtime.mjs');

function fakeDependencies({failLaunch = false} = {}) {
  const calls = {generated:[], launch:[], closed:false};
  const page = {
    evaluate:async fn => fn.toString().includes('navigator.userAgent')
      ? 'Mozilla/5.0 Camoufox fixture'
      : ({width:1280,height:720}),
  };
  const context = {
    pages:() => [page],
    newPage:async()=>page,
    browser:()=>({version:()=> 'Firefox 152.0'}),
    close:async()=>{calls.closed=true;},
  };
  const firefox = {launchPersistentContext:async(profile, options)=>{
    calls.launch.push({profile,options});
    if (failLaunch) throw new Error('fixture launch failure');
    return context;
  }};
  return {
    calls,
    loadDependencies:async()=>({firefox,launchOptions:async options=>{
      calls.generated.push(options);
      return {generatedByCamoufox:true,env:{CAMOU_CONFIG_1:'fixture-generated-config'}};
    }}),
  };
}

test('uses isolated Firefox launch options, inherited proxy and natural browser identity', async t => {
  const deps = fakeDependencies();
  const proxyBefore = process.env.HTTPS_PROXY;
  const priorCa = process.env.BOT_DIAGNOSTICS_CA;
  const priorCodexCa = process.env.CODEX_PROXY_CERT;
  const priorCamouConfig = process.env.CAMOU_CONFIG_1;
  delete process.env.BOT_DIAGNOSTICS_CA;
  delete process.env.CODEX_PROXY_CERT;
  delete process.env.CAMOU_CONFIG_1;
  t.after(()=>{
    if (priorCa === undefined) delete process.env.BOT_DIAGNOSTICS_CA; else process.env.BOT_DIAGNOSTICS_CA=priorCa;
    if (priorCodexCa === undefined) delete process.env.CODEX_PROXY_CERT; else process.env.CODEX_PROXY_CERT=priorCodexCa;
    if (priorCamouConfig === undefined) delete process.env.CAMOU_CONFIG_1; else process.env.CAMOU_CONFIG_1=priorCamouConfig;
  });
  const browser = await openCamoufox({loadDependencies:deps.loadDependencies,runCommand:()=>{throw new Error('unexpected certutil');}});
  t.after(()=>browser.close());
  assert.equal(deps.calls.generated.length, 1);
  const generated = deps.calls.generated[0];
  assert.equal(generated.headless, true);
  assert.equal(generated.os, 'linux');
  assert.equal(generated.humanize, false);
  assert.equal(generated.geoip, false);
  assert.deepEqual(generated.exclude_addons, ['UBO']);
  assert.equal(Object.hasOwn(generated, 'ff_version'), false, 'installed browser version is detected by Camoufox');
  assert.equal(generated.executable_path, browserExecutable);
  assert.deepEqual(generated.proxy, {server:proxyBefore,bypass:'127.0.0.1,localhost'});
  assert.equal(generated.env.HTTPS_PROXY, proxyBefore);
  assert.match(generated.env.FONTCONFIG_PATH, /browser\/fontconfig\/linux$/);
  const launched = deps.calls.launch[0].options;
  assert.equal(Object.hasOwn(launched, 'timezoneId'), false);
  assert.equal(launched.executablePath, browserExecutable);
  assert.equal(launched.ignoreHTTPSErrors, false);
  assert.equal(launched.viewport, null);
  assert.deepEqual(launched.proxy, generated.proxy);
  assert.equal(launched.env.CAMOU_CONFIG_1, 'fixture-generated-config', 'generated Camoufox fingerprint config survives the Playwright launch');
  assert.equal(browser.kind, 'playwright');
  assert.equal(browser.runtime.engine, 'firefox');
  assert.equal(browser.runtime.version, '152.0.4-beta.30');
  assert.equal(browser.runtime.camoufox_js, '0.12.0');
  assert.equal(browser.runtime.playwright_core, '1.60.0');
  assert.equal(browser.runtime.budgeted_navigation, false);
  assert.equal(browser.runtime.proxy_ca_trusted, false);
  assert.equal(browser.ua, 'Mozilla/5.0 Camoufox fixture');
  assert.deepEqual(browser.runtime.viewport, {width:1280,height:720});
  assert.equal(process.env.HTTPS_PROXY, proxyBefore, 'launch must not mutate the inherited proxy');
  assert.equal(process.env.CAMOU_CONFIG_1, undefined, 'generated fingerprint config stays scoped to the browser process');
});

test('imports an explicit fixture CA into the disposable Firefox profile', async t => {
  const caFile = path.join(root, 'fixture-ca.pem');
  fs.writeFileSync(caFile, '-----BEGIN CERTIFICATE-----\nfixture-ca\n-----END CERTIFICATE-----\n');
  const previous = process.env.BOT_DIAGNOSTICS_CA;
  process.env.BOT_DIAGNOSTICS_CA = caFile;
  t.after(()=>{ if (previous === undefined) delete process.env.BOT_DIAGNOSTICS_CA; else process.env.BOT_DIAGNOSTICS_CA=previous; });
  const deps = fakeDependencies();
  const certutilCalls = [];
  const browser = await openCamoufox({fixture:true,loadDependencies:deps.loadDependencies,
    runCommand:(command,args)=>certutilCalls.push({command,args}),timezoneId:'Asia/Tokyo'});
  const profile = deps.calls.launch[0].profile;
  t.after(()=>browser.close());
  assert.equal(browser.runtime.proxy_ca_trusted, true);
  assert.equal(deps.calls.launch[0].options.timezoneId, 'Asia/Tokyo');
  assert.deepEqual(certutilCalls.map(call=>call.args.slice(0,2)), [['-N','--empty-password'],['-A','-n']]);
  assert.equal(certutilCalls[0].args[2], '-d');
  assert.equal(certutilCalls[0].args[3], `sql:${profile}`);
  assert.equal(certutilCalls[1].args[certutilCalls[1].args.indexOf('-t')+1], 'C,,');
  assert.equal(fs.existsSync(profile), true);
  assert.equal(fs.readdirSync(profile).some(name=>name.startsWith('.proxy-ca-')), false,
    'temporary PEM copies are removed after certutil imports them');
});

test('fixture mode ignores the environment proxy CA unless an explicit fixture CA is set', async t => {
  const previous = process.env.BOT_DIAGNOSTICS_CA;
  delete process.env.BOT_DIAGNOSTICS_CA;
  process.env.CODEX_PROXY_CERT = path.join(root, 'must-not-import.pem');
  t.after(()=>{
    if (previous === undefined) delete process.env.BOT_DIAGNOSTICS_CA; else process.env.BOT_DIAGNOSTICS_CA=previous;
    delete process.env.CODEX_PROXY_CERT;
  });
  const deps = fakeDependencies();
  let commands = 0;
  const browser = await openCamoufox({fixture:true,loadDependencies:deps.loadDependencies,
    runCommand:()=>{commands++;}});
  t.after(()=>browser.close());
  assert.equal(commands, 0);
  assert.equal(browser.runtime.proxy_ca_trusted, false);
});

test('rejects unsupported extensions before loading optional dependencies', async () => {
  let loads = 0;
  await assert.rejects(openCamoufox({extensions:['/tmp/extension'],loadDependencies:async()=>{loads++;}}),
    /unsupported_capability:extensions/);
  assert.equal(loads, 0);
});

test('cleans its temporary profile when Firefox startup fails', async () => {
  const deps = fakeDependencies({failLaunch:true});
  await assert.rejects(openCamoufox({loadDependencies:deps.loadDependencies,runCommand:()=>{throw new Error('unexpected certutil');}}),
    /fixture launch failure/);
  assert.equal(deps.calls.closed, false);
  assert.equal(fs.existsSync(deps.calls.launch[0].profile), false);
});

test.after(()=>fs.rmSync(root,{recursive:true,force:true}));
