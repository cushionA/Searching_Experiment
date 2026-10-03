import fs from 'node:fs';
import path from 'node:path';
import {config, limits as L, local, sleep, sha} from './runtime.mjs';

export function safeError(error) {
  let message = String(error?.message || error);
  for (const [name, value] of Object.entries(process.env)) {
    if (value && /PASSWORD|TOKEN|SECRET|API_KEY/i.test(name)) message = message.split(value).join('[redacted]');
  }
  message = message.replace(/(https?:\/\/)[^\s/@]+(?::[^\s/@]*)?@/g, '$1[redacted]@');
  message = message.replace(/(?:__cf_bm|_cfuvid|(?:[\w-]*token[\w-]*))=[^\s;"\n]+/gi, '[redacted-cookie]');
  return { name: error?.name || 'Error', message: message.slice(0, 1800) };
}
export function keptHeaders(headers) {
  const retained = ['content-type', 'content-length', 'content-encoding', 'server', 'location', 'cf-ray',
    'cf-mitigated', 'retry-after', 'via', 'x-cache', 'x-amz-cf-id', 'x-amz-cf-pop', 'x-request-id',
    'x-denied-reason', 'x-blocked-by', 'content-security-policy', 'x-cdn', 'x-sucuri-id',
    'x-sucuri-block', 'x-akamai-transformed', 'x-amzn-waf-action', 'akamai-grn'];
  return Object.fromEntries(Object.entries(headers).filter(([key]) => retained.includes(key.toLowerCase())));
}

const BROWSER_POLICY = 'browser_observation';
const legacyPolicy = 'grounding';
function appendPolicy(directory, policy, authorization) {
  if (typeof authorization !== 'string' || !authorization.trim()) throw new Error('policy authorization is required');
  const filename = path.join(directory, 'policy-history.json');
  const history = fs.existsSync(filename) ? JSON.parse(fs.readFileSync(filename)) : [];
  const previous_hash = history.at(-1)?.hash || null;
  const entry = { policy, authorization, recorded_at: new Date().toISOString(), previous_hash };
  entry.hash = sha(JSON.stringify(entry));
  history.push(entry);
  fs.writeFileSync(filename + '.tmp', JSON.stringify(history, null, 2) + '\n');
  fs.renameSync(filename + '.tmp', filename);
}

export class Evidence {
  constructor(directory, resume = false, options = {}) {
    this.policy = options.policy || legacyPolicy;
    if (![legacyPolicy, BROWSER_POLICY].includes(this.policy)) throw new Error(`Unknown evidence policy: ${this.policy}`);
    this.browserObservation = this.policy === BROWSER_POLICY;
    if (resume) {
      this.directory = directory;
      const ledger = JSON.parse(fs.readFileSync(path.join(directory, 'ledger.json')));
      const saved = JSON.parse(fs.readFileSync(path.join(directory, 'config.json')));
      if (JSON.stringify(saved) !== JSON.stringify(config)) throw new Error('Cannot change an existing run configuration');
      this.budgets = ledger.budgets;
      this.records = ledger.records;
      this.results = JSON.parse(fs.readFileSync(path.join(directory, 'results.json')));
      this.last = {};
      for (const r of this.records) this.last[new URL(r.url).origin] = Math.max(this.last[new URL(r.url).origin] || 0, Date.parse(r.finished_at || r.started_at));
      this.delayChain = Promise.resolve();
      if (options.policy) appendPolicy(directory, this.policy, options.authorization);
      return;
    }
    if (fs.existsSync(directory)) throw new Error('Existing run cannot be overwritten');
    this.directory = directory;
    fs.mkdirSync(path.join(directory, 'blobs'), { recursive: true });
    this.budgets = {};
    this.records = [];
    this.results = [];
    this.last = {};
    this.delayChain = Promise.resolve();
    this.save('config.json', config);
    if (options.policy) appendPolicy(directory, this.policy, options.authorization);
  }
  save(name, value) {
    const destination = path.join(this.directory, name);
    fs.writeFileSync(destination + '.tmp', JSON.stringify(value, null, 2) + '\n');
    fs.renameSync(destination + '.tmp', destination);
  }
  blob(body) {
    const hash = sha(body);
    fs.writeFileSync(path.join(this.directory, 'blobs', hash), body);
    return hash;
  }
  reserve(key, url, kind) {
    const b = this.budgets[key] ||= { requests: 0, bytes_charged: 0 };
    if (!this.browserObservation && b.requests >= L.requests_per_client_target) throw new Error('request_budget');
    const cap = this.browserObservation ? Number.MAX_SAFE_INTEGER : Math.min(L.bytes_per_response, L.bytes_per_client_target - b.bytes_charged);
    if (!this.browserObservation && cap <= 0) throw new Error('byte_budget');
    b.requests++;
    if (!this.browserObservation) b.bytes_charged += cap;
    const record = { index: this.records.length + 1, key, url, kind, cap, policy: this.policy,
      started_at: new Date().toISOString(), status: 'interrupted', bytes_charged: this.browserObservation ? 0 : cap,
      route: local(url) ? 'local_fixture' : 'environment_proxy', ...(this.stageContext || {}) };
    this.records.push(record);
    this.flush();
    return record;
  }
  flush() {
    this.save('ledger.json', { budgets: this.budgets, records: this.records });
    this.save('results.json', this.results);
  }
  async wait(url, extraDelay = 0) {
    const run = this.delayChain.then(async () => {
      const origin = new URL(url).origin;
      const delay = local(url) || this.browserObservation ? 0 : Math.max(L.delay_seconds, extraDelay) * 1000;
      await sleep(Math.max(0, (this.last[origin] || 0) + delay - Date.now()));
      this.last[origin] = Date.now();
    });
    this.delayChain = run.catch(() => {});
    await run;
  }
  finish(record, status, headers, body, truncated = false) {
    body = Buffer.from(body);
    if (record.policy !== BROWSER_POLICY && body.length > record.cap) { body = body.subarray(0, record.cap); truncated = true; }
    if (record.policy === BROWSER_POLICY) this.budgets[record.key].bytes_charged += body.length;
    else this.budgets[record.key].bytes_charged -= record.cap - body.length;
    Object.assign(record, { status, headers: keptHeaders(headers), bytes_charged: body.length,
      body_sha256: this.blob(body), truncated, finished_at: new Date().toISOString() });
    this.flush();
  }
  fail(record, error) {
    if (record.policy === BROWSER_POLICY) record.bytes_charged = 0;
    Object.assign(record, { status: 'error', error: safeError(error), finished_at: new Date().toISOString() });
    this.flush();
  }
  result(value) { this.results.push({ ...value, ...(this.stageContext || {}) }); this.flush(); }
}

export function verify(directory) {
  const ledger = JSON.parse(fs.readFileSync(path.join(directory, 'ledger.json')));
  const results = JSON.parse(fs.readFileSync(path.join(directory, 'results.json')));
  const errors = [];
  const policyFile = path.join(directory, 'policy-history.json');
  let policyHistory = [];
  if (fs.existsSync(policyFile)) {
    try {
      policyHistory = JSON.parse(fs.readFileSync(policyFile));
      let previous = null;
      for (let i = 0; i < policyHistory.length; i++) {
        const {hash, ...entry} = policyHistory[i];
        if (entry.previous_hash !== previous || hash !== sha(JSON.stringify(entry)) || !entry.authorization || ![legacyPolicy, BROWSER_POLICY].includes(entry.policy)) errors.push(`policy_history:${i + 1}`);
        previous = hash;
      }
    } catch { errors.push('policy_history:invalid'); }
  }
  const approvedPolicies = new Set(policyHistory.map(x => x.policy));
  for (const record of ledger.records) {
    if (record.body_sha256) {
      const body = fs.readFileSync(path.join(directory, 'blobs', record.body_sha256));
      if (sha(body) !== record.body_sha256 || body.length !== record.bytes_charged) errors.push(`blob:${record.index}`);
    }
    const policy = record.policy || legacyPolicy;
    if(![legacyPolicy,BROWSER_POLICY].includes(policy)) errors.push(`unknown_policy:${record.index}`);
    if (policy === BROWSER_POLICY && !approvedPolicies.has(BROWSER_POLICY)) errors.push(`policy_missing:${record.index}`);
    if (policy !== BROWSER_POLICY && record.bytes_charged > record.cap) errors.push(`response_cap:${record.index}`);
    if (record.status === 'interrupted') errors.push(`inflight:${record.index}`);
  }
  for (const [key, b] of Object.entries(ledger.budgets)) {
    const records = ledger.records.filter(x => x.key === key);
    if (records.length !== b.requests || records.reduce((sum, x) => sum + x.bytes_charged, 0) !== b.bytes_charged) errors.push(`accounting:${key}`);
    const legacy = records.filter(x => (x.policy || legacyPolicy) !== BROWSER_POLICY);
    if (legacy.length > L.requests_per_client_target || legacy.reduce((sum, x) => sum + x.bytes_charged, 0) > L.bytes_per_client_target) errors.push(`budget:${key}`);
  }
  for (const result of results) if (result.dom_sha256) {
    const body = fs.readFileSync(path.join(directory, 'blobs', result.dom_sha256));
    if (sha(body) !== result.dom_sha256) errors.push(`dom:${result.client}/${result.target || result.detector}`);
  }
  for(const filename of fs.readdirSync(path.join(directory,'blobs'))) {
    if(!/^[a-f0-9]{64}$/.test(filename) || sha(fs.readFileSync(path.join(directory,'blobs',filename)))!==filename) errors.push(`source_or_blob:${filename}`);
  }
  return { ok: !errors.length, errors, records: ledger.records.length, results: results.length,
    note: 'stored-body accounting and hashes verified; legacy limits checked only for grounding records' };
}
