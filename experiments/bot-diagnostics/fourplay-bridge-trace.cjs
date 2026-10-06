// Passive phase probe for the existing bridge, enabled only by the fixture harness.
const fs=require('node:fs');
const fplay=require('@lawlers/4play');
const output=process.env.PHASE_BRIDGE_TRACE;
if(!output) throw new Error('PHASE_BRIDGE_TRACE required');
const tab=value=>({id:value?.id,url:value?.url,status:value?.status,container:value?.container});
const write=(kind,data)=>fs.appendFileSync(output,JSON.stringify({at:new Date().toISOString(),kind,data})+'\n');
for(const method of ['tab_open','get_tab_list','tab_close']) {
  const original=fplay[method];
  fplay[method]=async(...args)=>{
    const start=performance.now();const result=await original(...args);
    write(method,{ms:performance.now()-start,value:method==='get_tab_list'?result.map(tab):method==='tab_open'?tab(result):result});return result;
  };
}
for(const event of ['dom_ready','dom_load_fail','web_response']) fplay.event.on(event,value=>write(event,
  {...tab(value),type:value?.type,error:value?.error}));
