# Schema

All files are JSON Lines (one JSON object per line).

## `queries.jsonl` (both benchmarks)
| field | meaning |
|---|---|
| `query_id` | unique id |
| `benchmark` | `kilt-hotpotqa` or `loong` |
| `question` | the question text |
| `answer` | gold answer |
| `type` (KILT) | `bridge` or `comparison` |
| `level` (LOONG) | `1` = single-fact lookup, `2` = multi-document comparison |
| `gold_doc_ids` (KILT) / `gold_doc_refs` (LOONG) | documents that contain the answer/evidence |
| `evidence_doc_ids` (KILT) | documents holding the annotated evidence |
| `candidate_entities` (LOONG) | entities named in a comparison question |
| `negative_doc_ids` (KILT) | distractor documents in the candidate pool |

## `kilt/evidence.jsonl` (evidence spans, WITH text)
| field | meaning |
|---|---|
| `query_id`, `evidence_id` | ids |
| `doc_id`, `url` | Wikipedia document and source URL |
| `role` | e.g. `answer_bearing`, `supporting_context` |
| `is_required` | span is needed to answer |
| `is_primary` | anchor/bridge document flag |
| `char_start`, `char_end` | character offsets into `docs/<doc_id>.txt` |
| `evidence_text` | the span text (shippable: Wikipedia CC BY-SA) |

## `loong/evidence_offsets.jsonl` (spans, OFFSETS ONLY — no document text)
Same as above but **without** `evidence_text` (Loong documents are not redistributable).
Fields: `query_id`, `evidence_id`, `doc_ref`, `char_start`, `char_end`, `role`,
`is_required`, `company`, `metric`. Recover the text by slicing
`doc[char_start:char_end]` after rebuilding the documents.

## `loong/doc_manifest.jsonl`
| field | meaning |
|---|---|
| `doc_ref` | relative document path (e.g. `doc/financial/2024-<company>-j.txt`) |
| `sha256` | hash of the original document, to verify your rebuilt copy matches |
