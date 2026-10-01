// Passive fixture marker. No requests, telemetry, fingerprint changes, or automation patches.
function mark() { if(document.documentElement) document.documentElement.setAttribute('data-bot-diagnostics-extension','loaded'); }
if(document.documentElement) mark();
else new MutationObserver((_,observer)=>{if(document.documentElement){mark();observer.disconnect();}}).observe(document,{childList:true,subtree:true});
