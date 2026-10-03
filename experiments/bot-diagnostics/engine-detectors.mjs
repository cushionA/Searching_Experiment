#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {startFixture,detectorRun} from './runner.mjs';
import {Evidence,verify} from './evidence.mjs';
import {snapshotSources} from './runtime.mjs';

const here=path.dirname(fileURLToPath(import.meta.url));
const availableTools=['rebrowser-lightpanda','patchright','obscura-patched'];
const defaultTools=availableTools.slice();
function parseArgs(argv) {
  let directory,tools=defaultTools.slice();
  for(const arg of argv) {
    if(arg.startsWith('--tools=')) {
      tools=arg.slice('--tools='.length).split(',');
      if(tools.some(tool=>!availableTools.includes(tool))||new Set(tools).size!==tools.length)
        throw new Error(`invalid tools; choose unique names from ${availableTools.join(',')}`);
    } else if(arg.startsWith('--')) throw new Error(`unknown option: ${arg}`);
    else if(directory) throw new Error('provide one output directory');
    else directory=arg;
  }
  if(!directory) throw new Error('usage: node engine-detectors.mjs NEW_OUTPUT_DIRECTORY [--tools=name,name]');
  return {directory:path.resolve(directory),tools};
}

async function main() {
  const options=parseArgs(process.argv.slice(2));
  const fixture=await startFixture();
  let evidence;
  try {
    fs.mkdirSync(path.dirname(options.directory),{recursive:true});
    evidence=new Evidence(options.directory);
    evidence.save('sources.json',snapshotSources(evidence));
    evidence.save('environment.json',{started_at:new Date().toISOString(),node:process.version,
      tools:options.tools,fixture_only:true,external_requests_allowed:false});
    for(const tool of options.tools) {
      const browserOptions=tool==='rebrowser-lightpanda'?{lightpanda:{release:'1.0.0',profile:'compat'}}:{};
      await detectorRun(evidence,tool,fixture.origin,browserOptions);
    }
  } finally {await fixture.close();evidence?.flush();}
  const verification=verify(evidence.directory);
  evidence.save('verification.json',verification);
  const detectors=evidence.results.map(result=>({client:result.client,detector:result.detector,outcome:result.outcome,
    red_flags:result.red_flags||[],unassessed:result.unassessed||[],botd:result.botd?.result??null,
    unavailable_components:result.botd?.components&&Object.entries(result.botd.components)
      .filter(([,value])=>value.state<0).map(([name])=>name),
    trigger_errors:result.trigger_errors||[],page_errors:result.page_errors||[],error:result.error||null}));
  const summary={directory:options.directory,tools:options.tools,verification,
    detector_runs:detectors,execution_success:verification.ok&&detectors.length===options.tools.length*2
      &&detectors.every(result=>result.outcome==='detector_completed'&&!result.error
        &&!result.trigger_errors.length&&!result.page_errors.length)};
  fs.writeFileSync(path.join(options.directory,'summary.json'),`${JSON.stringify(summary,null,2)}\n`,{flag:'wx'});
  console.log(JSON.stringify(summary,null,2));
  if(!summary.execution_success)process.exitCode=1;
}

main().catch(error=>{console.error(`engine detector run failed: ${error.stack||error}`);process.exitCode=1;});
