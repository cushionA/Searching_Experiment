async function crawl(api,input){
  const maxItems=Math.min(3,Math.max(0,Number(input.max_items)||0));
  const maxScroll=Math.min(6,Math.max(0,Number(input.max_scroll_actions)||0));
  const excluded=new Set((input.exclude_urls||[]).map(u=>String(u).split("#")[0]));
  const seen=new Set();
  const initial=[];
  const revealed=[];
  const add=(list,items)=>{
    for(const item of items||[]){
      if(!item||!item.url) continue;
      let url;
      try{url=new URL(item.url,input.start_url);}catch{continue;}
      url.hash="";
      const value=url.href;
      if(url.origin!==new URL(input.start_url).origin||value===String(input.start_url).split("#")[0]||excluded.has(value)||seen.has(value)) continue;
      seen.add(value);
      list.push(value);
    }
  };
  add(initial,await api.links(".toctree-wrapper a.reference[href]"));
  let previousState="";
  for(let i=0;i<maxScroll&&i<6&&revealed.length<maxItems;i++){
    const result=await api.scroll();
    if(!result) break;
    if(result.state_id&&result.state_id===previousState) break;
    previousState=result.state_id||"";
    add(revealed,result.links);
  }
  const candidates=revealed.concat(initial);
  let saved=0;
  let attempts=0;
  const attemptLimit=Math.min(12,candidates.length);
  for(let i=0;i<candidates.length&&saved<maxItems&&attempts<attemptLimit&&i<12;i++){
    const url=candidates[i];
    if(excluded.has(url)) continue;
    attempts++;
    const result=await api.save(url);
    if(result&&result.saved) saved++;
  }
  return {saved};
}
