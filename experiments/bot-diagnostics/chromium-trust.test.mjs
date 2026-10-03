import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { constants } from 'node:fs';
import { inspectChromiumTrust, assertChromiumTrustWritable } from './chromium-trust.mjs';

async function fixture(fn) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'chromium-trust-'));
  try { await fn(root); } finally { await rm(root, { recursive: true, force: true }); }
}

test('existing legacy NSS database takes precedence over XDG and inspects metadata only', async () => {
  await fixture(async root => {
    const home = path.join(root, 'home');
    const legacy = path.join(home, '.pki', 'nssdb');
    const xdg = path.join(root, 'xdg', 'pki', 'nssdb');
    await mkdir(legacy, { recursive: true });
    await mkdir(xdg, { recursive: true });
    await writeFile(path.join(legacy, 'cert9.db'), 'private database bytes');
    let accesses = 0;
    const result = await inspectChromiumTrust({ homeDirectory: home, dataDirectory: path.join(root, 'xdg'), accessFn: async () => { accesses++; } });
    assert.equal(result.path, legacy);
    assert.equal(result.source, 'legacy');
    assert.deepEqual(result.files, { 'cert9.db': true, 'key4.db': false, 'pkcs11.txt': false });
    assert.deepEqual(result.accessFailures, []);
    assert.equal(accesses, 2); // directory and existing file metadata checks only
  });
});

test('missing NSS store is reported without creating or failing on it', async () => {
  await fixture(async root => {
    const home = path.join(root, 'home');
    const data = path.join(root, 'xdg');
    const result = await assertChromiumTrustWritable({ homeDirectory: home, dataDirectory: data });
    assert.equal(result.path, path.join(data, 'pki', 'nssdb'));
    assert.equal(result.source, 'xdg');
    assert.equal(result.exists, false);
    assert.deepEqual(result.accessFailures, []);
  });
});

test('existing database permission failures provide actionable error and details', async () => {
  await fixture(async root => {
    const home = path.join(root, 'home');
    const db = path.join(home, '.pki', 'nssdb');
    const cert = path.join(db, 'cert9.db');
    await mkdir(db, { recursive: true });
    await writeFile(cert, 'opaque');
    const accessFn = async (target, mode) => {
      if (target === cert && (mode & constants.W_OK)) throw Object.assign(new Error('readonly'), { code: 'EACCES' });
    };
    const result = await inspectChromiumTrust({ homeDirectory: home, accessFn });
    assert.deepEqual(result.accessFailures, [{ path: cert, access: 'read_write', code: 'EACCES' }]);
    await assert.rejects(assertChromiumTrustWritable({ homeDirectory: home, accessFn }), error =>
      error.message === `chromium_nss_db_not_writable: ${db}; grant filesystem write access to this existing NSS directory; certificate verification remains enabled`);
  });
});
