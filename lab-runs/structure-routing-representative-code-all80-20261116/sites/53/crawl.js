async function crawl(api,input){
 const limit=Math.min(3,Number(input.max_items)||3);
 const excluded=new Set((input.exclude_urls||[]).map(x=>String(x)));
 const listing=input.start_url;
 const found=[];
 const add=items=>{for(const item of items||[]){const url=typeof item==='string'?item:item&&item.url;if(!url||url===listing||excluded.has(url)||found.includes(url))continue;if(!url.startsWith(new URL(listing).origin+'/'))continue;if(!/\/news\/articles\//.test(new URL(url).pathname))continue;found.push(url);}};
 add(await api.links('main article a[href]'));
 for(let i=0;i<Math.min(6,Number(input.max_scroll_actions)||6)&&i<6;i++){
  const result=await api.scroll();
  add(result&&result.links);
  if(found.length<limit)add(await api.links('main article a[href]'));
 }
 let saved=0;
 for(let i=0;i<found.length&&saved<limit&&i<12;i++){
  const result=await api.save(found[i]);
  if(result&&result.saved)saved++;
 }
 return {saved,observed_candidates:found.length};
}

