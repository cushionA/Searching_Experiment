import os from 'node:os';
import path from 'node:path';
import { access, stat } from 'node:fs/promises';
import { constants } from 'node:fs';

const DB_FILES = ['cert9.db', 'key4.db', 'pkcs11.txt'];

/** Inspect Chromium's Linux NSS database location without opening its contents. */
export async function inspectChromiumTrust({
  homeDirectory = os.homedir(),
  dataDirectory = process.env.XDG_DATA_HOME || path.join(homeDirectory, '.local', 'share'),
  accessFn = access,
  statFn = stat,
} = {}) {
  const legacyPath = path.join(homeDirectory, '.pki', 'nssdb');
  const legacyExists = await exists(legacyPath, statFn, true);
  const source = legacyExists ? 'legacy' : 'xdg';
  const directory = legacyExists ? legacyPath : path.join(dataDirectory, 'pki', 'nssdb');
  const directoryExists = legacyExists || await exists(directory, statFn, true);
  const files = Object.fromEntries(await Promise.all(DB_FILES.map(async name => [
    name, directoryExists && await exists(path.join(directory, name), statFn),
  ])));
  const accessFailures = [];
  if (directoryExists) {
    await check(directory, constants.R_OK | constants.W_OK | constants.X_OK, 'read_write_execute', accessFn, accessFailures);
    for (const name of DB_FILES) {
      if (files[name]) await check(path.join(directory, name), constants.R_OK | constants.W_OK, 'read_write', accessFn, accessFailures);
    }
  }
  return { path: directory, source, exists: directoryExists, files, accessFailures };
}

/** Fail early when an existing NSS store cannot be used for Chromium trust. */
export async function assertChromiumTrustWritable(options) {
  const result = await inspectChromiumTrust(options);
  if (result.exists && result.accessFailures.length) {
    throw new Error(`chromium_nss_db_not_writable: ${result.path}; grant filesystem write access to this existing NSS directory; certificate verification remains enabled`);
  }
  return result;
}

async function exists(target, statFn, directory = false) {
  try { const info = await statFn(target); return directory ? info.isDirectory() : true; } catch (error) {
    if (error?.code === 'ENOENT' || error?.code === 'ENOTDIR') return false;
    throw error;
  }
}

async function check(target, mode, access, accessFn, failures) {
  try { await accessFn(target, mode); } catch (error) {
    failures.push({ path: target, access, code: error?.code || 'EACCES' });
  }
}
