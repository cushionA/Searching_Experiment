import test from 'node:test';
import assert from 'node:assert/strict';
import {chooseManifestURLs, globalCommitted} from './b0.mjs';

test('B0 manifest uses only independently validated target documents and caps at three', () => {
  const gold = {documents: [
    {label: 'entry', requested_url: 'https://site.test/', candidate_valid: true},
    {label: 'target-1', requested_url: 'https://site.test/a', candidate_valid: true},
    {label: 'target-2', requested_url: 'https://site.test/b', candidate_valid: false},
    {label: 'target-3', requested_url: 'https://site.test/c', candidate_valid: true},
    {label: 'target-4', requested_url: 'https://site.test/d', candidate_valid: true},
    {label: 'target-5', requested_url: 'https://site.test/e', candidate_valid: true},
  ]};
  assert.deepEqual(chooseManifestURLs(gold), ['https://site.test/a', 'https://site.test/c', 'https://site.test/d']);
});

test('B0 global reservation uses document counts and includes prior B0 spend', () => {
  const fixture = {
    'evaluator-results.preflight-v1.jsonl': [{requests: {count: 742}}],
    'evaluator-results-v2.jsonl': [{document_attempts: 21, requests: 500}],
    'results.jsonl': [
      {method: 'B1', http_requests: 9999, document_requests: 5, document_responses: 6},
      {method: 'C1', http_requests: 8888, document_requests: 8, document_responses: 7},
    ],
    'challenge-results.jsonl': [{requests: 700, document_requests: 4, document_responses: 5}],
    'b0-results.jsonl': [{document_requests: 2, document_responses: 1}],
  };
  assert.deepEqual(globalCommitted('/unused/no-files-read', fixture), {
      v1_conservative_documents: 742, v2_document_attempts: 21,
      candidate_rows_conservative: 14, challenge_rows_conservative: 5,
      b0_reserved_documents: 2, committed: 784,
  });
});
