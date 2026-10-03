import test from 'node:test';
import assert from 'node:assert/strict';
import { LightweightSessionPool } from './session-pool.mjs';

test('reuses the current usable session and returns an isolated cookie snapshot', () => {
  const pool = new LightweightSessionPool();
  const session = pool.getSession();
  assert.equal(pool.getSession(), session);
  const cookies = [{ name: 'sid', value: 'secret' }];
  const saved = pool.snapshotCookies(session, cookies);
  cookies[0].value = 'changed';
  saved[0].value = 'changed again';
  assert.equal(session.cookies[0].value, 'secret');
  assert.deepEqual(pool.state().map(({ cookie_count, ...metadata }) => metadata.cookie_bytes > 0 && cookie_count), [1]);
  assert.equal(JSON.stringify(pool.state()).includes('secret'), false);
});

test('retires after two failures, clears cookies, and allocates a new id', () => {
  const pool = new LightweightSessionPool();
  const first = pool.getSession();
  pool.snapshotCookies(first, [{ name: 'sid', value: 'secret' }]);
  pool.markBad(first);
  assert.equal(first.retired, false);
  pool.markBad(first);
  assert.equal(first.retired, true);
  assert.deepEqual(first.cookies, []);
  assert.equal(pool.session, null);
  const second = pool.getSession();
  assert.notEqual(second.id, first.id);
  assert.equal(JSON.stringify(pool.state()).includes('secret'), false);
});

test('markGood consumes usage and reduces error score without going below zero', () => {
  const pool = new LightweightSessionPool();
  const session = pool.getSession();
  pool.markBad(session);
  pool.markGood(session);
  assert.equal(session.uses, 2);
  assert.equal(session.errorScore, 0.5);
  pool.markGood(session);
  assert.equal(session.errorScore, 0);
});

test('expires by TTL and creates a fresh session', () => {
  let time = 1_000;
  const pool = new LightweightSessionPool({ maxAgeMillis: 50, now: () => time });
  const first = pool.getSession();
  time += 50;
  const second = pool.getSession();
  assert.notEqual(second.id, first.id);
  assert.equal(second.createdAt, time);
});

test('allocates a new id after the usage threshold and keeps the pool bounded', () => {
  const pool = new LightweightSessionPool({ maxPoolSize: 1, maxUsageCount: 2 });
  const first = pool.getSession();
  pool.markGood(first);
  assert.equal(pool.getSession(), first);
  pool.markGood(first);
  const second = pool.getSession();
  assert.notEqual(second.id, first.id);
  assert.equal(pool.state().length, 1);
  assert.equal(first.cookies.length, 0);
  assert.equal(first.retired, true);
});

test('rejects cookie count and UTF-8 byte overflow without truncating or replacing prior data', () => {
  const pool = new LightweightSessionPool();
  const session = pool.getSession();
  pool.snapshotCookies(session, [{ name: 'sid', value: 'kept' }]);
  assert.throws(() => pool.snapshotCookies(session, Array.from({ length: 65 }, (_, i) => ({ name: String(i) }))),
    error => error.code === 'cookie_storage_limit');
  assert.throws(() => pool.snapshotCookies(session, [{ value: 'あ'.repeat(11_000) }]),
    error => error.code === 'cookie_storage_limit');
  assert.deepEqual(session.cookies, [{ name: 'sid', value: 'kept' }]);
});

test('prunes the oldest retired session when the bounded pool needs a slot', () => {
  const pool = new LightweightSessionPool({ maxPoolSize: 2 });
  const first = pool.getSession();
  pool.retire(first);
  const second = pool.getSession();
  pool.retire(second);
  const third = pool.getSession();
  assert.notEqual(third.id, first.id);
  assert.notEqual(third.id, second.id);
  assert.equal(pool.state().length, 2);
  assert.equal(pool.state().some(item => item.id === first.id), false);
  assert.equal(pool.session, third);
});
