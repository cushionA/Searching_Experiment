async function crawl(api,input){
  const listing=input.start_url;
  const excluded=new Set(Array.isArray(input.exclude_urls)?input.exclude_urls:[]);
  const maximum=Math.max(0,Number(input.max_items)||0);
  const found=[];
  const seen=new Set();
  const add=(items,prioritize)=>{
    const added=[];
    for(const item of items||[]){
      const url=typeof item==="string"?item:item&&item.url;
      if(!url||seen.has(url)||excluded.has(url)||!/^https?:\/\/[^/]+\/.*\/[^/]+_\d+\/index\.html(?:[?#].*)?$/i.test(url)) continue;
      seen.add(url);
      added.push(url);
    }
    if(prioritize) found.unshift(...added); else found.push(...added);
  };
  add(await api.links("article.product_pod a[href]"),false);
  const scrollLimit=Math.min(6,Math.max(0,Number(input.max_scroll_actions)||0));
  for(let i=0;i<scrollLimit&&found.length<maximum;i++){
    const result=await api.scroll();
    add(result&&result.links,true);
    add(await api.links("article.product_pod a[href]"),false);
  }
  let saved=0;
  for(let i=0;i<found.length&&saved<maximum;i++){
    const result=await api.save(found[i]);
    if(result&&result.saved) saved++;
  }
  return {saved};
}
