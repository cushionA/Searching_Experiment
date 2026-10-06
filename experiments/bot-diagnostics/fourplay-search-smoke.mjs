import fs from 'node:fs';
import path from 'node:path';
import {Evidence,verify,safeError} from './runner.mjs';
import {ToolAdapter} from './adapters.mjs';
import {classifySearchObservation} from './fourplay-search-classification.mjs';
import {extractPage} from './fourplay-search-parser.mjs';

const ENGINES={
  brave:{origin:'https://search.brave.com',path:'/search'},
  google:{origin:'https://www.google.com',path:'/search'}
};
const DEFAULT_QUERY='Python documentation';

function parseArgs(args) {
  let directory=null,engine='brave',query=DEFAULT_QUERY,observeMs=1500;
  for(let index=0;index<args.length;index++) {
    const value=args[index];
    if(value==='--engine'||value==='--query'||value==='--observe-ms') {
      const next=args[++index];
      if(!next) throw new Error(`missing_value:${value}`);
      if(value==='--engine') engine=next;
      else if(value==='--query') query=next;
      else observeMs=Number(next);
    } else if(value.startsWith('--engine=')) engine=value.slice(9);
    else if(value.startsWith('--query=')) query=value.slice(8);
    else if(value.startsWith('--observe-ms=')) observeMs=Number(value.slice(13));
    else if(value.startsWith('-')) throw new Error(`unknown_option:${value}`);
    else if(directory===null) directory=value;
    else throw new Error('too_many_directories');
  }
  if(!directory) throw new Error('Usage: fourplay-search-smoke.mjs NEW_OUTPUT_DIRECTORY [--engine brave|google] [--query TEXT] [--observe-ms N]');
  if(!Object.hasOwn(ENGINES,engine)) throw new Error('engine_must_be_brave_or_google');
  if(typeof query!=='string'||!query.trim()||query.length>1000) throw new Error('invalid_query');
  if(!Number.isInteger(observeMs)||observeMs<0||observeMs>6000) throw new Error('invalid_observe_ms');
  return {directory:path.resolve(directory),engine,query:query.trim(),observeMs};
}


const {directory,engine,query,observeMs}=parseArgs(process.argv.slice(2));
if(fs.existsSync(directory)) throw new Error('output_directory_already_exists');
const engineConfig=ENGINES[engine];
const escapedQuery=encodeURIComponent(query).replace(/[!'()*]/g,char=>`%${char.charCodeAt(0).toString(16).toUpperCase()}`);
const searchURL=`${engineConfig.origin}${engineConfig.path}?q=${escapedQuery}`;
if(new URL(searchURL).origin!==engineConfig.origin) throw new Error('search_url_origin_mismatch');

const evidence=new Evidence(directory,false,{policy:'browser_observation',
  authorization:'User requested a single public search observation through 4play'});
const site={id:`fourplay-${engine}`,origins:[engineConfig.origin],links:{home:searchURL,targets:[]}};
const adapter=new ToolAdapter({tool:'4play',site,evidence,fixture:false,
  profile:{id:'search-smoke',extensions:[]},options:{captureScreenshots:false,observeMs}});
let result=null,runError=null,closeError=null;
try {
  await adapter.open();
  result=await adapter.goto(searchURL);
} catch(error) {runError=safeError(error);}
finally {try {await adapter.close();} catch(error) {closeError=safeError(error);}}

let page={visibleText:'',links:[],officialDocs:[],officialDocsCitations:[]};
if(result?.dom_sha256) {
  const html=fs.readFileSync(path.join(directory,'blobs',result.dom_sha256),'utf8');
  page=extractPage(html,result.final_url||searchURL);
}
const observationCompleted=!!result?.dom_sha256&&!['execution_error','navigation_unverified'].includes(result.outcome);
const searchClassification=classifySearchObservation({query,defaultQuery:DEFAULT_QUERY,outcome:result?.outcome,
  candidateCount:page.links.length,officialDocsCount:page.officialDocs.length,
  officialDocsCitationCount:page.officialDocsCitations.length});
const verification=verify(directory);
const summary={engine,query,requested_url:searchURL,final_url:result?.final_url||null,
  http_status:result?.http_status??null,browser_outcome:result?.outcome||'execution_error',
  observation_state:observationCompleted?'completed':result?.dom_sha256?'partial':'failed',
  ...searchClassification,search_success_rule:query===DEFAULT_QUERY?'resolved link URL host docs.python.org required; citation alone leaves success unverified':'unverified; links are observations only',
  candidate_link_count:page.links.length,candidate_link_count_is_serp_result_count:false,
  candidates:page.links,official_docs_count:page.officialDocs.length,
  official_docs:page.officialDocs,official_docs_citation_count:page.officialDocsCitations.length,
  official_docs_citations:page.officialDocsCitations,visible_text_prefix:page.visibleText,
  candidate_scan_truncated:page.candidateScanTruncated||false,
  observation_limits:result?.observation_limits||[],result_record:result,
  ...(runError?{run_error:runError}:{}),...(closeError?{close_error:closeError}:{}),verification};
evidence.save('summary.json',summary);
evidence.save('verification.json',verification);
evidence.flush(true);
console.log(JSON.stringify({directory,...summary},null,2));
if(!verification.ok||runError||closeError) process.exitCode=1;
