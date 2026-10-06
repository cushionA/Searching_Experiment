/* Match completion to an observed document, never a newly created blank tab. */
const idOf=value=>String(typeof value==='object'?value?.id:value);
function documentURL(value) {
  try {const url=new URL(value);if(!['http:','https:'].includes(url.protocol)) return null;url.hash='';return url.href;}
  catch {return null;}
}
function completedDocument(tab,session) {
  const url=documentURL(tab?.url);
  return !!url&&tab.status==='complete'&&tab.id===session.tab.id&&idOf(tab.container)===idOf(session.container)&&
    session.responses.some(response=>response.type==='main_frame'&&response.id===tab.id&&
      idOf(response.container)===idOf(session.container)&&documentURL(response.url)===url);
}
function terminalFailure(session,tab,requestedURL) {
  const urls=[documentURL(requestedURL),documentURL(tab?.url)].filter(Boolean);
  return session.errors.some(error=>error.error==='response_capture_limit'||
    (error.id===session.tab.id&&idOf(error.container)===idOf(session.container)&&
      urls.includes(documentURL(error.url))&&!/^HTTP\/[\d.]+\s+[45]\d\d\b/i.test(error.error||'')));
}
async function waitForDocument(session,{getTabs,requestedURL,timeoutMs=18000,pollMs=100,
  now=()=>performance.now(),sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}) {
  const started=now();let polls=0;
  const result=outcome=>({outcome,elapsed_ms:now()-started,polls});
  while(now()-started<timeoutMs) {
    let timer,tabs;const remaining=timeoutMs-(now()-started);
    try {tabs=await Promise.race([getTabs(),new Promise(resolve=>{timer=setTimeout(()=>resolve(null),remaining);})]);}
    finally {clearTimeout(timer);}
    polls++;
    if(tabs===null) return result('timeout');
    const tab=Array.isArray(tabs)?tabs.find(tab=>tab.id===session.tab.id):null;
    if(completedDocument(tab,session)) return result('complete');
    if(terminalFailure(session,tab,requestedURL)) return result('navigation_failed');
    await sleep(Math.min(pollMs,Math.max(0,timeoutMs-(now()-started))));
  }
  return result('timeout');
}
module.exports={completedDocument,waitForDocument};
