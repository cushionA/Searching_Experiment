# Datasheet

Following *Datasheets for Datasets* (Gebru et al.).

## Motivation
Built to evaluate **retrieval quality** in RAG, not just answer correctness. Each question
carries gold **evidence spans**, so one can measure whether a system retrieved the passages
the answer depends on (doc-hit, evidence-recall, retrieval→answer funnels).

## Composition
- `kilt/` — 100 Wikipedia multi-hop questions (50 bridge, 50 comparison) over 199 docs, 241 evidence spans (from HotpotQA/KILT).
- `loong/` — 100 financial-filing questions (50 single-fact L1, 50 comparison L2) over 144 docs, 239 evidence spans (from Loong). Documents not included (references + offsets + hashes only).
- No personal data beyond what is already public in Wikipedia / public financial filings.

## Collection & annotation
Questions/answers come from the upstream benchmarks (HotpotQA, Loong). The **evidence spans**
are our contribution: located per query with character offsets, roles (answer-bearing /
supporting), and a required-evidence flag. See the paper for the annotation procedure.

## Uses
Retrieval and RAG evaluation. Known caveats: LOONG-L2 evidence marks per-company comparison
values (not a single answer span); BrowseComp curated negatives are excluded upstream.

## Distribution & licensing
See `LICENSE` and `NOTICE`. KILT ships in full (CC BY-SA 4.0); LOONG ships references only
(rebuild documents from source); BrowseComp-Plus is referenced, not redistributed.

## Maintenance
Version 1.0. A citable archival version (DOI) accompanies the non-anonymized release.
