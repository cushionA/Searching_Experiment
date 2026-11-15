import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {runSitemapBenchmark} from './sitemap-benchmark.mjs';

const args = Object.fromEntries(process.argv.slice(2).reduce((out, item, i, all) => {
  if (!item.startsWith('--')) return out;
  const [key, inline] = item.slice(2).split('=', 2);
  out.push([key, inline ?? (all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : 'true')]);
  return out;
}, []));

function readJsonl(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map(JSON.parse);
}

function writeJsonl(file, value) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.appendFileSync(file, JSON.stringify(value) + '\n');
}

function safeHeaderSubset(headers) {
  const result = {};
  for (const name of ['content-type', 'content-length', 'content-encoding', 'location']) {
    const value = headers.get(name);
    if (value !== null) result[name] = value;
  }
  return result;
}

export async function runSitemapSupplement({runDir, targets, phase, executablePath} = {}) {
  if (!['cold', 'warm'].includes(phase)) throw new Error('phase_must_be_cold_or_warm');
  const originalFetch = globalThis.fetch;
  for (const target of targets) {
    let sequence = 0;
    const directory = path.join(runDir, 'artifacts', String(target.id), 'B2', 'metadata', phase);
    const records = path.join(directory, 'responses.jsonl');
    const instrumentedFetch = async (input, init) => {
      const response = await originalFetch(input, init);
      const bytes = Buffer.from(await response.clone().arrayBuffer());
      const requestedUrl = input instanceof Request ? input.url : String(input);
      const digest = crypto.createHash('sha256').update(bytes).digest('hex');
      const stem = `${String(++sequence).padStart(4, '0')}-${digest.slice(0, 12)}`;
      const bodyFile = path.join(directory, `${stem}.body`);
      fs.mkdirSync(directory, {recursive: true});
      fs.writeFileSync(bodyFile, bytes);
      writeJsonl(records, {id: target.id, phase, sequence, requested_url: requestedUrl, response_url: response.url || requestedUrl,
        status: response.status, headers: safeHeaderSubset(response.headers), body_file: path.relative(runDir, bodyFile),
        body_bytes: bytes.length, body_sha256: digest, request_method: init?.method || 'GET', redirect_mode: init?.redirect || 'follow'});
      return response;
    };
    globalThis.fetch = instrumentedFetch;
    try {
      await runSitemapBenchmark({runDir, targets: [target], method: 'B2', phase, executablePath});
    } finally {
      globalThis.fetch = originalFetch;
    }
  }
}

async function main() {
  const runDir = path.resolve(args.run || '');
  if (!args.run) throw new Error('required_argument:--run');
  if (args.live !== 'true') throw new Error('live_mode_requires_explicit_--live_true');
  const phase = args.phase || 'cold';
  if (!['cold', 'warm'].includes(phase)) throw new Error('phase_must_be_cold_or_warm');
  const targetsFile = path.resolve(args.targets || path.join(runDir, 'targets.jsonl'));
  const ids = new Set(String(args.ids || '').split(',').filter(Boolean));
  const targets = readJsonl(targetsFile).filter(row => !ids.size || ids.has(String(row.id)));
  if (!targets.length) throw new Error('no_targets_selected');
  await runSitemapSupplement({runDir, targets, phase, executablePath: args.executable});
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url)
  main().catch(error => { console.error(JSON.stringify({error: error.message})); process.exitCode = 1; });
