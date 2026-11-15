async function crawl(api,input){
  const limit=Math.max(0,Math.min(Number(input.max_items)||0,3));
  const excluded=new Set(Array.isArray(input.exclude_urls)?input.exclude_urls:[]);
  const candidates=[];
  const candidateSet=new Set();
  const listingSeen=new Set();
  let listing=input.start_url;
  let scrolls=0;
  const remember=async()=>{
    const found=await api.links('.test-items-container .card-head-url[href]');
    for(const link of found){
      const url=link&&link.url;
      if(typeof url==='string'&&url&&url!==input.start_url&&!excluded.has(url)&&!candidateSet.has(url)){
        candidateSet.add(url);
        candidates.push(url);
      }
    }
  };
  for(let i=0;i<12&&listing&&candidates.length<limit;i++){
    const state=await api.state();
    listing=state&&state.url?state.url:listing;
    if(listingSeen.has(listing))break;
    listingSeen.add(listing);
    await remember();
    if(candidates.length<limit&&scrolls<6){
      await api.scroll();
      scrolls++;
      await remember();
    }
    if(candidates.length>=limit||!(await api.exists('.page-link.next[href]')))break;
    const before=listing;
    const moved=await api.click('.page-link.next[href]');
    const after=moved&&moved.url;
    if(!after||after===before||listingSeen.has(after))break;
    listing=after;
  }
  let saved=0;
  for(let i=0;i<candidates.length&&saved<limit;i++){
    const result=await api.save(candidates[i]);
    if(result&&result.saved)saved++;
  }
  return {saved};
}