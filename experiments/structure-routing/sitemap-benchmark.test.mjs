import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {urlShape} from './sitemap-benchmark.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));

test('URL shape preserves directory and query-key structure while normalizing variable values', () => {
  assert.equal(urlShape('https://example.test/products/123?sort=price#specs'), urlShape('https://example.test/products/987?sort=title#other'));
  assert.equal(urlShape('https://example.test/products/blue-chair'), urlShape('https://example.test/products/red-chair'));
  assert.equal(urlShape('https://example.test/docs/about.html'), urlShape('https://example.test/docs/bugs.html'));
  assert.notEqual(urlShape('https://example.test/products/123?sort=price'), urlShape('https://example.test/products/987?page=2'));
  assert.notEqual(urlShape('https://example.test/products/123'), urlShape('https://other.test/products/123'));
});

test('B2 fixture saves cold and unseen warm HTML in a separate process', async t => {
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname === '/robots.txt') { response.writeHead(200, {'content-type': 'text/plain'}); response.end('User-agent: *\nSitemap: /sitemap.xml\n'); return; }
    if (url.pathname === '/sitemap.xml') {
      response.writeHead(200, {'content-type': 'application/xml'});
      response.end(`<urlset>${Array.from({length: 6}, (_, index) => `<url><loc>http://127.0.0.1:${server.address().port}/items/${index + 1}?variant=${index + 1}#details</loc></url>`).join('')}</urlset>`);
      return;
    }
    if (/^\/items\/\d+$/.test(url.pathname)) {
      response.writeHead(200, {'content-type': 'text/html'});
      response.end(`<!doctype html><html><head><title>Fixture item ${url.pathname.split('/').pop()}</title></head><body><main><h1>Item ${url.pathname.split('/').pop()}</h1><p>Public fixture content for route validation. This longer content is intentionally included so the fixture meets the saved-page content threshold.</p><a href="/items/6?variant=6#details">Next item</a></main></body></html>`);
      return;
    }
    response.writeHead(404, {'content-type': 'text/html'}); response.end('<title>Not found</title>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'structure-routing-b2-'));
  t.after(() => fs.rmSync(temp, {recursive: true, force: true}));
  const baseUrl = `http://127.0.0.1:${server.address().port}/`;
  const targetFile = path.join(temp, 'targets.jsonl');
  fs.writeFileSync(targetFile, JSON.stringify({id: 'fixture', domain: '127.0.0.1', start_url: baseUrl,
    allowed_hosts: ['127.0.0.1'], tasks: ['fixture detail'], practice: true, group: 'test'}) + '\n');
  const script = path.join(here, 'sitemap-benchmark.mjs');
  const run = phase => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script, '--live', 'true', '--run', temp, '--targets', targetFile, '--ids', 'fixture', '--method', 'B2', '--phase', phase],
      {cwd: path.resolve(here, '../..'), windowsHide: true});
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
    const timer = setTimeout(() => child.kill(), 120000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('close', (code, signal) => { clearTimeout(timer); resolve({code, signal, stdout, stderr}); });
  });
  const cold = await run('cold');
  assert.equal(cold.code, 0, cold.stderr || cold.stdout);
  const warm = await run('warm');
  assert.equal(warm.code, 0, warm.stderr || warm.stdout);
  const rows = fs.readFileSync(path.join(temp, 'results.jsonl'), 'utf8').trim().split(/\r?\n/).map(JSON.parse);
  const coldRow = rows.find(row => row.phase === 'cold');
  const warmRow = rows.find(row => row.phase === 'warm');
  assert.equal(coldRow.observations.length, 3, JSON.stringify(coldRow));
  assert.ok(coldRow.observations.every(row => row.page_valid), JSON.stringify(coldRow));
  assert.equal(warmRow.observations.length, 3, JSON.stringify(warmRow));
  assert.ok(coldRow.observations.every(row => row.page_valid && row.source_html && row.rendered_dom));
  assert.ok(warmRow.observations.every(row => row.page_valid && row.source_html && row.rendered_dom));
  assert.equal(warmRow.cost.sitemap_discovery.requests, 0);
  assert.equal(new Set([...coldRow.observations, ...warmRow.observations].map(row => row.requested_url)).size, 6);
});
