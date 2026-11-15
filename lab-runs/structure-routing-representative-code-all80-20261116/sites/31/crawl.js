async function crawl(api,input){
  const start=input.start_url;
  const excluded=new Set(input.exclude_urls||[]);
  const candidates=[];
  const seen=new Set();
  const remember=(links)=>{
    for(const link of links||[]){
      const url=typeof link==="string"?link:link&&link.url;
      if(typeof url!=="string"||!url||seen.has(url)||excluded.has(url))continue;
      seen.add(url);
      if(url===start)continue;
      let parsed;
      try{parsed=new URL(url,start)}catch{continue}
      if(parsed.origin!==new URL(start).origin)continue;
      if(!parsed.pathname.startsWith("/stat-search")||!parsed.searchParams.has("toukei"))continue;
      candidates.push(url);
    }
  };
  remember(await api.links({css:"main a[href*=\"/stat-search?toukei=\"]"}));
  let previousState=null;
  for(let n=0;n<6&&candidates.length<input.max_items;n++){
    const result=await api.scroll();
    remember(result&&result.links);
    const state=result&&result.state_id;
    if(state&&state===previousState)break;
    previousState=state||null;
    if(!(result&&result.links&&result.links.length))break;
  }
  let saved=0;
  for(let n=0;n<candidates.length&&saved<input.max_items;n++){
    const result=await api.save(candidates[n]);
    if(result&&result.saved)saved++;
  }
  return {saved,observed_candidates:candidates.length};
}
