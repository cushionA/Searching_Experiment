/** Reproduce the browser/worker connection on a loopback fixture with real OWLv2. */
import path from 'node:path';
import fs from 'node:fs';
import {parseArgs} from 'node:util';
import {startFixtureServer} from '../../local_visual_browser.fixture.mjs';
import {main} from '../../run_local_visual_browser.mjs';

const {values} = parseArgs({options: {
  'model-root': {type: 'string'}, 'output-dir': {type: 'string'}, python: {type: 'string'},
}});
if (!values['model-root'] || !values['output-dir']) throw new Error('model-root and output-dir are required');
const fixture = await startFixtureServer();
try {
  const args = ['--url', fixture.origin + '/?mode=empty-B',
    '--model-root', values['model-root'], '--output-dir', values['output-dir'],
    '--max-attempts', '1', '--timeout-ms', '90000'];
  if (values.python) args.push('--python', values.python);
  const code = await main(args);
  const summary = JSON.parse(fs.readFileSync(path.join(values['output-dir'], 'summary.json')));
  if (code !== 0 || summary.outcome !== 'passed_after_image_challenge'
    || summary.attempts !== 1 || summary.classifications !== 1) throw new Error('Local browser smoke failed');
} finally { await fixture.close(); }
