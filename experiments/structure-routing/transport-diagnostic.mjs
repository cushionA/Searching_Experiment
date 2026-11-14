import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {installDocumentGuard} from './document-guard.mjs';
import {environmentContextOptions} from './environment.mjs';
const require=createRequire(import.meta.url);
const {chromium}=require('../../.deps/bot-diagnostics/node_modules/playwright');
const run=path.resolve('lab-runs/structure-routing-20261115');
const directory=path.join(run,'artifacts/stage-c-transport-diagnostic');
if(fs.existsSync(path.join(directory,'result.json')))throw new Error('diagnostic_already_completed');
fs.mkdirSync(directory,{recursive:true});
const rows=[];
for(const arm of [{name:'inherited_proxy_http1',proxy:true,args:['--disable-http2']},{name:'browser_direct',proxy:false,args:[]}]){
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'/usr/bin/chromium',args:arm.args});
 const context=await browser.newContext({...arm.proxy?environmentContextOptions():{},locale:'ja-JP',timezoneId:'Asia/Tokyo',viewport:{width:1365,height:900},serviceWorkers:'block'});
 const page=await context.newPage();let requests=0,responses=0;
 page.on('request',()=>requests++);page.on('response',r=>{if(r.request().resourceType()==='document')responses++});
 const guard=await installDocumentGuard(page,{limit:1,beforeRequest:({method,url})=>method==='GET'&&new URL(url).hostname==='books.toscrape.com'});
 let status=null,error=null,body=Buffer.alloc(0),title='';
 try{
  const response=await page.goto('https://books.toscrape.com/',{waitUntil:'domcontentloaded',timeout:20000});
  status=response?.status()??null;body=response?await response.body():body;title=await page.title();
  fs.writeFileSync(path.join(directory,`${arm.name}.source.html`),body);
  fs.writeFileSync(path.join(directory,`${arm.name}.dom.html`),await page.content());
 }catch(e){error=String(e.message).slice(0,250)}
 await guard.seal();await context.close();await browser.close();
 rows.push({arm:arm.name,browser_version:browser.version(),document_attempts:guard.counters.reserved,document_responses:responses,requests,status,body_bytes:body.length,title,error,guard:guard.counters,robots_basis:'recent inherited-proxy curl robots404; unavailable policy per protocol; diagnostic only, not target success'});
 fs.writeFileSync(path.join(directory,'progress.json'),JSON.stringify(rows,null,2));
}
const result={stage:'C',type:'transport_diagnostic',at:new Date().toISOString(),document_cap:2,document_attempts:rows.reduce((n,x)=>n+x.document_attempts,0),requests:rows.reduce((n,x)=>n+x.requests,0),rows};
fs.writeFileSync(path.join(directory,'result.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result));
