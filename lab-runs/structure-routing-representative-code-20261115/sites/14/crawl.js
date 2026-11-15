async function crawl(api,input){
  const limit=Math.min(3,Math.max(0,Number(input.max_items)||0));
  const start=new URL(input.start_url);
  const excluded=new Set((input.exclude_urls||[]).map(x=>{try{return new URL(x).href}catch{return String(x)}}));
  const initial=[];
  const revealed=[];
  const seen=new Set([start.href]);
  const add=(items,target)=>{
    for(const item of items||[]){
      if(!item||!item.url) continue;
      let u;
      try{u=new URL(item.url,start.href)}catch{continue}
      u.hash='';
      const url=u.href;
      if(u.origin!==start.origin||!/^\/docs\//.test(u.pathname)||!url.endsWith('.html')||seen.has(url)||excluded.has(url)) continue;
      seen.add(url);
      target.push(url);
    }
  };
  add(await api.links('main a[href]'),initial);
  for(let i=0;i<6;i++){
    const result=await api.scroll();
    add(result&&result.links,revealed);
  }
  const candidates=revealed.concat(initial);
  const saved=[];
  for(let i=0;i<candidates.length&&saved.length<limit;i++){
    const result=await api.save(candidates[i]);
    if(result&&result.saved) saved.push(result.url||candidates[i]);
  }
  return saved;
}