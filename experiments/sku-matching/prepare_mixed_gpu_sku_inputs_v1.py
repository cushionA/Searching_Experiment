#!/usr/bin/env python3
"""Freeze a label-free mixed real-data replay for the existing choice gates.

Old preparations are copied without reranking. Independent dev inputs are
reconstructed only from captured bytes, with the pinned v7 handoff limiting
pending work. Every AU matrix cell remains in the host audit, including stock
cells. This adapter performs no acquisition, inference, or label loading.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / "claude_v9_gate"))
import sku_gate_sources as SOURCES
import collect_au_samples_expanded as COLLECT
import fetch_rakuten as RAKUTEN
import prepare_generic_model_sku_v1 as GENERIC
from build_scoped_html_blocks_v1 import extract_blocks
from integrate_claude_description_v1 import axis_text, document, encoded, sha, write_rows
from prepare_claude_alignment_v7 import ARCHIVE_SHA256, UPSTREAM_HEAD, finite, read_archive, whole
from prepare_scoped_html_trial_v1 import literal_refs, render_passage
from trial_full_scoped_condition_choices_v1 import load_inputs as validate_inputs, prediction_base
from trial_scoped_html_evidence_v1 import query_for

VERSION = "mixed-real-gpu-sku-inputs-v1"
DEFAULT_MEMBERSHIP = ROOT / ".lab-output/sku-luna-mixed-gpu-selection-20261012-v1/membership.jsonl"
DEFAULT_GENERIC = ROOT / ".lab-output/sku-generic-model-inputs-20261010-v2"
DEFAULT_V7 = ROOT / ".lab-output/sku-claude-v7-integration-review-20261012-v1"
DEFAULT_NOVEL = ROOT / ".lab-output/sku-full-novel-prep-20261011-v1"
NAMES = ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row_plans.jsonl", "case_plans.jsonl")
DESCRIPTION_FIELDS = ("itemComment", "extraItemComment", "extraItemCommentWithVideo", "detailComment", "detailLinkName")


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_bytes().splitlines() if line.strip()]


def file_sha(path):
    return sha(Path(path).read_bytes())


def normalized_path(value):
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("raw source path must be checkout-relative")
    return str(path)


class CapturedStore(SOURCES.RawStore):
    """RawStore interface over original local files and selected ZIP members."""

    def __init__(self, root, archives=()):
        super().__init__(root)
        self.archives, self.registry, self.aliases = {}, {}, defaultdict(set)
        for archive in archives:
            path = Path(archive)
            zipped = ZipFile(path)
            names = zipped.namelist()
            if len(set(names)) != len(names):
                raise ValueError("duplicate ZIP member")
            for name in names:
                normalized_path(name)
            self.archives[path] = zipped

    def raw(self, rel, expected_sha256=None):
        key = normalized_path(rel)
        self.aliases[key].add(str(rel))
        if key not in self._bytes:
            local = self.root / key
            choices = [(path, zipped, key) for path, zipped in self.archives.items() if key in zipped.namelist()]
            # A captured archive, when supplied, takes precedence over another
            # checkout's similarly named data. Repeated members must be equal.
            if choices:
                bodies = [zipped.read(name) for _, zipped, name in choices]
                if len({sha(body) for body in bodies}) != 1:
                    raise ValueError("conflicting original raw bytes across archives")
                body = bodies[0]
                refs = [{"archive": str(path.resolve()), "archive_sha256": file_sha(path), "member": name}
                        for path, _, name in choices]
            else:
                body = local.read_bytes()
                refs = [{"path": str(local.resolve())}]
            self._bytes[key], self._sha[key] = body, sha(body)
            self.registry[key] = {"sha256": sha(body), "bytes": len(body), "original_sources": refs}
        if expected_sha256 is not None and self._sha[key] != expected_sha256:
            raise ValueError("original raw SHA256 differs: " + key)
        return self._bytes[key]

    def sha(self, rel):
        self.raw(rel)
        return self._sha[normalized_path(rel)]


def verify_record_spans(store, value):
    if isinstance(value, dict):
        if all(key in value for key in ("raw_file", "sha256", "locator", "start", "end", "quote")):
            store.raw(value["raw_file"], value["sha256"])
            if not SOURCES.verify_span(store, value):
                raise ValueError("original literal source span differs")
        for child in value.values():
            verify_record_spans(store, child)
    elif isinstance(value, list):
        for child in value:
            verify_record_spans(store, child)


def capture_reference(store, value):
    """Read only explicit raw references; never walk a raw directory or ZIP."""
    if isinstance(value, dict):
        if isinstance(value.get("raw_file"), str) and isinstance(value.get("sha256"), str):
            store.raw(value["raw_file"], value["sha256"])
        for child in value.values():
            capture_reference(store, child)
    elif isinstance(value, list):
        for child in value:
            capture_reference(store, child)


def reconstruct_au(store, item_id, raw_file):
    """Reuse the acquisition parser's matrix grain; never infer combinations."""
    raw = store.raw(raw_file)
    obj = json.loads(raw)
    info, digest = obj["itemInfo"], sha(raw)
    options = obj.get("itemOptions")
    flags = []
    if not isinstance(options, dict):
        options = {}
        if info.get("hasFreeOptions") or info.get("hasPaidOptions"):
            flags.append("options_capture_missing")
    product, cells = COLLECT.normalize_sku(str(item_id), str(info.get("shopUserId", "")),
        {"raw_file": raw_file, "sha256": digest}, raw, {}, b"", obj, options, [], 0)
    sku, rows, stocks = info.get("skuInfo") or {}, [], []
    if not cells or not sku.get("skuId"):
        raise ValueError("captured AU SKU matrix or skuId is missing")
    for number, line in enumerate(cells, 1):
        cell = json.loads(line)
        r, c = cell["row_index"], cell["column_index"]
        axes = []
        for kind, index in (("row", r), ("column", c)):
            name = (sku.get("optionName") or {}).get(kind)
            values = sku.get(kind + "Names") or []
            if index >= len(values):
                raise ValueError("captured AU matrix cell lacks its source axis value")
            value = values[index]
            if not value:
                continue
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValueError("captured AU axis name/value is not a source string")
            axes.append({"axis_name": name, "value": value,
                "axis_name_span": SOURCES.json_leaf_span(store, raw_file, "$.itemInfo.skuInfo.optionName." + kind),
                "value_span": SOURCES.json_leaf_span(store, raw_file, f"$.itemInfo.skuInfo.{kind}Names[{index}]")})
        key = f"au:{item_id}:{sku['skuId']}:{r}:{c}"
        rows.append({"row_key": key, "row_index": r, "column_index": c, "sku_id": sku["skuId"], "axes": axes,
            "array_source": {"raw_file": raw_file, "sha256": digest,
                             "json_path": f"$.itemInfo.skuInfo.stockList[{r}][{c}]"}})
        stocks.append({"row_key": key, "source_record_raw": cell, "source_line": number})
    descriptions, iframe_urls = [], []
    for key in DESCRIPTION_FIELDS:
        value = info.get(key)
        if not isinstance(value, str) or not value:
            continue
        lines, urls = GENERIC.parse_html(value.encode("utf-8"), "utf-8")
        iframe_urls.extend(urls)
        for line in lines:
            descriptions.append({"text": line, "text_kind": "derived_visible_html_text",
                "source_field": "$.itemInfo." + key,
                "source_ref": {"raw_file": raw_file, "sha256": digest, "locator": "$.itemInfo." + key}})
    return ({"product_id": str(item_id), "title": info.get("itemTitle"),
        "title_source": SOURCES.json_leaf_span(store, raw_file, "$.itemInfo.itemTitle"),
        "axis_names": sku.get("optionName"), "rows": rows,
        "descriptions": GENERIC.dedupe_text_records(descriptions), "iframe_urls": list(dict.fromkeys(iframe_urls)),
        "purchase_options": []}, {"raw_file": raw_file, "sha256": digest, "item_info_raw": info,
        "item_options_raw": options if "itemOptions" in obj else None, "normalized_acquisition_record": product,
        "all_stock_cells": stocks, "raw_excluded_rows": [],
        "row_policy": "all captured stockList cells, as in the pinned producer; stock is audit-only"}, flags)


def reconstruct_product(store, case, au_raw):
    chosen = case.get("rakuten_selected", case.get("rakuten"))
    au, audit, flags = reconstruct_au(store, case["au_product_id"], au_raw)
    rel, expected = chosen["raw_file"], chosen["sha256"]
    raw = store.raw(rel, expected)
    encoding = next((a.get("value_span", {}).get("locator", {}).get("encoding") for a in chosen["axes"]
                     if a.get("value_span", {}).get("locator", {}).get("encoding")), None)
    if encoding is None:
        match = re.search(rb"charset\s*=\s*[\"']?([\w.-]+)", raw[:4096], re.I)
        encoding = match.group(1).decode("ascii") if match else "utf-8"
    text = raw.decode(encoding)
    data = RAKUTEN.app_data(text)
    sku_info = (((data or {}).get("api") or {}).get("data") or {}).get("itemInfoSku") or {}
    title = sku_info.get("title")
    title_span = SOURCES.html_title_span(store, rel, encoding, title) if isinstance(title, str) else None
    if title_span is None:
        match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        if not match:
            flags.append("rakuten_title_source_missing")
            title = ""
        else:
            title = match[1]
            title_span = SOURCES.make_span(store, rel, {"kind": "html_text", "encoding": encoding,
                "path": "html/head/title"}, match.start(1), match.end(1), title)
    lines, iframe_urls = GENERIC.parse_html(raw, encoding)
    descriptions = GENERIC.dedupe_text_records([{"text": line, "text_kind": "derived_visible_html_text",
        "source_ref": {"raw_file": rel, "sha256": expected, "locator": "whole_document_visible_text"}} for line in lines])
    product = {"dossier_id": case["dossier_id"], "au": au, "rakuten": {"url": chosen["url"],
        "title": title, "title_source": title_span, "descriptions": descriptions, "iframe_urls": iframe_urls}}
    verify_record_spans(store, chosen)
    audit["rakuten"] = {"raw_file": rel, "sha256": expected, "encoding": encoding,
        "full_decoded_html_chars": len(text), "selected_variant_id": chosen["variant_id"],
        "selected_variant_attributes_from_capture": SOURCES.rakuten_variant_attributes(store, rel, encoding, chosen["variant_id"])}
    if audit["rakuten"]["selected_variant_attributes_from_capture"] != chosen.get("variant_attributes", []):
        flags.append("rakuten_selected_attributes_differ")
    if any(not str(a.get("axis_label", "")).strip() for a in chosen["axes"]):
        flags.append("source_schema_empty_rakuten_axis_label")
    if any(not str(a.get("axis_name", "")).strip() for row in au["rows"] for a in row["axes"]):
        flags.append("source_schema_empty_au_axis_name")
    return product, audit, flags


def selected_variant_audit(store, chosen):
    """Return the original variant object and its observed array index."""
    first = next(axis["value_span"] for axis in chosen["axes"] if axis.get("value_span"))
    rel, encoding = first["raw_file"], first["locator"]["encoding"]
    raw = store.raw(rel, first["sha256"])
    data = RAKUTEN.app_data(raw.decode(encoding))
    sku_info = (((data or {}).get("api") or {}).get("data") or {}).get("itemInfoSku") or {}
    variants = [(number, value) for number, value in enumerate(sku_info.get("sku") or [])
                if str(value.get("variantId")) == str(chosen["variant_id"])]
    if len(variants) != 1:
        raise ValueError("selected variant is missing or ambiguous in captured SKU array")
    number, variant = variants[0]
    if chosen.get("source_row_index") is not None and chosen["source_row_index"] != number:
        raise ValueError("selected variant captured row index differs")
    return {"raw_file": rel, "sha256": sha(raw), "encoding": encoding,
        "source_row_index": number, "variant_id": chosen["variant_id"], "original_variant_record": variant}


def archive_jsonl(zipped, member):
    return [json.loads(line) for line in zipped.read(member).splitlines() if line.strip()]


def dev_group(store, membership, case):
    archive = next(path for path in store.archives if path.resolve() == (store.root / membership["source_archive"]).resolve())
    zipped = store.archives[archive]
    case_member = membership["source_case_ref"]["member"]
    run = case_member.split("/run-")[1].split("/")[0]
    parent = case_member.split("/run-")[0]
    member = parent + f"/groups-{run}/groups.jsonl"
    groups = [group for group in archive_jsonl(zipped, member) if group.get("dossier_id") == case["dossier_id"]]
    if len(groups) != 1 or groups[0].get("split") != "dev" or groups[0].get("excluded"):
        raise ValueError("independent selected case is not a unique eligible sidecar dev group")
    number = archive_jsonl(zipped, case_member)[membership["source_case_ref"]["line"] - 1]
    if number != case:
        raise ValueError("selected original case differs from captured archive line")
    return {"original_group": groups[0], "source_ref": {"archive": str(archive.resolve()),
        "archive_sha256": file_sha(archive), "member": member, "member_sha256": sha(zipped.read(member))}}


def add_new_plans(case, product, decision, handoffs, provenance, documents, requests, tasks, row_plans):
    fixed = {row["row_key"]: row for row in product["au"]["rows"]}
    rak = {axis["axis_key"]: axis for axis in case["rakuten"]["axes"]}
    if len(rak) != len(case["rakuten"]["axes"]):
        raise ValueError("duplicate captured Rakuten axis keys")
    states, reason = decision["row_states"], decision["reason"]
    if sum(states.values()) != len(fixed):
        raise ValueError("upstream aggregate row counts differ from captured full AU matrix")
    contexts = {h["au_row_key"]: h for h in handoffs}
    if len(contexts) != len(handoffs) or len(contexts) != states.get("pending_description_check", 0) or set(contexts) - set(fixed):
        raise ValueError("upstream pending handoff rows do not cover the fixed matrix")
    selected = list(contexts)
    if reason == "all_conditions_aligned_on_one_row":
        if states.get("aligned") != 1 or decision.get("top_row_key") not in fixed:
            raise ValueError("upstream direct decision lacks its unique captured row")
        selected = [decision["top_row_key"]]
    elif reason not in {"pending_description_check", "symmetric_condition_unresolved", "contradiction_on_every_row", "several_rows_aligned"}:
        raise ValueError("unsupported captured producer decision reason")
    options = {name: list(dict.fromkeys(a["value"] for row in fixed.values() for a in row["axes"] if a["axis_name"] == name))
               for name in dict.fromkeys(a["axis_name"] for row in fixed.values() for a in row["axes"])}

    def task(row, direction, axis, choices, origin, handoff=None, condition=None):
        side = "au" if direction == "rakuten_to_au" else "rakuten"
        doc_id = "fixed-" + side + ":" + product["dossier_id"]
        documents.setdefault(doc_id, document(product, side, provenance))
        doc = documents[doc_id]
        target = [{"axis_name": a["axis_name"], "value": a["value"]} for a in row["axes"]] if side == "au" else [
                 {"axis_name": a["axis_label"], "value": a["value"]} for a in rak.values()]
        semantic = {"direction": direction, "axis_name": axis["axis_label"] if side == "au" else axis["axis_name"],
            "selected_value": axis["value"], "option_values": deepcopy(choices), "target_document_id": doc_id,
            "target_document_sha256": doc["document_sha256"], "target_selected_axes": target,
            "target_selected_axes_text": axis_text(target)}
        digest = sha(encoded(semantic))
        rid = "generic-description:" + digest
        requests.setdefault(rid, {"request_id": rid, "request_sha256": digest, **semantic,
            "selected_value_is_binding_only": True, "applicability_required": True,
            "production_adoption_allowed": False, "upstream_core_is_proof": False})
        identity = {"case_id": case["case_id"], "au_row_key": row["row_key"], "direction": direction,
            "axis_key": axis.get("axis_key", axis.get("axis_name"))}
        tid = "condition:" + sha(encoded(identity))
        tasks.append({"task_id": tid, **identity, "request_id": rid, "dossier_id": case["dossier_id"],
            "au_product_id": case["au_product_id"], "source_sku_key": case["rakuten"]["source_sku_key"],
            "raw_condition": deepcopy(axis), "option_values": deepcopy(choices), "origin": origin,
            "source_ref": deepcopy(provenance), "source_handoff": deepcopy(handoff), "producer_condition": deepcopy(condition),
            "focused_verification_hints": deepcopy((condition or {}).get("residual") or []),
            "status": "not_evaluated", "verification_required": True, "production_adoption_allowed": False})
        return {"kind": "model", "task_id": tid, "request_id": rid, "direction": direction, "axis_key": identity["axis_key"]}

    for row_key in selected:
        row = fixed[row_key]
        au = {axis["axis_name"]: axis for axis in row["axes"]}
        if len(au) != len(row["axes"]):
            raise ValueError("duplicate captured AU axis names")
        represented_rak, represented_au, bindings = set(), set(), []
        handoff = contexts.get(row_key)

        def identical(r, a, condition=None):
            represented_rak.add(r["axis_key"])
            represented_au.add(a["axis_name"])
            bindings.append({"kind": "upstream_identical", "rakuten_axis_key": r["axis_key"], "au_axis_name": a["axis_name"],
                "raw_condition": deepcopy(r), "mapped_au_condition": deepcopy(a), "source_ref": deepcopy(provenance),
                "producer_condition": deepcopy(condition), "independent_semantic_gold": False})

        if handoff:
            if handoff.get("case_id") != case["case_id"] or handoff.get("au_product_id") != case["au_product_id"]:
                raise ValueError("handoff fixed case/product differs")
            for key in ("source_sku_key", "sku_record_key", "variant_id", "url"):
                if handoff["rakuten_sku"].get(key) != case["rakuten"].get(key):
                    raise ValueError("handoff fixed selected SKU differs")
            if handoff["au_row_axes"] != [{key: a[key] for key in ("axis_name", "value", "value_span")} for a in row["axes"]]:
                # Producer references may use Windows separators. Keep their
                # originals while comparing source identity and literal leaves.
                expected = deepcopy(handoff["au_row_axes"])
                actual = [{key: deepcopy(a[key]) for key in ("axis_name", "value", "value_span")} for a in row["axes"]]
                for values in (expected, actual):
                    for axis in values:
                        axis["value_span"]["raw_file"] = normalized_path(axis["value_span"]["raw_file"])
                if expected != actual:
                    raise ValueError("reconstructed AU row axes cannot join producer's original row")
            if handoff["au_axis_options"] != options:
                raise ValueError("reconstructed complete AU options differ from producer")
            for condition in handoff["conditions"]:
                status = condition["status"]
                name = condition.get("au_axis")
                if condition["kind"] == "au_only":
                    bindings.append(task(row, "au_to_rakuten", au[name], options[name], "v7_au_only", handoff, condition))
                    represented_au.add(name)
                    continue
                key = condition["axis_key"]
                r = rak[key]
                if condition.get("value") != r["value"] or name is not None and condition.get("au_value") != au[name]["value"]:
                    raise ValueError("handoff whole selected condition differs")
                if status == "aligned" and whole(r["value"]) == whole(au[name]["value"]):
                    identical(r, au[name], condition)
                    continue
                origin = "one_sided" if status == "one_sided" else "v7_" + status + ("_nonidentical" if status == "aligned" else "")
                bindings.append(task(row, "rakuten_to_au", r, r["family_values"], origin, handoff, condition))
                represented_rak.add(key)
                if name is not None:
                    bindings.append(task(row, "au_to_rakuten", au[name], options[name], origin, handoff, condition))
                    represented_au.add(name)
        else:
            for r in rak.values():
                matches = [a for a in au.values() if whole(r["value"]) == whole(a["value"])]
                if len(matches) == 1 and sum(whole(other["value"]) == whole(matches[0]["value"]) for other in rak.values()) == 1:
                    identical(r, matches[0])
            for key in rak.keys() - represented_rak:
                bindings.append(task(row, "rakuten_to_au", rak[key], rak[key]["family_values"], "v7_direct_unresolved"))
                represented_rak.add(key)
        for name in au.keys() - represented_au:
            bindings.append(task(row, "au_to_rakuten", au[name], options[name], "v7_unrepresented_au_axis", handoff))
            represented_au.add(name)
        row_plans.append({"case_id": case["case_id"], "au_row_key": row_key,
            "upstream_state": "pending_description_check" if handoff else "aligned", "condition_bindings": bindings,
            "rakuten_axis_count": len(rak), "au_axis_count": len(au), "source_ref": deepcopy(provenance),
            "all_original_axes_covered": represented_rak == set(rak) and represented_au == set(au), "production_adoption_allowed": False})
    return {"case_id": case["case_id"], "dossier_id": case["dossier_id"], "cohort": case["cohort"],
        "au_product_id": case["au_product_id"], "source_sku_key": case["rakuten"]["source_sku_key"],
        "upstream_decision": deepcopy(decision), "source_ref": deepcopy(provenance), "candidate_row_keys": selected,
        "total_fixed_au_row_count": len(fixed), "upstream_contradiction_row_count": states.get("contradiction", 0),
        "unresolved_omitted_row_count": states.get("symmetric_unresolved", 0) + (states.get("aligned", 0) if reason != "all_conditions_aligned_on_one_row" else 0),
        "all_pending_rows_received": True, "production_adoption_allowed": False}


def prepare(membership_path, output_dir, generic_dir=DEFAULT_GENERIC, v7_dir=DEFAULT_V7, novel_dir=DEFAULT_NOVEL, root=ROOT):
    output_dir, root = Path(output_dir), Path(root)
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite frozen mixed inputs")
    membership = read_rows(membership_path)
    ids = {row["case_id"] for row in membership}
    if len(ids) != len(membership):
        raise ValueError("mixed membership has duplicate case ids")
    archive_paths = {root / entry["source_archive"] for row in membership for entry in row["memberships"] if entry.get("source_archive")}
    store = CapturedStore(root, sorted(archive_paths))
    for row in membership:
        for entry in row["memberships"]:
            if entry.get("source_archive") and file_sha(root / entry["source_archive"]) != entry["source_archive_sha256"]:
                raise ValueError("membership source archive SHA differs")
    inputs = {str(Path(membership_path).resolve()): file_sha(membership_path)}
    generic_cases = {row["case_id"]: row for row in read_rows(Path(generic_dir) / "cases.jsonl")}
    generic_products = {row["dossier_id"]: row for row in read_rows(Path(generic_dir) / "products.jsonl")}
    for name in ("cases.jsonl", "products.jsonl", "manifest.json"):
        inputs[str((Path(generic_dir) / name).resolve())] = file_sha(Path(generic_dir) / name)
    combined = {name: [] for name in NAMES}
    old_ids = ids & generic_cases.keys()
    for prep in (Path(v7_dir) / "real-prepared", Path(novel_dir)):
        manifest = json.loads((prep / "manifest.json").read_bytes())
        if manifest.get("labels_read") is not False:
            raise ValueError("old preparation must explicitly exclude labels")
        source = {name: read_rows(prep / name) for name in NAMES}
        for name in (*NAMES, "manifest.json"):
            actual = file_sha(prep / name)
            if name != "manifest.json" and manifest["output_sha256"][name] != actual:
                raise ValueError("old frozen preparation SHA differs")
            inputs[str((prep / name).resolve())] = actual
        tasks = [task for task in source["tasks.jsonl"] if task["case_id"] in old_ids]
        requests = {task["request_id"] for task in tasks}
        selected_requests = [request for request in source["requests.jsonl"] if request["request_id"] in requests]
        document_ids = {request["target_document_id"] for request in selected_requests}
        combined["tasks.jsonl"].extend(tasks)
        combined["requests.jsonl"].extend(selected_requests)
        combined["documents.jsonl"].extend(doc for doc in source["documents.jsonl"] if doc["document_id"] in document_ids)
        for name in ("row_plans.jsonl", "case_plans.jsonl"):
            combined[name].extend(plan for plan in source[name] if plan["case_id"] in old_ids)
    plans = {plan["case_id"]: plan for plan in combined["case_plans.jsonl"]}
    if plans.keys() != old_ids:
        raise ValueError("not every selected old case has its unchanged original preparation")
    docs = {doc["document_id"]: doc for doc in combined["documents.jsonl"]}
    requests = {request["request_id"]: request for request in combined["requests.jsonl"]}
    archive = Path(v7_dir) / "20261012-sku-align-v6-v7-outputs.zip"
    _, _, payload, _, external_paths = read_archive(archive, ARCHIVE_SHA256)
    inputs[str(archive.resolve())] = file_sha(archive)
    for path in external_paths:
        inputs[str(path.resolve())] = file_sha(path)
    outputs_cases, products, failures = [], {}, []
    for member in membership:
        identity = member["case_id"]
        entry = member["memberships"][0]
        flags, group = [], None
        try:
            if identity in old_ids:
                case, product = deepcopy(generic_cases[identity]), deepcopy(generic_products[generic_cases[identity]["dossier_id"]])
                capture_reference(store, [case, product])
                verify_record_spans(store, [case, product])
                au_raw = product["au"]["title_source"]["raw_file"]
                raw = store.raw(au_raw)
                info = json.loads(raw)["itemInfo"]
                audit = {"raw_file": au_raw, "sha256": sha(raw), "item_info_raw": info,
                    "row_policy": "original frozen product rows copied without pruning"}
            else:
                original = deepcopy(entry["original_case"])
                group = dev_group(store, entry, original)
                case = GENERIC.make_case(original, entry["source_cohort"])
                # GENERIC.make_case intentionally removes transport metadata.
                # The mixed host artifact retains the original fixed row/URL
                # identity fields as well as the choice runner's normal fields.
                case["rakuten"] = deepcopy(original["rakuten_selected"])
                case["original_split"], case["split"], case["group_id"] = original["split"], "dev", group["original_group"]["group_id"]
                cohort = "eval-v3" if entry["source_cohort"].endswith("v3") else "eval-v2"
                prefix = f".lab-output/sku-independent-real-20261011/{cohort}/align-v7-token/align/"
                decision_member, handoff_member = prefix + "decisions.jsonl", prefix + "handoff.jsonl"
                decisions = [finite(json.loads(line)) for line in payload[decision_member].splitlines() if json.loads(line).get("case_id") == identity]
                handoffs = [finite(json.loads(line)) for line in payload[handoff_member].splitlines() if json.loads(line).get("case_id") == identity]
                if len(decisions) != 1:
                    raise ValueError("selected independent case lacks one pinned v7 decision")
                au_paths = {h["au_product_source"]["raw_file"] for h in handoffs}
                if not au_paths:
                    names = [name for zipped in store.archives.values() for name in zipped.namelist()
                             if name.endswith("/" + case["au_product_id"] + "-item.json")]
                    if len(set(names)) != 1:
                        raise ValueError("captured fixed AU item path is missing or ambiguous")
                    au_paths = {names[0]}
                if len({normalized_path(path) for path in au_paths}) != 1:
                    raise ValueError("handoff names multiple fixed AU captures")
                au_raw = next(iter(au_paths))
                product, audit, flags = reconstruct_product(store, original, au_raw)
                provenance = {"archive": str(archive.resolve()), "archive_sha256": ARCHIVE_SHA256,
                    "member": decision_member, "member_sha256": sha(payload[decision_member]),
                    "input_case_source": deepcopy(entry["source_case_ref"]), "labels_read": False}
                plans[identity] = add_new_plans(case, product, decisions[0], handoffs, provenance, docs, requests,
                    combined["tasks.jsonl"], combined["row_plans.jsonl"])
                combined["case_plans.jsonl"].append(plans[identity])
            products[product["dossier_id"]] = product
            audit["selected_rakuten_source_record"] = selected_variant_audit(store, case["rakuten"])
            score_eligible = all(e.get("primary_score_eligible", False) for e in member["memberships"])
            fatal_flags = [flag for flag in flags if flag.startswith("source_schema_") or flag == "rakuten_selected_attributes_differ"]
            outputs_cases.append({"schema_version": VERSION, "case_id": identity, "dossier_id": case["dossier_id"],
                "cohort": case["cohort"], "source_strata": member["membership_strata"], "memberships": deepcopy(member["memberships"]),
                "group_id": case.get("group_id"), "split": case.get("split", "development_replay"),
                "dev_group_provenance": group, "primary_score_eligible": score_eligible and not fatal_flags,
                "selection_primary_score_eligible": score_eligible, "source_flags": flags,
                "source_complete_for_sku_matrix": True, "optional_source_capture_incomplete": "options_capture_missing" in flags,
                "fixed_au_url": "https://wowma.jp/item/" + case["au_product_id"],
                "fixed_rakuten_url": case["rakuten"]["url"], "original_case": case, "original_product": product,
                "au_rows": deepcopy(product["au"]["rows"]), "rakuten_selected": deepcopy(case["rakuten"]),
                "upstream_case_plan": deepcopy(plans[identity]), "raw_audit": audit,
                "synthetic": False, "labels_read": False, "production_adoption_allowed": False})
        except (KeyError, ValueError, OSError, IndexError, TypeError) as error:
            failures.append({"case_id": identity, "source_incomplete": True, "primary_score_eligible": False,
                "error_type": type(error).__name__, "error": str(error), "original_membership": member})
    if failures:
        # A failed case's original membership remains available for diagnosis.
        # It cannot acquire a fabricated candidate, source, or task.
        failed = {failure["case_id"] for failure in failures}
        for name in ("tasks.jsonl", "row_plans.jsonl", "case_plans.jsonl"):
            combined[name] = [value for value in combined[name] if value["case_id"] not in failed]
    selected_request_ids = {task["request_id"] for task in combined["tasks.jsonl"]}
    combined["requests.jsonl"] = [value for key, value in requests.items() if key in selected_request_ids]
    selected_document_ids = {request["target_document_id"] for request in combined["requests.jsonl"]}
    combined["documents.jsonl"] = [value for key, value in docs.items() if key in selected_document_ids]
    bindings = defaultdict(list)
    for task in combined["tasks.jsonl"]:
        bindings[task["request_id"]].append(task)
    baseline = [{**prediction_base(request, bindings), "state": "unknown", "winning_value": None,
        "quote_refs": [], "reason": "unevaluated_input_placeholder", "model_inference_run": False} for request in combined["requests.jsonl"]]
    blocks, excluded = [], []
    for doc in combined["documents.jsonl"]:
        if doc["side"] != "au":
            continue
        raw_refs = {(normalized_path(ref["raw_file"]), ref["sha256"]) for ref in doc["title"]["source_refs"]}
        if len(raw_refs) != 1:
            raise ValueError("fixed AU document must bind one original item capture")
        rel, digest = next(iter(raw_refs))
        info = json.loads(store.raw(rel, digest))["itemInfo"]
        seen = set()
        for field in DESCRIPTION_FIELDS:
            html = info.get(field)
            if not isinstance(html, str) or not html or html in seen:
                continue
            seen.add(html)
            for block in extract_blocks(html, {"raw_file": rel, "sha256": digest, "json_path": "$.itemInfo." + field}, doc["title"]["text"]):
                refs, missing = literal_refs(block, doc)
                new = {**block, "target_document_id": doc["document_id"], "target_document_sha256": doc["document_sha256"],
                    "quote_refs": refs, "model_passage": render_passage(block), "scope_status": "unproved"}
                if missing or not refs:
                    excluded.append({**new, "exclusion_reason": "not_all_visible_parts_bind_original_document", "unmatched_parts": missing})
                else:
                    blocks.append(new)
    retrieval = []
    by_doc = defaultdict(list)
    for block in blocks:
        by_doc[block["target_document_id"]].append(block)
    for request in combined["requests.jsonl"]:
        if request["direction"] != "rakuten_to_au":
            continue
        for block in by_doc[request["target_document_id"]]:
            retrieval.append({"id": "scoped-html-rank:" + sha((request["request_id"] + block["block_id"]).encode()),
                "request_id": request["request_id"], "target_document_id": request["target_document_id"], "block_id": block["block_id"],
                "query": query_for(request, docs[request["target_document_id"]]), "passage": block["model_passage"], "quote_refs": block["quote_refs"]})
    model_tasks = [{**deepcopy(task), "source_strata": next(case["source_strata"] for case in outputs_cases if case["case_id"] == task["case_id"]),
        "normalized_request": deepcopy(requests[task["request_id"]]), "labels_read": False,
        "whole_values_and_full_choices_preserved": True, "numeric_core_is_proof": False} for task in combined["tasks.jsonl"]]
    output_dir.mkdir(parents=True, exist_ok=False)
    extra = {"gpu-cases.jsonl": outputs_cases, "model-tasks.jsonl": model_tasks, "products.jsonl": list(products.values()),
        "membership.jsonl": membership, "failures.jsonl": failures, "baseline-predictions.jsonl": baseline,
        "blocks.jsonl": blocks, "excluded-blocks.jsonl": excluded, "retrieval-requests.jsonl": retrieval}
    for name, values in {**combined, **extra}.items():
        write_rows(output_dir / name, values)
    raw_registry = []
    raw_dir = output_dir / "raw-captures"
    raw_dir.mkdir()
    for rel, spec in sorted(store.registry.items()):
        name = spec["sha256"] + Path(rel).suffix
        path = raw_dir / name
        if not path.exists():
            path.write_bytes(store.raw(rel))
        raw_registry.append({"raw_file": rel, "original_aliases": sorted(store.aliases[rel]), **spec,
            "bundle_file": "raw-captures/" + name})
    write_rows(output_dir / "raw-source-registry.jsonl", raw_registry)
    manifest = {"schema_version": VERSION, "phase": "prepare", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "upstream_head": UPSTREAM_HEAD, "adapter_sha256": file_sha(Path(__file__)), "synthetic": False,
        "labels_read": False, "labels_in_model_inputs": False, "model_inference_run": False,
        "production_adoption_allowed": False, "development_only": True, "independent_test": False,
        "errors_only": False,
        "actual_population": "selected mixed development replay; all prior errors plus independent dev and old controls",
        "baseline_predictions_are_unevaluated": True, "baseline_must_be_regenerated_for_all_requests": True,
        "whole_values_and_alternatives_preserved": True, "candidate_url_routing": False,
        "upstream_row_state_contract": "pinned aggregate counts/top row plus original pending handoffs; omitted identities are never fabricated",
        "membership_case_count": len(membership), "case_count": len(combined["case_plans.jsonl"]),
        "primary_score_eligible_case_count": sum(case["primary_score_eligible"] for case in outputs_cases),
        "cohort_case_counts": dict(Counter(case["cohort"] for case in outputs_cases)),
        "source_flag_counts": dict(Counter(flag for case in outputs_cases for flag in case["source_flags"])),
        "stratum_case_counts": dict(Counter(stratum for case in outputs_cases for stratum in case["source_strata"])),
        "baseline_matched_count": sum(plan["upstream_decision"]["decision"] == "matched" for plan in combined["case_plans.jsonl"]),
        "row_count": len(combined["row_plans.jsonl"]), "host_au_row_count": sum(len(case["au_rows"]) for case in outputs_cases),
        "request_count": len(combined["requests.jsonl"]), "task_count": len(combined["tasks.jsonl"]),
        "document_count": len(combined["documents.jsonl"]), "block_count": len(blocks), "failure_count": len(failures),
        "input_sha256": inputs, "source_raw_sha256": {rel: spec["sha256"] for rel, spec in store.registry.items()},
        "raw_capture_output_sha256": {"raw-captures/" + path.name: file_sha(path) for path in raw_dir.iterdir()},
        "output_sha256": {name: file_sha(output_dir / name) for name in (*combined, *extra, "raw-source-registry.jsonl")}}
    (output_dir / "manifest.json").write_bytes(encoded(manifest) + b"\n")
    validate_inputs(output_dir)
    if any(file_sha(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("frozen source changed during mixed preparation")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--membership", type=Path, default=DEFAULT_MEMBERSHIP)
    parser.add_argument("--generic", type=Path, default=DEFAULT_GENERIC)
    parser.add_argument("--v7", type=Path, default=DEFAULT_V7)
    parser.add_argument("--novel", type=Path, default=DEFAULT_NOVEL)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.membership, args.out, args.generic, args.v7, args.novel)
    print(json.dumps({key: result[key] for key in ("case_count", "request_count", "task_count", "host_au_row_count", "source_flag_counts", "failure_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
