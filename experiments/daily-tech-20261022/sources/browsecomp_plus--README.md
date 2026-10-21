# BrowseComp-Plus — evidence spans (offsets only)

We ship **only our evidence-span annotations** here. We do **not** ship questions,
answers, or document/evidence text, because BrowseComp (OpenAI) asks that examples not
appear in plain text online. Get those from upstream and join by id:

1. **Questions & answers** — download the *obfuscated* set and decrypt with the upstream
   script: https://github.com/texttron/BrowseComp-Plus
2. **Documents (corpus)** — open on HuggingFace: `Tevatron/browsecomp-plus-corpus`
3. **Join** — `evidence_offsets.jsonl` is keyed by `query_id` (upstream BrowseComp-Plus id)
   and `doc_id` (corpus docid). Recover a span's text as `doc[char_start:char_end]`.

`query_ids.json` lists the 100 queries in our subset.

Fields (`evidence_offsets.jsonl`): `query_id`, `evidence_id`, `doc_id`, `char_start`,
`char_end`, `role`, `evidence_group_id`.
