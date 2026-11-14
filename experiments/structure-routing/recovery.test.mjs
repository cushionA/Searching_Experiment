import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createServer} from 'node:http';

const repo = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
const runner = path.join(repo, 'experiments/structure-routing/runner.mjs');

function runRunner(args, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, args, options);
    let stdout = '', stderr = '';
    const timeout = setTimeout(() => child.kill('SIGKILL'), 30000);
    child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
    child.once('error', error => { clearTimeout(timeout); reject(error); });
    child.once('close', (code, signal) => {
      clearTimeout(timeout);
      resolve({status: code, signal, stdout, stderr});
    });
  });
}

test('network recovery is isolated and shares the existing per-method document budget', async () => {
  const server = createServer((_request, response) => {
    response.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
    response.end('<!doctype html><html><head><title>Fixture</title></head><body><main><h1>Fixture page</h1><p>Local recovery test.</p></main></body></html>');
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'structure-recovery-'));
  try {
    const port = server.address().port;
    const target = {id: 'fixture', domain: 'localhost', start_url: `http://127.0.0.1:${port}/`,
      allowed_hosts: ['127.0.0.1'], tasks: ['fixture'], practice: false};
    fs.writeFileSync(path.join(dir, 'targets.jsonl'), `${JSON.stringify(target)}\n`);

    const mainRows = ['B1', 'C1'].map(method => ({id: 'fixture', domain: 'localhost', method, mode: 'cold', status: 'completed',
      document_requests: 14, document_responses: 14, operations: 0, elapsed_ms: 0, pages: []}));
    const mainResultsFile = path.join(dir, 'results.jsonl');
    const mainResultsBytes = mainRows.map(row => JSON.stringify(row)).join('\n') + '\n';
    fs.writeFileSync(mainResultsFile, mainResultsBytes);

    const originalStateFile = path.join(dir, 'site-state', 'fixture.json');
    const originalStateBytes = JSON.stringify({completed: [
      'fixture:B1:cold', 'fixture:B1:G', 'fixture:B1:R', 'fixture:C1:cold', 'fixture:C1:G', 'fixture:C1:R',
    ]}, null, 2) + '\n';
    fs.mkdirSync(path.dirname(originalStateFile), {recursive: true});
    fs.writeFileSync(originalStateFile, originalStateBytes);

    const originalRulesFile = path.join(dir, 'artifacts', 'fixture', 'B1', 'cold', 'rules.json');
    const originalRulesBytes = JSON.stringify({sentinel: 'preserve-main-cold-rules'}, null, 2) + '\n';
    fs.mkdirSync(path.dirname(originalRulesFile), {recursive: true});
    fs.writeFileSync(originalRulesFile, originalRulesBytes);

    const child = await runRunner([runner, '--run', dir, '--ids', 'fixture', '--methods', 'B1,C1',
      '--phase', 'both', '--resume', '--measurement', 'network_recovery'], {
      cwd: repo,
      env: {...process.env, CHROME_PATH: process.env.CHROME_PATH || '/usr/bin/chromium'},
    });
    assert.equal(child.status, 0, `runner failed (${child.signal || child.status}): ${child.stderr || child.stdout}`);

    const recoveryFile = path.join(dir, 'recovery-results.jsonl');
    const recoveryRows = fs.readFileSync(recoveryFile, 'utf8').trim().split(/\r?\n/).map(line => JSON.parse(line));
    assert.equal(recoveryRows.length, 6);
    assert.ok(recoveryRows.every(row => row.execution_origin === 'network_recovery'));
    assert.deepEqual(recoveryRows.map(row => `${row.method}:${row.mode}`).sort(),
      ['B1:G', 'B1:R', 'B1:cold', 'C1:G', 'C1:R', 'C1:cold'].sort());

    for (const method of ['B1', 'C1']) {
      const methodRows = recoveryRows.filter(row => row.method === method);
      const recoveryDocuments = methodRows.reduce((sum, row) => sum + Math.max(row.document_requests || 0, row.document_responses || 0), 0);
      assert.ok(recoveryDocuments <= 1, `${method} recovery used ${recoveryDocuments} documents`);
      assert.ok(14 + recoveryDocuments <= 15, `${method} exceeded its cumulative 15-document cap`);
    }

    const coldRows = recoveryRows.filter(row => row.mode === 'cold');
    assert.ok(coldRows.every(row => row.document_requests === 1 && row.document_responses === 1));
    for (const row of coldRows) {
      assert.ok(row.pages.length > 0);
      for (const page of row.pages) {
        assert.match(page.response_html, /^network_recovery\/cold\//);
        const artifact = path.join(dir, 'artifacts', 'fixture', row.method, page.response_html);
        assert.ok(fs.statSync(artifact).size > 0, `missing recovery HTML ${artifact}`);
      }
    }

    assert.equal(fs.readFileSync(mainResultsFile, 'utf8'), mainResultsBytes);
    assert.equal(fs.readFileSync(originalStateFile, 'utf8'), originalStateBytes);
    assert.equal(fs.readFileSync(originalRulesFile, 'utf8'), originalRulesBytes);
  } finally {
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(dir, {recursive: true, force: true});
  }
});
