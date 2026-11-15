async function crawl(api,input){
 const start=input.start_url;
 const excluded=Array.isArray(input.exclude_urls)?input.exclude_urls:[];
 const limit=Math.max(0,Math.min(3,Number(input.max_items)||0));
 const targets=[];
 const seen=new Set([start,...excluded]);
 const origin=new URL(start).origin;
 const add=items=>{
  if(!Array.isArray(items))return;
  for(const link of items){
   if(!link||typeof link.url!=="string")continue;
   let parsed;
   try{parsed=new URL(link.url,start)}catch{continue}
   const url=parsed.href;
   if(parsed.origin!==origin||seen.has(url)||url===start||parsed.hash&&url.split("#")[0]===start.split("#")[0])continue;
   if(!String(link.text||"").trim())continue;
   if(!(new URL(link.url).pathname.includes('/learn/')))continue;
   seen.add(url);targets.push(url);
  }
 };
 add(await api.links("main a[href], a[href]"));
 const maxScroll=Math.max(0,Math.min(6,Number(input.max_scroll_actions)||0));
 let scrolls=0;
 for(let iteration=0;iteration<12&&targets.length<limit&&scrolls<maxScroll;iteration++){
  const result=await api.scroll();scrolls++;
  if(result&&Array.isArray(result.links))add(result.links);
  add(await api.links("main a[href], a[href]"));
 }
 let saved=0;
 for(let iteration=0;iteration<12&&saved<limit&&iteration<targets.length;iteration++){
  const result=await api.save(targets[iteration]);
  if(result&&result.saved)saved++;
 }
 return {saved,discovered:targets.length,scrolls};
}
