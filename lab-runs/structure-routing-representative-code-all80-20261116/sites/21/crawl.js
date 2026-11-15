async function crawl(api,input){
  const maxItems=Math.max(0,Math.min(3,Number(input.max_items)||0));
  const maxLoops=Math.max(0,Math.min(12,Number(input.max_loop_iterations)||12));
  const maxScroll=Math.max(0,Math.min(6,Number(input.max_scroll_actions)||6));
  const start=String(input.start_url||"");
  const excluded=Array.isArray(input.exclude_urls)?input.exclude_urls:[];
  const scrolled=[];
  const initial=[];
  const add=(target,items)=>{
    if(!Array.isArray(items))return;
    for(const item of items){
      const url=typeof item==="string"?item:item&&item.url;
      if(typeof url!=="string"||!url||url===start)continue;
      let parsed;
      let origin;
      try{parsed=new URL(url);origin=new URL(start).origin;}catch{continue;}
      if(parsed.origin!==origin||!parsed.pathname.includes("/abs/"))continue;
      if(excluded.includes(url)||target.includes(url))continue;
      target.push(url);
    }
  };
  add(initial,await api.links('#articles dt a[href^="/abs/"]'));
  let scrolls=0;
  for(let i=0;i<maxLoops&&scrolls<maxScroll&&scrolled.length<maxItems*4;i++){
    const result=await api.scroll();
    scrolls++;
    add(scrolled,result&&result.links);
  }
  const candidates=scrolled.concat(initial);
  let saved=0;
  for(let i=0;i<maxLoops&&i<candidates.length&&saved<maxItems;i++){
    const result=await api.save(candidates[i]);
    if(result&&result.saved)saved++;
  }
  return {saved,scroll_actions:scrolls};
}