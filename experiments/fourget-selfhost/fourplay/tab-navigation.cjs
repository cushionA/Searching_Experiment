/* Open a Firefox tab at blank, then navigate it with an ordinary page script. */
function idOf(value) {
  return String(typeof value === 'object' ? value?.id : value);
}

async function waitForBlankTab(tab, {getTabs, container, timeoutMs=5000, pollMs=100,
  now=()=>performance.now(), sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}) {
  if (!tab || tab.id == null) throw new Error('tab_open_failed');
  const started=now();
  while (now()-started < timeoutMs) {
    const remaining=timeoutMs-(now()-started);
    let timer,tabs;
    try {
      tabs=await Promise.race([Promise.resolve().then(getTabs),new Promise(resolve=>{
        timer=setTimeout(()=>resolve(null),remaining);
      })]);
    } finally { clearTimeout(timer); }
    if (tabs === null) throw new Error('blank_tab_timeout');
    const observed=Array.isArray(tabs)?tabs.find(item=>item?.id===tab.id):null;
    if (observed && idOf(observed.container) !== idOf(container)) throw new Error('tab_container_mismatch');
    if (observed?.url==='about:blank' && observed.status==='complete' &&
        idOf(observed.container)===idOf(container)) return observed;
    await sleep(Math.min(pollMs,Math.max(0,timeoutMs-(now()-started))));
  }
  throw new Error('blank_tab_timeout');
}

function injectionSucceeded(result) {
  if (result?.status !== true) return false;
  const frames=Array.isArray(result.result)?result.result:null;
  if (!frames) return result.result === true;
  const main=frames.find(frame=>frame.frameId===0) || frames[0];
  return main?.result === true;
}

function pageNavigationExpression(url) {
  const pageSource=`location.assign(${JSON.stringify(url)});`;
  return `(()=>{const d=document;if(!d||!d.documentElement)return false;const s=d.createElement('script');s.textContent=${JSON.stringify(pageSource)};try{d.documentElement.appendChild(s);return true;}finally{s.remove();}})()`;
}

async function openDocumentTab({tabOpen,getTabs,inject,container,url,onTab,
  timeoutMs=5000,pollMs=100,now,sleep}) {
  let target;
  try { target=new URL(url); } catch { throw new Error('invalid_navigation_url'); }
  if (!['http:','https:'].includes(target.protocol) || target.username || target.password)
    throw new Error('invalid_navigation_url');
  const tab=await tabOpen('about:blank',false,container);
  if (typeof onTab==='function') await onTab(tab);
  await waitForBlankTab(tab,{getTabs,container,timeoutMs,pollMs,...(now?{now}:{}),...(sleep?{sleep}:{})});
  const expression=pageNavigationExpression(target.href);
  const result=await inject(tab,expression,true);
  if (!injectionSucceeded(result)) throw new Error('navigation_injection_failed');
  return tab;
}

module.exports={waitForBlankTab,pageNavigationExpression,openDocumentTab};
