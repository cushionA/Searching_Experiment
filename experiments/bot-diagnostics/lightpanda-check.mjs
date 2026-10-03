// Local capability and detector checks. This never visits a production website.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {openBrowser,startFixture,detectorRun} from './runner.mjs';
import {Evidence,verify,safeError} from './evidence.mjs';
import {here,snapshotSources,sha,deps} from './runtime.mjs';
import {tableContract} from './lightpanda-shims.test.mjs';
import {installTableCompatibility} from './lightpanda-shims.mjs';

const directory = process.argv[2];
if(!directory || process.argv.length!==3) throw new Error('Usage: lightpanda-check.mjs NEW_OUTPUT_DIRECTORY');
fs.mkdirSync(path.dirname(path.resolve(directory)),{recursive:true});
fs.mkdirSync(directory);
const fixture = await startFixture();
const results = [];
try {
  for(const profile of ['baseline','compat','probe']) {
    const options = {lightpanda:{release:'1.0.0',profile}};
    const evidence = new Evidence(path.join(directory,profile));
    evidence.save('sources.json',{...snapshotSources(evidence),
      'lightpanda-shims.test.mjs':evidence.blob(fs.readFileSync(new URL('./lightpanda-shims.test.mjs',import.meta.url)))});
    evidence.save('environment.json',{node:process.version,rebrowser_runtime_fix:process.env.REBROWSER_PATCHES_RUNTIME_FIX_MODE||'addBinding',
      package_lock_sha256:sha(fs.readFileSync(path.join(deps,'package-lock.json'))),
      detector_source:JSON.parse(fs.readFileSync(path.join(deps,'detector-source.json'))),
      fixture_only:true});
    let browser;
    const capability = {profile};
    try {
      browser = await openBrowser('rebrowser-lightpanda',options);
      capability.runtime = browser.runtime;
      // Verify preload ordering and persistence on two successive documents.
      capability.documents = [];
      for(let i=0;i<2;i++) {
        await browser.page.goto('data:text/html,<html><script>window.earlyInsertRow=typeof document.createElement("tbody").insertRow</script></html>',{waitUntil:'load'});
        capability.documents.push(await browser.page.evaluate(()=>({early:window.earlyInsertRow,
          probe:document.documentElement.getAttribute('data-lightpanda-probe')})));
        assert.equal(capability.documents.at(-1).early,profile==='baseline'?'undefined':'function');
        assert.equal(capability.documents.at(-1).probe,profile==='probe'?'loaded':null);
      }
      if(profile!=='baseline') capability.table_contract = await browser.page.evaluate(tableContract);
      // Existing implementations must be preserved, including on repeated installation.
      await browser.page.evaluate(()=>{globalThis.savedInsertRow=HTMLTableSectionElement.prototype.insertRow;});
      if(profile!=='baseline') {
        await browser.page.evaluate(installTableCompatibility);
        assert.equal(await browser.page.evaluate(()=>globalThis.savedInsertRow===HTMLTableSectionElement.prototype.insertRow),true);
      }
      const session = await browser.page.createCDPSession();
      try {
        capability.native_extension = {outcome:'loaded',result:await session.send('Extensions.loadUnpacked',
          {path:path.join(here,'extensions/observation-probe')})};
      } catch(error) {capability.native_extension={outcome:/wasn't found/.test(error.message)?'unsupported_capability':'execution_error',error:safeError(error)};}
      finally {await session.detach();}
      capability.functional_success = true;
    } catch(error) {capability.functional_success=false;capability.error=safeError(error);}
    finally {await browser?.close();}
    evidence.save('capabilities.json',capability);
    await detectorRun(evidence,'rebrowser-lightpanda',fixture.origin,options);
    const verification = verify(evidence.directory);
    evidence.save('verification.json',verification);
    results.push({profile,capability,verification,detectors:evidence.results.map(result=>({
      detector:result.detector,outcome:result.outcome,red_flags:result.red_flags,unassessed:result.unassessed,
      botd:result.botd?.result,
      unavailable_components:result.botd?.components && Object.entries(result.botd.components).filter(([,v])=>v.state<0).map(([k])=>k),
      error:result.error,trigger_errors:result.trigger_errors}))});
  }
} finally {await fixture.close();}
fs.writeFileSync(path.join(directory,'summary.json'),JSON.stringify(results,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(results,null,2));
// Baseline detector errors are observations, never detector passes. Compat must run fully.
if(results.some(row=>!row.capability.functional_success || !row.verification.ok
  || (row.profile!=='baseline' && row.detectors.some(d=>d.outcome!=='detector_completed')))) process.exitCode=1;
