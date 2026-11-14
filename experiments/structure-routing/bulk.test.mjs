import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';

test('bulk uses frozen rules, saves new HTML and stops at the reserved document limit', {timeout: 90000}, async () => {
  const server = http.createServer((request, response) => {
    if (request.url === '/robots.txt') { response.end('User-agent: *\nAllow: /'); return; }
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    const list = Array.from({length: 100}, (_, index) => `<li class="card"><a href="/item/${index}">Product ${index}</a></li>`).join('');
    response.end(`<html><head><title>Catalog</title></head><body><main><h1>Catalog</h1>${request.url === '/' ? `<ul>${list}</ul>` : `<p>${'Description '.repeat(40)}</p>`}</main></body></html>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../lab-runs/structure-routing-20261115');
  const run = fs.mkdtempSync(path.join(base, 'bulk-fixture-'));
  const origin = `http://127.0.0.1:${server.address().port}`;
  fs.writeFileSync(path.join(run, 'targets.jsonl'), JSON.stringify({id: 'fixture', domain: 'localhost',
    start_url: `${origin}/`, allowed_hosts: ['127.0.0.1'], practice: true}));
  const cold = path.join(run, 'artifacts/fixture/C1/cold');
  fs.mkdirSync(cold, {recursive: true});
  fs.writeFileSync(path.join(cold, 'rules.json'), JSON.stringify({source_pages: [`${origin}/`], pagination: [],
    repeated_link_groups: [{selector: 'li.card a', content_region_count: 100, navigation_region_count: 0}]}));
  try {
    const code = await new Promise(resolve => {
      const child = spawn(process.execPath, [fileURLToPath(new URL('./bulk.mjs', import.meta.url)), '--run', run, '--id', 'fixture', '--documents', '15']);
      const log = fs.createWriteStream(path.join(run, 'execution.log'));
      child.stdout.pipe(log);
      child.stderr.pipe(log);
      child.on('close', status => { log.end(); resolve(status); });
    });
    assert.equal(code, 0);
    const result = JSON.parse(fs.readFileSync(path.join(run, 'artifacts/fixture/C1/bulk/result.json')));
    assert.equal(result.document_attempts, 15);
    assert.equal(result.stop_reason, 'document_budget');
    assert.equal(result.observations.filter(row => row.kind === 'rule_selected_target').length, 14);
    assert.equal(result.milestones[0].valid_candidate_pages, 10);
    assert.equal(result.html_cache_reuse, false);
    assert.equal(result.external_llm_calls, 0);
    assert.equal(result.local_inference_calls, 0);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});
