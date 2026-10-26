#!/usr/bin/env node
/** Run a URL in the existing browser runtime with local image classification. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {openBrowser} from '../bot-diagnostics/runner.mjs';
import {LocalVisualClient, runVisualChallenge} from './local_visual_browser.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../..');
const digest = filename => crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');

export async function main(args = process.argv.slice(2)) {
  const {values} = parseArgs({args, options: {
    help: {type: 'boolean', short: 'h'}, url: {type: 'string'},
    'model-root': {type: 'string'}, 'output-dir': {type: 'string'},
    python: {type: 'string', default: path.join(repo, '.deps/local-visual-venv/bin/python')},
    headful: {type: 'boolean', default: false}, device: {type: 'string', default: 'cpu'},
    threads: {type: 'string', default: '2'},
    'max-attempts': {type: 'string', default: '20'}, 'max-rounds': {type: 'string', default: '20'},
    'timeout-ms': {type: 'string', default: '120000'},
    'refresh-timeout-ms': {type: 'string', default: '10000'},
    'clip-threshold': {type: 'string', default: '0.01'},
    'confidence-threshold': {type: 'string', default: '0.5'},
    'coverage-threshold': {type: 'string', default: '0.1'},
  }});
  if (values.help) {
    console.log('Usage: node run_local_visual_browser.mjs --url HTTPS_URL --model-root DIR --output-dir NEW_DIR\n'
      + 'Options: --python PATH --headful --device cpu|cuda --threads 2 --max-attempts 20\n'
      + '         --max-rounds 20 --timeout-ms 120000 --refresh-timeout-ms 10000\n'
      + '         --clip-threshold 0.01 --confidence-threshold 0.5 --coverage-threshold 0.1\n'
      + 'HTTP is supported for loopback fixtures. Browser runtime: playwright-baseline.');
    return 0;
  }
  if (!values.url || !values['model-root'] || !values['output-dir']) throw new Error('url, model-root, output-dir are required');
  const url = new URL(values.url);
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.username || url.password || !(url.protocol === 'https:' || loopback && url.protocol === 'http:')) {
    throw new Error('Use HTTPS or a loopback HTTP fixture, without URL credentials');
  }
  if (!['cpu', 'cuda'].includes(values.device)) throw new Error('device must be cpu or cuda');
  const numbers = {};
  for (const name of ['threads', 'max-attempts', 'max-rounds', 'timeout-ms', 'refresh-timeout-ms']) {
    const value = Number(values[name]);
    if (!Number.isInteger(value) || value <= 0) throw new Error(name + ' must be a positive integer');
    numbers[name] = value;
  }
  for (const name of ['clip-threshold', 'confidence-threshold', 'coverage-threshold']) {
    const value = Number(values[name]);
    if (!Number.isFinite(value) || name !== 'clip-threshold' && (value < 0 || value > 1)) throw new Error('Invalid ' + name);
    numbers[name] = value;
  }
  const directory = path.resolve(values['output-dir']);
  const modelRoot = path.resolve(values['model-root']);
  const modelManifestSha = digest(path.join(modelRoot, 'manifest.json'));
  if (fs.existsSync(directory)) throw new Error('Refusing to overwrite an existing output directory');
  fs.mkdirSync(directory, {recursive: true});
  const started = Date.now();
  const protocol = {status: 'running', url: url.href, model_root: modelRoot,
    model_manifest_sha256: modelManifestSha, browser: 'playwright-baseline', headful: values.headful,
    device: values.device, ...numbers, node: process.version,
    inference_api: false, inference_network: 'disabled in separate local Python worker',
    browser_network: 'normal browser runtime, inherited proxy and TLS validation',
    source_sha256: Object.fromEntries(['local_visual_browser.mjs', 'run_local_visual_browser.mjs',
      'local_visual_worker.py', 'local_visual.py'].map(name => [name, digest(path.join(here, name))]))};
  const writeProtocol = () => fs.writeFileSync(path.join(directory, 'protocol.json'), JSON.stringify(protocol, null, 2) + '\n');
  writeProtocol();
  const client = new LocalVisualClient({python: values.python, modelRoot, imageRoot: directory,
    device: values.device, threads: numbers.threads, clipThreshold: numbers['clip-threshold'],
    confidenceThreshold: numbers['confidence-threshold'], coverageThreshold: numbers['coverage-threshold']});
  let browser, result, interrupted = false, expired = false, rejectDeadline;
  const deadlinePromise = new Promise((_, reject) => { rejectDeadline = reject; });
  const deadlineTimer = setTimeout(() => {
    expired = true;
    const error = new Error('Overall browser run deadline exceeded');
    client.fail(error);
    rejectDeadline(error);
    browser?.close().catch(() => {});
  }, numbers['timeout-ms']);
  const interrupt = () => {
    interrupted = true;
    client.fail(new Error('Interrupted'));
    rejectDeadline(new Error('Interrupted'));
    browser?.close().catch(() => {});
  };
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);
  try {
    const launch = openBrowser('playwright-baseline', {fixture: loopback, headful: values.headful, profile: 'browser_observation'});
    launch.then(handle => {
      if (expired || interrupted) handle.close().catch(() => {});
    }, () => {});
    browser = await Promise.race([launch, deadlinePromise]);
    protocol.runtime = browser.runtime;
    writeProtocol();
    const networkFile = path.join(directory, 'browser-network.jsonl');
    browser.page.on('response', response => {
      fs.appendFileSync(networkFile, JSON.stringify({url: response.url(), status: response.status()}) + '\n');
    });
    const remaining = numbers['timeout-ms'] - (Date.now() - started);
    if (remaining <= 0) throw new Error('Browser startup exhausted the deadline');
    const response = await browser.page.goto(url.href, {waitUntil: 'domcontentloaded', timeout: Math.min(30000, remaining)});
    protocol.document_status = response?.status() ?? null;
    const recoveryRemaining = numbers['timeout-ms'] - (Date.now() - started);
    if (recoveryRemaining <= 0) throw new Error('Navigation exhausted the deadline');
    result = await runVisualChallenge({page: browser.page, predictor: client, directory,
      maxAttempts: numbers['max-attempts'], maxRounds: numbers['max-rounds'], timeoutMs: recoveryRemaining,
      refreshTimeoutMs: numbers['refresh-timeout-ms'], initialWaitMs: Math.min(10000, recoveryRemaining)});
    if (interrupted) result.outcome = 'interrupted';
    if (expired) result.outcome = 'deadline_exceeded';
    protocol.status = 'completed';
  } catch (error) {
    result = {outcome: interrupted ? 'interrupted' : expired ? 'deadline_exceeded' : 'browser_setup_or_execution_failed',
      error: {type: error.name, message: error.message}};
    protocol.status = 'failed';
  } finally {
    clearTimeout(deadlineTimer);
    if (browser?.page && !browser.page.isClosed()) {
      try {
        await browser.page.screenshot({path: path.join(directory, 'final.png'), fullPage: true, timeout: 2000});
        result.final_screenshot = 'final.png';
      } catch { /* Closing or expired sessions can make the final screenshot unavailable. */ }
    }
    await client.close();
    try { await browser?.close(); } catch { /* The interrupt handler may already have closed it. */ }
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
    if (client.stderr) fs.writeFileSync(path.join(directory, 'inference-stderr.log'), client.stderr);
    protocol.model_worker = client.metadata || null;
    protocol.elapsed_ms = Date.now() - started;
    protocol.outcome = result.outcome;
    writeProtocol();
    fs.writeFileSync(path.join(directory, 'summary.json'), JSON.stringify(result, null, 2) + '\n');
  }
  console.log(JSON.stringify({directory, ...result}));
  return result.outcome.startsWith('passed_') ? 0 : 2;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then(code => {process.exitCode = code;}).catch(error => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
