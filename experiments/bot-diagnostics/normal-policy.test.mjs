import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Evidence, verify} from './evidence.mjs';

const temp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'bot-observation-policy-'));
const auth = 'User authorized ordinary headful browser observation';

test('legacy evidence retains request and byte reservation limits', () => {
  const root = temp(), dir = path.join(root, 'legacy');
  try {
    const e = new Evidence(dir);
    for (let i = 0; i < 25; i++) {
      const record = e.reserve('client/target', `https://site.example/${i}`, 'main_document');
      e.finish(record, 200, {}, Buffer.from('x'));
    }
    assert.throws(() => e.reserve('client/target', 'https://site.example/26', 'main_document'), /request_budget/);
    const limited = new Evidence(path.join(root, 'bytes'));
    for (let i = 0; i < 4; i++) limited.fail(limited.reserve('client/target', `https://site.example/${i}`, 'main_document'), Error('failed'));
    assert.throws(() => limited.reserve('client/target', 'https://site.example/5', 'main_document'), /byte_budget/);
  } finally { fs.rmSync(root, {recursive:true, force:true}); }
});

test('browser observation exceeds legacy request and byte limits, stores full bodies, and charges failed requests zero bytes', () => {
  const root = temp(), dir = path.join(root, 'run');
  try {
    const e = new Evidence(dir, false, {policy:'browser_observation', authorization:auth});
    assert.equal(e.policy, 'browser_observation');
    for (let i = 0; i < 25; i++) {
      const record = e.reserve('headful/target', `https://site.example/${i}`, 'main_document');
      const body = Buffer.alloc(i === 0 ? 2_500_000 : 400_000, 7);
      e.finish(record, 200, {}, body);
    }
    const failed = e.reserve('headful/target', 'https://site.example/fail', 'subresource');
    e.fail(failed, Error('network error'));
    assert.equal(failed.bytes_charged, 0);
    assert.equal(e.budgets['headful/target'].requests, 26);
    assert.equal(e.budgets['headful/target'].bytes_charged, 12_100_000);
    assert.equal(e.records[0].cap, Number.MAX_SAFE_INTEGER);
    assert.equal(fs.statSync(path.join(dir, 'blobs', e.records[0].body_sha256)).size, 2_500_000);
    assert.equal(verify(dir).ok, true);
  } finally { fs.rmSync(root, {recursive:true, force:true}); }
});

test('explicit policy resume preserves the existing record prefix and appends tamper-evident authorization', () => {
  const root = temp(), dir = path.join(root, 'run');
  try {
    let e = new Evidence(dir);
    e.fail(e.reserve('client/target', 'https://site.example/old', 'main_document'), Error('old failure'));
    const prefix = JSON.stringify(e.records);
    e = new Evidence(dir, true, {policy:'browser_observation', authorization:auth});
    assert.equal(JSON.stringify(e.records.slice(0, 1)), prefix);
    const record = e.reserve('client/target', 'https://site.example/new', 'main_document');
    e.finish(record, 200, {}, Buffer.alloc(9));
    assert.equal(verify(dir).ok, true);
    const historyFile = path.join(dir, 'policy-history.json');
    const history = JSON.parse(fs.readFileSync(historyFile));
    assert.equal(history.length, 1);
    assert.equal(history[0].authorization, auth);
    history[0].authorization = 'rewritten';
    fs.writeFileSync(historyFile, JSON.stringify(history));
    assert.equal(verify(dir).ok, false);
    assert.ok(verify(dir).errors.includes('policy_history:1'));
  } finally { fs.rmSync(root, {recursive:true, force:true}); }
});
