import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const pwRoot = process.env.DAILY_TECH_PW_ROOT || '/tmp/daily-tech-pw/node_modules/playwright';
const { chromium } = require(pwRoot);
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
const dir = path.join(root, 'experiments/daily-tech-20261011');
const out = path.join(root, 'benchmarks/daily-tech-20261011');
const sha = data => crypto.createHash('sha256').update(data).digest('hex');
const protocolBytes = fs.readFileSync(path.join(dir, 'protocol.json'));
const protocol = JSON.parse(protocolBytes);
const input = fs.readFileSync(path.join(dir,'fixture.html'),'utf8');
const result = { started_at: new Date().toISOString(), playwright:require(pwRoot+'/package.json').version,
  node:process.version, protocol_sha256:sha(protocolBytes), fixture_sha256:sha(input),
  runner_sha256:sha(fs.readFileSync(new URL(import.meta.url))), requests:[],errors:[], timings:[] };
let browser;
const started = performance.now();
try {
  browser=await chromium.launch({headless:true});
  result.browser=browser.version();
  const context=await browser.newContext({offline:true});
  context.on('request',req=>result.requests.push(req.url()));
  const page=await context.newPage();
  page.on('pageerror',e=>result.errors.push(String(e)));
  await page.setContent(input);
  await page.evaluate(()=>{
    const anchor=(name,label)=>{const a=document.createElement('a');a.href=`https://fixture.invalid/${name}`;a.textContent=label;return a;};
    const open=document.getElementById('open-host').attachShadow({mode:'open'});
    open.append(anchor('open','公開シャドウのリンク'));
    const slot=document.createElement('slot');slot.name='nav';open.append(slot);
    const nested=document.createElement('div');open.append(nested);
    nested.attachShadow({mode:'open'}).append(anchor('nested','入れ子のリンク'));
    document.getElementById('closed-host').attachShadow({mode:'closed'}).append(anchor('closed','閉じたシャドウ'));
    document.getElementById('child').srcdoc='<a href="https://fixture.invalid/frame">子フレームリンク</a>';
  });
  const child=await page.locator('#child').elementHandle();
  const frame=await child.contentFrame();
  await frame.waitForSelector('a');
  result.live_locator=await page.locator('a').evaluateAll(as=>as.map(a=>a.href).sort());
  const hashes={default:new Set(),shadow:new Set()};
  for(let i=0;i<protocol.repeats;i++){
    for(const mode of i%2?['shadow','default']:['default','shadow']){
      const t=performance.now();const html=await page.content({includeShadow:mode==='shadow'});
      result.timings.push({repeat:i,mode,ms:performance.now()-t,bytes:Buffer.byteLength(html)});
      hashes[mode].add(sha(html));
      if(i===0)fs.writeFileSync(path.join(out,mode+'.html'),html);
    }
  }
  fs.writeFileSync(path.join(out,'frame.html'),await frame.content({includeShadow:true}));
  result.snapshot_hashes=Object.fromEntries(Object.entries(hashes).map(([k,v])=>[k,[...v]]));
  result.node_max_rss_kib=process.resourceUsage().maxRSS;
} catch(e) {result.errors.push(String(e));process.exitCode=1;}
finally {if(browser)await browser.close();result.total_seconds=(performance.now()-started)/1000;fs.writeFileSync(path.join(out,'shadow-result.json'),JSON.stringify(result,null,2)+'\n');}
