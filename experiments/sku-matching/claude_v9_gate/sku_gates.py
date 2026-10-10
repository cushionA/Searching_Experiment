"""Fixed-pair SKU identity gates: A (same-row structure) and B (quoted requirements).

Input: one fixed AU product + its complete AU SKU array, and one selected
Rakuten SKU. No product search or rerouting happens here. Prices, stock,
shipping, coupons, labels, and model outputs are never read.

Both methods evaluate every requirement on the same candidate AU row. Missing
AU axes stay unknown; explicit contrary values are contradictions. Derived page
conflicts (for example a handle that one page excludes for the selected
combination) block acceptance but never justify unmatched on their own.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

from . import sku_gate_atoms as atoms_mod
from .sku_gate_atoms import (atomize, color_base_vocab, compact, code_crosswalk, describe_lines,
                            fact_family, atom_value_key, title_facts, FABRIC, NAMED_SIZES, SIZE_CODES)
from . import sku_gate_sources as src

TASK_VERSION = "sku-gate-task-v9"
CURTAIN_COMPONENTS = ("drape", "lace")
# Separately packed items a contents list can enumerate. Built-in features
# (armrest, top board, bookshelf, handle) are never inferred from list absence.
PACKAGE_COMPONENTS = ("lace", "drape", "tassel", "hook", "door", "panel", "blanket", "electric_blanket",
                      "storage_bag", "futon", "pad", "mattress", "duvet_cover")
SOURCE_CONFIGS = {
    "sku_only": {"rakuten_title": False, "au_title": False, "descriptions": False, "closed_list": False, "derived": False},
    "sku_rakuten_title": {"rakuten_title": True, "au_title": False, "descriptions": False, "closed_list": False, "derived": False},
    "sku_au_title": {"rakuten_title": False, "au_title": True, "descriptions": False, "closed_list": False, "derived": False},
    "sku_both_titles": {"rakuten_title": True, "au_title": True, "descriptions": False, "closed_list": False, "derived": False},
    "sku_descriptions_no_titles": {"rakuten_title": False, "au_title": False, "descriptions": True, "closed_list": True, "derived": True},
    "full": {"rakuten_title": True, "au_title": True, "descriptions": True, "closed_list": True, "derived": True},
    "full_no_closed_list": {"rakuten_title": True, "au_title": True, "descriptions": True, "closed_list": False, "derived": True},
    "full_no_derived_conflicts": {"rakuten_title": True, "au_title": True, "descriptions": True, "closed_list": True, "derived": False},
    # Every source as in "full", but a page-specification difference against the Rakuten page is a
    # notice on the adopted row, never a reason to hold or exclude it.
    "full_spec_notice": {"rakuten_title": True, "au_title": True, "descriptions": True, "closed_list": True,
                         "derived": True, "derived_decides": False},
    # Rakuten contributes only the selected SKU values; everything else comes from the fixed AU page.
    "au_side_only": {"rakuten_title": False, "au_title": True, "descriptions": True, "closed_list": True, "derived": True,
                     "rakuten_description": False, "rakuten_attributes": False},
}
PRIMARY_CONFIG = "full_spec_notice"
NON_IDENTITY_FIELDS = ("price", "stock", "inventory", "shipping", "coupon", "promotion", "availability")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Input construction
# ---------------------------------------------------------------------------

def _scope_tag(block: dict) -> str:
    if block.get("scope") == "series_or_sibling_context":
        return "series_or_sibling_context"
    field = block.get("source_field", "")
    if field.endswith("itemComment") and not field.endswith("extraItemComment"):
        return "search_keywords"
    if field.endswith("detailComment"):
        return "detail_comment"
    return block.get("scope") or "product_page"


def _au_description_lines(store, dossier) -> list[dict]:
    desc = dossier["au_product"]["description"]
    rel = desc["source"]["raw_file"]
    store.raw(rel, desc["source"]["sha256"])
    positions: dict[str, int] = {}
    lines = []
    for block in desc["blocks"]:
        field = block["source_field"]
        span = src.json_leaf_span(store, rel, field, block["text"], positions.get(field, 0))
        if span is None:
            span_any = src.json_leaf_span(store, rel, field, block["text"], 0)
            span = span_any
        if span is not None:
            positions[field] = span["end"]
        lines.append({"text": block["text"], "span": span, "scope_tag": _scope_tag(block),
                      "source_field": field, "source_line": block.get("source_line")})
    return lines


def _rakuten_description_lines(store, dossier) -> list[dict]:
    desc = dossier["rakuten_product"]["description"]
    rel, encoding = desc["source"]["raw_file"], desc["source"].get("encoding", "EUC-JP")
    store.raw(rel, desc["source"]["sha256"])
    texts = [line for line in desc["individual_description_excerpt"].splitlines()]
    spans = src.locate_lines_in_text(store, rel, encoding, texts, "item_desc")
    lines = []
    in_keywords = False
    for text, span in zip(texts, spans):
        if compact(text).startswith("▼検索"):
            in_keywords = True
        lines.append({"text": text.strip(), "span": span,
                      "scope_tag": "search_keywords" if in_keywords else "rakuten_item_desc"})
    return lines


def _purchase_option_lines(store, arrays_rel: str, arrays_index: dict, product_id: str) -> list[dict]:
    line_no = arrays_index.get(product_id)
    if line_no is None:
        return []
    record = store.jsonl_line(arrays_rel, line_no)
    out = []
    for i, option in enumerate(record.get("au_product_options_raw", {}).get("free_options", [])):
        path = f"$.au_product_options_raw.free_options[{i}].title"
        span = src.jsonl_leaf_span(store, arrays_rel, line_no, path)
        if span:
            out.append({"text": span["quote"], "span": span})
    return out


def _purchase_option_facts(lines: list[dict]) -> list[dict]:
    """Explicit per-width piece counts such as ※幅100cmは2枚、幅150cmは1枚になります."""
    facts = []
    for index, line in enumerate(lines):
        text = atoms_mod.Text(line["text"])
        for m in re.finditer(r"幅\s*(\d+)\s*cmは\s*(\d+)\s*枚(?:セット|組)?", text.norm):
            cond_s, cond_e = text.raw_span(m.start(), m.start(2) - 1)
            atom_s, atom_e = text.raw_span(m.start(2), m.end())
            facts.append({"kind": "purchase_option_condition", "scope": "conditional", "section": "purchase_option",
                          "line_index": index, "line": line["text"], "span": line["span"],
                          "condition": {"kind": "sku_condition", "text": line["text"][cond_s:cond_e],
                                        "atoms": [{"type": "dimension", "role": "labeled", "labels": ["width"],
                                                   "value": [float(m.group(1))], "quote": line["text"][cond_s:cond_e],
                                                   "offset": [cond_s, cond_e]}]},
                          "atom": {"type": "piece_total", "value": int(m.group(2)),
                                   "quote": line["text"][atom_s:atom_e], "offset": [atom_s, atom_e]}})
    return facts


def build_product_context(store, dossier: dict, rakuten_case: dict, arrays_rel: str, arrays_index: dict) -> dict:
    """Everything about one fixed pair that does not depend on the selected SKU."""
    au = dossier["au_product"]
    au_rel = au["source"]["raw_file"]
    store.raw(au_rel, au["source"]["sha256"])
    sku_info = store.json(au_rel)["itemInfo"]["skuInfo"]
    axis_paths = {"row": "$.itemInfo.skuInfo.optionName.row", "column": "$.itemInfo.skuInfo.optionName.column"}
    rows = []
    for row in dossier["au_rows"]:
        grain = row["source_grain"]
        r, c = grain["row_index"], grain["column_index"]
        axes = []
        for (name, value), (kind, value_path) in zip(
                [(a["axis_name_raw"], a["value_raw"]) for a in row["axes_raw"]],
                [("row", f"$.itemInfo.skuInfo.rowNames[{r}]"), ("column", f"$.itemInfo.skuInfo.columnNames[{c}]")]):
            if not value:
                continue
            raw_value = src.resolve_json_path(store.json(au_rel), value_path)
            raw_name = sku_info["optionName"][kind]
            if raw_value != value or raw_name != name:
                raise ValueError(f"AU row {row['row_key']} differs from raw skuInfo")
            axes.append({"axis_name": name, "value": value,
                         "axis_name_span": src.json_leaf_span(store, au_rel, axis_paths[kind]),
                         "value_span": src.json_leaf_span(store, au_rel, value_path)})
        if str(row["source_record_ref"]["sku_id"]) != str(sku_info["skuId"]) or not row["row_key"].startswith(
                f"au:{au['product_id']}:{sku_info['skuId']}:{r}:{c}"):
            raise ValueError(f"Row key does not match raw skuInfo: {row['row_key']}")
        rows.append({"row_key": row["row_key"], "row_index": r, "column_index": c,
                     "sku_id": row["source_record_ref"]["sku_id"], "axes": axes,
                     "array_source": {"file": grain["file"], "line": grain["line"]}})
    title_text = src.resolve_json_path(store.json(au_rel), "$.itemInfo.itemTitle")
    au_title_span = src.json_leaf_span(store, au_rel, "$.itemInfo.itemTitle")
    rak = dossier["rakuten_product"]
    rak_src = rak["description"]["source"]
    rak_rel, encoding = rak_src["raw_file"], rak_src.get("encoding", "EUC-JP")
    store.raw(rak_rel, rak_src["sha256"])
    selectors = src.rakuten_variant_selectors(store, rak_rel, encoding)
    rak_title_text = rak["title_evidence_raw"]["text"]
    rak_title_span = src.html_title_span(store, rak_rel, encoding, rak_title_text)
    vocab, families, variant_tokens = derive_vocabulary(
        [(a["axis_name"], a["value"]) for row in rows for a in row["axes"]], selectors)
    au_lines = _au_description_lines(store, dossier)
    rak_lines = _rakuten_description_lines(store, dossier)
    po_lines = _purchase_option_lines(store, arrays_rel, arrays_index, au["product_id"])
    crosswalks = []
    for side, lines in (("au_description", au_lines), ("rakuten_description", rak_lines)):
        for line in lines:
            if line.get("scope_tag") in ("search_keywords",):
                continue
            for cw in code_crosswalk(line["text"]):
                span = src.sub_span(line["span"], cw["offset"][0], cw["offset"][1] - cw["offset"][0]) if line["span"] else None
                crosswalks.append({**cw, "side": side, "span": span})
    return {
        "dossier_id": dossier["dossier_id"], "pair_ref": dossier["pair_ref"],
        "au_product": {"product_id": au["product_id"], "raw_file": au_rel, "sha256": au["source"]["sha256"],
                       "title": title_text, "title_span": au_title_span,
                       "axis_names": {"row": sku_info["optionName"]["row"], "column": sku_info["optionName"]["column"]}},
        "au_rows": rows,
        "rakuten_product": {"url": rak["source"]["url"], "raw_file": rak_rel, "sha256": rak_src["sha256"],
                            "encoding": encoding, "title": rak_title_text, "title_span": rak_title_span,
                            "selector_families": families},
        "color_vocab": sorted(vocab), "variant_tokens": list(variant_tokens),
        "au_description_lines": au_lines, "rakuten_description_lines": rak_lines,
        "au_purchase_option_lines": po_lines, "size_code_crosswalks": crosswalks,
        "excluded_non_identity_fields": list(NON_IDENTITY_FIELDS),
    }


def derive_vocabulary(au_axis_values: list[tuple[str, str]], selectors: list[dict]):
    """Color words from explicit color axes and variant tokens per Rakuten axis family."""
    color_values = [v for name, v in au_axis_values if any(w in name for w in ("カラー", "色"))]
    color_values += [v for s in selectors if any(w in (s["label"] or s["key"]) for w in ("カラー", "色")) for v in s["values"]]
    vocab = color_base_vocab(color_values)
    families, variant_tokens = [], set()
    for s in selectors:
        label = s["label"] or s["key"]
        fam_atoms = [atomize(v, label, vocab, tuple(s["values"]))["atoms"] for v in s["values"]]
        tokens = sorted({a["value"] for group in fam_atoms for a in group if a["type"] == "variant"})
        variant_tokens.update(tokens)
        families.append({"key": s["key"], "label": s["label"], "label_span": s.get("label_span"),
                         "values": s["values"], "variant_tokens": tokens})
    return vocab, families, tuple(sorted(variant_tokens))


def build_case_input(store, case: dict, context: dict) -> dict:
    rak = case["rakuten"]
    source = rak["source"]
    store.raw(source["raw_file"], source["sha256"])
    variant_id = source["source_sku_key"].split("#", 1)[1].rsplit(":", 1)[0]
    value_spans = src.rakuten_selected_values(store, source["raw_file"], context["rakuten_product"]["encoding"], variant_id)
    families = context["rakuten_product"]["selector_families"]
    labels = {a["key"]: a.get("label") or a["key"] for a in rak["axes_labels"]}
    axes = []
    for index, (option, span) in enumerate(zip(rak["option_values"], value_spans)):
        family = families[index]
        if family["key"] != option["axis_key"] or span.get("quote") != option["value"]:
            raise ValueError(f"{case['case_id']}: selected axis order differs from page JSON")
        axes.append({"axis_index": index, "axis_key": option["axis_key"], "axis_label": labels[option["axis_key"]],
                     "axis_label_span": family["label_span"], "value": option["value"], "value_span": span,
                     "family_values": family["values"]})
    return {"schema_version": "sku-gate-case-input-v1", "task_version": TASK_VERSION, "case_id": case["case_id"],
            "dossier_id": case["dossier_id"], "group_id": case["group_id"], "split": case["split"],
            "au_product_id": context["au_product"]["product_id"],
            "rakuten_selected": {"url": source["url"], "raw_file": source["raw_file"], "sha256": source["sha256"],
                                 "variant_id": variant_id, "source_row_key": source["source_row_key"],
                                 "source_sku_key": source["source_sku_key"], "sku_record_key": source["sku_record_key"],
                                 "source_row_index": source["source_row_index"], "axes": axes,
                                 "variant_attributes": src.rakuten_variant_attributes(
                                     store, source["raw_file"], context["rakuten_product"]["encoding"], variant_id)},
            "excluded_non_identity_fields": list(NON_IDENTITY_FIELDS)}


# ---------------------------------------------------------------------------
# Derived context (facts) shared by both methods
# ---------------------------------------------------------------------------

class PairFacts:
    """Typed facts derived once per fixed pair from the frozen product context."""

    def __init__(self, context: dict):
        self.context = context
        self.vocab = frozenset(context["color_vocab"])
        self.tokens = tuple(context["variant_tokens"])
        self.rows = []
        for row in context["au_rows"]:
            row_atoms = []
            for axis in row["axes"]:
                parsed = atomize(axis["value"], axis["axis_name"], self.vocab)
                for atom in parsed["atoms"]:
                    span = src.sub_span(axis["value_span"], atom["offset"][0], atom["offset"][1] - atom["offset"][0]) \
                        if axis.get("value_span") else None
                    row_atoms.append({**atom, "source": "au_row", "axis_name": axis["axis_name"], "span": span,
                                      "row_key": row["row_key"]})
            self.rows.append({"row_key": row["row_key"], "atoms": row_atoms, "axes": row["axes"]})
        self.au_axis_values = {}
        for row in context["au_rows"]:
            for axis in row["axes"]:
                self.au_axis_values.setdefault(axis["axis_name"], set()).add(axis["value"])
        self.au_axis_values = {k: tuple(sorted(v)) for k, v in self.au_axis_values.items()}
        self.row_families = {}
        for row in self.rows:
            for atom in row["atoms"]:
                self.row_families.setdefault(atom["type"], set()).add(atom_value_key(atom))
        self.au_title = self._title(context["au_product"]["title"], context["au_product"]["title_span"], "au_title")
        self.rak_title = self._title(context["rakuten_product"]["title"], context["rakuten_product"]["title_span"], "rakuten_title")
        self.au_desc = describe_lines(context["au_description_lines"], self.vocab, self.tokens)
        self.rak_desc = describe_lines(context["rakuten_description_lines"], self.vocab, self.tokens)
        self.au_po = _purchase_option_facts(context["au_purchase_option_lines"])
        self.crosswalks = context["size_code_crosswalks"]
        self.rak_families = []
        for fam in context["rakuten_product"]["selector_families"]:
            parsed = [atomize(v, fam["label"] or fam["key"], self.vocab, tuple(fam["values"])) for v in fam["values"]]
            self.rak_families.append({"key": fam["key"], "values": fam["values"], "parsed": parsed,
                                      "value_spans": fam.get("value_spans", [])})

    def _title(self, text, span, source):
        facts = title_facts(text, self.vocab, self.tokens)
        for fam in facts.values():
            for atom in fam["atoms"]:
                atom["source"] = source
                atom["span"] = src.sub_span(span, atom["offset"][0], atom["offset"][1] - atom["offset"][0]) if span else None
        return facts


def _fact_span(fact: dict, atom: dict):
    line_span = fact.get("span")
    if not line_span or "offset" not in atom:
        return None
    start, end = atom["offset"]
    if end > len(line_span["quote"]) or line_span["quote"][start:end] != atom["quote"]:
        return None
    return src.sub_span(line_span, start, end - start)


# ---------------------------------------------------------------------------
# Atom comparison
# ---------------------------------------------------------------------------

_POSITIONAL = ("unlabeled", "width", "length", "height", "depth")


def compare_dims(req: dict, cand: dict) -> str:
    a_role, b_role = req.get("role"), cand.get("role")
    if "top_size" in (a_role, b_role):
        if a_role == b_role == "top_size" and len(req["value"]) == len(cand["value"]):
            return "support" if req["value"] == cand["value"] else "conflict"
        return "incomparable"
    if a_role == b_role == "generic":
        if len(req["value"]) == len(cand["value"]):
            if req["value"] == cand["value"]:
                return "support"
            if len(req["value"]) == 1:
                return "incomparable"  # one unlabeled number may measure any dimension (68cm vs 15cm)
            # 120×60 vs 60×120: unlabeled numbers in another order are not a contradiction.
            return "incomparable" if sorted(req["value"]) == sorted(cand["value"]) else "conflict"
        return "incomparable"
    if a_role == "generic" or b_role == "generic":
        generic, labeled = (req, cand) if a_role == "generic" else (cand, req)
        if len(generic["value"]) < 2:
            return "incomparable"
        positions = [i for i, label in enumerate(labeled.get("labels") or []) if label in _POSITIONAL]
        if len(positions) != len(generic["value"]):
            return "incomparable"
        values = [labeled["value"][i] for i in positions]
        return "support" if values == generic["value"] else "conflict"
    a = {l: v for l, v in zip(req.get("labels") or [], req["value"]) if l != "unlabeled"}
    b = {l: v for l, v in zip(cand.get("labels") or [], cand["value"]) if l != "unlabeled"}
    common = sorted(set(a) & set(b))
    if not common:
        return "incomparable"
    def choices(atom, label, value):
        alternatives = atom.get("alternatives") or {}
        if isinstance(alternatives, dict) and label in alternatives:
            return alternatives[label]
        if isinstance(alternatives, list) and len(atom.get("labels") or []) == 1:
            return alternatives
        return [value]
    relations = []
    for label in common:
        av, bv = a[label], b[label]
        aa, ba = choices(req, label, av), choices(cand, label, bv)
        if av == bv or av in ba or bv in aa:
            relations.append("support")
        else:
            relations.append("conflict")
    return "support" if all(r == "support" for r in relations) else "conflict"


def comparable(req: dict, cand: dict) -> bool:
    if req["type"] != cand["type"]:
        return False
    if req["type"] in ("component_presence", "component_count", "component_material"):
        return req["component"] == cand["component"]
    if req["type"] == "unit_count":
        return req["unit"] == cand["unit"]
    if req["type"] == "measure":
        return (req["kind"], req.get("role")) == (cand["kind"], cand.get("role"))
    if req["type"] == "dimension":
        return compare_dims(req, cand) != "incomparable"
    return True


def compare_atoms(req: dict, cand: dict, contrast: callable) -> str:
    """support / conflict / unknown for comparable atoms."""
    kind = req["type"]
    if kind == "dimension":
        return compare_dims(req, cand)
    if atom_value_key(req) == atom_value_key(cand):
        return "support"
    if kind in ("named_size",):
        both_named = req["value"] in NAMED_SIZES and cand["value"] in NAMED_SIZES
        return "conflict" if both_named else "unknown"
    if kind in ("piece_total", "unit_count", "measure", "tier_count", "component_count", "component_presence",
                "transparency", "fabric", "seat_width"):
        return "conflict"
    if kind in ("color", "variant", "qualifier"):
        return "conflict" if contrast(req, cand) else "unknown"
    if kind == "component_material":
        return "conflict"
    return "unknown"


# ---------------------------------------------------------------------------
# Evidence gathering
# ---------------------------------------------------------------------------

def _ev(source, atom, relation, span=None, scope=None, note=None, condition=None):
    out = {"source": source, "relation": relation, "quote": atom.get("quote"),
           "value": atom.get("value"), "span": span, "scope": scope}
    if note:
        out["note"] = note
    if condition:
        out["condition"] = condition
    return out


class Evaluator:
    """Requirement-vs-row evaluation under one source configuration."""

    def __init__(self, facts: PairFacts, config: dict, require_verified: bool):
        self.f = facts
        self.cfg = config
        self.verified = require_verified
        self._au_cache: dict[str, list] = {}
        self._rak_cache: dict[str, list] = {}
        self._eval_cache: dict[tuple, dict] = {}

    def _align(self, req: dict, row_atoms: list[dict], row: dict) -> dict | None:
        """Resolve a different spelling through the two pages' own option lists (method A only)."""
        if req["type"] not in ("color", "variant", "qualifier") or not req.get("family_values"):
            return None
        target = option_cores(tuple(req["family_values"]) + (req["axis_value"],)).get(req["axis_value"])
        for atom in row_atoms:
            name = atom.get("axis_name")
            row_value = next((a["value"] for a in row["axes"] if a["axis_name"] == name), None)
            if not target or row_value is None:
                continue
            cores = option_cores(self.f.au_axis_values.get(name, (row_value,)))
            if cores.get(row_value) == target:
                return {"status": "support", "note": "aligned_by_option_lists",
                        "evidence": [_ev("au_row", atom, "support", atom.get("span"), "selected_au_row",
                                         note=f"option core {target} = {cores[row_value]}")]}
            other = next((v for v, c in cores.items() if c == target and v != row_value), None)
            if other:
                return {"status": "conflict", "note": "aligned_counterpart_on_other_row",
                        "evidence": [_ev("au_row", atom, "conflict", atom.get("span"), "selected_au_row",
                                         note=f"{req['axis_value']} corresponds to AU option {other}")]}
        return None

    def au_facts_for(self, row) -> list[dict]:
        if row["row_key"] not in self._au_cache:
            self._au_cache[row["row_key"]] = _exceptions_override(self.au_product_facts(row))
        return self._au_cache[row["row_key"]]

    def rak_facts_for(self, selected, attributes=None) -> list[dict]:
        key = canonical_json([[[a["type"], a.get("component"), a.get("role"), a.get("labels"), a["value"]]
                               for a in selected], [[x["title"], x["value"]] for x in attributes or []]])
        if key not in self._rak_cache:
            facts = self.rakuten_product_facts(selected)
            if self.cfg["derived"] and self.cfg.get("rakuten_attributes", True):
                facts = facts + attribute_facts(attributes)
            self._rak_cache[key] = _exceptions_override(facts)
        return self._rak_cache[key]

    def evaluate_cached(self, req: dict, row: dict, facts: list[dict]) -> dict:
        key = (canonical_json([req["type"], req.get("component"), req.get("role"), req.get("labels"), req["value"],
                               req.get("unit"), req.get("kind"), req.get("axis_value"), req.get("family_values")]),
               row["row_key"])
        if key not in self._eval_cache:
            self._eval_cache[key] = self.evaluate(req, row, facts)
        return self._eval_cache[key]

    # Contrast: is the difference between two categorical values explicit?
    def au_contrast(self, req, cand):
        values = self.f.row_families.get(req["type"], set())
        return atom_value_key(req) in values or self._rak_family_has(cand)

    def _rak_family_has(self, atom):
        for fam in self.f.rak_families:
            for parsed in fam["parsed"]:
                if any(a["type"] == atom["type"] and atom_value_key(a) == atom_value_key(atom) for a in parsed["atoms"]):
                    return True
        return False

    def product_contrast(self, req, cand):
        # Two family tokens of the same Rakuten axis are explicit alternatives.
        for fam in self.f.rak_families:
            vals = {atom_value_key(a) for p in fam["parsed"] for a in p["atoms"] if a["type"] == req["type"]}
            if atom_value_key(req) in vals and atom_value_key(cand) in vals:
                return True
        return False

    # --- condition evaluation on the same candidate row -------------------
    def condition_holds(self, cond_atoms: list[dict], row_atoms: list[dict], product_atoms: list[dict]) -> str:
        if not cond_atoms:
            return "holds"
        results = []
        for cond in cond_atoms:
            pool = [a for a in row_atoms if comparable(cond, a)] or \
                   [a for a in row_atoms if self._width_from_pair(cond, a)] or \
                   [a for a in product_atoms if comparable(cond, a)]
            if not pool:
                results.append("unknown")
                continue
            outcomes = set()
            for cand in pool:
                if self._width_from_pair(cond, cand):
                    outcomes.add("support" if cand["value"][0] == cond["value"][0] else "conflict")
                else:
                    outcomes.add(compare_atoms(cond, cand, self.product_contrast))
            results.append("support" if outcomes == {"support"} else "conflict" if "conflict" in outcomes and "support" not in outcomes else "unknown")
        if all(r == "support" for r in results):
            return "holds"
        if any(r == "conflict" for r in results):
            return "fails"
        return "unknown"

    @staticmethod
    def _width_from_pair(cond, cand):
        # Condition 幅Ncm on a generic W×L selection: the first value is the width.
        return (cond["type"] == "dimension" and cond.get("labels") == ["width"] and cand["type"] == "dimension"
                and cand.get("role") == "generic" and len(cand["value"]) == 2)

    # --- product-level facts for the AU side --------------------------------
    def au_product_facts(self, row) -> list[dict]:
        out = []
        if self.cfg["au_title"]:
            for fam, data in self.f.au_title.items():
                for atom in data["atoms"]:
                    out.append({"atom": atom, "source": "au_title", "scope": "fixed_au_title",
                                "single_valued": data["single_valued"], "family": fam, "span": atom.get("span")})
        if self.cfg["descriptions"]:
            out.extend(self._desc_facts(self.f.au_desc, row["atoms"], "au_description", out))
            for fact in self.f.au_po:
                status = self.condition_holds(fact["condition"]["atoms"], row["atoms"], [])
                if status == "holds":
                    out.append({"atom": fact["atom"], "source": "au_purchase_option", "scope": "purchase_option_condition",
                                "single_valued": True, "family": fact_family(fact["atom"]),
                                "span": _fact_span(fact, fact["atom"]), "condition": fact["condition"]["text"]})
        return out

    def rakuten_product_facts(self, selected_atoms) -> list[dict]:
        out = []
        if self.cfg["rakuten_title"]:
            selected_types = {fact_family(a) for a in selected_atoms} | {a["type"] for a in selected_atoms}
            for fam, data in self.f.rak_title.items():
                # A title family that is also a selection axis is series-level.
                if fam in selected_types or fam.split(":")[0] in selected_types:
                    continue
                for atom in data["atoms"]:
                    out.append({"atom": atom, "source": "rakuten_title", "scope": "rakuten_title",
                                "single_valued": data["single_valued"], "family": fam, "span": atom.get("span")})
        if self.cfg["descriptions"] and self.cfg.get("rakuten_description", True):
            out.extend(self._desc_facts(self.f.rak_desc, selected_atoms, "rakuten_description", out))
        return out

    def _desc_facts(self, desc, row_atoms, source, prior) -> list[dict]:
        product_atoms = [p["atom"] for p in prior if p.get("single_valued")]
        out = []
        # Unconditioned size/type/feature lines: single-valued per section family.
        section_values: dict[tuple, set] = {}
        for fact in desc:
            if fact["kind"] in ("size_line", "type_line", "features_line", "tier_options_line") and not fact.get("object_role") \
                    and (fact.get("condition") is None) and not fact.get("label_condition"):
                section_values.setdefault((fact["section"], fact_family(fact["atom"])), set()).add(atom_value_key(fact["atom"]))
        contents_scopes = []
        for fact in desc:
            kind = fact["kind"]
            atom = fact.get("atom")
            cond = fact.get("condition")
            if isinstance(cond, dict) and cond.get("kind") == "label":
                cond = None
            if kind == "page_declaration":
                out.append(self._pf(atom, fact, source, "page_declaration", True))
                continue
            if kind == "contents_item":
                contents_scopes.append(fact)
                continue
            status = "holds"
            if isinstance(cond, dict):
                if cond.get("kind") != "sku_condition":
                    continue
                status = self.condition_holds(cond["atoms"], row_atoms, product_atoms)
            inline = fact.get("inline_condition")
            if status == "holds" and inline:
                status = self.condition_holds(inline["atoms"], row_atoms, product_atoms) if inline["kind"] == "sku_condition" else "unknown"
            if status != "holds":
                continue
            if kind in ("conditional_exception", "explicit_absence"):
                out.append(self._pf(atom, fact, source, "explicit_exception", True, derived=True))
            elif kind in ("component_material", "spec_presence"):
                out.append(self._pf(atom, fact, source, "material_spec", True, derived=True))
            elif kind == "size_line":
                if fact.get("object_role") and atom.get("role") != "top_size":
                    continue
                lc = fact.get("label_condition")
                if lc:
                    held = self._label_condition(lc, row_atoms, product_atoms, fact)
                    if held is None:
                        continue
                    out.extend(held)
                    out.append(self._pf(atom, fact, source, "labeled_size_line", True))
                    continue
                single = len(section_values.get((fact["section"], fact_family(atom)), set())) == 1 or cond is not None
                out.append(self._pf(atom, fact, source, "size_section", single))
            elif kind in ("type_line", "features_line", "tier_options_line"):
                single = len(section_values.get((fact["section"], fact_family(atom)), set())) == 1 or cond is not None
                out.append(self._pf(atom, fact, source, fact["section"], single))
        out.extend(self._contents(contents_scopes, row_atoms, product_atoms, source))
        return out

    def _label_condition(self, lc, row_atoms, product_atoms, fact):
        """Size lines labeled with a size code/name apply when that size is this product's size."""
        name = lc.get("name")
        if lc.get("atoms"):
            status = self.condition_holds(lc["atoms"], row_atoms, product_atoms)
            return [] if status == "holds" else None
        if not name:
            # Unknown code (e.g. XS): the line applies only on a single-size page.
            return []
        named = [a for a in product_atoms + row_atoms if a["type"] == "named_size"]
        if named and all(a["value"] == name for a in named):
            return []
        if named:
            return None
        return []

    def _pf(self, atom, fact, source, scope, single, derived=False):
        return {"atom": atom, "source": source, "scope": scope, "single_valued": single,
                "family": fact_family(atom), "span": _fact_span(fact, atom), "line": fact.get("line"),
                "derived": derived}

    def _contents(self, items, row_atoms, product_atoms, source) -> list[dict]:
        if not items:
            return []
        in_scope, unknown_scope = [], False
        for item in items:
            cond = item.get("condition")
            status = "holds"
            if isinstance(cond, dict) and cond.get("kind") not in ("label",):
                status = self.condition_holds(cond["atoms"], row_atoms, product_atoms) if cond["kind"] == "sku_condition" else "unknown"
            inline = item.get("inline_condition")
            if status == "holds" and inline:
                status = self.condition_holds(inline["atoms"], row_atoms, product_atoms) if inline["kind"] == "sku_condition" else "unknown"
            if status == "holds":
                in_scope.append(item)
            elif status == "unknown":
                unknown_scope = True
        out = []
        counts: dict[str, list] = {}
        for item in in_scope:
            data = item["item"]
            for comp in data["components"]:
                out.append({"atom": {"type": "component_presence", "component": comp["component"], "value": True,
                                     "quote": comp["quote"], "offset": comp["offset"]},
                            "source": source, "scope": "contents", "single_valued": True,
                            "family": f"component_presence:{comp['component']}", "span": _fact_span(item, comp),
                            "line": item["line"]})
                if data["quantity"] is not None and len(data["components"]) == 1:
                    counts.setdefault(comp["component"], []).append((data["quantity"], item))
            for named in data["named_sizes"]:
                out.append({"atom": named, "source": source, "scope": "contents_item", "single_valued": True,
                            "family": "named_size", "span": _fact_span(item, named), "line": item["line"]})
            for token in self.f.tokens:
                at = item["line"].find(token)
                if at >= 0:
                    atom = {"type": "variant", "value": token, "quote": token, "offset": [at, at + len(token)]}
                    out.append({"atom": atom, "source": source, "scope": "contents_item", "single_valued": True,
                                "family": "variant", "span": _fact_span(item, atom), "line": item["line"]})
        for comp, entries in counts.items():
            if len(entries) == 1:
                quantity, item = entries[0]
                atom = {"type": "component_count", "component": comp, "value": quantity, "quote": item["line"],
                        "offset": [0, len(item["line"])]}
                out.append({"atom": atom, "source": source, "scope": "contents", "single_valued": True,
                            "family": f"component_count:{comp}", "span": item.get("span"), "line": item["line"]})
        curtain = [(q, i) for comp in CURTAIN_COMPONENTS for q, i in counts.get(comp, [])]
        if curtain and not unknown_scope:
            total = sum(q for q, _ in curtain)
            atom = {"type": "piece_total", "value": total, "quote": " + ".join(i["line"] for _, i in curtain),
                    "derivation": "sum_of_curtain_panels_in_closed_contents"}
            out.append({"atom": atom, "source": source, "scope": "closed_contents_sum", "single_valued": True,
                        "family": "piece_total", "span": None, "lines": [i["line"] for _, i in curtain],
                        "line_spans": [i.get("span") for _, i in curtain], "closed_list": True})
        if self.cfg["closed_list"] and in_scope and not unknown_scope:
            present = {c["component"] for item in in_scope for c in item["item"]["components"]}
            out.append({"closed_contents": True, "present": sorted(present), "source": source,
                        "scope": "closed_contents_list", "lines": [i["line"] for i in in_scope],
                        "line_spans": [i.get("span") for i in in_scope]})
        return out

    # --- requirement evaluation ---------------------------------------------
    def closed_list_allowed(self) -> bool:
        # Method B cites literal text only; absence from a list is not a quote.
        return self.cfg["closed_list"] and not self.verified

    def usable(self, fact) -> bool:
        if not self.verified:
            return True
        if fact.get("closed_contents") or fact.get("atom", {}).get("derivation") == "sum_of_curtain_panels_in_closed_contents":
            return all(fact.get("line_spans") or [None])
        return fact.get("span") is not None

    def evaluate(self, req: dict, row: dict, product_facts: list[dict]) -> dict:
        """Status of one requirement atom on one candidate row."""
        row_matches = [a for a in row["atoms"] if comparable(req, a)]
        if row_matches:
            outcomes = [(compare_atoms(req, a, self.au_contrast), a) for a in row_matches]
            evidence = [_ev("au_row", a, rel, a.get("span"), "selected_au_row") for rel, a in outcomes]
            kinds = {rel for rel, _ in outcomes}
            if kinds == {"support"}:
                return {"status": "support", "evidence": evidence}
            if "conflict" in kinds and "support" not in kinds:
                return {"status": "conflict", "evidence": evidence}
            if "support" in kinds and "conflict" in kinds:
                return {"status": "ambiguous", "evidence": evidence, "note": "row_internal_disagreement"}
            if not self.verified:
                aligned = self._align(req, [a for rel, a in outcomes if rel == "unknown"], row)
                if aligned:
                    return aligned
            # Same-row qualifier missing while a sibling row carries it: explicit contrast.
            return {"status": "unknown", "evidence": evidence, "note": "unresolved_spelling"}
        if req["type"] in ("fabric", "seat_width", "qualifier"):
            sibling = self._row_contrast_missing_qualifier(req, row)
            if sibling:
                return {"status": "conflict", "evidence": [sibling], "note": "sibling_row_carries_qualifier"}
        supports, conflicts, notes = [], [], []
        for fact in product_facts:
            if fact.get("closed_contents"):
                continue
            atom = fact["atom"]
            if not comparable(req, atom) or not self.usable(fact):
                continue
            if fact.get("derived"):
                continue
            if not fact["single_valued"]:
                notes.append({"source": fact["source"], "note": "series_level_multiple_values", "quote": atom.get("quote")})
                continue
            rel = compare_atoms(req, atom, self.product_contrast)
            evidence = _ev(fact["source"], atom, rel, fact.get("span"), fact["scope"],
                           condition=fact.get("condition"))
            if fact.get("line_spans"):
                evidence["spans"] = fact["line_spans"]
            if atom.get("derivation") == "size_code_lexicon":
                cited = self._crosswalk(atom)
                if cited:
                    evidence["crosswalk"] = cited
                elif self.verified:
                    notes.append({"source": fact["source"], "note": "size_code_without_page_crosswalk",
                                  "quote": atom.get("quote")})
                    continue
            if rel == "support":
                supports.append(evidence)
            elif rel == "conflict":
                conflicts.append(evidence)
        if (req["type"] == "component_presence" and not supports and not conflicts
                and req["component"] in PACKAGE_COMPONENTS and self.closed_list_allowed()):
            closed = [f for f in product_facts if f.get("closed_contents") and self.usable(f)]
            for fact in closed:
                if req["component"] in fact["present"]:
                    continue
                relation = "support" if req["value"] is False else "conflict"
                evidence = {"source": fact["source"], "relation": relation, "scope": "closed_contents_list",
                            "quote": " / ".join(fact["lines"]), "value": False, "spans": fact["line_spans"],
                            "note": "component absent from an explicit contents list for this row"}
                (supports if relation == "support" else conflicts).append(evidence)
        if supports and conflicts:
            # The fixed page's title outranks description lines, which also carry per-unit specs,
            # compatible-size lists and sibling products. Disagreement at the same rank stays ambiguous.
            best_support = min(SOURCE_RANK.get(e["source"], 9) for e in supports)
            best_conflict = min(SOURCE_RANK.get(e["source"], 9) for e in conflicts)
            if best_support < best_conflict:
                return {"status": "support", "evidence": supports, "overridden": conflicts, "notes": notes,
                        "note": "title_outranks_description"}
            if best_conflict < best_support:
                return {"status": "conflict", "evidence": conflicts, "overridden": supports, "notes": notes,
                        "note": "title_outranks_description"}
            return {"status": "ambiguous", "evidence": supports + conflicts, "notes": notes,
                    "note": "product_sources_disagree"}
        if supports:
            return {"status": "support", "evidence": supports, "notes": notes}
        if conflicts:
            return {"status": "conflict", "evidence": conflicts, "notes": notes}
        return {"status": "unknown", "evidence": [], "notes": notes}

    def _row_contrast_missing_qualifier(self, req, row):
        base = [a for a in row["atoms"] if a["type"] == "color"]
        if not base:
            return None
        for other in self.f.rows:
            if other["row_key"] == row["row_key"]:
                continue
            same_base = any(a["type"] == "color" and a["value"] == base[0]["value"] for a in other["atoms"])
            has = [a for a in other["atoms"] if a["type"] == req["type"] and a["value"] == req["value"]]
            if same_base and has:
                return _ev("au_row_family", has[0], "conflict", has[0].get("span"), "sibling_au_row",
                           note=f"row {other['row_key']} carries the qualifier; this row does not")
        return None

    def _crosswalk(self, atom):
        for cw in self.f.crosswalks:
            if cw["code"] == atom.get("code") and cw["named_size"] == atom["value"] and (cw["span"] or not self.verified):
                return {"quote": cw["quote"], "span": cw["span"], "side": cw["side"]}
        return None

    # --- AU-only atoms (reverse direction) -----------------------------------
    def evaluate_au_only(self, atom: dict, selected: list[dict], rak_facts: list[dict], row: dict) -> dict:
        if atom["type"] in ("fabric", "seat_width", "qualifier", "color", "variant"):
            base = [a for a in row["atoms"] if a["type"] == "color"]
            sel_base = [a for a in selected if a["type"] == "color"]
            if base and sel_base:
                for fam in self.f.rak_families:
                    for value_index, parsed in enumerate(fam["parsed"]):
                        has_base = any(a["type"] == "color" and a["value"] == sel_base[0]["value"] for a in parsed["atoms"])
                        has_q = any(a["type"] == atom["type"] and a["value"] == atom["value"] for a in parsed["atoms"])
                        if has_base and has_q:
                            span = (fam.get("value_spans") or [])[value_index] if value_index < len(
                                fam.get("value_spans") or []) else None
                            return {"status": "conflict", "note": "rakuten_sibling_value_carries_qualifier",
                                    "evidence": [{"source": "rakuten_selector_family", "quote": parsed["raw"],
                                                  "span": span, "relation": "conflict"}]}
        supports, conflicts = [], []
        for fact in rak_facts:
            if fact.get("closed_contents") or fact.get("derived") or not self.usable(fact):
                continue
            other = fact["atom"]
            if not comparable(atom, other) or not fact["single_valued"]:
                continue
            rel = compare_atoms(atom, other, self.product_contrast)
            ev = _ev(fact["source"], other, rel, fact.get("span"), fact["scope"])
            if fact.get("line_spans"):
                ev["spans"] = fact["line_spans"]
            if rel == "support":
                supports.append(ev)
            elif rel == "conflict":
                conflicts.append(ev)
        if supports and conflicts:
            return {"status": "ambiguous", "evidence": supports + conflicts}
        if conflicts:
            return {"status": "conflict", "evidence": conflicts}
        if supports:
            return {"status": "support", "evidence": supports}
        return {"status": "unverified", "evidence": []}

    # --- derived page conflicts ---------------------------------------------
    def derived_conflicts(self, au_facts, rak_facts) -> list[dict]:
        """Page-specification disagreements for this row and the selected SKU.

        Covered: component presence/material statements, explicit component
        counts in the same contents scope, and body dimensions stated once per
        side (size section, page declaration, Rakuten per-SKU attributes).
        """
        if not self.cfg["derived"]:
            return []
        out = []
        au = [f for f in au_facts if self.usable(f) and not f.get("closed_contents")]
        rk = [f for f in rak_facts if self.usable(f) and not f.get("closed_contents")]

        def statement(f):
            atom = f["atom"]
            return f.get("derived") and atom["type"] != "dimension" or (
                f.get("scope") == "contents" and atom["type"] == "component_count")
        for a in filter(statement, au):
            for r in filter(statement, rk):
                if fact_family(a["atom"]) == fact_family(r["atom"]) and atom_value_key(a["atom"]) != atom_value_key(r["atom"]):
                    out.append(self._conflict(fact_family(a["atom"]), a, r))
        au_dims, rk_dims = self._body_dims(au), self._body_dims(rk)
        # A title that lists several values for a dimension (高さ55/62/70cm) makes that dimension variable.
        variable = {fam.split(":")[2] for title in (self.f.au_title, self.f.rak_title) for fam, data in title.items()
                    if fam.startswith("dimension:labeled:") and not data["single_valued"]}
        for label in sorted((set(au_dims) & set(rk_dims)) - variable):
            (a_val, a_fact), (r_val, r_fact) = au_dims[label], rk_dims[label]
            if a_val is not None and r_val is not None and a_val != r_val:
                out.append(self._conflict(f"body_dimension:{label}", a_fact, r_fact))
        return out

    @staticmethod
    def _conflict(family, a, r):
        return {"family": family,
                "au": {"source": a["source"], "quote": a["atom"].get("quote"), "value": a["atom"]["value"], "span": a.get("span")},
                "rakuten": {"source": r["source"], "quote": r["atom"].get("quote"), "value": r["atom"]["value"], "span": r.get("span")}}

    @staticmethod
    def _body_dims(facts) -> dict:
        """label -> (value, fact); a label stated with two values on one side is ambiguous (None)."""
        out: dict[str, tuple] = {}
        for f in facts:
            atom = f["atom"]
            if (atom["type"] != "dimension" or atom.get("role") != "labeled" or not f.get("single_valued")
                    or f.get("scope") not in DERIVED_DIMENSION_SCOPES):
                continue
            for label, value in zip(atom.get("labels") or [], atom["value"]):
                if label in ("unlabeled",):
                    continue
                if atom.get("alternatives"):
                    out[label] = (None, f)
                    continue
                if label in out and out[label][0] != value:
                    out[label] = (None, f)
                else:
                    out.setdefault(label, (value, f))
        return out


# Rakuten per-SKU attribute titles that state a body dimension of the selected SKU.
ATTRIBUTE_DIMENSIONS = {"本体横幅": "width", "本体縦幅": "length", "本体奥行": "depth", "本体高さ": "height",
                        "マットレスの厚さ": "thickness", "天板高さ": "height"}
ATTRIBUTE_FABRIC_TITLES = ("素材（生地・毛糸）", "素材(生地・毛糸)", "生地")
ATTRIBUTE_NAMED_SIZE_TITLES = ("寝具のサイズ",)
DERIVED_DIMENSION_SCOPES = ("size_section", "labeled_size_line", "page_declaration", "selected_sku_attributes")


def attribute_facts(attributes: list[dict]) -> list[dict]:
    out = []
    for attr in attributes or []:
        label = ATTRIBUTE_DIMENSIONS.get(attr["title"])
        if not label or (attr.get("unit") or "cm") != "cm":
            continue
        try:
            value = float(attr["value"])
        except ValueError:
            continue
        atom = {"type": "dimension", "role": "labeled", "labels": [label], "value": [value],
                "quote": attr["value"], "attribute_title": attr["title"]}
        out.append({"atom": atom, "source": "rakuten_variant_attributes", "scope": "selected_sku_attributes",
                    "single_valued": True, "family": f"dimension:labeled:{label}:1", "span": attr.get("value_span"),
                    "derived": True})
    for attr in attributes or []:
        # Structured per-SKU properties of the selected variant; they corroborate AU-only conditions.
        value, span = attr.get("value") or "", attr.get("value_span")
        atoms = []
        if attr["title"] in ATTRIBUTE_FABRIC_TITLES:
            for piece in re.split(r"[・、,/／\s]+", value):
                if piece in FABRIC:
                    start = value.find(piece)
                    atoms.append({"type": "fabric", "value": FABRIC[piece], "quote": piece, "offset": [start, start + len(piece)]})
        elif attr["title"] in ATTRIBUTE_NAMED_SIZE_TITLES and value in NAMED_SIZES:
            atoms.append({"type": "named_size", "value": value, "quote": value, "offset": [0, len(value)]})
        for atom in atoms:
            atom["attribute_title"] = attr["title"]
            out.append({"atom": atom, "source": "rakuten_variant_attributes", "scope": "selected_sku_attributes",
                        "single_valued": True, "family": fact_family(atom), "derived": False,
                        "span": src.sub_span(span, atom["offset"][0], len(atom["quote"])) if span else None})
    return out


def _exceptions_override(facts: list[dict]) -> list[dict]:
    """A condition-specific exception overrides the generic spec presence on the same side."""
    excepted = {f["atom"]["component"] for f in facts
                if f.get("derived") and f.get("scope") == "explicit_exception"
                and f["atom"]["type"] == "component_presence"}
    return [f for f in facts if not (f.get("derived") and f.get("scope") == "material_spec"
                                     and f["atom"]["type"] == "component_presence"
                                     and f["atom"]["component"] in excepted)]


# ---------------------------------------------------------------------------
# Requirements from the selected Rakuten SKU
# ---------------------------------------------------------------------------

def selected_atoms(case_input: dict, facts: PairFacts) -> list[dict]:
    out = []
    for axis in case_input["rakuten_selected"]["axes"]:
        parsed = atomize(axis["value"], axis["axis_label"], facts.vocab, tuple(axis["family_values"]))
        for i, atom in enumerate(parsed["atoms"]):
            if atom.get("alias"):
                continue  # L(200×250cm): the bracketed dimension is the requirement
            span = src.sub_span(axis["value_span"], atom["offset"][0], atom["offset"][1] - atom["offset"][0]) \
                if axis.get("value_span") else None
            out.append({**atom, "requirement_id": f"r{axis['axis_index']}.{i}", "axis_index": axis["axis_index"],
                        "axis_key": axis["axis_key"], "axis_label": axis["axis_label"],
                        "axis_label_quote": (axis.get("axis_label_span") or {}).get("quote"),
                        "axis_label_span": axis.get("axis_label_span"), "axis_value_quote": axis["value"],
                        "span": span, "decomposition": parsed["decomposition"], "residue": parsed["residue"],
                        "scope": "rakuten_selected_sku_row", "axis_value": axis["value"],
                        "family_values": list(axis["family_values"])})
    return out


def _requirement_card(atom: dict) -> dict:
    card = {k: atom.get(k) for k in ("requirement_id", "type", "value", "quote", "span", "scope", "axis_index",
                                      "axis_key", "axis_label", "axis_label_quote", "axis_label_span",
                                      "axis_value_quote", "decomposition", "residue")}
    for key in ("component", "role", "labels", "derivation", "unit", "kind"):
        if atom.get(key) is not None:
            card[key] = atom[key]
    card["axis_provenance"] = {"axis_key": atom["axis_key"], "axis_label_quote": atom.get("axis_label_quote"),
                               "selected_value_quote": atom["axis_value_quote"]}
    return card


# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------

# Grammatical classifiers a page may attach to every option of an axis (角型/丸型, Mサイズ/Lサイズ).
# Only these may be removed as a shared affix; colour words and numbers never are.
CLASSIFIER_AFFIXES = ("タイプ", "サイズ", "モデル", "仕様", "型", "形", "用")


def _norm_option(value: str) -> str:
    return re.sub(r"[\s()（）\[\]【】×xX・/／\-]", "", unicodedata.normalize("NFKC", value))


def option_cores(values) -> dict:
    """Option value -> its discriminating core within its own option list.

    A prefix or suffix shared by every option of the list is removed only when it is a
    classifier in CLASSIFIER_AFFIXES; the cores are then compared across the two pages.
    """
    norm = {v: _norm_option(v) for v in values}
    distinct = sorted(set(norm.values()))
    if len(distinct) < 2:
        return norm
    prefix, suffix = os.path.commonprefix(distinct), os.path.commonprefix([v[::-1] for v in distinct])[::-1]
    prefix = next((c for c in sorted(CLASSIFIER_AFFIXES, key=len, reverse=True) if prefix.startswith(c)), "")
    suffix = next((c for c in sorted(CLASSIFIER_AFFIXES, key=len, reverse=True) if suffix.endswith(c)), "")
    cores = {}
    for value, n in norm.items():
        core = n[len(prefix):len(n) - len(suffix)] if suffix else n[len(prefix):]
        cores[value] = core or n
    return cores


SOURCE_RANK = {"au_title": 0, "rakuten_title": 0, "au_purchase_option": 1, "au_description": 2,
               "rakuten_description": 2}


_SPECIAL_REVIEW_CODES = {"unquoted_requirement": "unquoted_requirement",
                         "multiple_rows_satisfy_all_requirements": "several_rows_fully_proven",
                         "multiple_rows_satisfy_all_cards": "several_rows_fully_proven",
                         "partial_decomposition": "partial_decomposition",
                         "competing_row_not_excluded": "competing_row_not_excluded"}


def binary_decision(reqs: list[dict], rows: list[dict], row_status: dict, details: dict, decision: str,
                    top: str | None, reason: str | None) -> dict:
    """Adopt (matched) only a row the gate proved uniquely; exclude (unmatched) everything else.

    Nothing unproven is adopted: no absence is assumed, no AU-only condition passes without
    corroboration, and several candidate rows are never resolved by their order. Reason codes
    say why a SKU was adopted or excluded.
    """
    if decision == "matched":
        results = details[top][0]
        codes = ["proven_unique_row"]
        if any(r.get("note") == "aligned_by_option_lists" for r in results):
            codes.append("value_aligned_by_option_lists")
        if any(e.get("scope") == "closed_contents_list" for r in results for e in r.get("evidence", [])):
            codes.append("absence_by_closed_contents_list")
        return {"decision": "matched", "top_row_key": top, "reason_codes": codes}
    if decision == "unmatched":
        return {"decision": "unmatched", "top_row_key": None, "reason_codes": ["explicit_contradiction_on_every_row"]}
    codes = {_SPECIAL_REVIEW_CODES[reason]} if reason in _SPECIAL_REVIEW_CODES else set()
    by_id = {r["requirement_id"]: r for r in reqs}
    for row in rows:
        status = row_status[row["row_key"]]
        if status in ("conflict", "full"):
            continue
        results, _, _ = details[row["row_key"]]
        failing = next((r for r in results if r["status"] != "support"), None)
        if failing is None:
            codes.add("au_only_condition_not_corroborated" if status == "au_only_unproven" else "page_spec_discrepancy")
            continue
        req = by_id[failing["requirement_id"]]
        if failing["status"] == "ambiguous":
            codes.add("mixed_sources")
        elif failing.get("note") == "unresolved_spelling":
            codes.add("different_value_on_row_axis")
        elif req["type"] == "component_presence" and req["value"] is False:
            codes.add("absence_not_proven")
        else:
            codes.add("requirement_not_proven_on_au")
    return {"decision": "unmatched", "top_row_key": None, "reason_codes": sorted(codes) or ["not_proven"]}


def _row_summary(row_key, status, results, au_only, derived, detail):
    first_block = None
    for res in results:
        if res["status"] in ("conflict", "unknown", "ambiguous"):
            ev = (res.get("evidence") or [{}])[0]
            first_block = {"requirement_id": res["requirement_id"], "status": res["status"],
                           "source": ev.get("source"), "quote": ev.get("quote")}
            if res["status"] == "conflict":
                break
    out = {"row_key": row_key, "status": status,
           "supported": sum(r["status"] == "support" for r in results),
           "requirements": len(results), "first_blocker": first_block}
    if detail:
        out["atom_results"] = results
        out["au_only_atoms"] = au_only
        out["derived_conflicts"] = derived
    return out


def sibling_swaps(store, case_input: dict):
    """Label-free metamorphic variants: one selected value replaced by a sibling option.

    The sibling is quoted from the page's own option list. Variant attributes belong to the
    original variant and are dropped. Siblings whose option span cannot be resolved are skipped.
    """
    sel = case_input["rakuten_selected"]
    for i, axis in enumerate(sel["axes"]):
        encoding = axis["value_span"]["locator"].get("encoding", "utf-8")
        for sibling in axis["family_values"]:
            if unicodedata.normalize("NFKC", sibling) == unicodedata.normalize("NFKC", axis["value"]):
                continue
            span = src.rakuten_selector_value_span(store, sel["raw_file"], encoding, axis["axis_index"],
                                                   axis["axis_key"], sibling)
            if span is None:
                continue
            swapped = copy.deepcopy(case_input)
            swapped["rakuten_selected"]["axes"][i].update(value=sibling, value_span=span)
            swapped["rakuten_selected"]["variant_attributes"] = []
            yield {"axis_index": axis["axis_index"], "from": axis["value"], "to": sibling}, swapped


def make_evaluator(method: str, facts: PairFacts, config_name: str) -> Evaluator:
    return Evaluator(facts, SOURCE_CONFIGS[config_name], require_verified=(method == "B"))


def run_method(method: str, case_input: dict, facts: PairFacts, config_name: str,
               evaluator: Evaluator | None = None) -> dict:
    """Method A (structural) or B (quoted) for one case under one source config."""
    evaluator = evaluator or make_evaluator(method, facts, config_name)
    reqs = selected_atoms(case_input, facts)
    rak_facts = evaluator.rak_facts_for(reqs, case_input["rakuten_selected"].get("variant_attributes"))
    rows_out, full_rows, conflict_rows, row_status = [], [], [], {}
    details = {}
    for row in facts.rows:
        au_facts = evaluator.au_facts_for(row)
        results = []
        matched_types = set()
        for req in reqs:
            res = evaluator.evaluate_cached(req, row, au_facts)
            results.append({"requirement_id": req["requirement_id"], **res})
            for a in row["atoms"]:
                if comparable(req, a):
                    matched_types.add(id(a))
        au_only = []
        for a in row["atoms"]:
            if id(a) in matched_types or a.get("alias"):
                continue
            res = evaluator.evaluate_au_only(a, reqs, rak_facts, row)
            au_only.append({"type": a["type"], "value": a["value"], "quote": a["quote"], "span": a.get("span"), **res})
        derived = evaluator.derived_conflicts(au_facts, rak_facts)
        deciding = derived if evaluator.cfg.get("derived_decides", True) else []
        # A condition only the AU row states (10本, パイル) must be corroborated by the Rakuten side.
        unproven = [a for a in au_only if a["status"] != "support"]
        statuses = [r["status"] for r in results]
        has_conflict = "conflict" in statuses or any(a["status"] == "conflict" for a in au_only)
        all_support = all(s == "support" for s in statuses) and statuses
        if method == "A":
            # Structural gate: explicit contradiction wins over mixed product sources.
            has_conflict = has_conflict or "ambiguous" in statuses and any(
                any(e.get("relation") == "conflict" for e in r.get("evidence", [])) for r in results if r["status"] == "ambiguous")
        if has_conflict:
            status = "conflict"
        elif all_support and not unproven and not deciding:
            status = "full"
        elif all_support and unproven:
            status = "au_only_unproven"
        elif all_support and deciding:
            status = "derived_conflict"
        elif "ambiguous" in statuses:
            status = "ambiguous"
        else:
            status = "partial"
        row_status[row["row_key"]] = status
        details[row["row_key"]] = (results, au_only, derived)
        if status == "full":
            full_rows.append(row["row_key"])
        if status == "conflict":
            conflict_rows.append(row["row_key"])
    total = len(facts.rows)
    decision, top, reason = "review", None, None
    partial_cards = [r["requirement_id"] for r in reqs if r["decomposition"] != "complete"]
    if method == "A":
        if len(full_rows) == 1 and len(conflict_rows) == total - 1:
            decision, top, reason = "matched", full_rows[0], "one_full_row_and_all_other_rows_conflict"
        elif len(full_rows) == 1:
            reason = "competing_row_not_excluded"
        elif len(full_rows) > 1:
            reason = "multiple_rows_satisfy_all_requirements"
        elif len(conflict_rows) == total:
            decision, reason = "unmatched", "every_au_row_has_explicit_conflict"
        else:
            reason = _review_reason(row_status)
    else:
        others_excluded = len(conflict_rows) == total - 1
        if len(full_rows) == 1 and others_excluded and not partial_cards:
            decision, top, reason = "matched", full_rows[0], "all_cards_supported_on_one_row_and_other_rows_excluded"
        elif len(full_rows) == 1 and partial_cards:
            reason = "partial_decomposition"
        elif len(full_rows) == 1:
            reason = "competing_row_not_excluded"
        elif len(full_rows) > 1:
            reason = "multiple_rows_satisfy_all_cards"
        elif len(conflict_rows) == total:
            decision, reason = "unmatched", "every_au_row_has_verified_contradiction"
        else:
            reason = _review_reason(row_status)
    if decision != "review" and any(not r.get("span") for r in reqs):
        # A requirement whose selected value has no verified source span cannot decide either way.
        decision, top, reason = "review", None, "unquoted_requirement"
    binary = binary_decision(reqs, facts.rows, row_status, details, decision, top, reason)
    if binary["decision"] == "matched" and not evaluator.cfg.get("derived_decides", True):
        # A difference from the Rakuten page does not decide here; it is reported on the adopted row.
        binary["notices"] = [{"kind": "page_spec_discrepancy", **n} for n in details[top][2]]
    focus = set(full_rows) | ({top} if top else set())
    if not focus:
        ranked = sorted(facts.rows, key=lambda r: (row_status[r["row_key"]] == "conflict",
                                                    -sum(x["status"] == "support" for x in details[r["row_key"]][0])))
        focus = {r["row_key"] for r in ranked[:2]}
    for row in facts.rows:
        results, au_only, derived = details[row["row_key"]]
        # Preserve the complete row-level evaluation for the integrated adapter;
        # later evidence-completion stages may need to revisit any unknown row.
        rows_out.append(_row_summary(row["row_key"], row_status[row["row_key"]], results, au_only, derived, True))
    sel = case_input["rakuten_selected"]
    return {"schema_version": f"sku-gate-{method.lower()}-output-v1", "task_version": TASK_VERSION, "method": method,
            "source_config": config_name, "case_id": case_input["case_id"], "dossier_id": case_input["dossier_id"],
            "au_product_id": case_input["au_product_id"], "decision": decision, "top_row_key": top,
            "reason": reason, "candidate_row_keys": full_rows, "row_status_counts": _counts(row_status),
            "binary": binary,
            "requirements": [_requirement_card(r) for r in reqs],
            "rakuten_provenance": {k: sel[k] for k in ("url", "raw_file", "sha256", "variant_id", "source_row_key",
                                                       "source_sku_key", "sku_record_key", "source_row_index")},
            "au_provenance": {"product_id": facts.context["au_product"]["product_id"],
                              "raw_file": facts.context["au_product"]["raw_file"],
                              "sha256": facts.context["au_product"]["sha256"], "row_count": total},
            "rows": rows_out}


def _counts(row_status):
    out = {}
    for status in row_status.values():
        out[status] = out.get(status, 0) + 1
    return out


def _review_reason(row_status):
    values = set(row_status.values())
    if "derived_conflict" in values:
        return "page_specification_conflict_on_candidate_row"
    if "au_only_unproven" in values:
        return "au_only_condition_not_corroborated"
    if "ambiguous" in values:
        return "ambiguous_or_conflicting_sources"
    return "unresolved_requirement"
