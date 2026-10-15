#!/usr/bin/env python3
"""Diagnostic PhraseMatcher view for unresolved native SKU requests."""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import time

from spacy.matcher import PhraseMatcher
import spacy
from run_installed_library_selection_v1 import catalogs, selected_state

def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def write_jsonl(path, rows):
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def selected_body(selection):
    """Return only the body frozen by the spec-table policy."""
    refs = ([table.get("source_ref") for table in selection.get("tables", [])]
            if selection.get("mode") == "spec_table" else selection.get("raw_sources", []))
    return selection["text"], refs


def phrase_hits(nlp, matcher, option_id, text, scope="selected_body_text", source_refs=None):
    """Emit native PhraseMatcher spans relative to the exact searched string."""
    doc = nlp.make_doc(text); found = []
    for match_id, start, end in matcher(doc):
        begin, finish = doc[start].idx, doc[end - 1].idx + len(doc[end - 1].text)
        found.append({"option_value": option_id[nlp.vocab.strings[match_id]], "scope": scope,
                      "start": begin, "end": finish, "source_span": [begin, finish],
                      "coordinate_unit": "unicode_characters", "quote": text[begin:finish],
                      "source_refs": source_refs or []})
    return found


def run(spec_dir: Path, catalog_dir: Path, views_path: Path, output: Path):
    run_start = time.perf_counter()
    if output.exists(): raise FileExistsError(output)
    input_paths = [spec_dir / "requests.jsonl", spec_dir / "documents.jsonl",
                   spec_dir / "spec-table-selections.jsonl", spec_dir / "SPEC_TABLE_POLICY.json",
                   spec_dir.parent / "public-bundle/contexts.jsonl",
                   catalog_dir / "requests.jsonl", catalog_dir / "documents.jsonl", views_path]
    pins = {str(p): digest(p) for p in input_paths}
    requests, documents, selections = (read_jsonl(p) for p in input_paths[:3])
    contexts = read_jsonl(input_paths[4]); cat_requests, cat_documents = (read_jsonl(p) for p in input_paths[5:7])
    policy = json.loads(input_paths[3].read_text(encoding="utf-8")); view_data = json.loads(views_path.read_text(encoding="utf-8"))
    view = view_data["views"]["spacy_norm"]
    docs = {d["document_id"]: d for d in documents}; selected = {d["document_id"]: d for d in selections}
    if len(docs) != 40 or set(docs) != set(selected): raise ValueError("DEV40 source coverage mismatch")
    if any(docs[k]["document_sha256"] != selected[k]["document_sha256"] for k in docs): raise ValueError("document SHA mismatch")
    if policy.get("semantic_or_sku_scope_proof") is not False: raise ValueError("policy proof contract changed")
    context_texts = defaultdict(set)
    for item in contexts: context_texts[item["document_sha256"]].add(item["body_text"])
    if any(context_texts[d["document_sha256"]] != {selected[k]["text"]} for k, d in docs.items()):
        raise ValueError("public contexts differ from frozen selected text")

    cat_docs, families = catalogs(cat_requests, cat_documents)
    classified = []
    for request in requests:
        state, reason = selected_state(request, cat_docs, families, view)
        if state == "unknown": classified.append((request, reason))
    matcher_start = time.perf_counter(); nlp = spacy.blank("ja"); matcher = PhraseMatcher(nlp.vocab, attr="NORM")
    option_id = {}
    values = dict.fromkeys(v for r in requests for v in r.get("option_values", []) if isinstance(v, str) and v)
    for index, value in enumerate(values):
        pattern = nlp.make_doc(value)
        if len(pattern):
            label = f"request_option_{index}"; matcher.add(label, [pattern]); option_id[label] = value
    matcher_build_seconds = time.perf_counter() - matcher_start

    match_start = time.perf_counter(); body_cache, title_cache, observations = {}, {}, []
    for request, reason in classified:
        doc_id = request["target_document_id"]; document, selection = docs[doc_id], selected[doc_id]
        if doc_id not in body_cache:
            text, refs = selected_body(selection)
            body_cache[doc_id] = phrase_hits(nlp, matcher, option_id, text, source_refs=refs)
        allowed = set(v for v in request.get("option_values", []) if isinstance(v, str))
        hits = [h for h in body_cache[doc_id] if h["option_value"] in allowed]
        if doc_id not in title_cache:
            title = document["title"]
            title_cache[doc_id] = phrase_hits(nlp, matcher, option_id, title["text"], "title", title.get("source_refs", []))
        title_hits = [h for h in title_cache[doc_id] if h["option_value"] in allowed]
        text, refs = selected_body(selection); desc = document.get("description", {}).get("text", "")
        observations.append({"request": request, "native_state": "unknown", "native_state_reason": reason,
            "target_document_id": doc_id, "target_document_sha256": document["document_sha256"],
            "spec_selection_mode": selection["mode"], "search_route": selection["mode"],
            "selected_text_sha256": sha256(text.encode()).hexdigest(), "matched_description_hits": hits,
            "matched_title_hits": title_hits,
            "reading_focus_segments": [{"scope": "selected_body_text", "text": text, "source_refs": refs}],
            "original_description_chars": len(desc), "reading_focus_chars": len(text),
            "potential_chars_reduced": max(0, len(desc)-len(text)),
            "semantic_or_sku_scope_proof": False, "production_adoption_allowed": False,
            "matches_are_reading_focus_only": True})

    output.mkdir(parents=True)
    write_jsonl(output / "request-observations.jsonl", observations)
    write_jsonl(output / "document-snapshots.jsonl", [{"document": d, "spec_table_selection": selected[d["document_id"]]} for d in documents])
    modes = Counter(f"{s['mode']}:{s['side']}" for s in selections)
    summary = {"schema": "probe-library-residual-text-v2", "request_count": len(requests),
        "unknown_request_count": len(observations), "unknown_by_state_reason": dict(Counter(o["native_state_reason"] for o in observations)),
        "description_hits": sum(bool(o["matched_description_hits"]) for o in observations),
        "title_hits": sum(bool(o["matched_title_hits"]) for o in observations),
        "no_hit_kept_full_selected_text": sum(not o["matched_description_hits"] for o in observations),
        "selection_mode_by_side": dict(modes), "table_selection_policy": policy, "source_pins": pins,
        "public_context_rows": len(contexts), "public_context_document_count": len(context_texts),
        "selected_text_matches_public_contexts_for_all_documents": True,
        "code_sha256": digest(Path(__file__)), "selection_helper_sha256": digest(Path(__file__).with_name("run_installed_library_selection_v1.py")),
        "views_sha256": pins[str(views_path)], "spacy_version": view_data["versions"]["spaCy"],
        "trained_components": [], "models": [], "teacher_labels_used": False,
        "patterns_source": "DEV40 request.option_values verbatim; PhraseMatcher attr=NORM",
        "handwritten_matcher_patterns": False, "accept_reject_or_absence_claims": False,
        "labels_read": False, "production_adoption_allowed": False,
        "original_description_chars": sum(o["original_description_chars"] for o in observations),
        "reading_focus_chars": sum(o["reading_focus_chars"] for o in observations),
        "potential_chars_reduced": sum(o["potential_chars_reduced"] for o in observations),
        "scope_unproved_focus_may_omit_needed_context": True, "not_a_token_or_cost_estimate": True,
        "matcher_build_seconds": matcher_build_seconds, "matching_seconds": time.perf_counter()-match_start,
        "total_seconds": time.perf_counter()-run_start}
    write_json(output / "SUMMARY.json", summary)
    if any(digest(p) != pins[str(p)] for p in input_paths): raise ValueError("input changed during run")
    return summary


if __name__ == "__main__":
    base = Path(".lab-output")
    root = base / "sku-v7-small-nli-spec-first-20261014-v1/host"
    catalog = base / "sku-classical-parser-20261015-v1/population-inputs"
    views = base / "sku-installed-library-matching-20261015-v1/views.json"
    output = base / "sku-installed-library-matching-20261015-v1/residual-text-v2"
    print(json.dumps(run(root, catalog, views, output), ensure_ascii=False))
