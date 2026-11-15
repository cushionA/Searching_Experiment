async function crawl(api,input){
  const start=input.start_url;
  const limit=Math.max(0,Math.min(Number(input.max_items)||0,3));
  const excluded=new Set([start,...(Array.isArray(input.exclude_urls)?input.exclude_urls:[])]);
  const seen=new Set();
  const candidates=[];
  const origin=new URL(start).origin;
  const maxLoops=12;
  const maxScrolls=6;
  let scrolls=0;
  for(let i=0;i<maxLoops&&candidates.length<limit&&scrolls<maxScrolls;i++){
    const result=await api.scroll();
    scrolls++;
    const found=Array.isArray(result&&result.links)?result.links:await api.links('a[href*="/products/"][href$=".html"]');
    for(const link of found){
      const url=link&&link.url;
      if(!url||seen.has(url)||excluded.has(url)) continue;
      seen.add(url);
      try{
        const parsed=new URL(url);
        if(parsed.origin!==origin||!/^\/products\/.+\.html$/.test(parsed.pathname)) continue;
      }catch(e){continue;}
      candidates.push(url);
    }
  }
  for(const url of candidates){
    if(candidates.length===0) break;
    await api.save(url);
    if(candidates.indexOf(url)+1>=limit) break;
  }
}
