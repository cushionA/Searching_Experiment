import test from 'node:test';
import assert from 'node:assert/strict';
import {config} from './runtime.mjs';
import {selectedClients,runtimeMetadataKind} from './runner-cli.mjs';

test('legacy runner keeps its default clients and admits explicit 4play selection',()=>{
  const original=process.env.BOT_DIAGNOSTICS_CLIENTS;
  try {
    delete process.env.BOT_DIAGNOSTICS_CLIENTS;
    assert.deepEqual(selectedClients(),config.clients);
    process.env.BOT_DIAGNOSTICS_CLIENTS='4play';
    assert.deepEqual(selectedClients(),['4play']);
    process.env.BOT_DIAGNOSTICS_CLIENTS='4play,patchright';
    assert.deepEqual(selectedClients(),['4play','patchright']);
    process.env.BOT_DIAGNOSTICS_CLIENTS='unknown';
    assert.throws(selectedClients,/Unknown client/);
  } finally {
    if(original===undefined) delete process.env.BOT_DIAGNOSTICS_CLIENTS;
    else process.env.BOT_DIAGNOSTICS_CLIENTS=original;
  }
});

test('4play-only run metadata does not require local browser versions',()=>{
  assert.equal(runtimeMetadataKind(['4play']),'remote_4play');
  assert.equal(runtimeMetadataKind(['4play','patchright']),'local_browser_engines');
});
