async function crawl(api,input){
  const listingUrl=input.start_url;
  const excluded=new Set((input.exclude_urls||[]).map(u=>{try{return new URL(u,listingUrl).href}catch{return String(u)}}));
  const seen=new Set();
  const initial=[];
  const revealed=[];
  const collect=async(bucket)=>{
    const selector=await api.count('main a.product-card[href]')>0?'main a.product-card[href]':'main a[href]';
    const links=await api.links(selector);
    for(const link of links||[]){
      if(!link||!link.url) continue;
      let url;
      try{url=new URL(link.url,listingUrl).href}catch{continue}
      if(url===listingUrl||seen.has(url)||excluded.has(url)) continue;
      let base;
      let parsed;
      try{base=new URL(listingUrl);parsed=new URL(url)}catch{continue}
      if(parsed.origin!==base.origin||parsed.href.split('#')[0]===base.href.split('#')[0]) continue;
      seen.add(url);
      bucket.push(url);
    }
  };
  await collect(initial);
  const maxScroll=Math.min(6,Math.max(0,Number(input.max_scroll_actions)||6));
  const maxLoops=Math.min(12,Math.max(1,Number(input.max_loop_iterations)||12));
  let scrolls=0;
  for(let i=0;i<maxLoops&&scrolls<maxScroll;i++){
    const before=seen.size;
    await api.scroll();
    scrolls++;
    await api.wait(500);
    await collect(revealed);
    if(seen.size===before) break;
  }
  const maxItems=Math.max(0,Number(input.max_items)||0);
  const candidates=[...revealed,...initial];
  const saved=[];
  for(let i=0;i<candidates.length&&saved.length<maxItems&&i<maxLoops*10;i++){
    const url=candidates[i];
    if(excluded.has(url)) continue;
    const result=await api.save(url);
    if(result&&result.saved) saved.push(result);
  }
  return saved;
}
