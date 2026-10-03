import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeSessionPolicy, retryAfterMs, sessionResponsePolicy } from './session-policy.mjs';

test('policy enables with defaults, disables with falsey settings, and rejects unknown keys', () => {
  const defaults = {
    max_pool_size: 3, max_session_rotations: 2, max_retries_per_url: 1,
    max_retries_per_site: 2, max_session_uses: 20, max_session_age_seconds: 900,
    base_delay_ms: 1000, max_backoff_ms: 30000, max_auto_wait_ms: 30000,
  };
  assert.deepEqual(normalizeSessionPolicy(true), defaults);
  assert.equal(normalizeSessionPolicy(false), null);
  assert.equal(normalizeSessionPolicy(null), null);
  assert.equal(normalizeSessionPolicy(undefined), null);
  assert.deepEqual(normalizeSessionPolicy({ max_pool_size: 5 }), { ...defaults, max_pool_size: 5 });
  assert.throws(() => normalizeSessionPolicy({ unrelated: 123 }), /unknown session policy key/);
});

test('policy rejects out-of-range values and backoff shorter than base delay', () => {
  assert.throws(() => normalizeSessionPolicy({ max_retries_per_url: 2 }), RangeError);
  assert.throws(() => normalizeSessionPolicy({ max_pool_size: 2.5 }), RangeError);
  assert.throws(() => normalizeSessionPolicy({ base_delay_ms: 5000, max_backoff_ms: 4999 }), /greater than or equal/);
});

test('Retry-After parses integer seconds and HTTP dates, preserving long waits', () => {
  const now = Date.UTC(2026, 0, 1);
  assert.equal(retryAfterMs('12', now), 12000);
  assert.equal(retryAfterMs('Thu, 01 Jan 2026 00:01:00 GMT', now), 60000);
  assert.equal(retryAfterMs('Thu, 01 Jan 2026 01:00:00 GMT', now), 3600000);
});

test('Retry-After rejects invalid grammar and maps past dates to zero', () => {
  const now = Date.UTC(2026, 0, 1);
  assert.equal(retryAfterMs('-1', now), null);
  assert.equal(retryAfterMs('1.5', now), null);
  assert.equal(retryAfterMs('tomorrow', now), null);
  assert.equal(retryAfterMs('Thu, 31 Dec 2025 23:59:00 GMT', now), 0);
});

test('429 backoff takes priority over a challenge body and includes Retry-After signal', () => {
  assert.deepEqual(sessionResponsePolicy(
    { status: 429, outcome: 'challenge_observed' }, { 'Retry-After': '45' },
  ), { action: 'backoff', reason: 'http_429' });
  assert.deepEqual(sessionResponsePolicy({ status: 503, outcome: 'access_denied_observed' }, {}), {
    action: 'backoff', reason: 'http_503',
  });
});

test('TLS, robots, and exhausted budget stop without retry; ordinary challenges retire', () => {
  for (const reason of ['navigation_error', 'robots', 'budget_limited']) {
    assert.deepEqual(sessionResponsePolicy({ reason }), { action: 'stop', reason });
  }
  assert.deepEqual(sessionResponsePolicy({ status: 403, outcome: 'challenge_observed' }), {
    action: 'retire', reason: 'challenge_observed',
  });
  assert.deepEqual(sessionResponsePolicy({ outcome: 'challenge_observed' }, {}, { canRecoverChallenge: true }), {
    action: 'stop', reason: 'configured_challenge_recovery',
  });
});

test('actual failure fields stop before an HTTP retry status, including all robots outcomes', () => {
  assert.deepEqual(sessionResponsePolicy({ budget_limited: true, status: 429 }), {
    action: 'stop', reason: 'budget_limited',
  });
  assert.deepEqual(sessionResponsePolicy({ navigation_error: { message: 'TLS handshake failed' }, status: 429 }), {
    action: 'stop', reason: 'navigation_error',
  });
  assert.deepEqual(sessionResponsePolicy({ operation: { outcome: 'operation_error' }, status: 429 }), {
    action: 'stop', reason: 'operation_error',
  });
  for (const outcome of ['robots_denied', 'robots_unavailable', 'robots_invalid_html']) {
    assert.deepEqual(sessionResponsePolicy({ outcome }), { action: 'stop', reason: outcome });
  }
  assert.deepEqual(sessionResponsePolicy({ reason: 'challenge_observed', outcome: 'robots_denied' }), {
    action: 'stop', reason: 'robots_denied',
  });
});

test('only verified content is good and unverified navigation stops', () => {
  assert.deepEqual(sessionResponsePolicy({ outcome: 'content_observed' }), {
    action: 'good', reason: 'content_observed',
  });
  assert.deepEqual(sessionResponsePolicy({ reason: 'navigation_unverified' }), {
    action: 'stop', reason: 'navigation_unverified',
  });
  assert.deepEqual(sessionResponsePolicy({ reason: 'redirect_outside_scope' }), {
    action: 'stop', reason: 'redirect_outside_scope',
  });
});
