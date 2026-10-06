import {createHash} from 'node:crypto';
import {classify} from './observations.mjs';

export async function readDOMSnapshot(page) {
  return page.evaluate(()=>({url:location.href,title:document.title,text:(document.body?.innerText||'').slice(0,200000),
    links:[...document.querySelectorAll('a[href]')].map(a=>({href:a.href,text:(a.innerText||'').trim()})).filter(a=>/^https?:/.test(a.href)&&a.text).slice(0,2000)}));
}
export function summarizeDOM(snapshot,terms=[]) {
  const text=snapshot.text||'';
  const candidates=(snapshot.links||[]).filter(link=>terms.length&&terms.some(term=>link.text.includes(term)));
  return {url:snapshot.url,title:snapshot.title,text_chars:text.length,links:(snapshot.links||[]).length,
    matching_links:candidates.length,matching_hrefs:[...new Set(candidates.map(link=>link.href))],
    terms_present:terms.every(term=>text.includes(term)),
    signature:createHash('sha256').update(JSON.stringify([text,snapshot.links])).digest('hex'),
    outcome:classify(200,text).outcome};
}
// Read-only bounded polling; no click, recovery, navigation or CAPTCHA action.
export async function waitForDOMStability(page,{timeoutMs=5000,stableMs=750,pollMs=250,minTextChars=200,terms=[]}={},
  {read=readDOMSnapshot,now=()=>performance.now(),sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}={}) {
  if(!Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>10000||!Number.isInteger(stableMs)||stableMs<1||stableMs>timeoutMs||
    !Number.isInteger(pollMs)||pollMs<1||pollMs>timeoutMs||!Number.isInteger(minTextChars)||minTextChars<0||
    !Array.isArray(terms)||terms.some(term=>typeof term!=='string'||!term)) throw new Error('invalid_dom_wait_policy');
  const started=now(),samples=[];let previous=null,stableSince=started;
  const finish=outcome=>({outcome,elapsed_ms:now()-started,policy:{timeoutMs,stableMs,pollMs,minTextChars,terms},samples});
  while(now()-started<timeoutMs) {
    const remaining=timeoutMs-(now()-started);let timer;
    let snapshot;
    try {snapshot=await Promise.race([read(page),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('dom_read_timeout')),remaining);})]);}
    catch(error) {if(error.message==='dom_read_timeout') return finish('timeout');throw error;}
    finally {clearTimeout(timer);}
    const summary=summarizeDOM(snapshot,terms);samples.push({elapsed_ms:now()-started,...summary});
    if(['challenge_observed','access_denied_observed'].includes(summary.outcome)) return finish('stopped_'+summary.outcome);
    if(summary.signature!==previous) stableSince=now();previous=summary.signature;
    if(summary.text_chars>=minTextChars&&summary.terms_present&&summary.matching_links>0&&now()-stableSince>=stableMs) return finish('ready');
    await sleep(Math.min(pollMs,Math.max(0,timeoutMs-(now()-started))));
  }
  return finish('timeout');
}
