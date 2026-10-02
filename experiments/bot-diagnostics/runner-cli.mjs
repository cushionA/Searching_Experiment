import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {here, repo, deps, config, proxy, sha, snapshotSources} from './runtime.mjs';
import {Evidence, verify} from './evidence.mjs';
import {startFixture, detectorRun, httpSite, browserSite} from './runner.mjs';

function selectedClients() {
  const requested = process.env.BOT_DIAGNOSTICS_CLIENTS?.split(',') || config.clients;
  if (requested.some(name => !config.clients.includes(name))) throw new Error('Unknown client');
  return requested;
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
  fs.mkdirSync(path.dirname(directory), { recursive: true });
  const lockPath = directory + '.lock';
  const lock = fs.openSync(lockPath, 'wx');
  fs.writeSync(lock, JSON.stringify({ pid: process.pid, started_at: new Date().toISOString() }));
  try {
  const evidence = new Evidence(directory, mode === 'resume-sites');
  const invocation = { started_at: new Date().toISOString(), mode, clients: selectedClients(), targets: selectedTargets().map(x => x.name),
    runner_sha256: evidence.blob(fs.readFileSync(path.join(here, 'runner.mjs'))),
    sources: snapshotSources(evidence) };
  const historyPath = path.join(directory, 'invocations.json');
  const history = fs.existsSync(historyPath) ? JSON.parse(fs.readFileSync(historyPath)) : [];
  evidence.save('invocations.json', [...history, invocation]);
  if (mode !== 'resume-sites') evidence.save('environment.json', { started_at: new Date().toISOString(), node: process.version,
    chromium: execFileSync(process.env.BOT_DIAGNOSTICS_CHROMIUM || '/usr/bin/chromium', ['--version'], { encoding: 'utf8' }).trim(),
    lightpanda: execFileSync(path.join(deps, 'lightpanda'), ['version'], { encoding: 'utf8' }).trim(),
    lightpanda_sha256: sha(fs.readFileSync(path.join(deps, 'lightpanda'))),
    detector_source: JSON.parse(fs.readFileSync(path.join(deps, 'detector-source.json'))),
    package_lock_sha256: sha(fs.readFileSync(path.join(deps, 'package-lock.json'))),
    proxy: { protocol: new URL(proxy).protocol, host: new URL(proxy).hostname, port: new URL(proxy).port },
    runs_per_cell: 1, tls_fingerprint_at_origin: 'not measured',
    limits_note: 'All HTTP requests admitted by the runner are counted, including robots and errors. Local browser fixtures are offline tests. Byte caps cover retained/charged bodies; Chromium can receive more before the response is truncated for storage.' });
  let fixture;
  try {
    if (!['sites', 'resume-sites'].includes(mode)) {
      fixture = await startFixture();
      for (const name of selectedClients()) await detectorRun(evidence, name, fixture.origin);
    }
    if (mode !== 'detectors') {
      for (const name of selectedClients()) for (const target of selectedTargets()) {
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
