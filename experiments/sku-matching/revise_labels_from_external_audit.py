#!/usr/bin/env python3
"""Create a new, source-bound Luna label revision from the supplied audit bundle.

The audit is treated as a set of review proposals. It is never treated as human
gold, and the frozen source labels are only read. Raw source hashes and quoted
text are checked before a proposal can affect an output label.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import zipfile
from typing import Any

from audit_luna_sku_labels import read_jsonl, validate_label_records

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PROPOSAL_COUNTS = {"F01": 4, "F02": 4, "F03": 27}
REQUIRED_AUDIT_MEMBERS = {
    "AUDIT-MANIFEST.json",
    "label-audit-report.md", "audit_labels.py", "findings.json",
    "review-proposals-35.jsonl", "rationale-fix-proposal.json",
    "source-excerpts.md", "computed/structural-audit.json",
    "computed/reference-fix-proposals.jsonl",
}
NORMALIZE_SPACE = re.compile(r"\s+")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalized(value: str) -> str:
    decoded = html.unescape(value)
    # AU descriptions may contain <br> markup inside otherwise plain JSON text.
    decoded = re.sub(r"<[^>]*>", " ", decoded)
    return NORMALIZE_SPACE.sub(" ", decoded).strip()


def json_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from json_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from json_strings(child)


def raw_contains_quote(raw: bytes, quote: str) -> bool:
    """Check quoted source text in raw JSON or HTML-embedded JSON/text."""
    target = normalized(quote)
    if not target:
        return False
    # AU captures are UTF-8 JSON; some Rakuten HTML snapshots are EUC-JP.
    for encoding in ("utf-8", "euc_jp", "cp932"):
        try:
            text = raw.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
        if target in normalized(text):
            return True
        # Walking parsed JSON values also handles JSON escapes in embedded data.
        for candidate in (text, html.unescape(text)):
            try:
                document = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            if any(target in normalized(value) for value in json_strings(document)):
                return True
        for script in re.findall(
                r"<script\b[^>]*\bid=[\"']item-page-app-data[\"'][^>]*>(.*?)</script>",
                text, flags=re.I | re.S):
            try:
                document = json.loads(html.unescape(script))
            except json.JSONDecodeError:
                continue
            if any(target in normalized(value) for value in json_strings(document)):
                return True
    return False


def path_for(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_audit_bundle(audit_zip: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], str, list[dict[str, Any]]]:
    archive_sha = sha256_file(audit_zip)
    with zipfile.ZipFile(audit_zip) as archive:
        names = [item.filename for item in archive.infolist()]
        prefix = "sku-label-audit-20261010/"
        if len(names) != len(set(names)) or any(
                name.startswith("/") or ".." in Path(name).parts for name in names):
            raise ValueError("Audit ZIP has duplicate or unsafe member paths")
        by_suffix = {name[len(prefix):]: name for name in names if name.startswith(prefix)}
        if set(by_suffix) != REQUIRED_AUDIT_MEMBERS:
            raise ValueError("Unexpected audit ZIP member set")
        manifest = json.loads(archive.read(by_suffix["AUDIT-MANIFEST.json"]))
        # The untrusted bundle's executable code is never loaded or run. Its
        # manifest only serves as an integrity list for the data we inspect.
        for name, metadata in manifest.get("files", {}).items():
            payload = archive.read(by_suffix[name])
            if len(payload) != metadata.get("bytes") or sha256_bytes(payload) != metadata.get("sha256"):
                raise ValueError(f"Audit bundle member hash mismatch: {name}")
        proposals = [json.loads(line) for line in archive.read(
            by_suffix["review-proposals-35.jsonl"]).decode("utf-8").splitlines() if line.strip()]
        rationale = json.loads(archive.read(by_suffix["rationale-fix-proposal.json"]))
        ref_fixes = [json.loads(line) for line in archive.read(
            by_suffix["computed/reference-fix-proposals.jsonl"]).decode("utf-8").splitlines() if line.strip()]
        findings = json.loads(archive.read(by_suffix["findings.json"]))
    if Counter(row.get("finding_id") for row in proposals) != Counter({"F01": 4, "F02": 4, "F03": 27}):
        raise ValueError("Audit review proposals do not have the expected F01/F02/F03 counts")
    if not manifest.get("source_unchanged_verified"):
        raise ValueError("Audit bundle does not attest unchanged source captures")
    return manifest, proposals, rationale, ref_fixes, archive_sha, findings


def verify_inputs(inputs: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    manifest_path = inputs / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest.get("output_sha256", {}).items():
        path = inputs / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Input file hash mismatch: {name}")
    dossiers = {}
    expected_dossiers = manifest.get("dossier_sha256", {})
    if not expected_dossiers:
        raise ValueError("Input manifest has no dossier hashes")
    for name, expected in expected_dossiers.items():
        path = inputs / name
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"Dossier hash mismatch: {name}")
        dossiers[Path(name).stem] = json.loads(path.read_text(encoding="utf-8"))
    return manifest, read_jsonl(inputs / "cases.jsonl"), dossiers


def source_metadata(dossier: dict[str, Any], side: str) -> dict[str, Any]:
    if side == "au":
        return dossier.get("au_product", {}).get("source", {})
    return dossier.get("rakuten_product", {}).get("source", {})


def verify_audit_evidence(evidence: dict[str, Any], case: dict[str, Any],
                          dossier: dict[str, Any]) -> dict[str, Any]:
    side = evidence.get("side")
    if side not in {"au", "rakuten"} or evidence.get("dossier_id") != case.get("dossier_id"):
        raise ValueError(f"External evidence is not bound to case {case.get('case_id')}")
    metadata = source_metadata(dossier, side)
    raw_file, digest = str(evidence.get("raw_file", "")), str(evidence.get("raw_sha256", ""))
    if raw_file != str(metadata.get("raw_file", "")) or digest != str(metadata.get("sha256", "")):
        raise ValueError(f"External evidence source differs from case dossier: {case.get('case_id')}")
    if side == "rakuten":
        case_source = case.get("rakuten", {}).get("source", {})
        if raw_file != str(case_source.get("raw_file", "")) or digest != str(case_source.get("sha256", "")):
            raise ValueError(f"External Rakuten evidence differs from case source: {case.get('case_id')}")
    raw_path = path_for(raw_file)
    raw = raw_path.read_bytes()
    if sha256_bytes(raw) != digest:
        raise ValueError(f"Raw source SHA mismatch: {raw_file}")
    quote = str(evidence.get("quote", ""))
    if not raw_contains_quote(raw, quote):
        raise ValueError(f"Audit quote not found in verified source: {case.get('case_id')}")
    return {"side": side, "raw_file": raw_file, "raw_sha256": digest, "quote": quote,
            "source_kind": evidence.get("source_kind")}


def resolve_json_path(document: Any, json_path: str) -> Any:
    if not json_path.startswith("$."):
        raise ValueError(f"Unsupported source JSONPath: {json_path}")
    value = document
    for member in json_path[2:].split("."):
        if not isinstance(value, dict) or member not in value:
            raise ValueError(f"JSONPath does not resolve: {json_path}")
        value = value[member]
    return value


def verify_title_reference_fix(fix: dict[str, Any], case: dict[str, Any],
                               dossier: dict[str, Any], label: dict[str, Any]) -> None:
    index = fix.get("evidence_index")
    cites = label.get("evidence")
    if not isinstance(index, int) or not isinstance(cites, list) or index >= len(cites):
        raise ValueError(f"Invalid evidence index in title reference correction: {case['case_id']}")
    cite = cites[index]
    if (fix.get("source") != "au_title" or cite.get("source") != "au_title"
            or cite.get("source_ref") != fix.get("old_source_ref")
            or cite.get("quote") != fix.get("quote")
            or fix.get("old_source_ref") != "$.itemInfo.itemName"
            or fix.get("proposed_source_ref") != "$.itemInfo.itemTitle"):
        raise ValueError(f"Title reference correction does not match label evidence: {case['case_id']}")
    metadata = source_metadata(dossier, "au")
    raw_file = path_for(str(metadata.get("raw_file", "")))
    raw = raw_file.read_bytes()
    if sha256_bytes(raw) != metadata.get("sha256"):
        raise ValueError(f"AU title source SHA mismatch: {case['case_id']}")
    document = json.loads(raw)
    title = resolve_json_path(document, fix["proposed_source_ref"])
    if not isinstance(title, str) or fix["quote"] not in title:
        raise ValueError(f"Corrected AU title quote is not bound to raw title: {case['case_id']}")


def verified_raw_title(dossier: dict[str, Any], side: str) -> tuple[str, str, str]:
    product_key = "au_product" if side == "au" else "rakuten_product"
    product = dossier.get(product_key, {})
    metadata = source_metadata(dossier, side)
    raw_file = str(metadata.get("raw_file", ""))
    digest = str(metadata.get("sha256", ""))
    raw = path_for(raw_file).read_bytes()
    if sha256_bytes(raw) != digest:
        raise ValueError(f"Raw title source SHA mismatch: {raw_file}")
    title_evidence = product.get("title_evidence_raw", {})
    title = title_evidence.get("text")
    if title_evidence.get("use") != "audit_only_not_semantic_input" or not isinstance(title, str) or not title:
        raise ValueError(f"Missing audit-only raw title evidence in {dossier.get('dossier_id')}")
    if side == "au":
        raw_doc = json.loads(raw)
        title_in_capture = resolve_json_path(raw_doc, "$.itemInfo.itemTitle")
        if title != title_in_capture:
            raise ValueError(f"AU raw title evidence differs from $.itemInfo.itemTitle: {dossier.get('dossier_id')}")
    elif not raw_contains_quote(raw, title):
        raise ValueError(f"Rakuten raw title evidence is absent from captured page: {dossier.get('dossier_id')}")
    return title, raw_file, digest


def rebind_masked_title_citations(labels: dict[str, dict[str, Any]],
                                  cases: dict[str, dict[str, Any]],
                                  dossiers: dict[str, dict[str, Any]],
                                  ledger: list[dict[str, Any]], audit_sha: str) -> int:
    """Point citations at exact, SHA-verified raw-title excerpts when masking hid them."""
    changed = 0
    for case_id, label in labels.items():
        case = cases[case_id]
        dossier = dossiers[case["dossier_id"]]
        for index, cite in enumerate(label.get("evidence", [])):
            source = cite.get("source")
            if source not in {"au_title", "rakuten_title"}:
                continue
            side = "au" if source == "au_title" else "rakuten"
            product_key = "au_product" if side == "au" else "rakuten_product"
            semantic_title = str(dossier.get(product_key, {}).get("title_raw", ""))
            quote = str(cite.get("quote", ""))
            if quote and normalized(quote) in normalized(semantic_title):
                continue
            raw_title, raw_file, digest = verified_raw_title(dossier, side)
            if not quote or normalized(quote) not in normalized(raw_title):
                # Leave an existing citation untouched; normal validation will
                # report it rather than broadening the title source.
                continue
            old_ref = str(cite.get("source_ref", ""))
            new_ref = f"dossiers/{case['dossier_id']}.json#$.{product_key}.title_evidence_raw.text"
            cite["source_ref"] = new_ref
            ledger.append({"case_id": case_id, "action": "rebind_masked_title_citation_to_verified_raw_excerpt",
                           "evidence_index": index, "source": source,
                           "old_source_ref": old_ref, "new_source_ref": new_ref, "quote": quote,
                           "raw_file": raw_file, "raw_sha256": digest,
                           "audit_bundle_sha256": audit_sha, "human_verified": False})
            changed += 1
    return changed


def rationale_citations(rationale: dict[str, Any], case: dict[str, Any],
                        dossier: dict[str, Any]) -> list[dict[str, str]]:
    citations = []
    for evidence in rationale.get("evidence", []):
        verified = verify_audit_evidence(evidence, case, dossier)
        if verified["side"] == "au":
            citations.append({"source": "au_description",
                              "source_ref": f"dossiers/{case['dossier_id']}.json#$.au_product.description.blocks",
                              "quote": verified["quote"]})
        else:
            citations.append({"source": "rakuten_description",
                              "source_ref": f"dossiers/{case['dossier_id']}.json#$.rakuten_product.description.individual_description_excerpt",
                              "quote": verified["quote"]})
    return citations


def revise(inputs: Path, source_labels: Path, audit_zip: Path, output: Path) -> dict[str, Any]:
    inputs = inputs.resolve()
    source_labels = source_labels.resolve()
    audit_zip = audit_zip.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite label revision: {output}")
    input_manifest, cases, dossiers = verify_inputs(inputs)
    if len(cases) != 1383 or input_manifest.get("case_count") != 1383:
        raise ValueError("Expected the frozen 1,383-case annotation set")
    old_manifest_path = source_labels.parent / "manifest.json"
    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    if old_manifest.get("schema_version") != "luna-sku-label-manifest-v1":
        raise ValueError("Unexpected source label manifest schema")
    if sha256_file(source_labels) != old_manifest.get("output_sha256", {}).get(source_labels.name):
        raise ValueError("Source labels SHA does not match frozen manifest")
    old_labels = read_jsonl(source_labels)
    case_by_id = {case["case_id"]: case for case in cases}
    dossier_by_case = {cid: dossiers[case["dossier_id"]] for cid, case in case_by_id.items()}
    old_by_id = {label["case_id"]: label for label in old_labels}
    if len(old_by_id) != len(old_labels) or set(old_by_id) != set(case_by_id):
        raise ValueError("Source labels do not exactly cover current annotation cases")
    if len(old_labels) != 1383 or Counter(row.get("decision") for row in old_labels) != {
            "matched": 470, "unmatched": 911, "review": 2}:
        raise ValueError("Source labels are not the expected frozen v1 470/911/2 set")

    audit_manifest, proposals, rationale_fix, ref_fixes, audit_sha, findings = read_audit_bundle(audit_zip)
    proposal_by_id = {proposal["case_id"]: proposal for proposal in proposals}
    if len(proposal_by_id) != 35:
        raise ValueError("Duplicate external audit proposal case")
    # Ref fixes are approved narrowly: only exact extant AU title citations can
    # change, after resolving the proposed JSONPath and quote against raw bytes.
    ref_fix_by_case: dict[str, list[dict[str, Any]]] = {}
    for fix in ref_fixes:
        ref_fix_by_case.setdefault(fix["case_id"], []).append(fix)
    revised = {case_id: json.loads(json.dumps(label)) for case_id, label in old_by_id.items()}
    ledger: list[dict[str, Any]] = []
    for case_id, fixes in ref_fix_by_case.items():
        if case_id not in revised or case_id not in case_by_id:
            raise ValueError(f"Title reference fix names an unknown case: {case_id}")
        case, label = case_by_id[case_id], revised[case_id]
        if len(fixes) != 1:
            raise ValueError(f"Expected one title reference fix for {case_id}")
        fix = fixes[0]
        verify_title_reference_fix(fix, case, dossier_by_case[case_id], label)
        index = fix["evidence_index"]
        label["evidence"][index]["source_ref"] = fix["proposed_source_ref"]
        ledger.append({"case_id": case_id, "action": "correct_au_title_source_ref",
                       "evidence_index": index, "old_source_ref": fix["old_source_ref"],
                       "new_source_ref": fix["proposed_source_ref"], "quote": fix["quote"],
                       "raw_file": dossier_by_case[case_id]["au_product"]["source"]["raw_file"],
                       "raw_sha256": dossier_by_case[case_id]["au_product"]["source"]["sha256"],
                       "audit_bundle_sha256": audit_sha, "finding_id": "F04"})
    if len(ref_fix_by_case) != 342:
        raise ValueError(f"Expected 342 verified AU title reference corrections, got {len(ref_fix_by_case)}")
    masked_title_rebindings = rebind_masked_title_citations(
        revised, case_by_id, dossiers, ledger, audit_sha)

    for case_id, proposal in proposal_by_id.items():
        if case_id not in case_by_id:
            raise ValueError(f"Audit proposal names unknown case: {case_id}")
        case, dossier, label = case_by_id[case_id], dossier_by_case[case_id], revised[case_id]
        if (proposal.get("pair_ref") != dossier.get("pair_ref")
                or proposal.get("group_id") != case.get("group_id")
                or proposal.get("split") != case.get("split")
                or proposal.get("rakuten_sku_record_key") != case["rakuten"]["source"]["sku_record_key"]
                or proposal.get("original_decision") != label["decision"]
                or proposal.get("original_matching_au_row_keys") != label.get("matching_au_row_keys")):
            raise ValueError(f"Audit proposal identity or original label mismatch: {case_id}")
        evidence = [verify_audit_evidence(item, case, dossier) for item in proposal.get("evidence", [])]
        before = {key: label.get(key) for key in ("decision", "matching_au_row_keys", "rationale")}
        label["decision"] = "review"
        label["matching_au_row_keys"] = []
        label["rationale"] = str(proposal["reason"])
        ledger.append({"case_id": case_id, "action": "hold_for_external_audit_review",
                       "finding_id": proposal["finding_id"], "condition": proposal["recommendation_condition"],
                       "old_fields": before, "new_fields": {key: label.get(key) for key in before},
                       "verified_evidence": evidence, "audit_bundle_sha256": audit_sha,
                       "human_verified": False, "independent_product_identity_gold": False})

    rationale_case_id = rationale_fix.get("case_id")
    if rationale_case_id not in case_by_id:
        raise ValueError("Rationale correction names an unknown case")
    rationale_case = case_by_id[rationale_case_id]
    rationale_label = revised[rationale_case_id]
    if (rationale_label.get("decision") != "review"
            or rationale_label.get("decision") != rationale_fix.get("original_decision")
            or rationale_label.get("rationale") != rationale_fix.get("original_rationale")):
        raise ValueError("Rationale correction does not match the frozen review label")
    verified_rationale_evidence = [verify_audit_evidence(item, rationale_case,
                                    dossier_by_case[rationale_case_id])
                                   for item in rationale_fix.get("evidence", [])]
    before_rationale = rationale_label["rationale"]
    rationale_label["rationale"] = rationale_fix["proposed_rationale"]
    rationale_label["evidence"].extend(rationale_citations(
        rationale_fix, rationale_case, dossier_by_case[rationale_case_id]))
    ledger.append({"case_id": rationale_case_id, "action": "correct_review_rationale",
                   "finding_id": "F05", "old_rationale": before_rationale,
                   "new_rationale": rationale_label["rationale"],
                   "verified_evidence": verified_rationale_evidence,
                   "audit_bundle_sha256": audit_sha, "human_verified": False})

    core_labels = {case_id: json.loads(json.dumps(label)) for case_id, label in revised.items()}
    for case_id, proposal in proposal_by_id.items():
        if proposal["finding_id"] == "F03":
            # This separate file is explicitly limited to the five attributes
            # whose equality is not disputed; it does not claim full set identity.
            core_labels[case_id]["decision"] = proposal["original_decision"]
            core_labels[case_id]["matching_au_row_keys"] = proposal["original_matching_au_row_keys"]
            before_scope_rationale = core_labels[case_id].get("rationale")
            core_labels[case_id]["rationale"] = (
                "主要5属性の一致を対象にした限定ラベルです。フック数を含む付属品数量は対象外で、"
                "販売セット全体の同一性は未確定です。人手未確認の機械注釈です。")
            ledger.append({"case_id": case_id, "action": "retain_F03_match_in_core_attribute_scope",
                           "finding_id": "F03", "core_file": "labels-core-attributes.jsonl",
                           "old_primary_rationale": before_scope_rationale,
                           "core_rationale": core_labels[case_id]["rationale"],
                           "scope": "five_attribute_equality_only; accessory quantity excluded",
                           "human_verified": False, "independent_product_identity_gold": False})
    expected_primary = {"matched": 435, "unmatched": 911, "review": 37}
    expected_core = {"matched": 462, "unmatched": 911, "review": 10}
    if Counter(row["decision"] for row in revised.values()) != expected_primary:
        raise ValueError("Revised primary decision counts differ from 435/911/37")
    if Counter(row["decision"] for row in core_labels.values()) != expected_core:
        raise ValueError("Core-attribute decision counts differ from 462/911/10")

    # A scoped follow-up risk remains a warning; it is not another review label.
    f06 = next((row for row in findings if row.get("finding_id") == "F06"), None)
    if not f06 or f06.get("affected_case_count") != 54:
        raise ValueError("Expected F06 54-case additional-risk finding")
    unresolved_risks = [{"finding_id": "F06", "affected_case_count": 54,
                         "affected_case_ids": f06.get("affected_case_ids", []),
                         "summary": f06.get("reason"), "label_decision_changed": False,
                         "human_verified": False, "verified_evidence": []}]
    # F06 evidence is attached to finding-level dossiers, not case-specific; bind
    # each source and quote to its named dossier directly.
    for ev in f06.get("evidence", []):
        dossier_id = ev.get("dossier_id")
        dossier = dossiers.get(dossier_id)
        if dossier is None:
            raise ValueError("F06 evidence names a missing dossier")
        meta = source_metadata(dossier, ev.get("side", ""))
        raw = path_for(ev["raw_file"]).read_bytes()
        if (ev["raw_file"] != meta.get("raw_file") or ev["raw_sha256"] != meta.get("sha256")
                or sha256_bytes(raw) != ev["raw_sha256"] or not raw_contains_quote(raw, ev["quote"])):
            raise ValueError("F06 evidence failed raw source binding")
        unresolved_risks[0]["verified_evidence"].append({"dossier_id": dossier_id,
            "side": ev["side"], "raw_file": ev["raw_file"], "raw_sha256": ev["raw_sha256"],
            "quote": ev["quote"]})

    validation = validate_label_records(cases, dossiers, list(revised.values()),
        json.loads((inputs / "group_manifest.json").read_text(encoding="utf-8")), input_manifest)
    if validation["errors"]:
        raise ValueError(f"Revised labels failed standard validation: {validation['errors'][:3]}")
    core_validation = validate_label_records(cases, dossiers, list(core_labels.values()),
        json.loads((inputs / "group_manifest.json").read_text(encoding="utf-8")), input_manifest)
    if core_validation["errors"]:
        raise ValueError(f"Core-attribute labels failed standard validation: {core_validation['errors'][:3]}")

    output.mkdir(parents=True, exist_ok=False)
    labels_path = output / "labels.jsonl"
    core_path = output / "labels-core-attributes.jsonl"
    ledger_path = output / "corrections-ledger.jsonl"
    sort_key = lambda row: row["case_id"]
    def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
        payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")) + "\n"
                          for row in sorted(rows, key=sort_key)).encode("utf-8")
        path.write_bytes(payload)
    write_rows(labels_path, list(revised.values()))
    write_rows(core_path, list(core_labels.values()))
    write_rows(ledger_path, ledger)
    primary_counts = Counter(row["decision"] for row in revised.values())
    core_counts = Counter(row["decision"] for row in core_labels.values())
    protocol = {
        "model": "gpt-6-luna", "kind": "machine-annotated labels revised from source-verified external audit proposals",
        "human_verified": False, "independent_external_gold": False,
        "existing_model_predictions_used": False, "synthetic_data_included": False,
        "semantic_identity_separate_from_availability": True,
        "revision_is_gold": False,
    }
    manifest = {
        "schema_version": "luna-sku-label-manifest-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(inputs), "input_manifest_sha256": sha256_file(inputs / "manifest.json"),
        "cases_sha256": sha256_file(inputs / "cases.jsonl"), "case_count": len(revised),
        "decision_counts": dict(primary_counts), "annotation_protocol": protocol,
        "output_sha256": {"labels.jsonl": sha256_file(labels_path)},
        "supplemental_output_sha256": {core_path.name: sha256_file(core_path),
                                        ledger_path.name: sha256_file(ledger_path)},
        "source_labels": {"path": str(source_labels), "sha256": sha256_file(source_labels),
                          "manifest_sha256": sha256_file(old_manifest_path)},
        "audit_bundle": {"path": str(audit_zip), "sha256": audit_sha,
                         "manifest": audit_manifest},
        "revision_summary": {
            "primary_decision_counts": dict(primary_counts),
            "core_attribute_decision_counts": dict(core_counts),
            "review_proposal_count": len(proposal_by_id),
            "review_proposal_counts_by_finding": dict(Counter(p["finding_id"] for p in proposals)),
            "au_title_reference_fixes": len(ref_fix_by_case),
            "masked_title_citation_rebindings": masked_title_rebindings,
            "rationale_fixes": 1,
            "unresolved_additional_risks": unresolved_risks,
        },
        "core_attributes_scope": {
            "file": core_path.name,
            "description": "Limited five-attribute equality labels; F03 accessory-count differences remain matched only within this scope.",
            "human_verified": False, "independent_product_identity_gold": False,
            "decision_counts": dict(core_counts),
        },
        "corrections_ledger": ledger_path.name,
    }
    if output.name == "sku-real-luna-labels-20261010-v3":
        prior_dir = output.parent / "sku-real-luna-labels-20261010-v2"
        prior_manifest_path = prior_dir / "manifest.json"
        if prior_manifest_path.is_file():
            prior_manifest = json.loads(prior_manifest_path.read_text(encoding="utf-8"))
            manifest["superseded_prior_outputs"] = [{
                "path": str(prior_dir), "manifest_sha256": sha256_file(prior_manifest_path),
                "labels_sha256": prior_manifest.get("output_sha256", {}).get("labels.jsonl"),
                "status": "superseded_by_this_revision; preserved_unchanged",
                "reason": "The five-attribute-only companion labels received explicit scope-limited rationales.",
            }]
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--source-labels", type=Path, required=True)
    parser.add_argument("--audit-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = revise(args.inputs, args.source_labels, args.audit_zip, args.output)
    print(json.dumps(result["revision_summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
