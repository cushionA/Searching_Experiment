import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {here, repo, deps, config, proxy, sha, snapshotSources} from './runtime.mjs';
import {Evidence, verify} from './evidence.mjs';
import {startFixture, detectorRun, httpSite, browserSite} from './runner.mjs';

export function selectedClients() {
  const explicit=process.env.BOT_DIAGNOSTICS_CLIENTS;
  const requested=explicit===undefined?config.clients:explicit.split(',');
  const allowed=new Set([...config.clients,...(explicit===undefined?[]:['4play'])]);
  if (requested.some(name => !allowed.has(name))) throw new Error('Unknown client');
  return requested;
}

export function runtimeMetadataKind(clients) {
  return clients.length>0&&clients.every(name=>name==='4play')?'remote_4play':'local_browser_engines';
}

function selectedTargets() {
  const requested = process.env.BOT_DIAGNOSTICS_TARGETS?.split(',') || config.targets.map(x => x.name);
  if (requested.some(name => !config.targets.some(x => x.name === name))) throw new Error('Unknown target');
  return config.targets.filter(x => requested.includes(x.name));
}

export async function main() {
  const args = process.argv.slice(2);
  if (args[0] === 'verify') {
    const result = verify(path.resolve(args[1]));
    console.log(JSON.stringify(result, null, 2));
    if (!result.ok) process.exitCode = 1;
    return;
  }
  if (!proxy) throw new Error('This runner requires the inherited HTTPS proxy; no direct-network fallback');
  const mode = args[0] || 'all';
  if (!['all', 'detectors', 'sites', 'resume-sites'].includes(mode)) throw new Error('Usage: runner.mjs [all|detectors|sites|resume-sites|verify] RUN_DIRECTORY');
  const directory = path.resolve(args[1] || path.join(repo, 'lab-runs/bot-diagnostics-001'));
  const clients=selectedClients();
  fs.mkdirSync(path.dirname(directory), { recursive: true });
  const lockPath = directory + '.lock';
  const lock = fs.openSync(lockPath, 'wx');
  fs.writeSync(lock, JSON.stringify({ pid: process.pid, started_at: new Date().toISOString() }));
  try {
  const evidence = new Evidence(directory, mode === 'resume-sites', {policy:'browser_observation',
    authorization:'Bot diagnostics CLI uses normal browser observation; grounding constraints are scoped to grounding workflows.'});
  const invocation = { started_at: new Date().toISOString(), mode, clients, targets: selectedTargets().map(x => x.name),
    runner_sha256: evidence.blob(fs.readFileSync(path.join(here, 'runner.mjs'))),
    sources: snapshotSources(evidence), execution_policy:evidence.policy };
  const historyPath = path.join(directory, 'invocations.json');
  const history = fs.existsSync(historyPath) ? JSON.parse(fs.readFileSync(historyPath)) : [];
  evidence.save('invocations.json', [...history, invocation]);
  if (mode !== 'resume-sites') {
    const environment={started_at:new Date().toISOString(),node:process.version,
      proxy:{protocol:new URL(proxy).protocol,host:new URL(proxy).hostname,port:new URL(proxy).port},
      runs_per_cell:1,tls_fingerprint_at_origin:'not measured',
      limits_note:'Browser observation records requests and stored response bodies without legacy request/byte caps or robots gates. Local browser fixtures are offline tests.'};
    if(runtimeMetadataKind(clients)==='remote_4play') {
      environment.browser_backend={name:'4play bridge',browser_engine:'Firefox',runtime_source:'adapter_setup evidence'};
    } else {
      Object.assign(environment,{chromium:execFileSync(process.env.BOT_DIAGNOSTICS_CHROMIUM || '/usr/bin/chromium', ['--version'], { encoding: 'utf8' }).trim(),
        lightpanda:execFileSync(path.join(deps, 'lightpanda'), ['version'], { encoding: 'utf8' }).trim(),
        lightpanda_sha256:sha(fs.readFileSync(path.join(deps, 'lightpanda'))),
        detector_source:JSON.parse(fs.readFileSync(path.join(deps, 'detector-source.json'))),
        package_lock_sha256:sha(fs.readFileSync(path.join(deps, 'package-lock.json')))});
      if(clients.includes('4play')) environment.remote_browser_backend={name:'4play bridge',browser_engine:'Firefox',runtime_source:'adapter_setup evidence'};
    }
    evidence.save('environment.json',environment);
  }
  let fixture;
  try {
    if (!['sites', 'resume-sites'].includes(mode)) {
      if(clients.some(name=>name!=='4play')) fixture = await startFixture();
      const origin=fixture?.origin||'http://127.0.0.1';
      for (const name of clients) await detectorRun(evidence, name, origin);
    }
    if (mode !== 'detectors') {
      for (const name of clients) for (const target of selectedTargets()) {
        if (['wreq-js', 'impit'].includes(name)) await httpSite(evidence, name, target);
        else await browserSite(evidence, name, target);
        console.log(JSON.stringify(evidence.results.at(-1), (key, value) => ['blocked_requests', 'page_errors', 'visible_text_prefix'].includes(key) ? undefined : value));
      }
    }
  } finally { await fixture?.close(); evidence.flush(); }
  const verification = verify(directory);
  evidence.save('verification.json', verification);
  console.log(JSON.stringify({ directory, verification }));
  } finally { fs.closeSync(lock); fs.unlinkSync(lockPath); }
}
