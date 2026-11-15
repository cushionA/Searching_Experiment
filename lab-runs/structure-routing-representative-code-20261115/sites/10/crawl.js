async function crawl(api,input){
  const listingUrl=input.start_url;
  const maxItems=Math.max(0,Math.min(Number(input.max_items)||0,3));
  const maxScroll=Math.max(0,Math.min(Number(input.max_scroll_actions)||0,6));
  const maxLoops=Math.max(1,Math.min(Number(input.max_loop_iterations)||1,12));
  const excluded=new Set((input.exclude_urls||[]).map(String));
  const seen=new Set();
  const candidates=[];
  const newlyRevealed=[];
  const collect=async()=>{
    const links=await api.links('article.product a[href]');
    for(const link of links){
      if(!link||!link.url) continue;
      const url=String(link.url);
      if(!seen.has(url)){
        seen.add(url);
        if(!excluded.has(url)) candidates.push(url);
      }
    }
  };
  await collect();
  let loops=0;
  for(let n=0;n<maxScroll&&loops<maxLoops;n++){
    loops++;
    const beforeLinks=new Set(seen);
    const before=seen.size;
    await api.scroll();
    await api.wait(700);
    await collect();
    if(seen.size>before){
      const current=await api.links('article.product a[href]');
      for(const link of current){if(link&&link.url&&seen.has(String(link.url))&&!beforeLinks.has(String(link.url))&&!excluded.has(String(link.url))&&!newlyRevealed.includes(String(link.url))) newlyRevealed.push(String(link.url));}
    }
    if(seen.size===before) break;
  }
  const prioritized=[...newlyRevealed,...candidates.filter(url=>!newlyRevealed.includes(url))];
  const saved=[];
  for(const url of prioritized){
    if(saved.length>=maxItems||loops>=maxLoops) break;
    if(saved.length>0){if(loops+1>=maxLoops) break;await api.open(listingUrl);loops++;}
    if(loops>=maxLoops) break;
    const result=await api.save(url);
    loops++;
    if(result&&result.saved) saved.push(url);
  }
  return saved;
}
