async function crawl(api,input){
const seen=new Set(input.exclude_urls||[]);
const candidates=[];
const add=links=>{for(const link of links||[]){const url=link&&link.url;if(!url||seen.has(url))continue;let parsed;try{parsed=new URL(url)}catch{continue}if(!/^https?:$/.test(parsed.protocol)||!/(^|\.)go\.jp$/i.test(parsed.hostname)||/\.pdf$/i.test(parsed.pathname))continue;seen.add(url);candidates.push(url)}};
add(await api.links("#main section a[href]"));
const scrollLimit=Math.min(6,Math.max(0,input.max_scroll_actions||0));
for(let i=0;i<scrollLimit;i++){const result=await api.scroll();add(result&&result.links)}
const limit=Math.min(3,Math.max(0,input.max_items||0));
let saved=0;
for(let i=0;i<candidates.length&&saved<limit;i++){const result=await api.save(candidates[i]);if(result&&result.saved)saved++}
return {saved};
}
