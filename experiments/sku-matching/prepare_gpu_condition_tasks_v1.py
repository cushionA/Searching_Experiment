"""Prepare label-blind, source-backed condition/evidence packets for GPU review.

The AU full pools live only in the host plan. Model packets bind one AU row and
one selected Rakuten condition to complete, applicable source blocks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FROZEN = ROOT / ".lab-output/sku-kaggle-gpu-20261010-v9/dataset-upload"
OUT = ROOT / ".lab-output/sku-gpu-condition-tasks-20261010-v1"
CLAUDE_CODE = Path(os.environ.get("CLAUDE_CODE", "/workspace/Searching_Experiment-claude-share-20261010/experiments/sku-matching"))
if str(CLAUDE_CODE) not in sys.path:
    sys.path.insert(0, str(CLAUDE_CODE))
from sku_gate_atoms import atomize, compact  # noqa: E402
import sku_gate_sources as gate_sources  # noqa: E402

MAX_EVIDENCE_CHARS = 8000  # about 2k tokens; only whole blocks are admitted
INPUT_FILES = ("inputs.jsonl", "source-cases.jsonl")
FORBIDDEN_SCOPE = re.compile(r"series|sibling|link|search_keyword|search_words|recommend", re.I)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def axis_pairs(sku: str) -> list[tuple[str, str]]:
    """Split the prepared literal axis/value string without normalizing quotes."""
    pairs = []
    for bit in re.split(r"\s*/\s*", sku):
        if "=" not in bit:
            if pairs:
                name, value = pairs[-1]
                pairs[-1] = (name, value + " / " + bit.strip())
            continue
        name, value = bit.split("=", 1)
        pairs.append((name.strip(), value.strip()))
    return pairs


def condition_atoms(pairs: list[tuple[str, str]]) -> list[dict]:
    color_vocab = frozenset(v for n, v in pairs if "色" in n or "カラー" in n)
    out = []
    for axis_name, selected_value in pairs:
        parsed = atomize(selected_value, axis_name, color_vocab)
        for atom in parsed["atoms"]:
            payload = {k: v for k, v in atom.items() if k not in ("type", "quote", "offset", "axis_label", "derivation")}
            value = payload.get("value") if set(payload) == {"value"} else payload
            out.append({"axis_name_raw": axis_name, "selected_value_raw": selected_value,
                        "atom_kind": atom["type"], "atom_value": value,
                        "atom_quote": atom["quote"], "atom_offset": atom["offset"]})
        if not parsed["atoms"]:
            out.append({"axis_name_raw": axis_name, "selected_value_raw": selected_value,
                        "atom_kind": "untyped_value", "atom_value": compact(selected_value),
                        "atom_quote": selected_value, "atom_offset": [0, len(selected_value)]})
        elif parsed["residue"]:
            out.append({"axis_name_raw": axis_name, "selected_value_raw": selected_value,
                        "atom_kind": "unresolved_residue", "atom_value": parsed["residue"],
                        "atom_quote": selected_value, "atom_offset": [0, len(selected_value)]})
    for index, req in enumerate(out):
        req["condition_index"] = index
    return out


def normalized_words(text: str) -> set[str]:
    text = compact(text)
    return {w for w in re.findall(r"[\wぁ-んァ-ヶ一-龥]+", text) if len(w) > 1}


def relevant(atom: dict, quote: str) -> bool:
    value = atom["atom_value"]
    if isinstance(value, dict):
        value = value.get("value", value)
    if isinstance(value, list):
        def num_string(x):
            return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)
        return all(compact(num_string(x)) in compact(quote) for x in value)
    s = compact(str(value))
    if s and s in compact(quote):
        return True
    # Japanese compound atoms sometimes appear in a longer wording in the page.
    tokens = normalized_words(atom["atom_quote"])
    return bool(tokens and tokens <= normalized_words(quote))


COMPONENT_TERMS = {"lace": "レース", "drape": "ドレープ", "tassel": "タッセル", "hook": "フック",
                   "door": "ドア", "panel": "パネル", "blanket": "毛布", "electric_blanket": "電気毛布",
                   "armrest": "肘掛", "top_board": "天板", "bookshelf": "本棚", "handle": "持ち手",
                   "storage_bag": "収納袋", "duvet_cover": "布団カバー", "futon": "布団", "pad": "敷きパッド",
                   "mattress": "マットレス"}


def relevant_requirement(req: dict, quote: str) -> bool:
    if relevant(req, quote):
        return True
    name, kind, value = req["axis_name_raw"], req["atom_kind"], req["atom_value"]
    if kind == "component_presence":
        component_key = value.get("component") if isinstance(value, dict) else None
        component = COMPONENT_TERMS.get(component_key) or next((term for key, term in COMPONENT_TERMS.items() if key in name or term in name), None)
        if component is None:
            # A variant can itself name the added component, as in 毛布セット.
            component = next((term for term in COMPONENT_TERMS.values() if term in req["selected_value_raw"]), None)
        return bool(component and component in quote)
    return False


def source_kind(entry: dict) -> str:
    if entry.get("field") == "title":
        return "current_page_title"
    if entry.get("field") == "selected_sku":
        return "rakuten_selected_sku"
    if entry.get("field") == "selected_variant_attribute":
        return "rakuten_selected_sku_attribute"
    return "current_page_description"


def safe_rakuten_title(entry: dict) -> dict | None:
    quote = entry["quote"]
    # The captured title may start with an offer banner. Select the literal
    # product-title suffix as a raw substring so promotional prices stay out.
    if re.search(r"\d\s*%|クーポン|\d+円|価格", quote):
        close = quote.find("】")
        if close < 0:
            return None
        start = close + 1
        while start < len(quote) and quote[start].isspace():
            start += 1
        quote = quote[start:]
    if not quote or re.search(r"\d\s*%|クーポン|\d+円|価格", quote):
        return None
    return {**entry, "quote": quote}


def current_entry(entry: dict) -> bool:
    scope = str(entry.get("scope", ""))
    quote = str(entry.get("quote", ""))
    linked_text = re.search(r"こちら|コチラ|リンク|色違い|別ページ|シリーズ", quote)
    return (not FORBIDDEN_SCOPE.search(scope) and not linked_text
            and entry.get("field") in ("title", "description"))


def eligible_registry(registry: list[dict]) -> list[dict]:
    entries = [e for e in registry if current_entry(e)]
    # Remove page search-keyword tails even when the source registry labels the
    # entire Rakuten field as one individual description excerpt.
    limits = {}
    for entry in entries:
        ref = entry.get("source_ref", {})
        quote = compact(entry.get("quote", ""))
        if entry.get("field") == "description" and ("検索ワード" in quote or "▼検索" in quote):
            key = (entry.get("side"), ref.get("raw_file"), ref.get("raw_json_path"))
            limits[key] = min(limits.get(key, 10**9), ref.get("source_line", 10**9))
    eligible = []
    for entry in entries:
        ref = entry.get("source_ref", {})
        key = (entry.get("side"), ref.get("raw_file"), ref.get("raw_json_path"))
        if ref.get("source_line", 10**9) >= limits.get(key, 10**9):
            continue
        if re.search(r"⇒このショップ|レビューを見る|\d+代/|\(件\)$", entry.get("quote", "")):
            continue
        eligible.append(entry)
    return eligible


def raw_quote_location(entry: dict, quote: str) -> dict | None:
    """Resolve a quote through the frozen gate source-span API; unresolved is rejected."""
    ref = entry.get("source_ref") or {}
    if entry.get("verified_span"):
        span = entry["verified_span"]
        if span.get("quote") == quote and gate_sources.verify_span(gate_sources.RawStore(ROOT), span):
            return {"verified": True, "encoding": "decoded_raw_source", "locator": span["locator"],
                    "start": span["start"], "end": span["end"], "source_sha256": span["sha256"]}
    if entry.get("field") == "selected_sku":
        input_path = FROZEN / "source-cases.jsonl"
        data = input_path.read_bytes()
        at = None
        line_number = None
        char_line_offset = 0
        source_case_id = ref.get("input_case_id") or entry.get("case_id")
        for index, line in enumerate(data.decode("utf-8").splitlines(keepends=True), 1):
            obj = json.loads(line)
            if obj.get("case_id") == source_case_id and obj.get("rakuten_selected_sku") == quote:
                marker = '"rakuten_selected_sku":' + json.dumps(quote, ensure_ascii=False)
                within = line.find(marker)
                if within < 0:
                    return None
                # jsonl_leaf offsets are relative to the decoded JSON leaf,
                # as required by RawStore.verify_span.
                at = 0
                line_number = index
                break
            char_line_offset += len(line)
        if at is None:
            return None
        return {"verified": True, "encoding": "frozen_label_blind_source_cases",
                "locator": {"kind": "jsonl_leaf", "file": str(input_path.relative_to(ROOT)),
                                                 "line": line_number, "json_path": "$.rakuten_selected_sku"},
                "start": at, "end": at + len(quote), "source_sha256": sha256(data)}
    raw_file = ref.get("raw_file")
    expected = ref.get("raw_sha256")
    if not raw_file or not expected:
        return None
    store = gate_sources.RawStore(ROOT)
    try:
        store.raw(raw_file, expected)
        json_path = ref.get("raw_json_path")
        if json_path and json_path.startswith("$."):
            span = gate_sources.json_leaf_span(store, raw_file, json_path, quote)
        else:
            span = gate_sources.locate_lines_in_text(store, raw_file, "EUC-JP", [quote], "registry_quote")[0]
        if span and gate_sources.verify_span(store, span):
            return {"verified": True, "encoding": "decoded_raw_source", "locator": span["locator"],
                    "start": span["start"], "end": span["end"],
                    "source_sha256": span["sha256"]}
    except (KeyError, IndexError, ValueError, OSError, UnicodeDecodeError):
        return None
    return None


def selected_variant_attribute_entries(case: dict) -> list[dict]:
    """Resolve selected Rakuten SKU attributes directly in the variant record."""
    title = next((e for e in case["source_evidence_registry"]
                  if e.get("side") == "rakuten" and e.get("field") == "title"), None)
    if not title:
        return []
    ref = title["source_ref"]
    rel = ref["raw_file"]
    store = gate_sources.RawStore(ROOT)
    try:
        store.raw(rel, ref["raw_sha256"])
        text = store.text(rel, "EUC-JP")
    except (KeyError, ValueError, OSError, UnicodeDecodeError):
        return []
    selected = [compact(v) for _, v in axis_pairs(case["rakuten_selected_sku"])]
    decoder = json.JSONDecoder()
    marker = re.compile(r'\{"variantId":("(?:\\.|[^"\\])*")\s*,"selectorValues":\[')
    variant_ids = set()
    for match in marker.finditer(text):
        try:
            variant_id = json.loads(match.group(1))
            variant, _ = decoder.raw_decode(text, match.start())
        except (json.JSONDecodeError, ValueError):
            continue
        values = variant.get("selectorValues", [])
        if [compact(v) for v in values] == selected:
            variant_ids.add(variant_id)
    if len(variant_ids) != 1:
        return []
    variant_id = next(iter(variant_ids))
    try:
        attributes = gate_sources.rakuten_variant_attributes(store, rel, "EUC-JP", variant_id)
    except (KeyError, ValueError, OSError, UnicodeDecodeError):
        return []
    out = []
    for index, attr in enumerate(attributes):
        span = attr.get("value_span")
        if not span or not gate_sources.verify_span(store, span):
            continue
        title_text = attr["title"]
        if re.search(r"価格|金額|在庫|送料|クーポン|販売|発送|配送", title_text):
            continue
        out.append({"id": f"selected-attribute-{index}", "side": "rakuten", "field": "selected_variant_attribute",
                    "scope": f"selected_variant_attribute:{title_text}", "quote": attr["value"],
                    "source_ref": {"raw_file": rel, "raw_sha256": ref["raw_sha256"],
                                   "attribute_title": title_text}, "verified_span": span})
    return out


def evidence_block(entry: dict, input_sha: str) -> dict:
    ref = entry.get("source_ref") or {}
    leaf_offset = raw_quote_location(entry, entry["quote"])
    if leaf_offset is None:
        return None
    verified_file = leaf_offset["locator"].get("file") or ref.get("raw_file")
    source_ref = {"source_side": entry.get("side"), "raw_file": verified_file,
                  "locator": leaf_offset["locator"], "registry_id": entry.get("id"),
                  "raw_sha256": leaf_offset["source_sha256"]}
    return {"source_kind": source_kind(entry), "quote": entry["quote"], "scope": entry.get("scope", "unknown"),
            "source_ref": source_ref, "source_sha256": leaf_offset["source_sha256"], "leaf_offset": leaf_offset}


def axis_family(name: str) -> str:
    if any(x in name for x in ("カラー", "色")):
        return "color"
    if any(x in name for x in ("サイズ", "寸法")):
        return "size"
    if any(x in name for x in ("枚数", "セット内容")):
        return "count"
    if "タイプ" in name or "種類" in name:
        return "type"
    return compact(name)


def atom_payload(atom: dict):
    payload = {k: v for k, v in atom.items() if k not in ("type", "quote", "offset", "axis_label", "derivation")}
    return payload.get("value") if set(payload) == {"value"} else payload


def equivalent(atom: dict, req: dict) -> bool:
    atom_kind = atom.get("type", atom.get("atom_kind"))
    if atom_kind != req["atom_kind"]:
        return False
    av = atom.get("atom_value") if "atom_value" in atom else atom_payload(atom)
    rv = req["atom_value"]
    if atom_kind == "dimension" and isinstance(av, dict) and isinstance(rv, dict):
        return av.get("value") == rv.get("value")
    return av == rv


def row_condition_status(row: dict, requirements: list[dict]) -> tuple[list[dict], list[dict]]:
    row_pairs = axis_pairs(row["sku"])
    parsed_atoms = []
    for name, value in row_pairs:
        decomposition = atomize(value, name)
        row_atoms = decomposition["atoms"]
        for atom in row_atoms:
            parsed_atoms.append({"axis_name_raw": name, "value_raw": value, "atom_kind": atom["type"],
                           "atom_value": atom_payload(atom), "atom_offset": atom["offset"],
                           "atom_quote": atom["quote"]})
        if not row_atoms:
            parsed_atoms.append({"axis_name_raw": name, "value_raw": value, "atom_kind": "untyped_value",
                                 "atom_value": compact(value), "atom_offset": [0, len(value)], "atom_quote": value})
        elif decomposition["residue"]:
            residue = decomposition["residue"]
            start = value.find(residue)
            parsed_atoms.append({"axis_name_raw": name, "value_raw": value, "atom_kind": "unresolved_residue",
                                 "atom_value": residue, "atom_offset": [max(start, 0), max(start, 0) + len(residue)],
                                 "atom_quote": residue})
    statuses = []
    matched_atoms = set()
    for index, req in enumerate(requirements):
        related = [(i, atom) for i, atom in enumerate(parsed_atoms)
                   if axis_family(atom["axis_name_raw"]) == axis_family(req["axis_name_raw"])
                   and atom["atom_kind"] == req["atom_kind"]]
        exact = [(i, a) for i, a in related if equivalent(a, req)]
        if exact:
            status = "supported_by_AU_row"
            matched_atoms.update(i for i, _ in exact)
        elif related and req["atom_kind"] in ("dimension", "color", "named_size", "transparency", "fabric", "tier_count"):
            status = "typed_conflict"
        else:
            status = "unknown_or_missing"
        statuses.append({"condition_index": index, "axis_name_raw": req["axis_name_raw"],
                         "selected_value_raw": req["selected_value_raw"], "atom_kind": req["atom_kind"],
                         "atom_value": req["atom_value"], "status": status})
    additional = [{**atom, "relation_to_selected_rakuten_conditions": "AU_only_or_unmatched"}
                  for i, atom in enumerate(parsed_atoms) if i not in matched_atoms]
    return statuses, additional


def au_row_source_axes(row: dict, registry: list[dict]) -> list[dict]:
    """Exact original AU row/column name and value spans for one host-bound row."""
    entry = next(e for e in registry if e.get("side") == "au" and e.get("field") == "title")
    ref = entry["source_ref"]
    store = gate_sources.RawStore(ROOT)
    store.raw(ref["raw_file"], ref["raw_sha256"])
    parts = row["row_key"].split(":")
    row_i, col_i = int(parts[-2]), int(parts[-1])
    raw = store.json(ref["raw_file"])
    row_name = gate_sources.resolve_json_path(raw, "$.itemInfo.skuInfo.optionName.row")
    col_name = gate_sources.resolve_json_path(raw, "$.itemInfo.skuInfo.optionName.column")
    out = []
    for axis_name, name_path, value_path in (
        (row_name, "$.itemInfo.skuInfo.optionName.row", f"$.itemInfo.skuInfo.rowNames[{row_i}]"),
        (col_name, "$.itemInfo.skuInfo.optionName.column", f"$.itemInfo.skuInfo.columnNames[{col_i}]"),
    ):
        value = gate_sources.resolve_json_path(raw, value_path)
        if not value:
            continue
        name_span = gate_sources.json_leaf_span(store, ref["raw_file"], name_path, axis_name)
        value_span = gate_sources.json_leaf_span(store, ref["raw_file"], value_path, value)
        if not name_span or not value_span or not gate_sources.verify_span(store, name_span) or not gate_sources.verify_span(store, value_span):
            raise ValueError(f"Unable to verify AU row axis source span for {row['row_key']}")
        out.append({"axis_name_raw": axis_name, "value_raw": value, "axis_name_span": name_span,
                    "value_span": value_span})
    return out


def choose_focus_row(case: dict, requirements: list[dict]) -> tuple[dict | None, list[dict]]:
    rows = case["au_rows"]
    globally_present_families = {axis_family(name) for row in rows for name, _ in axis_pairs(row["sku"])}
    constrainable = [i for i, req in enumerate(requirements)
                     if axis_family(req["axis_name_raw"]) in globally_present_families]
    ranked = []
    for row in rows:
        row_pairs = axis_pairs(row["sku"])
        found = set()
        for name, val in row_pairs:
            for atom in atomize(val, name)["atoms"]:
                for i, req in enumerate(requirements):
                    if axis_family(req["axis_name_raw"]) != axis_family(name):
                        continue
                    if equivalent(atom, req):
                        found.add(i)
        ranked.append((row, found))
    valid = [row for row, found in ranked if constrainable and all(i in found for i in constrainable)]
    candidates = valid
    # Every requirement on an AU-present axis must match the same row. An
    # absent axis is a Rakuten-only condition and remains a diagnostic packet.
    if len(candidates) == 1:
        return candidates[0], candidates
    if not candidates:
        best = max((len(found.intersection(constrainable)) for _, found in ranked), default=0)
        candidates = [row for row, found in ranked if len(found.intersection(constrainable)) == best]
    return None, candidates


def packets_for_case(case: dict, input_sha: str) -> tuple[list[dict], dict]:
    pairs = axis_pairs(case["rakuten_selected_sku"])
    requirements = condition_atoms(pairs)
    # Bind each derived atom to the exact case-bound frozen selected-SKU leaf.
    source_cases_path = FROZEN / "source-cases.jsonl"
    source_cases_bytes = source_cases_path.read_bytes()
    source_cases_text = source_cases_bytes.decode("utf-8")
    source_case_line = None
    source_case_line_number = None
    cursor = 0
    for line_number, line in enumerate(source_cases_text.splitlines(keepends=True), 1):
        obj = json.loads(line)
        if obj.get("case_id") == case["case_id"]:
            source_case_line = line.rstrip("\r\n")
            source_case_line_number = line_number
            break
        cursor += len(line)
    sku_value = case["rakuten_selected_sku"]
    selected_sku_marker = '"rakuten_selected_sku":' + json.dumps(sku_value, ensure_ascii=False)
    selected_sku_in_line = source_case_line.find(selected_sku_marker) if source_case_line else -1
    if selected_sku_in_line < 0:
        raise ValueError(f"Could not bind selected SKU leaf to source case {case['case_id']}")
    sku_leaf_start = 0
    selected_sku_source_span = {"source_side": "rakuten", "raw_file": str(source_cases_path.relative_to(ROOT)),
                                "locator": {"kind": "jsonl_leaf", "line": source_case_line_number,
                                            "json_path": "$.rakuten_selected_sku"},
                                "raw_sha256": sha256(source_cases_bytes), "verified": True,
                                "start": sku_leaf_start, "end": sku_leaf_start + len(sku_value),
                                "quote": sku_value, "derivation": "case_bound_frozen_selected_sku"}
    for req in requirements:
        req["source_span"] = selected_sku_source_span
    focus, candidates = choose_focus_row(case, requirements)
    pool = case["au_rows"]
    status_rows, additional_atoms = [], []
    for row in pool:
        statuses, extras = row_condition_status(row, requirements)
        status_rows.append({"row_key": row["row_key"], "condition_status": statuses,
                            "au_only_or_unmatched_atoms": extras})
        additional_atoms.extend({"row_key": row["row_key"], **atom} for atom in extras)
    host = {"case_id": case["case_id"], "dossier_id": case["dossier_id"],
            "au_row_count": len(pool), "au_rows": pool,
            "row_condition_statuses": status_rows,
            "au_only_or_unmatched_atom_count": len(additional_atoms),
            "au_only_or_unmatched_atoms": additional_atoms,
            "focus_status": "unique_partial_candidate_on_au_present_axes" if focus else "host_review_nonunique_or_unmatched",
            "focus_row_key": focus["row_key"] if focus else None,
            "max_match_candidate_row_keys": [r["row_key"] for r in candidates],
            "required_conditions": requirements, "required_packet_ids": [],
            "required_au_conditions": [], "source_evidence_rejections": [], "evidence_overflow": [],
            "blocking_issues": []}
    if not focus:
        host["blocking_issues"].append("nonunique_or_unmatched_focus_row")
        return [], host

    registry = case["source_evidence_registry"]
    allowed = eligible_registry(registry)
    variant_attributes = selected_variant_attribute_entries(case)
    packets = []
    source_rejections, evidence_overflow = [], []

    def evidence_for(req: dict, side: str, include_selected_rakuten: bool = False) -> list[dict]:
        opposite = "au" if side == "rakuten" else "rakuten"
        matching = [e for e in allowed if e.get("side") == opposite and e.get("field") == "description"
                    and relevant_requirement(req, e["quote"])]
        if opposite == "rakuten":
            matching += [e for e in variant_attributes if relevant_requirement(req, e["quote"])
                         or relevant_requirement(req, e["scope"].split(":", 1)[-1])]
        titles = [e for e in allowed if e.get("side") == opposite and e.get("field") == "title"]
        if opposite == "au":
            matching += titles
        else:
            matching += [safe for e in titles if (safe := safe_rakuten_title(e)) is not None]
        if include_selected_rakuten:
            matching += [e for e in registry if e.get("field") == "selected_sku" and e.get("side") == "rakuten"]
        matching.sort(key=lambda e: (e.get("field") != "title", e.get("id", "")))
        selected, size, seen = [], 0, set()
        for entry in matching:
            if entry["id"] in seen:
                continue
            cost = len(entry["quote"])
            if size + cost > MAX_EVIDENCE_CHARS:
                evidence_overflow.append({"condition_side": side, "axis_name_raw": req["axis_name_raw"],
                                          "atom_kind": req["atom_kind"], "registry_id": entry.get("id"),
                                          "reason": "whole_block_overflow_review_required"})
                continue
            seen.add(entry["id"])
            block = evidence_block(entry, input_sha)
            if block is None:
                source_rejections.append({"condition_side": side, "axis_name_raw": req["axis_name_raw"],
                                          "atom_kind": req["atom_kind"], "registry_id": entry.get("id"),
                                          "reason": "raw_quote_span_unresolved"})
                continue
            selected.append(block)
            size += cost
        return selected

    au_product_id = focus["row_key"].split(":")[1]
    context = {"au_url": f"https://wowma.jp/item/{au_product_id}",
               "rakuten_selected_axes": [{"axis_name_raw": n, "selected_value_raw": v} for n, v in pairs],
               "au_selected_axes": axis_pairs(focus["sku"])}
    for ix, req in enumerate(requirements):
        packets.append({"packet_id": f"{case['case_id']}:{focus['row_key']}:r:{ix:02d}",
                        "case_id": case["case_id"], "au_row_key": focus["row_key"],
                        "condition": {"side": "rakuten", "host_condition_index": ix, **{k: req[k] for k in
                                       ("axis_name_raw", "selected_value_raw", "atom_kind", "atom_value")}},
                        "context": context, "evidence": evidence_for(req, "rakuten")})
    # Reverse audit every selected AU-row atom that the Rakuten selection does
    # not establish. The model sees current Rakuten-side evidence for these.
    _, au_only_atoms = row_condition_status(focus, requirements)
    for ix, atom in enumerate(au_only_atoms):
        req = {"axis_name_raw": atom["axis_name_raw"], "selected_value_raw": atom["value_raw"],
               "atom_kind": atom["atom_kind"], "atom_value": atom["atom_value"],
               "atom_quote": atom["value_raw"], "atom_offset": [0, len(atom["value_raw"])]}
        packets.append({"packet_id": f"{case['case_id']}:{focus['row_key']}:a:{ix:02d}",
                        "case_id": case["case_id"], "au_row_key": focus["row_key"],
                        "condition": {"side": "au", "host_condition_index": f"au_only:{ix}", **{k: req[k] for k in
                                       ("axis_name_raw", "selected_value_raw", "atom_kind", "atom_value")}},
                        "context": context, "evidence": evidence_for(req, "au", include_selected_rakuten=True)})
    host["source_evidence_rejections"] = source_rejections
    host["evidence_overflow"] = evidence_overflow
    host["required_packet_ids"] = [p["packet_id"] for p in packets]
    focus_axes = au_row_source_axes(focus, registry)
    required_au = []
    for i, atom in enumerate(au_only_atoms):
        source_axis = next((a for a in focus_axes if a["axis_name_raw"] == atom["axis_name_raw"]
                            and a["value_raw"] == atom["value_raw"]), None)
        if source_axis is None:
            host["blocking_issues"].append(f"missing_au_row_source_span:au_only:{i}")
            continue
        s, e = atom["atom_offset"]
        span = gate_sources.sub_span(source_axis["value_span"], s, e - s)
        required_au.append({"condition_index": f"au_only:{i}", "axis_name_raw": atom["axis_name_raw"],
                            "selected_value_raw": atom["value_raw"], "atom_kind": atom["atom_kind"],
                            "atom_value": atom["atom_value"],
                            "source_span": {"source_side": "au", "raw_file": span["raw_file"],
                                            "locator": span["locator"], "raw_sha256": span["sha256"],
                                            "verified": True, "start": span["start"], "end": span["end"],
                                            "quote": span["quote"]}})
    host["focus_row_source_axes"] = focus_axes
    host["required_au_conditions"] = required_au
    host["blocking_issues"] = [] if host["focus_row_key"] else ["nonunique_or_unmatched_focus_row"]
    if len(required_au) != len(au_only_atoms):
        host["blocking_issues"].append("incomplete_au_only_condition_provenance")
    if evidence_overflow:
        host["blocking_issues"].append("evidence_block_overflow_requires_host_review")
    if source_rejections:
        host["blocking_issues"].append("unresolved_raw_source_span_requires_host_review")
    if any(req["atom_kind"] in ("unresolved_residue", "untyped_value") for req in requirements):
        host["blocking_issues"].append("incomplete_selected_condition_decomposition")
    if any(atom["atom_kind"] in ("unresolved_residue", "untyped_value") for atom in au_only_atoms):
        host["blocking_issues"].append("incomplete_au_only_condition_decomposition")
    conditional = []
    for packet in packets:
        if not packet["evidence"]:
            host["blocking_issues"].append(f"empty_evidence:{packet['packet_id']}")
        for block in packet["evidence"]:
            if re.search(r"場合|とき|時は|選択の場合", block["quote"]):
                conditional.append({"packet_id": packet["packet_id"], "source_ref": block["source_ref"],
                                    "quote": block["quote"], "reason": "conditional_applicability_requires_host_resolution"})
    host["conditional_evidence_gaps"] = conditional
    if conditional:
        host["blocking_issues"].append("conditional_clause_applicability_requires_host_review")
    return packets, host


def prepare(out: Path = OUT) -> dict:
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite {out}")
    input_rows = read_jsonl(FROZEN / "inputs.jsonl")
    source_cases = read_jsonl(FROZEN / "source-cases.jsonl")
    if len(input_rows) != 14 or len(source_cases) != 14:
        raise ValueError("Expected the frozen 14-case set")
    if [x["case_id"] for x in input_rows] != [x["case_id"] for x in source_cases]:
        raise ValueError("Frozen case IDs/order differ")
    if len({x["case_id"] for x in input_rows}) != 14:
        raise ValueError("Duplicate case ID")
    input_sha = sha256((FROZEN / "inputs.jsonl").read_bytes())
    cases_sha = sha256((FROZEN / "source-cases.jsonl").read_bytes())
    packets, host_cases = [], []
    total_rows = 0
    for case in source_cases:
        # The model source bundle uses registry quotes only; all AU rows remain host-side.
        generated, host = packets_for_case(case, input_sha)
        packets.extend(generated)
        host_cases.append(host)
        total_rows += host["au_row_count"]
    out.mkdir(parents=True)
    model_dir = out / "model"
    host_dir = out / "host"
    model_dir.mkdir(); host_dir.mkdir()
    packet_text = "".join(canonical(p) + "\n" for p in packets)
    host_text = "".join(canonical(c) + "\n" for c in host_cases)
    (model_dir / "packets.jsonl").write_text(packet_text, encoding="utf-8")
    (host_dir / "fullpools.jsonl").write_text(host_text, encoding="utf-8")
    source_hashes = {n: sha256((FROZEN / n).read_bytes()) for n in INPUT_FILES}
    code_hash = sha256(Path(__file__).read_bytes())
    dependency_names = ("sku_gate_atoms.py", "sku_gate_sources.py", "sku_gates.py", "run_sku_gates.py")
    dependency_hashes = {n: sha256((CLAUDE_CODE / n).read_bytes()) for n in dependency_names}
    manifest = {"schema_version": "gpu-condition-task-manifest-v1", "task_version": "gpu-condition-task-v1",
                "case_count": len(source_cases), "case_ids_in_order": [x["case_id"] for x in source_cases],
                "all_au_rows_host_only": True, "au_row_count_total": total_rows,
                "packet_count": len(packets), "condition_count": len(packets),
                "host_review_case_count": sum(c["focus_row_key"] is None for c in host_cases),
                "model_packets_sha256": sha256(packet_text.encode()), "host_fullpools_sha256": sha256(host_text.encode()),
                "frozen_input_sha256": source_hashes, "preparer_sha256": code_hash,
                "claude_gate_source_path": str(CLAUDE_CODE), "claude_gate_source_sha256": dependency_hashes,
                "labels_read": False, "selection_basis": "selected SKU atoms and source text only",
                "evidence_char_limit": MAX_EVIDENCE_CHARS, "evidence_truncation": "whole-block admission only; no quote truncation"}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    print(json.dumps(prepare(args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
