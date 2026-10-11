"""Read-only-source replay stages for the latest Luna 50-case set via Gemini.

The provider driver owns protocol freezing and API calls.  This module only
prepares immutable inputs/requests and collects locally returned JSON.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

EXP = Path(__file__).resolve().parent
V9 = EXP / "results/20261010T201000Z-luna-broad50-v9"
V13 = EXP / "results/20261010T210700Z-luna-width-contents-v13"
sys.path.insert(0, str(EXP))
import trial_luna_sku_matching as core  # noqa: E402
import sku_luna_current_row_dimension_task as dimensions  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _request_text(req: dict[str, Any]) -> str:
    return req["body"]["contents"][0]["parts"][0]["text"]


def _body_sha(raw: bytes) -> str:
    req = json.loads(raw)
    body = req["body"]
    return _sha(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())


def _load_v13_run():
    path = V13 / "run.py"
    spec = importlib.util.spec_from_file_location("gemini_replay_v13_run", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load V13 stage helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(run_dir: str | Path) -> dict[str, Any]:
    """Create a new replay directory with 50 answer-free full requests and 70 chunks."""
    dest = Path(run_dir).resolve()
    if dest.exists():
        existing = {p.name for p in dest.iterdir()}
        if existing - {"auth-check.json"} or ("auth-check.json" in existing and not (dest / "auth-check.json").is_file()):
            raise ValueError(f"replay destination may contain only auth-check.json: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    src = V13 / "derived"
    v13_manifest = _read(src / "manifest.json")
    v13_entries = {e["case_id"]: e for e in v13_manifest["entries"]}
    inputs_bytes = (src / "inputs.json").read_bytes()
    contexts_bytes = (src / "source-contexts.json").read_bytes()
    (dest / "derived").mkdir(exist_ok=True)
    (dest / "derived/inputs.json").write_bytes(inputs_bytes)
    (dest / "derived/source-contexts.json").write_bytes(contexts_bytes)
    cases = json.loads(inputs_bytes)
    contexts = json.loads(contexts_bytes)
    if len(cases) != 50 or len({c["case_id"] for c in cases}) != 50:
        raise ValueError("V13 source does not contain fifty unique cases")
    for name in ("requests", "answers"):
        (dest / "derived" / name).mkdir(exist_ok=False)

    full_entries = []
    for i, case in enumerate(cases, 1):
        old = v13_entries[case["case_id"]]
        raw = (src / old["request"]).read_bytes()
        rel = f"requests/{i:02}.json"
        (dest / "derived" / rel).write_bytes(raw)
        full_entries.append({"case_id": case["case_id"], "request": rel,
                             "answer": f"answers/{i:02}.json", "request_sha256": _sha(raw),
                             "request_body_sha256": _body_sha(raw),
                             "source_round": "v13-effective-fullcase",
                             "source_request": old["request"]})

    manifest = {k: copy.deepcopy(v) for k, v in v13_manifest.items() if k != "entries"}
    manifest.update({"backend": "vertex", "model_requested": "gemini-3.5-flash-lite",
                     "served_model_id": None, "model_runtime_verified": False, "usage": None,
                     "planned_calls": 50, "inputs_sha256": _sha(inputs_bytes),
                     "source_contexts_sha256": _sha(contexts_bytes), "entries": full_entries,
                     "labels_read": False, "independent_gold_used": False})
    _save(dest / "derived/manifest.json", manifest)

    v9_dispatch = _read(V9 / "matching-dispatch-manifest.json")
    v13_dispatch = _read(V13 / "matching-dispatch-manifest.json")
    failed = {x["case_id"] for x in _read(V13 / "matching-chunk-evaluation.json")["prior_raw_fallback_cases"]}
    fresh_case_ids = {e["original_case_id"] for e in v13_dispatch["entries"]} - failed
    v9_by_case: dict[str, list[dict[str, Any]]] = {}
    v13_by_case: dict[str, list[dict[str, Any]]] = {}
    for entry in v9_dispatch["entries"]:
        v9_by_case.setdefault(entry["original_case_id"], []).append(entry)
    for entry in v13_dispatch["entries"]:
        v13_by_case.setdefault(entry["original_case_id"], []).append(entry)

    chunks_dir = dest / "matching-chunk-requests"
    chunks_dir.mkdir()
    (dest / "matching-chunk-answers").mkdir()
    chunk_entries = []
    source_audit = []
    for case in cases:
        cid = case["case_id"]
        use_v13 = cid in fresh_case_ids
        source_round = "v13-fresh" if use_v13 else ("v9-fallback" if cid in failed else "v9-original")
        source_entries = v13_by_case[cid] if use_v13 else v9_by_case[cid]
        source_entries = sorted(source_entries, key=lambda e: e["row_start"])
        start = 0
        for old in source_entries:
            if old["row_start"] != start or old["row_count"] <= 0 or old["row_count"] > 27:
                raise ValueError(f"invalid chunk coverage metadata: {cid}")
            start += old["row_count"]
            raw = ((V13 if use_v13 else V9) / old["request"]).read_bytes()
            # Verify that the immutable prompt's projected condition payload is
            # exactly this source case's selected values and row slice.
            req_obj = json.loads(raw)
            body_text = _request_text(req_obj)
            marker = "INPUT="
            if marker not in body_text:
                raise ValueError(f"chunk prompt has no INPUT marker: {cid}")
            payload = json.loads(body_text.split(marker, 1)[1])
            expected_r = [{"axis": x["axis"], "value": x["value"]} for x in case["rakuten_conditions"]]
            expected_rows = []
            for ri, row in enumerate(case["au_rows"][old["row_start"]:old["row_start"] + old["row_count"]]):
                conds = [{"source_id": f"A{j}", "axis": c["axis"], "value": c["value"]}
                         for j, c in enumerate(row["conditions"])]
                expected_rows.append({"row_index": ri, "conditions": conds,
                                      "allowed_row_source_ids": [x["source_id"] for x in conds]})
            expected_checks = []
            for i, row in enumerate(case["au_rows"][old["row_start"]:old["row_start"] + old["row_count"]]):
                expected_checks.append({"row_index": i, "rakuten_checks": expected_r,
                                        "au_checks": [{"axis": c["axis"], "value": c["value"]}
                                                      for c in row["conditions"]]})
            expected_attrs = [{k: x.get(k) for k in ("axis", "value", "unit")}
                              for x in case.get("selected_attributes", [])]
            if (payload.get("rakuten_conditions") != expected_r or payload.get("au_rows") != expected_rows or
                    payload.get("selected_attributes") != expected_attrs or
                    payload.get("check_targets") != expected_checks):
                raise ValueError(f"chunk prompt input projection differs from source case: {cid}")
            index = len(chunk_entries) + 1
            rel = f"matching-chunk-requests/{index:03}.json"
            out = chunks_dir / f"{index:03}.json"
            out.write_bytes(raw)
            chunk = {"case_id": old["case_id"], "original_case_id": cid,
                     "row_start": old["row_start"], "row_count": old["row_count"],
                     "request": rel, "answer": f"matching-chunk-answers/{index:03}.json",
                     "request_sha256": _sha(raw), "request_body_sha256": _body_sha(raw),
                     "source_round": source_round, "source_request": old["request"]}
            chunk_entries.append(chunk)
            source_audit.append({k: chunk[k] for k in ("original_case_id", "row_start", "row_count",
                                                          "source_round", "source_request",
                                                          "request_sha256", "request_body_sha256")})
        if start != len(case["au_rows"]):
            raise ValueError(f"chunk rows do not cover exactly once: {cid} ({start})")
    if len(chunk_entries) != 70:
        raise ValueError(f"expected seventy chunks, got {len(chunk_entries)}")
    if sum(x["row_count"] for x in chunk_entries) != 934:
        raise ValueError("expected exact 934-row task coverage")
    _save(dest / "matching-dispatch-manifest.json", {
        "planned_calls": 70, "distinct_case_count": 50, "candidate_rows_per_task_max": 27,
        "labels_read": False, "entries": chunk_entries,
        "input_sha256": _sha(inputs_bytes), "source_contexts_sha256": _sha(contexts_bytes),
        "source_mix": {"v9_original_cases": 45, "v13_fresh_cases": 4,
                       "v9_fallback_cases": 1, "fallback_case_ids": sorted(failed)},
        "source_request_audit": source_audit})
    return {"run_dir": str(dest), "case_count": 50, "chunk_count": 70,
            "input_sha256": _sha(inputs_bytes), "source_contexts_sha256": _sha(contexts_bytes),
            "source_mix": {"v9_original_cases": 45, "v13_fresh_cases": 4,
                           "v9_fallback_cases": 1, "fallback_case_ids": sorted(failed)}}


def collect_matching(run_dir: str | Path) -> dict[str, Any]:
    """Validate and concatenate Gemini chunks; incomplete cases stay not_evaluated."""
    root = Path(run_dir).resolve()
    ddir = root / "derived"
    cases = {c["case_id"]: c for c in core.read(ddir / "inputs.json")}
    manifest = core.read(ddir / "manifest.json")
    dispatch = _read(root / "matching-dispatch-manifest.json")
    groups = {cid: [] for cid in cases}
    for ent in dispatch["entries"]:
        raw_path = root / ent["answer"]
        valid, why, raw = False, "missing_answer", None
        if raw_path.exists():
            try:
                raw = core.read(raw_path)
                case = copy.deepcopy(cases[ent["original_case_id"]])
                case["au_rows"] = case["au_rows"][ent["row_start"]:ent["row_start"] + ent["row_count"]]
                valid, why = core.smoke.validate(case, raw)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                why = "invalid_json_or_structure"
        groups[ent["original_case_id"]].append((ent, raw, valid, why))
    vmap = {c["case_id"]: c for c in cases.values()}
    for ent in manifest["entries"]:
        cid = ent["case_id"]
        pieces = sorted(groups[cid], key=lambda p: p[0]["row_start"])
        pos = 0
        for piece, _, _, _ in pieces:
            if piece["row_start"] != pos:
                raise ValueError(f"dispatch gap/overlap for {cid}")
            pos += piece["row_count"]
        if pos != len(vmap[cid]["au_rows"]):
            raise ValueError(f"dispatch does not cover case {cid}")
        answer_path = ddir / ent["answer"]
        if answer_path.exists():
            raise ValueError(f"refusing overwrite: {answer_path}")
        all_valid = all(x[2] for x in pieces)
        if all_valid:
            composed = {"rows": [row for _, raw, _, _ in pieces for row in raw["rows"]]}
        else:
            composed = {"chunk_errors": [{"request": p[0]["request"],
                                            "answer": p[0]["answer"],
                                            "validation_reason": p[3]} for p in pieces if not p[2]]}
        _save(answer_path, composed)
        ent["output_origin"] = {"method": "Gemini chunk raw-row concatenation" if all_valid else "incomplete Gemini response; invalid placeholder",
                                "all_chunks_valid": all_valid,
                                "chunks": [{**p[0], "validation_ok": p[2], "validation_reason": p[3],
                                            "answer_sha256": _sha((root / p[0]["answer"]).read_bytes())
                                            if (root / p[0]["answer"]).exists() else None} for p in pieces],
                                "composed_answer_sha256": _sha(answer_path.read_bytes())}
    core.save(ddir / "manifest.json", manifest)
    ev = core.evaluate(ddir)
    result = {"task_count": len(dispatch["entries"]),
              "validation_pass": sum(ok for parts in groups.values() for _, _, ok, _ in parts),
              "case_count": len(cases), "cases_valid": sum(x["validation_ok"] for x in ev["cases"]),
              "counts": ev["counts"]}
    _save(root / "matching-chunk-evaluation.json", result)
    return result


def _v13_guard_for(run_dir: Path):
    v13 = _load_v13_run()
    v13.DIR = run_dir / "derived"
    v13.install_guard()
    return v13


def signature_prepare(run_dir: str | Path) -> dict[str, Any]:
    """Bind dimension checks to Gemini candidates and current candidate hashes."""
    root = Path(run_dir).resolve()
    ddir = root / "derived"
    ev = core.evaluate(ddir)
    cases = {c["case_id"]: c for c in core.read(ddir / "inputs.json")}
    contexts = core.read(ddir / "source-contexts.json")
    source_manifest = core.read(V13 / "derived/signature-v3-manifest.json")
    source_by_key = {(e["case_id"], e["row_key"]): e for e in source_manifest["entries"]}
    if (ddir / "signature-v3-manifest.json").exists():
        raise ValueError("signature stage already exists")
    # Use the ordinary frozen signature-v3 builder as the default for candidates
    # without an exact V13 binding or a known task-specific repair protocol.
    entries = core.prepare_signature(ddir, "signature-v3")
    adjustable_ids = set(_read(EXP / "results/20261010T204500Z-luna-adjustable-dimensions-v11/selection.json")["selected"])
    spec = importlib.util.spec_from_file_location("gemini_replay_adjustable", EXP / "sku_luna_adjustable_dimension_task.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load adjustable dimension helper")
    adjustable = importlib.util.module_from_spec(spec); spec.loader.exec_module(adjustable)
    dispatch = []
    for entry in entries:
        cid = entry["case_id"]
        case = cases[cid]
        row = next(r for r in case["au_rows"] if r["row_key"] == entry["row_key"])
        targets = entry["targets"]
        old = source_by_key.get((cid, row["row_key"]))
        exact = old is not None and core.canonical(old["targets"]) == core.canonical(targets)
        reqpath = ddir / entry["request"]
        if exact:
            reqpath.write_bytes((V13 / "derived" / old["request"]).read_bytes())
            entry["request_origin"] = "v13-exact-body-copy"
        elif entry.get("inference_required", True):
            if cid in adjustable_ids:
                req = adjustable.compact_request(_read(reqpath))
                origin = "adjustable-discrete-height-task"
            elif "カーテン" in " ".join(str(s.get("text", "")) for s in contexts[cid].values() if s.get("kind") == "title"):
                req = dimensions.request(case, row, [{k: a.get(k) for k in ("axis", "value", "unit")} for a in targets], contexts[cid])
                origin = "current-row-curtain-dimension-task"
            else:
                origin = "core-signature-v3-default"
            if origin != "core-signature-v3-default":
                _save(reqpath, req)
            entry["request_origin"] = origin
        entry["request_sha256"] = _sha(reqpath.read_bytes())
        if entry.get("inference_required", True):
            dispatch.append({**entry, "request": "derived/" + entry["request"],
                             "answer": "derived/" + entry["answer"]})
    core.save(ddir / "signature-v3-manifest.json", {"schema": "selected-dimensions-verification-v3", "entries": entries})
    _save(root / "signature-dispatch-manifest.json", {"planned_calls": len(dispatch), "labels_read": False,
                                                       "entries": dispatch})
    return {"candidate_count": len(entries), "planned_calls": len(dispatch),
            "local_empty": len(entries) - len(dispatch)}


def _dynamic_review_fields(case: dict[str, Any], result: dict[str, Any], signature: dict[str, Any] | None) -> dict[str, Any]:
    prediction = {k: result.get(k) for k in ("decision", "candidate_row_key", "validation_ok", "validation_reason", "guard_issues")}
    trace = []
    rowmap = {r["row_key"]: r for r in case["au_rows"]}

    def ref(sid: str, row: dict[str, Any]) -> str:
        if sid.startswith("A") and sid[1:].isdigit():
            i = int(sid[1:])
            if i >= len(row["conditions"]):
                raise ValueError("AU source reference outside current row")
            return row["conditions"][i]["condition_id"]
        return sid

    for row in (result.get("expanded_response") or {}).get("rows", []):
        trace.append({"row_key": row["row_key"], **{
            side: [{"condition_id": c["condition_id"], "status": c["status"],
                    "source_ids": [ref(x["source_id"], rowmap[row["row_key"]]) for x in c["evidence"]]}
                   for c in row[side]] for side in ("rakuten_checks", "au_checks")}})
    dim = {k: (signature or {}).get(k) for k in ("passed", "applicable", "validation_ok", "guard_issues")}
    dim["expanded_checks"] = []
    if signature and signature.get("expanded_checks"):
        row = rowmap[signature["row_key"]]
        for check in signature["expanded_checks"]:
            dim["expanded_checks"].append({"target": {k: check["target"].get(k) for k in ("axis", "value", "unit")},
                                            "status": check["status"],
                                            "evidence": [{"source_id": ref(x["source_id"], row), "quote": x["quote"]}
                                                         for x in check["evidence"]]})
    return {"prediction": prediction, "matching_evidence_trace": trace, "dimension_verification": dim}


def reviews_prepare(run_dir: str | Path) -> dict[str, Any]:
    """Retain V13 review prompts and fixed payload; refresh all prediction fields."""
    root = Path(run_dir).resolve()
    ddir = root / "derived"
    v13 = _v13_guard_for(root)
    ev = {x["case_id"]: x for x in core.evaluate(ddir)["cases"]}
    sig = core.signature_results(ddir, "signature-v3")
    cases = {c["case_id"]: c for c in core.read(ddir / "inputs.json")}
    src_manifest = _read(V13 / "review-dispatch-manifest.json")
    reqdir, ansdir = root / "review-requests", root / "review-answers"
    reqdir.mkdir(exist_ok=False); ansdir.mkdir(exist_ok=False)
    entries = []
    for i, old in enumerate(src_manifest["entries"], 1):
        cid = old["case_id"]
        req = _read(V13 / old["request"])
        if len(req.get("cases", [])) != 1 or req["cases"][0].get("case_id") != cid:
            raise ValueError("V13 review request is not single-case")
        item = req["cases"][0]
        item.update(_dynamic_review_fields(cases[cid], ev[cid], sig.get(cid)))
        rp = reqdir / f"{i:02}.json"
        _save(rp, req)
        entries.append({"case_id": cid, "case_ids": [cid], "request": str(rp.relative_to(root)),
                        "answer": f"review-answers/{i:02}.json", "request_sha256": _sha(rp.read_bytes()),
                        "inference_required": True, "reused_from": {"round": "v13", "request": old["request"],
                                                                         "fixed_instructions_and_source_payload": True}})
    _save(root / "review-dispatch-manifest.json", {"planned_calls": 50, "case_coverage": 50,
                                                    "labels_read": False, "entries": entries})
    _save(root / "new-review-dispatch-manifest.json", {"planned_calls": 50, "entries": entries})
    return {"planned_calls": len(entries), "case_coverage": len(entries)}


def gemini_review_body(request: dict[str, Any]) -> dict[str, Any]:
    """Wrap the frozen one-case review prompt in Vertex Gemini JSON-schema form."""
    item_order = ["case_id", "verdict", "row_key", "reason", "review_evidence_ids"]
    schema = {"type": "ARRAY", "minItems": 1, "maxItems": 1,
              "items": {"type": "OBJECT", "properties": {
        "case_id": {"type": "STRING"}, "verdict": {"type": "STRING", "enum": ["confirm", "reject", "unresolved"]},
        "row_key": {"type": "STRING", "nullable": True}, "reason": {"type": "STRING"},
        "review_evidence_ids": {"type": "ARRAY", "items": {"type": "STRING"}}},
        "required": item_order, "propertyOrdering": item_order}}
    content = request["instructions"] + "\nCASE_JSON=" + json.dumps(request["cases"], ensure_ascii=False, separators=(",", ":"))
    return {"contents": [{"role": "user", "parts": [{"text": content}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 8192,
                                 "responseMimeType": "application/json", "responseSchema": schema}}


def reviews_collect(run_dir: str | Path) -> dict[str, Any]:
    """Validate review responses, preserve failures as unresolved, and export gated links."""
    root = Path(run_dir).resolve()
    ddir = root / "derived"
    _v13_guard_for(root)
    cases = {c["case_id"]: c for c in core.read(ddir / "inputs.json")}
    contexts = core.read(ddir / "source-contexts.json")
    ev = {e["case_id"]: e for e in core.evaluate(ddir)["cases"]}
    sig = core.signature_results(ddir, "signature-v3")
    reviews = []
    audit = []
    failures: dict[str, str] = {}
    for ent in _read(root / "review-dispatch-manifest.json")["entries"]:
        cid = ent["case_id"]
        failure_code = None
        try:
            answer_path = root / ent["answer"]
            if not answer_path.exists():
                failure_code = "missing_answer"
                raise ValueError
            raw = answer_path.read_bytes()
            if _sha((root / ent["request"]).read_bytes()) != ent["request_sha256"]:
                failure_code = "request_integrity_failed"
                raise ValueError
            try:
                arr = json.loads(raw)
            except json.JSONDecodeError:
                failure_code = "malformed_json"
                raise ValueError
            if not isinstance(arr, list) or len(arr) != 1:
                failure_code = "invalid_review_array"
                raise ValueError
            review = arr[0]
            if not isinstance(review, dict) or set(review) != {"case_id", "verdict", "row_key", "reason", "review_evidence_ids"}:
                failure_code = "invalid_review_fields"
                raise ValueError
            if review["case_id"] != cid or review["verdict"] not in {"confirm", "reject", "unresolved"}:
                failure_code = "review_identity_or_verdict_invalid"
                raise ValueError
            if review["row_key"] is not None and not isinstance(review["row_key"], str):
                failure_code = "invalid_row_key"
                raise ValueError
            if not isinstance(review["reason"], str) or not review["reason"]:
                failure_code = "invalid_reason"
                raise ValueError
            ids = review["review_evidence_ids"]
            allowed = ({s["source_id"] for s in cases[cid]["sources"]} |
                       {c["condition_id"] for row in cases[cid]["au_rows"] for c in row["conditions"]})
            if not isinstance(ids, list) or not all(isinstance(s, str) for s in ids) or len(ids) != len(set(ids)) or not set(ids) <= allowed:
                failure_code = "invalid_evidence_ids"
                raise ValueError
            if any(contexts[cid][s]["scope"] != "fixed_product" for s in ids if s.startswith("S")):
                failure_code = "review_source_scope_mismatch"
                raise ValueError
            if review["verdict"] == "confirm":
                candidate = ev[cid]
                signature = sig.get(cid, {})
                if (not ids or candidate["decision"] != "candidate" or not signature.get("passed") or
                    review["row_key"] != candidate["candidate_row_key"] or review["row_key"] != signature.get("row_key") or
                    signature.get("candidate_answer_sha256") != candidate["answer_sha256"]):
                    failure_code = "confirm_gate_failed"
                    raise ValueError
                row = next(r for r in cases[cid]["au_rows"] if r["row_key"] == review["row_key"])
                if any(s.startswith("A:") and s not in {c["condition_id"] for c in row["conditions"]} for s in ids):
                    failure_code = "condition_from_wrong_row"
                    raise ValueError
            elif review["row_key"] is not None or (review["verdict"] == "reject" and not ids):
                failure_code = "nonconfirm_evidence_or_row_invalid"
                raise ValueError
            reviews.append(review)
            audit.append({"case_id": cid, "status": "validated"})
        except Exception as exc:
            failure_code = failure_code or type(exc).__name__
            failures[cid] = failure_code
            reviews.append({"case_id": cid, "verdict": "unresolved", "row_key": None,
                            "reason": "not_evaluated: review response unavailable or invalid", "review_evidence_ids": []})
            audit.append({"case_id": cid, "status": "not_evaluated", "failure_code": failure_code})
    if {r["case_id"] for r in reviews} != set(cases):
        raise ValueError("review coverage mismatch")
    _save(ddir / "final-reviews.json", reviews)
    summary = core.export_links(ddir, ddir / "final-reviews.json", "signature-v3")
    if failures:
        core_summary = copy.deepcopy(summary)
        _save(root / "core-export-summary.json", core_summary)
        failed_ids = set(failures)
        for item in summary["cases"]:
            if item["case_id"] in failed_ids:
                item["decision"] = "not_evaluated"
                item["reason"] = "review_not_evaluated:invalid_or_missing_response"
                item["failure"] = {"stage": "review", "code": failures[item["case_id"]]}
        links_path = ddir / "links.jsonl"
        linked_ids = {json.loads(line)["case_id"] for line in links_path.read_text(encoding="utf-8").splitlines() if line.strip()}
        if linked_ids & failed_ids:
            raise ValueError("invalid review case appeared in exported links")
        from collections import Counter
        summary["counts"] = dict(Counter(x["decision"] for x in summary["cases"]))
        summary["exported_links"] = len(linked_ids)
        _save(ddir / "reviewed-summary.json", summary)
    _save(root / "review-collection-audit.json", {"reviews": audit, "summary": summary})
    return summary

