import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {Evidence, verify} from './evidence.mjs';
import {snapshotSources} from './runtime.mjs';
import {prepare} from './manifest.mjs';
import {ToolAdapter} from './adapters.mjs';
import {runScenario} from './scenario.mjs';
import {openBrowser} from './runner.mjs';

// Compare the same injected roles, cookie session and redirect on loopback only.
const directory = process.argv[2];
const headful = process.argv[3] === '--headful';
if (!directory || process.argv.length > 4 || (process.argv[3] && !headful)) {
  throw new Error('Usage: camoufox-smoke.mjs NEW_OUTPUT_DIRECTORY [--headful]');
}
const requests = [];
const server = http.createServer((request, response) => {
  const cookie = (request.headers.cookie || '').includes('fixture_session=present');
  requests.push({path:request.url, cookie_present:cookie});
  if (request.url === '/') response.setHeader('Set-Cookie', 'fixture_session=present; Path=/; SameSite=Lax');
  if (request.url === '/redirect') {
    response.writeHead(302, {Location:'/inside'}); response.end(); return;
  }
  if (request.url === '/inside' && !cookie) {
    response.writeHead(403); response.end('<title>Access Denied</title>Missing session'); return;
  }
  response.setHeader('Content-Type', 'text/html; charset=utf-8');
  response.end('<!doctype html><title>Shared fixture</title><p id="result">pending</p><script>document.querySelector("#result").textContent="JS ready"</script>');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
try {
  const origin = `http://127.0.0.1:${server.address().port}`;
  const manifest = prepare({schema:1, tools:['playwright-baseline','camoufox'], sites:[{
    id:'fixture', origins:[origin], links:{home:origin+'/', targets:[origin+'/redirect']},
  }]}, {fixture:true});
  const evidence = new Evidence(path.resolve(directory), false, {
    policy:'browser_observation', authorization:'Local Camoufox comparison fixture only',
  });
  evidence.save('scenario.json', manifest);
  evidence.save('sources.json', snapshotSources(evidence));
  const outcomes = [];
  for (const tool of manifest.tools) {
    const adapter = new ToolAdapter({tool, site:manifest.sites[0], evidence, fixture:true,
      profile:manifest.profiles[0], options:manifest.options,
      browserOpener:(name, options) => openBrowser(name, {...options, headful}),
    });
    try {
      await adapter.open();
      const result = await runScenario({adapter, site:manifest.sites[0]});
      assert.equal(result.state, 'navigation_completed', JSON.stringify(result));
      assert.equal(await adapter.browser.page.locator('#result').innerText(), 'JS ready');
      outcomes.push(result);
    } finally { await adapter.close(); }
  }
  evidence.save('pipeline-results.json', outcomes);
  evidence.save('fixture-requests.json', requests);
  const verification = verify(evidence.directory);
  evidence.save('verification.json', verification);
  assert.equal(verification.ok, true, JSON.stringify(verification));
  for (const tool of manifest.tools) {
    const records = evidence.records.filter(record => record.key === tool+'/fixture');
    assert.ok(records.some(record => record.status === 302), 'redirect response must be recorded');
    assert.ok(records.some(record => record.url === origin+'/inside' && record.status === 200));
    const setup = evidence.results.find(result => result.client === tool && result.role === 'adapter_setup');
    assert.equal(setup.headless, !headful);
    assert.ok(evidence.results.some(result => result.client === tool && result.screenshot
      && fs.existsSync(path.join(evidence.directory, result.screenshot))));
  }
  assert.ok(requests.filter(request => request.path === '/inside').every(request => request.cookie_present));
  console.log(JSON.stringify({ok:true, directory:evidence.directory, tools:manifest.tools,
    headless:!headful, session_cookie_verified:true, javascript_verified:true,
    redirect_accounting_verified:true, verification}));
} finally { await new Promise(resolve => server.close(resolve)); }
