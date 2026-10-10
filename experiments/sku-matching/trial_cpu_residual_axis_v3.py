#!/usr/bin/env python3
"""Label-free residual-axis ablation: recompute source gates and apply cache.

No model inference or labels are read. Prior v10 predictions were referenced
for a label-free diagnostic (337 matched); source gates are recomputed here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_TASK_DIR = ROOT / ".lab-output/sku-cpu-residual-task-20261010-v10"
DEFAULT_RESIDUAL_RUN = ROOT / ".lab-output/sku-cpu-residual-task-20261010-v11/gliner-decide"
DEFAULT_SOURCEGATE = ROOT / ".lab-output/sku-structured-task-trials-20261010-v10/hybrid/predictions.jsonl"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-cpu-residual-task-20261010-v16/ablation"
DEFAULT_LABELS = ROOT / ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json(path: Path, obj: Any) -> None:
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        f.flush()


def _v2_module():
    path = HERE / "trial_cpu_residual_axis_v2.py"
    spec = importlib.util.spec_from_file_location("cpu_residual_v2_source", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load frozen v2 prompt construction helpers")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(module)
    return module


def _hybrid_module():
    path = HERE / "evaluate_fixed_pool_hybrid.py"
    spec = importlib.util.spec_from_file_location("cpu_residual_v3_hybrid", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load fixed-pool hybrid source gate")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(module)
    return module


def _source_candidates(product: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for row in product.get("au_sku_rows", []):
        entity = row.get("current_entity")
        if not isinstance(entity, dict) or not entity.get("row_key"):
            raise RuntimeError("AU source row lacks current_entity/row_key")
        candidates.append(entity)
    if not candidates:
        raise RuntimeError("Fixed AU pool is empty")
    return candidates


def _recompute_sourcegates(task: dict[str, Any], product: dict[str, Any],
                           prior: dict[str, Any], hybrid: Any, evaluation: Any) -> dict[str, Any]:
    source_task = {**task, "au_candidates": _source_candidates(product)}
    ranking = {"case_id": task["case_id"], "top_row_key": prior.get("model_top_row_key"),
               "top10": [{"row_key": prior.get("model_top_row_key")}],
               "score": prior.get("model_score")}
    if ranking["top_row_key"] not in {x["row_key"] for x in source_task["au_candidates"]}:
        ranking["top_row_key"] = source_task["au_candidates"][0]["row_key"]
        ranking["top10"] = [{"row_key": ranking["top_row_key"]}]
    return {
        "hybrid_recomputed": hybrid.fixed_pool_prediction(source_task, ranking),
        "legacy_recomputed": evaluation.rule_prediction(source_task),
        "prior_prediction_diagnostic": prior,
    }


def _inputs(args: argparse.Namespace) -> dict[str, Path]:
    paths = {
        "tasks": args.task_dir / "tasks.jsonl",
        "products": args.task_dir / "products.jsonl",
        "task_manifest": args.task_dir / "manifest.json",
        "sourcegate": args.sourcegate,
        "sourcegate_summary": args.sourcegate.parent / "summary.json",
        "sourcegate_code": HERE / "evaluate_fixed_pool_hybrid.py",
        "sourcegate_gate_code": HERE / "evaluate_structured_skus.py",
        "v11_freeze": args.residual_run / "freeze.json",
        "v11_raw_predictions": args.residual_run / "raw_predictions.jsonl",
        "v11_raw_residual_outputs": args.residual_run / "raw_residual_outputs.jsonl",
        "v11_inference_manifest": args.residual_run / "inference-manifest.json",
        "v14_diagnostic_freeze": ROOT / ".lab-output/sku-cpu-residual-task-20261010-v14/ablation/freeze.json",
        "v14_diagnostic_raw": ROOT / ".lab-output/sku-cpu-residual-task-20261010-v14/ablation/raw_predictions.jsonl",
        "v14_diagnostic_summary": ROOT / ".lab-output/sku-cpu-residual-task-20261010-v14/ablation/summary.json",
        "v15_diagnostic_freeze": ROOT / ".lab-output/sku-cpu-residual-task-20261010-v15/ablation/freeze.json",
        "v15_diagnostic_raw": ROOT / ".lab-output/sku-cpu-residual-task-20261010-v15/ablation/raw_predictions.jsonl",
        "v15_diagnostic_summary": ROOT / ".lab-output/sku-cpu-residual-task-20261010-v15/ablation/summary.json",
        "v2_code": HERE / "trial_cpu_residual_axis_v2.py",
        "v3_code": Path(__file__).resolve(),
    }
    if paths["products"].is_file():
        products = read_jsonl(paths["products"])
        source_paths: dict[str, Path] = {}
        for product in products:
            desc = product.get("description_source_ref", {})
            raw_file = desc.get("raw_file")
            if raw_file:
                source_paths[str((ROOT / raw_file).resolve())] = (ROOT / raw_file).resolve()
            for row in product.get("au_sku_rows", []):
                for axis in row.get("axes", []):
                    raw_file = axis.get("source_ref", {}).get("raw_file")
                    if raw_file:
                        source_paths[str((ROOT / raw_file).resolve())] = (ROOT / raw_file).resolve()
        for index, path in enumerate(sorted(source_paths.values(), key=str)):
            paths[f"raw_source_{index:02d}"] = path
    return paths


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise FileExistsError(f"Refusing existing output: {args.output}")
    paths = _inputs(args)
    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        raise FileNotFoundError("Missing frozen inputs: " + ", ".join(missing))
    manifest = json.loads(paths["task_manifest"].read_text(encoding="utf-8"))
    v11_freeze = json.loads(paths["v11_freeze"].read_text(encoding="utf-8"))
    if manifest.get("tasks_sha256") != sha256(paths["tasks"]) or manifest.get("products_sha256") != sha256(paths["products"]):
        raise RuntimeError("Prepared v10 task artifacts differ from their manifest")
    if v11_freeze.get("tasks_sha256") != sha256(paths["tasks"]) or v11_freeze.get("products_sha256") != sha256(paths["products"]):
        raise RuntimeError("v11 model raw is not for these exact task/product inputs")
    tasks = read_jsonl(paths["tasks"])
    baseline = read_jsonl(paths["sourcegate"])
    if len(tasks) != 1383 or len(baseline) != len(tasks):
        raise RuntimeError("Expected complete real-case pool and fixed-pool sourcegate rows")
    if [x["case_id"] for x in tasks] != [x["case_id"] for x in baseline]:
        raise RuntimeError("Task and sourcegate case IDs/order differ")
    frozen = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": "sku-cpu-residual-task-20261010-v16",
        "method": "Recompute both v10 fixed-pool constrained_pair source gate and v2 canonical rule gate from frozen task plus complete AU source rows. For each matched chosen row, recheck selected axes on that same row; reject proven contradictions, retain row unknowns as review, and require every Rakuten-only residual condition to be entailed either by cached title/conditional-description output, direct selected-row source evidence for an already required equal known attribute, or strict source-verified curtain-count conservation. Unrelated AU candidate unknowns do not invalidate a unique source-proven row. AU-only selected axes remain mandatory and unresolved axes cause review.",
        "labels_used": False,
        "prior_accuracy_used": False,
        "prior_predictions_referenced_for_label_free_diagnostic": True,
        "prior_prediction_diagnostic": "v10 hybrid prediction artifact previously inspected label-blind (337 matched preflight); not used as recomputed gate output",
        "v14_raw_diagnostic_reviewed_label_blind": True,
        "v15_raw_diagnostic_reviewed_label_blind": True,
        "model_inference": False,
        "case_count": len(tasks),
        "same_gpu196_count": sum(bool(x.get("gpu196_cohort")) for x in tasks),
        "input_sha256": {name: sha256(path) for name, path in paths.items()},
        "v11_model": v11_freeze.get("model"),
        "v11_model_freeze_sha256": sha256(paths["v11_freeze"]),
        "label_status": "existing machine/unverified labels are not opened by this ablation; any later evaluation is development-only",
        "synthetic_benchmark_cases": 0,
    }
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "freeze.json", frozen)
    print(json.dumps(frozen, ensure_ascii=False, indent=2))
    return frozen


def _common_row_status(v2: Any, task: dict[str, Any], row: dict[str, Any],
                       product_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rvals, rconflicts = v2._axis_values(task["rakuten"].get("axes", []))
    rowvals, rowconflicts = v2._axis_values(row.get("axes", []))
    all_au_keys = set()
    row_by_key = {x.get("row_key"): x for x in product_rows}
    for candidate in product_rows:
        vals, _ = v2._axis_values(candidate.get("axes", []))
        all_au_keys.update(vals)
    common = sorted(set(rvals) & all_au_keys)
    proven_conflicts = []
    ambiguous = []
    missing = []
    for key in common:
        a, b = rvals.get(key), rowvals.get(key)
        if a is None or b is None:
            missing.append(key)
        elif a != b:
            if v2._proven_axis_conflict(key, a, b):
                proven_conflicts.append(key)
            else:
                ambiguous.append(key)
    au_only = sorted(k for k in rowvals if k not in rvals)
    if proven_conflicts:
        status = "contradicted"
    elif rconflicts or rowconflicts or missing or ambiguous or au_only:
        status = "unknown"
    else:
        status = "entailed"
    return {"status": status, "common_fields": common,
            "proven_conflicts": proven_conflicts, "ambiguous_differences": ambiguous,
            "missing_fields": missing, "axis_conflicts": sorted(set(rconflicts + rowconflicts)),
            "au_only_fields": au_only}


def _source_fact_supports_unknown(v2: Any, task: dict[str, Any], entity: dict[str, Any],
                                 residual: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve model-unknown only when the selected row has direct evidence."""
    key = residual.get("semantic_key")
    required = set(task.get("rakuten", {}).get("required_fields", []))
    attrs = entity.get("attrs", {})
    value = attrs.get(key)
    if not key or key not in required or value is None:
        return None
    if key in set(entity.get("unknown_fields", [])) or any(
            conflict.get("field") == key for conflict in entity.get("source_conflicts", [])):
        return None
    rakuten_axis = next((a for a in task.get("rakuten", {}).get("axes", [])
                         if a.get("semantic_key") == key), None)
    if rakuten_axis is None or rakuten_axis.get("normalized_value") != value:
        return None
    evidence = entity.get("evidence", {}).get(key, [])
    if not evidence:
        return None
    axis_name = str(rakuten_axis.get("axis_name_raw") or "")
    value_raw = str(rakuten_axis.get("value_raw") or "")
    supporting = []
    for item in evidence:
        quote = str(item.get("quote") or "")
        compact = "".join(quote.split())
        if isinstance(value, bool):
            if not axis_name or axis_name not in quote:
                continue
            if value is False:
                if not any(term in compact for term in ("なし", "無し", "含まない", "付属しない", "付いていない")):
                    continue
            elif any(term in compact for term in ("なし", "無し", "含まない", "付属しない", "付いていない")):
                continue
        else:
            needle = "".join(str(value).split())
            if not needle or needle not in compact:
                continue
        supporting.append({"quote": quote, "source_ref": item.get("source_ref")})
    if not supporting:
        return None
    return {"semantic_key": key, "normalized_value": value,
            "rakuten_axis_name_raw": axis_name, "rakuten_value_raw": value_raw,
            "supporting_evidence": supporting,
            "rule": "required field, selected-row known attr equals Rakuten axis, and direct evidence quote asserts value"}


class _ContentsTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append("".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _analyze_curtain_contents(sku_option_value: str, contents_cell: str,
                              expected_width_cm: int, requested: Any,
                              semantic_key: str) -> dict[str, Any] | None:
    """Use strict row-width-scoped quantity conservation; never infer from absence alone."""
    sku = re.fullmatch(r"\s*幅\s*(\d+)\s*[×xX]\s*丈\s*\d+(?:\.\d+)?\s*cm\s*\(\s*(\d+)\s*枚\s*\)\s*", sku_option_value)
    if not sku or int(sku.group(1)) != expected_width_cm:
        return None
    total_panels = int(sku.group(2))
    if total_panels <= 0:
        return None
    lines = [line.strip() for line in contents_cell.splitlines() if line.strip()]
    scopes: list[tuple[int, list[str]]] = []
    for index, line in enumerate(lines):
        header = re.fullmatch(r"【幅\s*(\d+)\s*cm】", line)
        if header:
            next_header = next((j for j in range(index + 1, len(lines))
                                if re.fullmatch(r"【幅\s*\d+\s*cm】", lines[j])), len(lines))
            scopes.append((int(header.group(1)), lines[index + 1:next_header]))
    matching = [scope for scope in scopes if scope[0] == expected_width_cm]
    if len(matching) != 1:
        return None
    scope_lines = matching[0][1]
    drape = lace = 0
    panel_lines = []
    for line in scope_lines:
        if "カーテン" not in line or any(accessory in line for accessory in ("タッセル", "フック")):
            continue
        quantity = re.fullmatch(r".*カーテン\s*(\d+)\s*枚", line)
        if not quantity:
            return None
        count = int(quantity.group(1))
        if "レース" in line:
            lace += count
        else:
            drape += count
        panel_lines.append(line)
    if not panel_lines or drape + lace != total_panels:
        return None
    if semantic_key == "lace":
        if not isinstance(requested, bool):
            return None
        relation = "entailed" if requested == (lace > 0) else "contradicted"
    elif semantic_key == "lace_count":
        if not isinstance(requested, (int, float)):
            return None
        relation = "entailed" if requested == lace else "contradicted"
    else:
        return None
    return {"relation": relation, "semantic_key": semantic_key,
            "requested_value": requested, "width_cm": expected_width_cm,
            "total_panel_count_from_selected_sku": total_panels,
            "drape_panel_count_in_same_width_contents_scope": drape,
            "lace_panel_count_in_same_width_contents_scope": lace,
            "panel_lines": panel_lines,
            "proof_rule": "selected SKU explicit total panels equals exhaustive contents-list panel lines within exact width scope; lace count compared by explicit inventory, not by missing keyword"}


def _curtain_contents_resolves(task: dict[str, Any], product: dict[str, Any], selected: dict[str, Any],
                               residual: dict[str, Any], project_root: Path = ROOT) -> dict[str, Any] | None:
    key = residual.get("semantic_key")
    if key not in {"lace", "lace_count"}:
        return None
    if selected.get("current_entity", {}).get("contents_list_assumption") != \
            "explicit contents list treated as exhaustive; not absence of title keyword":
        return None
    axis = next((x for x in selected.get("axes", []) if x.get("semantic_key") == "size"), None)
    if not axis:
        return None
    raw_value = str(axis.get("value_raw") or "")
    match = re.fullmatch(r"\s*幅\s*(\d+)\s*[×xX]\s*丈\s*\d+(?:\.\d+)?\s*cm\s*\(\s*\d+\s*枚\s*\)\s*", raw_value)
    if not match:
        return None
    width_cm = int(match.group(1))
    axis_ref = axis.get("source_ref", {})
    sku_path = (project_root / str(axis_ref.get("raw_file", ""))).resolve()
    source_grain = axis_ref.get("source_grain", {})
    line_number = source_grain.get("line")
    if not sku_path.is_file() or not isinstance(line_number, int) or line_number < 1:
        return None
    with sku_path.open(encoding="utf-8") as f:
        sku_record = next((json.loads(line) for i, line in enumerate(f, 1) if i == line_number), None)
    if not sku_record:
        return None
    if (str(sku_record.get("item_id")) != str(task.get("product_id"))
            or str(sku_record.get("sku_id")) != str(source_grain.get("sku_id"))
            or sku_record.get("column_option_name") != "サイズ"
            or sku_record.get("column_option_value") != raw_value
            or sku_record.get("row_index") != source_grain.get("row_index")
            or sku_record.get("column_index") != source_grain.get("column_index")):
        return None
    desc_ref = product.get("description_source_ref", {})
    page_path = (project_root / str(desc_ref.get("raw_file", ""))).resolve()
    if not page_path.is_file() or sha256(page_path) != desc_ref.get("sha256"):
        return None
    page = json.loads(page_path.read_text(encoding="utf-8"))
    html = page.get("itemInfo", {}).get("extraItemComment")
    if not isinstance(html, str):
        return None
    parser = _ContentsTableParser()
    parser.feed(html)
    content_rows = [cells for cells in parser.rows
                    if cells and "".join(cells[0].split()) == "内容" and len(cells) >= 2]
    if len(content_rows) != 1:
        return None
    requested_axis = next((x for x in task.get("rakuten", {}).get("axes", [])
                           if x.get("semantic_key") == key), None)
    if requested_axis is None:
        return None
    proof = _analyze_curtain_contents(raw_value, content_rows[0][1], width_cm,
                                      requested_axis.get("normalized_value"), key)
    if proof is None:
        return None
    proof["source_refs"] = {
        "selected_au_sku_option": {"file": str(sku_path), "line": line_number,
                                    "product_id": sku_record.get("item_id"), "sku_id": sku_record.get("sku_id"),
                                    "option_name": sku_record.get("column_option_name"),
                                    "option_value": sku_record.get("column_option_value")},
        "fixed_product_contents": {"file": str(page_path), "sha256": desc_ref.get("sha256"),
                                    "json_path": "$.itemInfo.extraItemComment", "table_heading": "内容",
                                    "width_scope": f"【幅{width_cm}cm】", "panel_quote_lines": proof["panel_lines"]},
    }
    return proof


def _case_decision(v2: Any, task: dict[str, Any], product: dict[str, Any],
                   sourcegate: dict[str, Any], residual_ids: list[str],
                   residual_raw: dict[str, Any]) -> dict[str, Any]:
    base = sourcegate.get("decision")
    row_key = sourcegate.get("top_row_key")
    candidates = {x["row_key"]: x for x in product.get("au_sku_rows", [])}
    selected = candidates.get(row_key)
    source_entity = selected.get("current_entity") if selected else None
    if base != "matched":
        return {"case_id": task["case_id"], "decision": base,
                "reason": "sourcegate_preserved_" + str(base), "top_row_key": row_key,
                "sourcegate_reason": sourcegate.get("reason"), "residual_ids": residual_ids,
                "residual_relations": [], "row_axis_check": None,
                "sourcegate_prediction": sourcegate, "selected_source_entity": source_entity}
    if selected is None:
        raise RuntimeError(f"Sourcegate chosen row is absent in task: {task['case_id']} / {row_key}")
    row_status = _common_row_status(v2, task, selected, product.get("au_sku_rows", []))
    if row_status["status"] == "contradicted":
        return {"case_id": task["case_id"], "decision": "review",
                "reason": "sourcegate_match_conflicts_with_same_row_selected_axis_audit",
                "top_row_key": row_key, "sourcegate_reason": sourcegate.get("reason"),
                "residual_ids": residual_ids, "residual_relations": [], "row_axis_check": row_status,
                "sourcegate_prediction": sourcegate, "selected_source_entity": source_entity}
    if row_status["status"] != "entailed":
        return {"case_id": task["case_id"], "decision": "review",
                "reason": "same_row_common_or_au_only_axis_unknown", "top_row_key": row_key,
                "sourcegate_reason": sourcegate.get("reason"), "residual_ids": residual_ids,
                "residual_relations": [], "row_axis_check": row_status,
                "sourcegate_prediction": sourcegate, "selected_source_entity": source_entity}
    residual_relations = []
    for rid in residual_ids:
        raw = residual_raw.get(rid)
        if raw is None:
            raise RuntimeError(f"Missing v11 cached residual output: {rid}")
        relation = raw.get("effective_relation")
        source_fact = (_source_fact_supports_unknown(v2, task, source_entity or {}, raw)
                       if relation == "unknown" else None)
        conservation = (_curtain_contents_resolves(task, product, selected, raw)
                        if relation == "unknown" and source_fact is None else None)
        resolved_relation = ("entailed" if source_fact else
                             conservation.get("relation") if conservation else relation)
        residual_relations.append({"residual_id": rid, "semantic_key": raw.get("semantic_key"),
                                   "axis_name_raw": raw.get("axis_name_raw"),
                                   "value_raw": raw.get("value_raw"),
                                   "model_relation": relation, "effective_relation": resolved_relation,
                                   "source_fact_resolution": source_fact,
                                   "contents_count_conservation": conservation,
                                   "title": raw.get("title"), "fallback": raw.get("fallback"),
                                   "evidence": raw.get("title", {}).get("evidence")})
    relations = [x["effective_relation"] for x in residual_relations]
    if "contradicted" in relations:
        decision, reason = "unmatched", "fixed_product_title_or_description_contradicts_rakuten_only_condition"
    elif any(x != "entailed" for x in relations):
        decision, reason = "review", "rakuten_only_condition_unresolved_by_fixed_product_evidence"
    else:
        decision, reason = "matched", "sourcegate_row_and_all_residual_conditions_entailed"
    return {"case_id": task["case_id"], "decision": decision, "reason": reason,
            "top_row_key": row_key, "sourcegate_reason": sourcegate.get("reason"),
            "residual_ids": residual_ids, "residual_relations": residual_relations,
            "row_axis_check": row_status, "sourcegate_prediction": sourcegate,
            "selected_source_entity": source_entity}


def run(args: argparse.Namespace) -> dict[str, Any]:
    freeze_path = args.output / "freeze.json"
    if not freeze_path.is_file():
        raise FileNotFoundError("Run --freeze before --run")
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    paths = _inputs(args)
    if frozen.get("input_sha256") != {name: sha256(path) for name, path in paths.items()}:
        raise RuntimeError("Frozen input hash mismatch")
    v2 = _v2_module()
    hybrid = _hybrid_module()
    evaluation = v2.current_eval
    tasks = read_jsonl(paths["tasks"])
    products = {x["dossier_id"]: x for x in read_jsonl(paths["products"])}
    sourcegate_rows = read_jsonl(paths["sourcegate"])
    v11_raw = read_jsonl(paths["v11_raw_predictions"])
    cached_residual_rows = read_jsonl(paths["v11_raw_residual_outputs"])
    source_by_case = {x["case_id"]: x for x in sourcegate_rows}
    v11_by_case = {x["case_id"]: x for x in v11_raw}
    cached_by_id = {x["residual_id"]: x for x in cached_residual_rows}
    if len(tasks) != 1383 or set(source_by_case) != {x["case_id"] for x in tasks} or set(v11_by_case) != set(source_by_case):
        raise RuntimeError("Requires complete exact-case coverage across sourcegate and cached residual outputs")
    # Reconstruct every prompt id from frozen label-free task/product inputs.
    residuals, case_residual_keys = v2._model_inputs(tasks, products)
    if {x["residual_id"] for x in residuals} != set(cached_by_id):
        raise RuntimeError("Cached residual prompt IDs differ from reconstructed task inputs")
    outputs = []
    for task in tasks:
        cid = task["case_id"]
        if task.get("gpu196_cohort") != v11_by_case[cid].get("gpu196_cohort"):
            raise RuntimeError(f"GPU cohort ID mismatch: {cid}")
        residual_ids = case_residual_keys.get(cid, [])
        # Validate that raw cached prompts for this case resolve to the same IDs
        # used by its original v11 source plan.
        plan_ids = {x.get("semantic_key") for x in v11_by_case[cid].get("plan", {}).get("residual_axes", [])}
        task_ids = {cached_by_id[rid].get("semantic_key") for rid in residual_ids}
        if plan_ids != task_ids:
            raise RuntimeError(f"Residual axes differ between v11 raw and reconstructed task: {cid}")
        product = products[task["fixed_au_product_ref"]["dossier_id"]]
        recomputed = _recompute_sourcegates(task, product, source_by_case[cid], hybrid, evaluation)
        variants = {}
        for name, gate in (("hybrid_v10_recomputed", recomputed["hybrid_recomputed"]),
                           ("canonical_v2_recomputed", recomputed["legacy_recomputed"])):
            variants[name] = _case_decision(v2, task, product, gate, residual_ids, cached_by_id)
        outputs.append({"case_id": cid, "gpu196_cohort": bool(task.get("gpu196_cohort")),
                        "variants": variants,
                        "prior_prediction_diagnostic": recomputed["prior_prediction_diagnostic"]})
    raw_path = args.output / "raw_predictions.jsonl"
    write_jsonl(raw_path, outputs)
    post_hash = {name: sha256(path) for name, path in paths.items()}
    if post_hash != frozen["input_sha256"]:
        raise RuntimeError("Frozen input changed during ablation")
    variant_summaries = {}
    for name in ("hybrid_v10_recomputed", "canonical_v2_recomputed"):
        predictions = [x["variants"][name] for x in outputs]
        decisions = Counter(x["decision"] for x in predictions)
        baseline_decisions = Counter(x["sourcegate_prediction"]["decision"] for x in predictions)
        changes = Counter((x["variants"][name]["sourcegate_prediction"]["decision"],
                           x["variants"][name]["decision"]) for x in outputs)
        variant_summaries[name] = {
            "sourcegate_decision_counts": dict(baseline_decisions),
            "residual_decision_counts": dict(decisions),
            "sourcegate_to_residual_changes": {f"{a}->{b}": n for (a, b), n in changes.items()},
            "model_changed_cases": sum(a != b for (a, b), n in changes.items() for _ in range(n)),
            "direct_source_fact_resolutions": sum(bool(r.get("source_fact_resolution"))
                                                   for p in predictions for r in p.get("residual_relations", [])),
            "curtain_count_conservation_resolutions": sum(bool(r.get("contents_count_conservation"))
                                                          for p in predictions for r in p.get("residual_relations", [])),
        }
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_freeze_sha256": sha256(freeze_path),
        "raw_predictions_sha256": sha256(raw_path),
        "raw_prediction_count": len(outputs), "same_gpu196_count": sum(bool(t.get("gpu196_cohort")) for t in tasks),
        "variants": variant_summaries,
        "title_relation_counts": dict(Counter(x.get("title", {}).get("relation") for x in cached_residual_rows)),
        "title_argmax_counts": dict(Counter(x.get("title", {}).get("model_label") for x in cached_residual_rows)),
        "fallback_relation_counts": dict(Counter(x.get("fallback", {}).get("relation") for x in cached_residual_rows if x.get("fallback"))),
        "fallback_argmax_counts": dict(Counter(x.get("fallback", {}).get("model_label") for x in cached_residual_rows if x.get("fallback"))),
        "inputs_before": frozen["input_sha256"], "inputs_after": post_hash,
        "labels_read": False, "prior_accuracy_read": False,
        "model_inference_reused_only": True,
        "sourcegate_recomputed_from_full_au_pool": True,
        "prior_v10_predictions_used_only_as_ranking_and_diagnostic": True,
        "sourcegate_case_level_review_not_overridden": True,
    }
    write_json(args.output / "summary.json", summary)
    result = {"summary": summary, "output": str(raw_path)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-dir", type=Path, default=DEFAULT_TASK_DIR)
    p.add_argument("--residual-run", type=Path, default=DEFAULT_RESIDUAL_RUN)
    p.add_argument("--sourcegate", type=Path, default=DEFAULT_SOURCEGATE)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--freeze", action="store_true")
    p.add_argument("--run", action="store_true")
    args = p.parse_args()
    if args.freeze == args.run:
        p.error("Choose exactly one of --freeze or --run")
    if args.freeze:
        freeze(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
