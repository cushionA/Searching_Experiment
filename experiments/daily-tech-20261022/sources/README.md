# Retrieval Evaluation Suite (with gold evidence spans)

An evaluation suite for **retrieval-augmented question answering** that annotates, for
every question, the **exact evidence passages** needed to answer it. This lets you
measure *retrieval quality* — did the system fetch the passages the answer depends on? —
not just whether the final answer was right. Most QA datasets give only (question, answer);
here you also get span-level gold evidence.

## What's inside

| benchmark | domain | what ships |
|---|---|---|
| `kilt/` | Wikipedia multi-hop (bridge + comparison), from HotpotQA | **full**: questions, answers, evidence spans (with text), and the Wikipedia documents |
| `loong/` | multi-document financial-filing QA (single-fact + comparison), from Loong | **references only**: questions, answers, evidence *offsets* (no document text), and a document manifest with SHA-256 hashes. Rebuild the documents from the Loong source (see `loong/rebuild_docs.py`). |
| `browsecomp_plus/` | obfuscated public-web puzzles, from BrowseComp-Plus | **evidence spans only** (offsets, keyed by upstream id): no questions/answers/text (OpenAI asks BrowseComp examples not appear in plain text). Get questions from upstream (obfuscated) and the corpus from HuggingFace; join by id. |

## Layout
```
kilt/   queries.jsonl  evidence.jsonl  docs/<doc_id>.txt
loong/  queries.jsonl  evidence_offsets.jsonl  doc_manifest.jsonl  rebuild_docs.py
SCHEMA.md   DATASHEET.md   LICENSE   NOTICE
```

## Quick start
```python
import json
q = [json.loads(l) for l in open("kilt/queries.jsonl")]
e = [json.loads(l) for l in open("kilt/evidence.jsonl")]
# per question: q["question"], q["answer"], q["gold_doc_ids"];
# per evidence span: e["doc_id"], e["char_start"], e["char_end"], e["is_required"], e["evidence_text"]
```
See `SCHEMA.md` for every field. For `loong/`, run `rebuild_docs.py` first to fetch the
documents, then read spans from `evidence_offsets.jsonl` against your local copy.

## Licensing (short version)
Annotations are released under **CC BY-SA 4.0**; helper code under **MIT**. Upstream data
keeps its own licenses (HotpotQA / Wikipedia CC BY-SA 4.0; Loong Apache-2.0 for the
questions). Full attributions and terms are in `NOTICE`.
