import test from 'node:test';
import assert from 'node:assert/strict';
import {classifySearchObservation} from './fourplay-search-classification.mjs';

const classify=(overrides={})=>classifySearchObservation({query:'Python documentation',defaultQuery:'Python documentation',
  outcome:'content_observed',candidateCount:0,officialDocsCount:0,...overrides});

test('default search requires the expected official documentation link',()=>{
  assert.deepEqual(classify({candidateCount:12,officialDocsCount:0}),
    {search_state:'expected_official_link_missing',search_succeeded:false});
  assert.deepEqual(classify({candidateCount:12,officialDocsCount:1}),
    {search_state:'official_docs_link_found',search_succeeded:true});
});

test('custom queries report observed links without claiming search success',()=>{
  assert.deepEqual(classify({query:'custom query',candidateCount:5,officialDocsCount:0}),
    {search_state:'candidate_links_observed',search_succeeded:null});
  assert.deepEqual(classify({query:'custom query',candidateCount:0}),
    {search_state:'zero_candidates',search_succeeded:null});
});

test('challenge and incomplete navigation never count as a successful search',()=>{
  assert.deepEqual(classify({outcome:'challenge_observed',candidateCount:9,officialDocsCount:2}),
    {search_state:'challenge',search_succeeded:false});
  assert.deepEqual(classify({outcome:'navigation_unverified',candidateCount:9}),
    {search_state:'unverified',search_succeeded:null});
});

test('citation without resolved official destination leaves default-query success unverified',()=>{
  assert.deepEqual(classify({candidateCount:1,officialDocsCount:0,officialDocsCitationCount:1}),
    {search_state:'official_docs_citation_found_destination_unresolved',search_succeeded:null});
});

test('resolved official URL determines success even when citations are also available',()=>{
  assert.deepEqual(classify({candidateCount:1,officialDocsCount:1,officialDocsCitationCount:1}),
    {search_state:'official_docs_link_found',search_succeeded:true});
});
