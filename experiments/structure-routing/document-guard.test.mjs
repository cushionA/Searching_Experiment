import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createRequire} from 'node:module';
import {installDocumentGuard} from './document-guard.mjs';

const require = createRequire(import.meta.url);
const {chromium} = require('../../.deps/bot-diagnostics/node_modules/playwright');

test('strictly reserves each redirect and cross-origin iframe document before send', {timeout: 30000}, async () => {
  const hits = [];
  const server = http.createServer((req, res) => {
    hits.push(`${req.headers.host}${req.url}`);
    if (req.url === '/start') { res.writeHead(302, {location: '/landing'}); res.end(); return; }
    if (req.url === '/frame-start') { res.writeHead(302, {location: `http://127.0.0.1:${server.address().port}/frame-final`}); res.end(); return; }
    res.setHeader('content-type', 'text/html');
    res.end(req.url === '/landing' ? `<iframe src="http://localhost:${server.address().port}/frame-start"></iframe>` : '<p>ok</p>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || '/usr/bin/chromium', args: ['--no-sandbox']});
  const context = await browser.newContext();
  const page = await context.newPage();
  const observed = [];
  const guard = await installDocumentGuard(page, {limit: 3, beforeRequest: info => { observed.push(info); }});
  try {
    await page.goto(`http://localhost:${server.address().port}/start`, {waitUntil: 'load', timeout: 10000}).catch(() => {});
    await page.waitForTimeout(250);
    assert.equal(guard.counters.reserved, 3);
    assert.equal(guard.counters.continued, 3);
    assert.deepEqual(observed.map(item => item.method), ['GET', 'GET', 'GET']);
    assert.equal(observed.filter(item => item.mainFrame).length, 2);
    assert.equal(observed.filter(item => !item.mainFrame).length, 1);
    assert.equal(guard.counters.denied, 1);
    assert.ok(hits.includes(`localhost:${server.address().port}/start`));
    assert.ok(hits.includes(`localhost:${server.address().port}/landing`));
    assert.ok(hits.includes(`localhost:${server.address().port}/frame-start`));
    assert.ok(!hits.includes(`127.0.0.1:${server.address().port}/frame-final`), 'over-budget redirect target must not reach the server');
    assert.equal(guard.counters.reserved, 3, 'reservations never exceed the cap');
  } finally {
    await guard.cleanup();
    await context.close();
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});


test('caps concurrent cross-site iframe documents, JavaScript redirects, and zero-budget navigation', {timeout: 60000}, async () => {
  const hits = [];
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
    if (pathname === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    hits.push({host: req.headers.host, pathname});
    res.setHeader('content-type', 'text/html; charset=utf-8');
    if (pathname === '/') {
      const frames = Array.from({length: 6}, (_, i) => `<iframe src="http://localhost:${server.address().port}/frame/${i}"></iframe>`).join('');
      res.end(`<body>${frames}</body>`);
    } else if (pathname.startsWith('/frame/')) {
      const id = pathname.split('/').at(-1);
      res.end(`<script>setTimeout(() => { location.href = '/js-final/${id}' }, 30)</script>`);
    } else res.end('<p>final</p>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || '/usr/bin/chromium', args: ['--no-sandbox', '--site-per-process']});
  const port = server.address().port;
  try {
    for (const limit of [0, 1, 2, 3, 5, 8]) {
      hits.length = 0;
      const context = await browser.newContext();
      const page = await context.newPage();
      const observed = [];
      const guard = await installDocumentGuard(page, {limit, beforeRequest: async () => { await new Promise(resolve => setTimeout(resolve, 20)); return true; }, onReserved: info => observed.push(info)});
      const responses = [];
      page.on('response', response => {
        if (response.request().resourceType() === 'document') responses.push(response.url());
      });
      await page.goto(`http://127.0.0.1:${port}/`, {waitUntil: 'load', timeout: 12000}).catch(() => {});
      await page.waitForTimeout(450);
      assert.ok(guard.counters.reserved <= limit, `limit ${limit}: reservations exceeded cap`);
      assert.ok(hits.length <= limit, `limit ${limit}: server received ${hits.length}: ${JSON.stringify(hits)}; counts=${JSON.stringify(guard.counters)} observed=${JSON.stringify(observed)}`);
      assert.ok(responses.length <= guard.counters.reserved, `limit ${limit}: responses exceeded reservations`);
      assert.equal(observed.length, guard.counters.reserved);
      assert.ok(observed.every(info => info.method === 'GET'));
      assert.equal(observed.filter(info => info.mainFrame).length, limit === 0 ? 0 : 1);
      if (limit >= 2) assert.ok(hits.some(hit => hit.host.startsWith('localhost:')), `limit ${limit}: cross-site iframe did not reach server`);
      if (limit === 8) assert.ok(hits.some(hit => hit.pathname.startsWith('/js-final/')), 'JavaScript navigation should be observed when budget allows');
      await guard.cleanup();
      await context.close();
    }
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});


test('relays interception into nested cross-site OOPIF targets', {timeout: 30000}, async () => {
  const hits = [];
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
    if (pathname === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    hits.push({host: req.headers.host, pathname});
    res.setHeader('content-type', 'text/html; charset=utf-8');
    if (pathname === '/') res.end(`<iframe src="http://localhost:${server.address().port}/level-one"></iframe>`);
    else if (pathname === '/level-one') res.end(`<iframe src="http://127.0.0.1:${server.address().port}/level-two"></iframe>`);
    else res.end('<p>nested</p>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || '/usr/bin/chromium', args: ['--no-sandbox', '--site-per-process']});
  const context = await browser.newContext();
  const page = await context.newPage();
  const guard = await installDocumentGuard(page, {limit: 10});
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/`, {waitUntil: 'load', timeout: 10000}).catch(() => {});
    await page.waitForTimeout(250);
    assert.equal(guard.counters.reserved, 3);
    assert.equal(guard.counters.continued, 3);
    assert.equal(guard.counters.denied, 0);
    assert.equal(hits.length, 3);
    assert.ok(hits.some(hit => hit.pathname === '/level-two'));
    const innermost = page.frames().find(frame => frame.url().endsWith('/level-two'));
    assert.ok(innermost, 'nested OOPIF must complete its final document navigation');
    assert.equal(await innermost.locator('p').textContent(), 'nested');
  } finally {
    await guard.cleanup();
    await context.close();
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});


test('repeated cached 301, 307, and 308 redirects never produce unreserved document responses', {timeout: 45000}, async () => {
  const hits = [];
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
    if (pathname === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    hits.push({host: req.headers.host, pathname, status: 200});
    const match = pathname.match(/^\/redirect-(301|307|308)\/(\d+)$/);
    if (match) {
      res.writeHead(Number(match[1]), {location: `http://localhost:${server.address().port}/destination/${match[1]}/${match[2]}`, 'cache-control': 'public, max-age=3600'});
      res.end(); return;
    }
    res.setHeader('content-type', 'text/html; charset=utf-8');
    res.end('<p>destination</p>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || '/usr/bin/chromium', args: ['--no-sandbox', '--site-per-process']});
  const context = await browser.newContext();
  const page = await context.newPage();
  const responses = [];
  page.on('response', response => {
    if (response.request().resourceType() === 'document') responses.push({url: response.url(), status: response.status()});
  });
  const guard = await installDocumentGuard(page, {limit: 20});
  try {
    for (const status of [301, 307, 308]) {
      for (let repeat = 0; repeat < 3; repeat++) {
        await page.goto(`http://127.0.0.1:${server.address().port}/redirect-${status}/${repeat}`, {waitUntil: 'load', timeout: 10000});
      }
    }
    await page.waitForTimeout(200);
    assert.equal(guard.counters.reserved, 18, `unexpected reservations: ${JSON.stringify(guard.counters)}`);
    assert.ok(responses.length <= guard.counters.reserved, `responses=${responses.length} reservations=${guard.counters.reserved}: ${JSON.stringify(responses)}`);
    assert.ok(hits.length <= guard.counters.reserved, `server hits=${hits.length} reservations=${guard.counters.reserved}: ${JSON.stringify(hits)}`);
    assert.equal(responses.filter(row => [301, 307, 308].includes(row.status)).length, 9);
  } finally {
    await guard.cleanup();
    await context.close();
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});


test('seal fails a document paused in an asynchronous policy callback before context close', {timeout: 15000}, async () => {
  const hits = [];
  const server = http.createServer((req, res) => {
    if (req.url === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    hits.push(req.url);
    res.setHeader('content-type', 'text/html; charset=utf-8');
    if (req.url === '/teardown') res.end('<script>setTimeout(() => { location.href = "/late" }, 20)</script>');
    else res.end('<p>late</p>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_PATH || '/usr/bin/chromium', args: ['--no-sandbox']});
  const context = await browser.newContext();
  const page = await context.newPage();
  let markStarted;
  const callbackStarted = new Promise(resolve => { markStarted = resolve; });
  const guard = await installDocumentGuard(page, {limit: 5, beforeRequest: async info => {
    if (info.url.endsWith('/late')) { markStarted(); await new Promise(resolve => setTimeout(resolve, 300)); }
    return true;
  }});
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/teardown`, {waitUntil: 'load', timeout: 5000});
    await callbackStarted;
    await guard.seal();
    await context.close();
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.deepEqual(hits, ['/teardown']);
    assert.equal(guard.counters.reserved, 1);
  } finally {
    await guard.cleanup();
    await context.close().catch(() => {});
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
