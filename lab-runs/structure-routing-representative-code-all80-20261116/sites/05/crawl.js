async function crawl(api,input){
  const listingUrl=input.start_url;
  const limit=Math.max(0,Math.min(Number(input.max_items)||0,3));
  const maxScroll=Math.max(0,Math.min(Number(input.max_scroll_actions)||0,6));
  const selector='.products .product h3 a[href]';
  const initial=[];
  const revealed=[];
  const excluded=new Set(Array.isArray(input.exclude_urls)?input.exclude_urls:[]);
  const add=(target,items)=>{for(const item of items||[]){const url=item&&item.url;if(typeof url==='string'&&url&&url!==listingUrl&&!excluded.has(url)&&!target.includes(url))target.push(url);}};
  if(await api.exists(selector))add(initial,await api.links(selector));
  for(let i=0;i<maxScroll;i++){const result=await api.scroll();const links=(result&&result.links||[]).filter(item=>item&&typeof item.url==='string'&&/\/product\//.test(item.url));add(revealed,links);}
  const candidates=revealed.concat(initial.filter(url=>!revealed.includes(url)));
  let saved=0;
  for(let i=0;i<candidates.length&&saved<limit;i++){const result=await api.save(candidates[i]);if(result&&result.saved)saved++;}
  return {saved};
}