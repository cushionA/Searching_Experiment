import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { classify, decodeBody, Evidence, verify } from './runner.mjs';

test('HTTP 202 AWS challenge is not a successful content response', () => {
  const c = classify(202, '<script src="https://example.token.awswaf.com/challenge.js"></script><script>AwsWafIntegration.getToken()</script>');
  assert.equal(c.outcome, 'challenge_observed');
  assert.equal(c.signals[0].name, 'aws_waf_challenge');
});
test('security-check response retains concrete Cloudflare evidence', () => {
  const c = classify(403, '<title>Security Check</title>Please enable JavaScript', {'cf-mitigated': 'challenge'});
  assert.equal(c.outcome, 'challenge_observed');
  assert.equal(c.site_block_reason, null);
});
test('shop pages mentioning robots, confirmation, and CAPTCHA scripts are not challenges', () => {
  const c = classify(200, '<title>ショップ</title>クーポンの確認 ロボット掃除機 <script>const captcha=true</script>');
  assert.equal(c.outcome, 'content_observed');
});
test('Shift_JIS content is decoded before classifying and extracting the title', () => {
  assert.equal(decodeBody(Buffer.from([0x82,0xa0]), {'content-type':'text/html; charset=Shift_JIS'}), 'あ');
});
test('errors remain charged on resume; exhausted budgets cannot be reset', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bot-diagnostics-test-'));
  const directory = path.join(root, 'run');
  try {
    let e = new Evidence(directory);
    e.fail(e.reserve('patchright/amazon', 'https://www.amazon.co.jp/', 'main_document'), new Error('TLS failure'));
    e = new Evidence(directory, true);
    assert.equal(e.budgets['patchright/amazon'].requests, 1);
    for (let i = 0; i < 3; i++) e.fail(e.reserve('patchright/amazon', 'https://www.amazon.co.jp/', 'main_document'), new Error('TLS failure'));
    assert.throws(() => e.reserve('patchright/amazon', 'https://www.amazon.co.jp/', 'main_document'), /byte_budget/);
    assert.equal(verify(directory).ok, true);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
test('verification finds a corrupted response body', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bot-diagnostics-test-'));
  const directory = path.join(root, 'run');
  try {
    const e = new Evidence(directory);
    const record = e.reserve('impit/homes', 'https://www.homes.co.jp/', 'main_document');
    e.finish(record, 200, {'content-type':'text/html'}, Buffer.from('original'));
    assert.equal(verify(directory).ok, true);
    fs.writeFileSync(path.join(directory, 'blobs', record.body_sha256), 'changed');
    assert.equal(verify(directory).ok, false);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
