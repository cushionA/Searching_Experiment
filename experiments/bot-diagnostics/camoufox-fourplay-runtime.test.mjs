import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {buildCamoufoxLaunchOptions,createCamoufoxLaunchHook,openCamoufoxFourplay} from './camoufox-fourplay-runtime.mjs';

function launchStub(options) {
  assert.equal(options.headless,false);
  assert.equal(options.os,'linux');
  assert.equal(options.humanize,false);
  assert.equal(options.geoip,false);
  assert.deepEqual(options.exclude_addons,['UBO']);
  if(options.config) assert.equal(options.config.allowAddonNewtab,true);
  return {env:{CAMOU_CONFIG_1:JSON.stringify({fonts:['A','B'],navigator:{platform:'Linux'}}),FONTCONFIG_PATH:'/tmp/fonts'},
    firefoxUserPrefs:{'privacy.resistFingerprinting':false,'browser.cache.disk.enable':false},args:['--no-remote']};
}

test('Camoufox generator settings survive without importing browser-control SDK',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'hybrid-generator-'));
  try {
    const binary=path.join(root,'camoufox-bin'); fs.writeFileSync(binary,'fixture');
    const built=await buildCamoufoxLaunchOptions({binary,loadLaunchOptions:async()=>launchStub});
    assert.equal(built.metadata.playwright_control,false);
    assert.equal(built.metadata.playwright_core_direct_import_statement,false);
    assert.equal(built.metadata.font_count,2);
    assert.equal(built.generated.env.CAMOU_CONFIG_1.includes('Linux'),true);
  } finally { fs.rmSync(root,{recursive:true,force:true}); }
});

test('launch hook carries generated config and preferences into an isolated Firefox profile',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'hybrid-launch-'));
  try {
    const profile=path.join(root,'profile'), extension=path.join(root,'extension'), launcher=path.join(root,'web-ext');
    fs.mkdirSync(profile); fs.mkdirSync(extension); fs.writeFileSync(launcher,'');
    const binary=path.join(root,'camoufox-bin'); fs.writeFileSync(binary,'');
    const generated=launchStub({headless:false,os:'linux',humanize:false,geoip:false,exclude_addons:['UBO']}); const opts={generated,binary}; let captured;
    const hook=createCamoufoxLaunchHook({launchOptions:opts,launcher,spawnProcess:(...args)=>{captured=args;return {pid:17};}});
    assert.deepEqual(hook({profile,extension}),{pid:17});
    assert.equal(captured[0],launcher);
    assert.ok(captured[1].includes(binary));
    assert.ok(captured[1].includes(extension));
    assert.match(fs.readFileSync(path.join(profile,'user.js'),'utf8'),/privacy\.resistFingerprinting/);
    assert.match(fs.readFileSync(path.join(profile,'user.js'),'utf8'),/user_pref\("privacy\.userContext\.enabled", true\)/);
    assert.equal(captured[2].env.CAMOU_CONFIG_1,generated.env.CAMOU_CONFIG_1);
  } finally { fs.rmSync(root,{recursive:true,force:true}); }
});

test('parallel wrappers pass isolated executable and extension without changing process configuration',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'hybrid-wrapper-'));
  const priorFirefox=process.env.BOT_DIAGNOSTICS_FIREFOX, priorExt=process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION;
  try {
    const openedConfigs=[];
    const gate=[];
    const runs=[0,1].map(async index=>{
      const binary=path.join(root,`camoufox-${index}`); fs.writeFileSync(binary,'fixture');
      const extension=path.join(root,`extension-${index}`), launcher=path.join(root,`web-ext-${index}`);
      fs.mkdirSync(extension); fs.writeFileSync(launcher,'');
      return openCamoufoxFourplay({binary,loadLaunchOptions:async()=>launchStub,
        extensionPath:extension,launcherPath:launcher,
        openFourplayImpl:async options=>{
          openedConfigs.push({executable:options.executable,extension:options.extension});
          await new Promise(resolve=>gate.push(resolve));
          return {runtime:{native:true},kind:'fourplay-native'};
        },spawnProcess:()=>({pid:1})});
    });
    while(gate.length<2) await new Promise(resolve=>setImmediate(resolve));
    gate.forEach(resolve=>resolve());
    const opened=await Promise.all(runs);
    assert.deepEqual(openedConfigs.map(value=>path.basename(value.executable)).sort(),['camoufox-0','camoufox-1']);
    assert.deepEqual(openedConfigs.map(value=>path.basename(value.extension)).sort(),['extension-0','extension-1']);
    assert.ok(opened.every(value=>value.runtime.browser_engine==='Camoufox'));
    assert.ok(opened.every(value=>value.runtime.browser_control==='4play WebExtension'));
    assert.ok(opened.every(value=>value.runtime.playwright_control===false));
    assert.ok(opened.every(value=>value.kind==='fourplay-native'));
    assert.equal(process.env.BOT_DIAGNOSTICS_FIREFOX,priorFirefox);
    assert.equal(process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION,priorExt);
  } finally {
    if(priorFirefox===undefined) delete process.env.BOT_DIAGNOSTICS_FIREFOX; else process.env.BOT_DIAGNOSTICS_FIREFOX=priorFirefox;
    if(priorExt===undefined) delete process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION; else process.env.BOT_DIAGNOSTICS_FOURPLAY_EXTENSION=priorExt;
    fs.rmSync(root,{recursive:true,force:true});
  }
});
