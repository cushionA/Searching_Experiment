export function classifySearchObservation({query,defaultQuery,outcome,candidateCount,officialDocsCount,officialDocsCitationCount=0}) {
  const challenge=outcome==='challenge_observed'||outcome==='access_denied_observed';
  const defaultCase=query===defaultQuery;
  if(challenge) return {search_state:'challenge',search_succeeded:defaultCase?false:null};
  if(outcome!=='content_observed') return {search_state:'unverified',search_succeeded:null};
  if(candidateCount===0) return {search_state:'zero_candidates',search_succeeded:defaultCase?false:null};
  if(!defaultCase) return {search_state:'candidate_links_observed',search_succeeded:null};
  if(officialDocsCount>0) return {search_state:'official_docs_link_found',search_succeeded:true};
  if(officialDocsCitationCount>0) return {search_state:'official_docs_citation_found_destination_unresolved',search_succeeded:null};
  return {search_state:'expected_official_link_missing',search_succeeded:false};
}
