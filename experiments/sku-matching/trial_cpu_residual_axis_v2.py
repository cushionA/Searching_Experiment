#!/usr/bin/env python3
"""Run a pinned local CPU classifier only on Rakuten-only SKU conditions.

Common axes are filtered deterministically at the same AU SKU row first. The
model classifies a residual fact against the fixed AU title, with a narrowly
scoped description fallback only when the title is unknown. Labels are opened
only after the full raw prediction file has been written and hashed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import resource
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_TASK_DIR = ROOT / ".lab-output/sku-cpu-residual-task-20261010-v10"
DEFAULT_OUTPUT = ROOT / ".lab-output/sku-cpu-residual-task-20261010-v11/gliner-decide"
DEFAULT_MODEL = ROOT / ".deps/sku-gliner-model"
DEFAULT_LABELS = ROOT / ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"
DEFAULT_V10 = ROOT / ".lab-output/sku-structured-task-trials-20261010-v10"
MODEL_REPO = "fastino/GLiNER2.5-multi-Decide"
MODEL_REVISION = "e173bca1f0c4217d7a55b3b0c705d5ece8a0218d"
LABELS = ["条件を満たす", "条件に矛盾する", "固定条件から不明"]
LABEL_TO_RELATION = {LABELS[0]: "entailed", LABELS[1]: "contradicted", LABELS[2]: "unknown"}
RELATION_TO_LABEL = {v: k for k, v in LABEL_TO_RELATION.items()}
MIN_CONFIDENCE = 0.70

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import evaluate_structured_skus as current_eval


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _axis_values(axes: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    values: dict[str, Any] = {}
    conflicts = []
    for axis in axes:
        key = axis.get("semantic_key")
        if not key or str(key).startswith("axis:") and key == "axis:unlabeled":
            continue
        value = axis.get("normalized_value")
        if key in values and values[key] != value:
            conflicts.append(str(key))
        else:
            values[str(key)] = value
    return values, sorted(set(conflicts))


def _proven_axis_conflict(key: str, left: Any, right: Any) -> bool:
    """Only typed numeric/bool mismatches prove exclusion without a crosswalk."""
    if isinstance(left, bool) and isinstance(right, bool):
        return left != right
    if (isinstance(left, (int, float)) and not isinstance(left, bool)
            and isinstance(right, (int, float)) and not isinstance(right, bool)):
        return left != right
    return False


def common_axis_plan(task: dict[str, Any]) -> dict[str, Any]:
    """Filter real AU rows by R/AU common axes without hiding value conflicts."""
    r_axes = task["rakuten"].get("axes", [])
    r_values, r_conflicts = _axis_values(r_axes)
    rows = task.get("au_sku_rows", [])
    au_present = set()
    for row in rows:
        vals, _ = _axis_values(row.get("axes", []))
        au_present.update(vals)
    common = sorted(set(r_values) & au_present)
    residual = sorted(set(r_values) - au_present)
    r_names = {x["semantic_key"]: x.get("axis_name_raw", "") for x in r_axes}
    residual_axes = []
    for key in residual:
        for axis in r_axes:
            if axis.get("semantic_key") == key:
                residual_axes.append({
                    "semantic_key": key, "axis_name_raw": axis.get("axis_name_raw", ""),
                    "value_raw": axis.get("value_raw", ""),
                    "normalized_value": axis.get("normalized_value"),
                    "source_ref": axis.get("source_ref", {}),
                })
                break

    eligible = []
    excluded = []
    unknown = []
    for row in rows:
        au_values, au_conflicts = _axis_values(row.get("axes", []))
        mismatches = [key for key in common if key in au_values and r_values.get(key) is not None
                      and au_values.get(key) is not None
                      and _proven_axis_conflict(key, au_values[key], r_values[key])]
        ambiguous_differences = [key for key in common if key in au_values and r_values.get(key) is not None
                                 and au_values.get(key) is not None and au_values[key] != r_values[key]
                                 and key not in mismatches]
        missing = [key for key in common if key not in au_values or au_values.get(key) is None
                   or r_values.get(key) is None]
        if mismatches:
            excluded.append({"row_key": row.get("row_key"), "fields": mismatches,
                             "reason": "common_axis_value_conflict"})
        elif au_conflicts or missing or ambiguous_differences:
            unknown.append({"row_key": row.get("row_key"),
                            "fields": sorted(set(au_conflicts + missing + ambiguous_differences)),
                            "reason": "common_axis_unknown_or_conflicting_within_row"})
        else:
            eligible.append(row)

    au_only_axes = []
    for row in eligible:
        vals, _ = _axis_values(row.get("axes", []))
        for key, value in vals.items():
            if key not in r_values:
                au_only_axes.append({"row_key": row.get("row_key"), "semantic_key": key,
                                     "value": value, "axis_names": [
                                         x.get("axis_name_raw", "") for x in row.get("axes", [])
                                         if x.get("semantic_key") == key]})
    return {
        "common_fields": common, "common_values": {k: r_values[k] for k in common},
        "common_source_axis_names": {k: r_names.get(k, "") for k in common},
        "residual_axes": residual_axes, "r_axis_conflicts": r_conflicts,
        "candidate_row_keys": [r["row_key"] for r in eligible],
        "excluded_common_value_conflicts": excluded,
        "candidate_common_unknowns": unknown,
        "au_only_axes": au_only_axes,
    }


def _conflict_fields(*entities: dict[str, Any]) -> list[str]:
    return sorted({str(c.get("field") or "unknown") for e in entities
                   for c in e.get("source_conflicts", []) if isinstance(c, dict)})


def legacy_decision(task: dict[str, Any]) -> dict[str, Any]:
    # The product sidecar wraps every source entity as {row_key, axes,
    # current_entity}; the established gate expects the entity itself.
    checked = [(c["current_entity"], current_eval.gate(task, c["current_entity"]))
               for c in task.get("au_sku_rows", [])]
    matches = [c for c, g in checked if g["decision"] == "matched"]
    reviews = [c for c, g in checked if g["decision"] == "review"]
    if len(matches) == 1:
        return {"decision": "matched", "row_keys": [matches[0]["row_key"]], "review_row_count": len(reviews)}
    if matches:
        return {"decision": "review", "row_keys": [x["row_key"] for x in matches],
                "reason": "multiple_compatible_au_rows", "review_row_count": len(reviews)}
    if reviews:
        return {"decision": "review", "row_keys": [x["row_key"] for x in reviews],
                "reason": "no_confirmed_match_but_candidate_reviews", "review_row_count": len(reviews)}
    return {"decision": "unmatched", "row_keys": [], "review_row_count": 0}


def _title_prompt(axis: dict[str, Any], title: str, common: dict[str, Any]) -> str:
    common_text = " / ".join(f"{k}={v}" for k, v in sorted(common.items())) or "共通SKU軸なし"
    return "\n".join([
        "同一商品として既に対応付け済みです。次はSKUの余剰条件1件だけを判定します。",
        f"AUの固定商品名: {title or '不明'}",
        f"AU SKU行との共通軸で確定したcontext: {common_text}",
        f"Rakuten側だけの選択軸: {axis.get('axis_name_raw', '')}={axis.get('value_raw', '')}",
        "固定商品名が、この選択軸の値を明示的に支持する、明示的に否定する、または不明のどれかを選んでください。",
        "軸名と値の意味、否定、枚数、対象、適用範囲を確認してください。語が書かれていないだけでは否定にしません。商品名に矛盾があれば矛盾、根拠が足りなければ不明です。",
        "条件付き説明や商品ページ内のシリーズ案内はこの判定に使わず、商品名だけを根拠にします。",
    ])


_SERIES_HEADER = ("シリーズ", "ラインナップ", "セットでおすすめ", "一覧はこちら", "コチラ", "こちら")
_DETAIL_HEADER = ("商品詳細", "商品仕様", "商 品 詳 細", "内容", "サイズ", "カラー", "色", "素材", "材質", "仕様")


def fallback_blocks(product: dict[str, Any], axis: dict[str, Any], common: dict[str, Any]) -> list[dict[str, Any]]:
    """Return narrow conditional product evidence; carry series-header scope forward."""
    key = str(axis.get("semantic_key", ""))
    terms = {str(axis.get("axis_name_raw", "")), str(axis.get("value_raw", ""))}
    if key == "lace":
        terms.update({"レース", "ドレープ", "付属", "含む", "なし", "選択"})
    elif key == "color":
        terms.update({str(axis.get("value_raw", "")), "カラー", "色"})
    elif key == "size":
        terms.update({str(axis.get("value_raw", "")), "幅", "丈", "サイズ"})
    else:
        terms.add(key.replace("axis:", ""))
    blocks = product.get("description_blocks", [])
    series_context = False
    previous_condition = ""
    result = []
    for block in blocks:
        text = str(block.get("text", "")).strip()
        scope = str(block.get("scope", ""))
        compact = text.replace(" ", "")
        if any(x in scope for x in ("series", "sibling", "navigation", "related")):
            series_context = True
        if any(x in compact for x in _SERIES_HEADER):
            series_context = True
        if any(compact.startswith(x) for x in _DETAIL_HEADER):
            series_context = False
        if compact.startswith("【") and compact.endswith("】"):
            previous_condition = compact[1:-1]
        if any(x in scope for x in ("series", "sibling", "navigation", "related")) or series_context:
            continue
        if not text or not any(term and term in text for term in terms):
            continue
        # Apply every recognizable conjunct in a conditional heading. If any
        # part cannot be mapped to a selected context value, omit the block so
        # the case remains review instead of accepting partial scope.
        if previous_condition:
            condition = previous_condition.replace(" ", "")
            context = json.dumps(common, ensure_ascii=False, sort_keys=True)
            numbers = re.findall(r"\d+(?:\.\d+)?", condition)
            context_numbers = set(re.findall(r"\d+(?:\.\d+)?", context))
            markers = {"color": ("色", "カラー"), "size": ("幅", "丈", "サイズ", "高さ", "奥行"),
                       "named_size": ("サイズ", "シングル", "ダブル"), "panel_size_cm": ("幅", "丈")}
            recognized = bool(numbers)
            if not recognized or any(n not in context_numbers for n in numbers):
                continue
            if any(any(marker in condition for marker in markers.get(key, ()))
                   and str(value).replace(" ", "") not in condition.replace(" ", "")
                   for key, value in common.items() if isinstance(value, str)):
                continue
        ref = product.get("description_source_ref", {})
        result.append({"text": text, "scope": scope,
                       "inherited_heading": previous_condition,
                       "source_ref": {**ref, "json_path": block.get("source_field"),
                                      "block_index": block.get("block_index"),
                                      "source_line": block.get("source_line")}})
    return result


def _fallback_prompt(axis: dict[str, Any], title: str, common: dict[str, Any],
                     blocks: list[dict[str, Any]]) -> str:
    lines = ["同一商品として対応付け済みです。固定商品名だけでは不明だったSKU余剰条件を、条件付き商品説明だけから判定します。",
             f"AUの固定商品名: {title or '不明'}",
             "AU SKU行との共通軸context: " + (" / ".join(f"{k}={v}" for k, v in sorted(common.items())) or "なし"),
             f"Rakuten側だけの選択軸: {axis.get('axis_name_raw', '')}={axis.get('value_raw', '')}",
             "次の引用は候補箇所です。見出し/条件/適用対象が選択SKUへ明示的に適用する場合だけ根拠にしてください。シリーズ案内や別商品紹介は無効です。語の不在を否定とみなさず、否定/数量/条件が不明なら不明を選びます。"]
    for b in blocks:
        lines.append(f"引用(scope={b['scope']}; heading={b['inherited_heading'] or 'なし'}): {b['text']}")
    lines.append("引用が条件を支持する、明示的に矛盾する、または不明から選んでください。")
    return "\n".join(lines)


def legacy_guard(task: dict[str, Any], candidate: dict[str, Any], residual_fields: set[str]) -> tuple[bool, dict[str, Any]]:
    """Allow only an old gate review caused solely by a residual-axis unknown."""
    result = current_eval.gate(task, candidate)
    conflicts = _conflict_fields(task["rakuten"], candidate,
                                  {"source_conflicts": task.get("current_page_context", {}).get("internal_source_conflicts", [])})
    if conflicts:
        return False, {**result, "legacy_guard": "source_conflict_preserved", "conflict_fields": conflicts}
    if result["decision"] == "matched":
        return True, {**result, "legacy_guard": "existing_gate_matched"}
    if result["decision"] != "review":
        return False, {**result, "legacy_guard": "existing_gate_conflict"}
    unresolved = set(result.get("unresolved_fields", []))
    missing = set(result.get("missing_fields", []))
    spelling = set(result.get("unresolved_spelling_fields", []))
    review_fields = unresolved | missing | spelling
    # A review cannot be waived if it came from any unrelated missing/unknown fact.
    if review_fields and review_fields <= residual_fields:
        return True, {**result, "legacy_guard": "residual_unknown_retained_and_resolved_only_if_title_supports"}
    return False, {**result, "legacy_guard": "existing_unknown_preserved"}


def decide_case(task: dict[str, Any], product: dict[str, Any], relations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if "au_sku_rows" not in task:
        task = {**task, "au_sku_rows": product.get("au_sku_rows", []),
                "current_page_context": task.get("current_page_context") or product.get("current_page_context", {})}
    plan = common_axis_plan(task)
    if plan["candidate_common_unknowns"] or plan["au_only_axes"]:
        return {"decision": "review", "reason": "common_axis_or_au_only_condition_unresolved",
                "plan": plan, "candidate_row_keys_after_legacy_guard": [],
                "legacy_guards": [], "residual_results": []}
    row_by_key = {r["row_key"]: r for r in task.get("au_sku_rows", [])}
    residual_fields = {a["semantic_key"] for a in plan["residual_axes"]}
    if plan["r_axis_conflicts"]:
        return {"decision": "review", "reason": "conflicting_rakuten_axis_values", "plan": plan,
                "residual_results": []}
    if plan["candidate_common_unknowns"]:
        return {"decision": "review", "reason": "common_axis_unknown_in_candidate_pool", "plan": plan,
                "residual_results": []}
    if not plan["candidate_row_keys"]:
        return {"decision": "unmatched", "reason": "no_au_row_matches_all_common_axes", "plan": plan,
                "residual_results": []}
    if plan["au_only_axes"]:
        return {"decision": "review", "reason": "unrepresented_au_only_axis", "plan": plan,
                "residual_results": []}

    usable = []
    guards = []
    for key in plan["candidate_row_keys"]:
        candidate = row_by_key[key]["current_entity"]
        ok, guard = legacy_guard(task, candidate, residual_fields)
        if guard.get("legacy_guard") == "existing_gate_matched":
            guard["retained_residual_evidence"] = {
                field: candidate.get("evidence", {}).get(field, []) for field in sorted(residual_fields)
                if candidate.get("evidence", {}).get(field)
            }
        guards.append({"row_key": key, **guard})
        if ok:
            usable.append(key)
    if not usable:
        all_known_conflicts = bool(guards) and all(x.get("legacy_guard") == "existing_gate_conflict" for x in guards)
        return {"decision": "unmatched" if all_known_conflicts else "review",
                "reason": ("all_common_rows_have_known_attribute_conflicts" if all_known_conflicts
                           else "all_common_rows_blocked_by_current_attrs_or_evidence"),
                "plan": plan, "legacy_guards": guards, "residual_results": []}

    residual_results = []
    for axis in plan["residual_axes"]:
        key = axis["semantic_key"]
        result = relations.get(key)
        if not result:
            residual_results.append({"semantic_key": key, "relation": "unknown", "source": "no_model_result"})
        else:
            residual_results.append({"semantic_key": key, **result})
    if any(x.get("relation") == "contradicted" for x in residual_results):
        decision, reason = "unmatched", "fixed_product_condition_contradicts_residual"
    elif any(x.get("relation") != "entailed" for x in residual_results):
        already_matched = [x for x in guards if x.get("legacy_guard") == "existing_gate_matched"
                           and x.get("row_key") in usable]
        if len(usable) == 1 and len(already_matched) == 1:
            decision, reason = "matched", "existing_cited_attribute_gate_preserved_after_title_unknown"
        else:
            decision, reason = "review", "residual_condition_unknown"
    elif len(usable) != 1:
        decision, reason = "review", "multiple_au_rows_match_all_retained_conditions"
    else:
        decision, reason = "matched", "common_axes_match_and_all_residuals_entailed"
    return {"decision": decision, "reason": reason, "plan": plan,
            "candidate_row_keys_after_legacy_guard": usable, "legacy_guards": guards,
            "residual_results": residual_results}


def _pinned_model(model_dir: Path) -> dict[str, Any]:
    from try_gliner import FILES, REPO, REVISION
    if REPO != MODEL_REPO or REVISION != MODEL_REVISION:
        raise RuntimeError("Existing GLiNER pin differs from the requested model revision")
    files = {}
    for name, expected in FILES.items():
        path = model_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"Pinned model file missing: {path}")
        actual_sha = sha256(path)
        actual_size = path.stat().st_size
        if actual_sha != expected["sha256"] or actual_size != expected["size_bytes"]:
            raise RuntimeError(f"Pinned model SHA/size mismatch: {name}")
        files[name] = {"sha256": actual_sha, "size_bytes": actual_size}
    source_manifest = json.loads((model_dir / "source-manifest.json").read_text(encoding="utf-8"))
    if source_manifest.get("repo") != REPO or source_manifest.get("revision") != REVISION:
        raise RuntimeError("Local model source manifest does not match the pin")
    return {"repo": REPO, "revision": REVISION, "files": files,
            "source_manifest_sha256": sha256(model_dir / "source-manifest.json")}


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    tasks_path = args.task_dir / "tasks.jsonl"
    products_path = args.task_dir / "products.jsonl"
    task_manifest_path = args.task_dir / "manifest.json"
    task_manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
    if task_manifest.get("tasks_sha256") != sha256(tasks_path) or task_manifest.get("products_sha256") != sha256(products_path):
        raise RuntimeError("Prepared task artifact does not match its manifest")
    if args.output.exists():
        raise FileExistsError(f"Refusing existing run directory: {args.output}")
    model_pin = _pinned_model(args.model_dir)
    reuse_source = None
    if args.reuse_residual_output:
        reuse_path = args.reuse_residual_output.resolve()
        source_freeze = reuse_path.parent / "freeze.json"
        if not reuse_path.is_file() or not source_freeze.is_file():
            raise FileNotFoundError("Exact-prompt reuse requires a durable prior residual output and freeze")
        prior = json.loads(source_freeze.read_text(encoding="utf-8"))
        if prior.get("model") != model_pin or prior.get("decision_labels") != LABELS or prior.get("minimum_model_confidence") != MIN_CONFIDENCE:
            raise RuntimeError("Prior output model/schema/threshold differs; refusing prompt reuse")
        reuse_source = {"path": str(reuse_path), "sha256": sha256(reuse_path),
                        "freeze_path": str(source_freeze), "freeze_sha256": sha256(source_freeze)}
    try:
        versions = {p: importlib.metadata.version(p) for p in ("gliner2", "torch", "transformers", "tokenizers", "safetensors")}
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("Run freeze with the prepared CPU GLiNER virtualenv") from exc
    freeze_doc = {
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "deterministic same-row common-axis filter; CPU GLiNER Decide only for R-only selected axis + value against fixed AU title; conditional description fallback only after title unknown; current attrs/evidence/conflicts retained; AU-only axes review",
        "decision_labels": LABELS, "minimum_model_confidence": MIN_CONFIDENCE,
        "task_dir": str(args.task_dir.resolve()),
        "task_manifest_sha256": sha256(task_manifest_path),
        "tasks_sha256": sha256(tasks_path), "products_sha256": sha256(products_path),
        "prepare_code_sha256": task_manifest["prepare_code_sha256"],
        "trial_code_sha256": sha256(Path(__file__)), "model": model_pin,
        "runtime_versions": versions,
        "reused_prompt_source": reuse_source,
        "label_status_for_later_eval": "machine-labelled; human verification status must be read only after raw predictions are saved",
        "no_gpu_api_gcp_or_download": True,
    }
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "freeze.json", freeze_doc, exclusive=True)
    return freeze_doc


def _model_inputs(tasks: list[dict[str, Any]], products: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    unique: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    case_residual_keys: dict[str, list[str]] = {}
    for task in tasks:
        product = products[task["fixed_au_product_ref"]["dossier_id"]]
        task = {**task, "au_sku_rows": product.get("au_sku_rows", [])}
        plan = common_axis_plan(task)
        keys = []
        for axis in plan["residual_axes"]:
            common_json = json.dumps(plan["common_values"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            unique_key = (task["product_id"], axis["semantic_key"], str(axis.get("axis_name_raw", "")),
                          str(axis.get("value_raw", "")), common_json)
            if unique_key not in unique:
                unique[unique_key] = {"residual_id": "res-" + hashlib.sha256("\0".join(unique_key).encode()).hexdigest()[:20],
                                      "product_id": task["product_id"], "axis": axis,
                                      "fixed_au_title": product.get("title_raw", ""),
                                      "title_source_ref": product.get("title_source_ref", {}),
                                      "common_context_examples": plan["common_values"],
                                      "title_input": _title_prompt(axis, product.get("title_raw", ""), plan["common_values"]),
                                      "description_fallback_candidates": fallback_blocks(product, axis, plan["common_values"])}
            keys.append(unique[unique_key]["residual_id"])
        case_residual_keys[task["case_id"]] = keys
    return list(unique.values()), case_residual_keys


def _prompt_cache(path: Path | None, frozen: dict[str, Any]) -> dict[tuple[str, str], Any]:
    if path is None:
        return {}
    expected = frozen.get("reused_prompt_source")
    if not expected or str(path.resolve()) != expected["path"] or sha256(path) != expected["sha256"]:
        raise RuntimeError("Prompt reuse source differs from the frozen source hash")
    cache = {}
    for row in read_jsonl(path):
        for stage in ("title", "fallback"):
            result = row.get(stage)
            if isinstance(result, dict) and result.get("raw_output") is not None:
                text = result.get("model_input", "")
                cache[(result.get("stage", ""), hashlib.sha256(text.encode()).hexdigest())] = result["raw_output"]
    return cache


def _call_model(model: Any, texts: list[str], batch_size: int, progress_path: Path | None = None) -> tuple[list[Any], float]:
    start = time.perf_counter()
    outputs = []
    for i in range(0, len(texts), batch_size):
        batch = model.batch_classify_text(texts[i:i + batch_size], {"relation": LABELS},
                                          batch_size=batch_size, include_confidence=True)
        outputs.extend(batch)
        if progress_path:
            with progress_path.open("a", encoding="utf-8") as stream:
                for offset, (text, output) in enumerate(zip(texts[i:i + batch_size], batch, strict=True)):
                    stream.write(json.dumps({"index": i + offset, "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                             "input": text, "output": output}, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
    return outputs, time.perf_counter() - start


def _result(output: Any, stage: str, text: str, threshold: float = MIN_CONFIDENCE) -> dict[str, Any]:
    value = output.get("relation") if isinstance(output, dict) else output
    label = value.get("label") if isinstance(value, dict) else value
    confidence = value.get("confidence") if isinstance(value, dict) else None
    relation = LABEL_TO_RELATION.get(label, "unknown")
    if confidence is None or float(confidence) < threshold:
        relation = "unknown"
    return {"stage": stage, "model_label": label, "model_confidence": confidence,
            "relation": relation, "confidence_threshold": threshold,
            "model_input": text, "raw_output": output}


def _existing_v10_same_case(task_dir: Path, case_ids: set[str]) -> dict[str, dict[str, Any]]:
    """Read prior CPU predictions only after current raw predictions are durable."""
    result = {}
    candidate_paths = set(task_dir.glob("*/predictions*.jsonl")) | set(task_dir.glob("*/decisions*.jsonl"))
    for path in sorted(candidate_paths):
        try:
            rows = read_jsonl(path)
        except (json.JSONDecodeError, OSError):
            continue
        by_id = {x.get("case_id"): x for x in rows if x.get("case_id") in case_ids}
        if by_id:
            result[path.relative_to(task_dir).as_posix()] = by_id
    for path in sorted(task_dir.glob("*/predictions*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rows = doc.get("rows", doc if isinstance(doc, list) else []) if isinstance(doc, (dict, list)) else []
        if isinstance(rows, list):
            by_id = {x.get("case_id"): x for x in rows if isinstance(x, dict) and x.get("case_id") in case_ids}
            if by_id:
                result[path.relative_to(task_dir).as_posix()] = by_id
    return result


def _prediction_label(row: dict[str, Any]) -> str | None:
    for key in ("decision", "prediction_decision", "prediction", "label"):
        value = row.get(key)
        if value in {"matched", "unmatched", "review"}:
            return value
    mapping = {"同じ選択SKU仕様": "matched", "異なる選択SKU仕様": "unmatched", "情報不足で要確認": "review",
               "same SKU and same product variant": "matched", "different SKU or product variant": "unmatched",
               "needs human review": "review"}
    for key in ("decision", "prediction_decision", "prediction", "label"):
        if row.get(key) in mapping:
            return mapping[row[key]]
    return None


def _metric(gold: list[str], pred: list[str]) -> dict[str, Any]:
    labels = ("matched", "unmatched", "review")
    matrix = {g: {p: 0 for p in labels} for g in labels}
    for g, p in zip(gold, pred, strict=True):
        matrix[g][p] += 1
    known = [i for i, x in enumerate(gold) if x != "review"]
    accepted = [i for i, x in enumerate(pred) if x == "matched"]
    correct = sum(gold[i] == pred[i] for i in known)
    return {"case_count": len(gold), "confusion_matrix": matrix,
            "known_label_accuracy_excluding_review": correct / len(known) if known else None,
            "known_case_count": len(known), "gold_review_count": len(gold) - len(known),
            "accepted_count": len(accepted), "accepted_gold_review_count": sum(gold[i] == "review" for i in accepted),
            "known_matched_precision": (sum(gold[i] == "matched" for i in accepted) /
                                        sum(gold[i] != "review" for i in accepted)
                                        if any(gold[i] != "review" for i in accepted) else None),
            "all_case_accuracy_including_review": sum(g == p for g, p in zip(gold, pred, strict=True)) / len(gold) if gold else None}


def _row_aware_metric(label_rows: list[dict[str, Any]], pred_rows: list[dict[str, Any]]) -> dict[str, Any]:
    base = _metric([x.get("decision") for x in label_rows], [x.get("decision") for x in pred_rows])
    def exact_row(g: dict[str, Any], p: dict[str, Any]) -> bool:
        return (set(p.get("candidate_row_keys_after_legacy_guard", []))
                == set(g.get("matching_au_row_keys", [])))
    accepted = [(g, p) for g, p in zip(label_rows, pred_rows, strict=True) if p.get("decision") == "matched"]
    correct_rows = sum(g.get("decision") == "matched" and
                       exact_row(g, p)
                       for g, p in accepted)
    matched_gold = [g for g in label_rows if g.get("decision") == "matched"]
    known_accepted = [(g, p) for g, p in accepted if g.get("decision") != "review"]
    correct_known = sum(g.get("decision") == "matched" and
                        exact_row(g, p)
                        for g, p in known_accepted)
    base.update({
        "row_correct_match_count": correct_rows,
        "matched_row_recall": correct_rows / len(matched_gold) if matched_gold else None,
        "matched_precision_including_gold_review": correct_rows / len(accepted) if accepted else None,
        "known_matched_precision_exact_row": correct_known / len(known_accepted) if known_accepted else None,
        "false_match_wrong_or_missing_row_count": sum(g.get("decision") == "matched" and
            not exact_row(g, p) for g, p in accepted),
        "false_unmatched_gold_matched_count": sum(g.get("decision") == "matched" and p.get("decision") == "unmatched"
            for g, p in zip(label_rows, pred_rows, strict=True)),
        "known_auto_coverage": (sum(p.get("decision") != "review" for g, p in zip(label_rows, pred_rows, strict=True)
                                    if g.get("decision") != "review") / base["known_case_count"]
                                if base["known_case_count"] else None),
        "hold_rate": (sum(p.get("decision") == "review" for p in pred_rows) / len(pred_rows) if pred_rows else None),
    })
    return base


def evaluate_after_raw(args: argparse.Namespace, raw_path: Path, raw_sha: str, freeze_doc: dict[str, Any]) -> dict[str, Any]:
    # This is deliberately the first point where labels or prior predictions are read.
    labels_path = args.labels
    labels = read_jsonl(labels_path)
    labels_by_id = {x["case_id"]: x for x in labels}
    if len(labels_by_id) != len(labels):
        raise ValueError("Duplicate labels case_id")
    raw_rows = read_jsonl(raw_path)
    ids = [x["case_id"] for x in raw_rows]
    if set(ids) != labels_by_id.keys():
        raise ValueError("Prediction and frozen label case IDs differ")
    predictions = {x["case_id"]: x for x in raw_rows}
    gold = {cid: labels_by_id[cid].get("decision", labels_by_id[cid].get("semantic_label")) for cid in ids}
    if not all(x in {"matched", "unmatched", "review"} for x in gold.values()):
        raise ValueError("Unexpected label value")
    cohorts = {"full1383": set(ids), "same_gpu196": {x["case_id"] for x in raw_rows if x["gpu196_cohort"]}}
    metrics = {}
    for cohort, members in cohorts.items():
        ordered = sorted(members)
        gold_rows = [gold[c] for c in ordered]
        label_rows = [labels_by_id[c] for c in ordered]
        metrics[cohort] = {"cpu_residual_row_aware": _row_aware_metric(label_rows, [predictions[c] for c in ordered]),
                           "legacy_rule": _metric(gold_rows, [predictions[c]["legacy_rule_decision"] for c in ordered])}
    category_metrics = {}
    by_category = defaultdict(list)
    for cid in ids:
        by_category[predictions[cid].get("category") or "unknown"].append(cid)
    for category, case_ids in sorted(by_category.items()):
        category_metrics[category] = _row_aware_metric(
            [labels_by_id[c] for c in case_ids], [predictions[c] for c in case_ids])
    dossier_metrics = []
    by_dossier = defaultdict(list)
    for cid in ids:
        by_dossier[predictions[cid].get("dossier_id") or "unknown"].append(cid)
    for dossier, case_ids in sorted(by_dossier.items()):
        report = _row_aware_metric([labels_by_id[c] for c in case_ids], [predictions[c] for c in case_ids])
        dossier_metrics.append({"dossier_id": dossier, "case_count": len(case_ids),
                                "matched_row_recall": report["matched_row_recall"],
                                "matched_precision_including_gold_review": report["matched_precision_including_gold_review"],
                                "known_auto_coverage": report["known_auto_coverage"],
                                "hold_rate": report["hold_rate"]})
    dossier_macro = {
        key: (sum(x[key] for x in dossier_metrics if x[key] is not None)
              / sum(x[key] is not None for x in dossier_metrics) if any(x[key] is not None for x in dossier_metrics) else None)
        for key in ("matched_row_recall", "matched_precision_including_gold_review", "known_auto_coverage", "hold_rate")
    }
    prior = _existing_v10_same_case(args.v10, set(ids))
    prior_metrics = {}
    for name, by_id in prior.items():
        overlap = sorted(set(ids) & by_id.keys())
        got = [(cid, _prediction_label(by_id[cid])) for cid in overlap]
        got = [(cid, p) for cid, p in got if p is not None]
        if got:
            prior_metrics[name] = {"same_case_count_with_decisions": len(got),
                                   "cpu_v10": _metric([gold[c] for c, _ in got], [p for _, p in got])}
            prior_rows = []
            gold_rows = []
            has_row = True
            for cid, _ in got:
                source = by_id[cid]
                row_keys = source.get("candidate_row_keys_after_legacy_guard")
                if row_keys is None and source.get("top_row_key"):
                    row_keys = [source["top_row_key"]]
                if row_keys is None and isinstance(source.get("row_keys"), list):
                    row_keys = source["row_keys"]
                if row_keys is None:
                    has_row = False
                prior_rows.append({**source, "decision": _prediction_label(source),
                                   "candidate_row_keys_after_legacy_guard": row_keys or []})
                gold_rows.append(labels_by_id[cid])
            if has_row:
                prior_metrics[name]["row_aware"] = _row_aware_metric(gold_rows, prior_rows)
    manifest = json.loads((args.task_dir / "manifest.json").read_text(encoding="utf-8"))
    label_manifest_path = labels_path.parent / "manifest.json"
    label_manifest = json.loads(label_manifest_path.read_text(encoding="utf-8")) if label_manifest_path.exists() else {}
    review_origins = Counter(x.get("reason") for x in raw_rows if x.get("legacy_rule_decision") == "review")
    result = {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_freeze_sha256": sha256(args.output / "freeze.json"),
        "raw_predictions_sha256_verified": sha256(raw_path) == raw_sha,
        "raw_predictions_sha256": raw_sha,
        "labels_sha256": sha256(labels_path),
        "labels_status": label_manifest.get("annotation_protocol", {}),
        "labels_human_verified": label_manifest.get("annotation_protocol", {}).get("human_verified"),
        "task_count": manifest["case_count"], "cohort_metrics": metrics,
        "category_metrics_full1383": category_metrics,
        "dossier_macro_mean": dossier_macro, "dossier_metrics": dossier_metrics,
        "cpu_v10_same_case_metrics": prior_metrics,
        "legacy_review_count_in_full_task_set": sum(x.get("legacy_rule_decision") == "review" for x in raw_rows),
        "legacy_review_origin_reasons": dict(review_origins),
        "legacy_review_cases_still_review": sum(x.get("legacy_rule_decision") == "review" and x["decision"] == "review" for x in raw_rows),
        "legacy_review_cases_resolved_or_changed": [x["case_id"] for x in raw_rows if x.get("legacy_rule_decision") == "review" and x["decision"] != "review"],
        "all_cases_included": True,
    }
    write_json(args.output / "scores.json", result, exclusive=True)
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    freeze_path = args.output / "freeze.json"
    if not freeze_path.is_file():
        raise FileNotFoundError("Create the frozen method first with --freeze")
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    task_manifest_path = args.task_dir / "manifest.json"
    manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
    if (frozen.get("tasks_sha256") != sha256(args.task_dir / "tasks.jsonl")
            or frozen.get("products_sha256") != sha256(args.task_dir / "products.jsonl")
            or frozen.get("trial_code_sha256") != sha256(Path(__file__))
            or frozen.get("prepare_code_sha256") != manifest.get("prepare_code_sha256")
            or frozen.get("model") != _pinned_model(args.model_dir)):
        raise RuntimeError("Frozen method, tasks, code, or model SHA changed since freeze")
    if (args.output / "raw_predictions.jsonl").exists() or (args.output / "scores.json").exists():
        raise FileExistsError("Prediction run already exists; refusing overwrite")
    os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      TOKENIZERS_PARALLELISM="false")
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    if torch.cuda.is_available():
        raise RuntimeError("CUDA is available; refusing to use GPU")
    load_start = time.perf_counter()
    from gliner2 import AutoExtractor
    model = AutoExtractor.from_pretrained(str(args.model_dir), local_files_only=True)
    model.eval()
    load_seconds = time.perf_counter() - load_start

    tasks = read_jsonl(args.task_dir / "tasks.jsonl")
    products = {x["dossier_id"]: x for x in read_jsonl(args.task_dir / "products.jsonl")}
    residuals, case_residuals = _model_inputs(tasks, products)
    prompt_cache = _prompt_cache(args.reuse_residual_output, frozen)
    texts = [x["title_input"] for x in residuals]
    title_progress = args.output / "title_batch_progress.jsonl"
    title_progress.touch(exist_ok=False)
    title_outputs: list[Any] = [None] * len(texts)
    missing_title_indices = []
    for i, text in enumerate(texts):
        cached = prompt_cache.get(("fixed_title_primary", hashlib.sha256(text.encode()).hexdigest()))
        if cached is None:
            missing_title_indices.append(i)
        else:
            title_outputs[i] = cached
    calculated, title_seconds = _call_model(model, [texts[i] for i in missing_title_indices], args.batch_size, title_progress)
    for i, output in zip(missing_title_indices, calculated, strict=True):
        title_outputs[i] = output
    rows_by_id = {}
    for item, output in zip(residuals, title_outputs, strict=True):
        result = _result(output, "fixed_title_primary", item["title_input"])
        result["evidence"] = {"quote": item["fixed_au_title"], "source_ref": item["title_source_ref"]}
        rows_by_id[item["residual_id"]] = {"residual_id": item["residual_id"], "product_id": item["product_id"],
                                            "semantic_key": item["axis"]["semantic_key"],
                                            "axis_name_raw": item["axis"]["axis_name_raw"],
                                            "value_raw": item["axis"]["value_raw"], "title": result,
                                            "fallback_candidates": item["description_fallback_candidates"]}

    fallback_items = []
    for item in residuals:
        result = rows_by_id[item["residual_id"]]["title"]
        if result["relation"] == "unknown" and item["description_fallback_candidates"]:
            fallback_items.append({**item, "fallback_input": _fallback_prompt(
                item["axis"], item["fixed_au_title"], item["common_context_examples"],
                item["description_fallback_candidates"])})
    fallback_progress = args.output / "fallback_batch_progress.jsonl"
    fallback_progress.touch(exist_ok=False)
    fallback_outputs: list[Any] = [None] * len(fallback_items)
    missing_fallback_indices = []
    for i, item in enumerate(fallback_items):
        key = ("conditional_description_secondary", hashlib.sha256(item["fallback_input"].encode()).hexdigest())
        cached = prompt_cache.get(key)
        if cached is None:
            missing_fallback_indices.append(i)
        else:
            fallback_outputs[i] = cached
    calculated_fallbacks, fallback_seconds = _call_model(
        model, [fallback_items[i]["fallback_input"] for i in missing_fallback_indices], args.batch_size, fallback_progress) if missing_fallback_indices else ([], 0.0)
    for i, output in zip(missing_fallback_indices, calculated_fallbacks, strict=True):
        fallback_outputs[i] = output
    for item, output in zip(fallback_items, fallback_outputs, strict=True):
        result = _result(output, "conditional_description_secondary", item["fallback_input"])
        result["evidence"] = item["description_fallback_candidates"]
        rows_by_id[item["residual_id"]]["fallback"] = result
        if result["relation"] != "unknown":
            rows_by_id[item["residual_id"]]["effective_relation"] = result["relation"]
        else:
            rows_by_id[item["residual_id"]]["effective_relation"] = "unknown"
    for item in residuals:
        row = rows_by_id[item["residual_id"]]
        row.setdefault("effective_relation", row["title"]["relation"])

    residual_map = {x["residual_id"]: x for x in residuals}
    raw_rows = []
    baseline_cache = {}
    for task in tasks:
        cid = task["case_id"]
        product = products[task["fixed_au_product_ref"]["dossier_id"]]
        expanded_task = {**task, "au_sku_rows": product.get("au_sku_rows", [])}
        relations = {}
        for rid in case_residuals.get(cid, []):
            item = rows_by_id[rid]
            relations[item["semantic_key"]] = {
                "relation": item["effective_relation"], "residual_id": rid,
                "title_result": item["title"], "fallback_result": item.get("fallback"),
            }
        prediction = decide_case(expanded_task, product, relations)
        if cid not in baseline_cache:
            baseline_cache[cid] = legacy_decision(expanded_task)
        raw_rows.append({"case_id": cid, "split": task.get("split"), "gpu196_cohort": task.get("gpu196_cohort"),
                         "product_id": task["product_id"], "dossier_id": product.get("dossier_id"),
                         "category": task["rakuten"].get("attrs", {}).get("category"),
                         "decision": prediction["decision"],
                         "reason": prediction["reason"], "plan": prediction.get("plan"),
                         "candidate_row_keys_after_legacy_guard": prediction.get("candidate_row_keys_after_legacy_guard", []),
                         "legacy_guards": prediction.get("legacy_guards", []),
                         "residual_results": prediction.get("residual_results", []),
                         "legacy_rule_decision": baseline_cache[cid]["decision"],
                         "legacy_rule_row_keys": baseline_cache[cid]["row_keys"],
                         "legacy_rule_review_candidate_count": baseline_cache[cid]["review_row_count"],
                         "current_unknowns_retained": {"rakuten": task["rakuten"].get("unknown_fields", []),
                                                        "selected_candidate_unknowns": [
                                                            row["current_entity"].get("unknown_fields", [])
                                                            for row in expanded_task.get("au_sku_rows", [])
                                                            if row["row_key"] in prediction.get("candidate_row_keys_after_legacy_guard", [])]},
                         "source_conflicts_retained": {"rakuten": task["rakuten"].get("source_conflicts", []),
                                                        "page": task.get("current_page_context", {}).get("internal_source_conflicts", [])}})

    raw_path = args.output / "raw_predictions.jsonl"
    write_jsonl(raw_path, raw_rows)
    raw_sha = sha256(raw_path)
    residual_path = args.output / "raw_residual_outputs.jsonl"
    write_jsonl(residual_path, list(rows_by_id.values()))
    inference_manifest = {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "freeze_sha256": sha256(freeze_path), "task_manifest_sha256": sha256(task_manifest_path),
        "raw_predictions_sha256": raw_sha, "raw_residual_outputs_sha256": sha256(residual_path),
        "case_count": len(raw_rows), "unique_residual_prompts": len(residuals),
        "title_prompt_count": len(texts), "title_reused_exact_prompt_count": len(texts) - len(missing_title_indices),
        "fallback_reused_exact_prompt_count": len(fallback_items) - len(missing_fallback_indices),
        "description_fallback_prompt_count": len(fallback_items),
        "title_inference_seconds": title_seconds, "fallback_inference_seconds": fallback_seconds,
        "model_load_seconds": load_seconds, "batch_size": args.batch_size,
        "threads": {"intra_op": torch.get_num_threads(), "inter_op": torch.get_num_interop_threads()},
        "cuda_available": torch.cuda.is_available(), "platform": platform.platform(),
        "python": platform.python_version(),
        "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "runtime_versions": frozen["runtime_versions"],
        "labels_read": False, "prior_predictions_read": False,
    }
    write_json(args.output / "inference-manifest.json", inference_manifest, exclusive=True)
    scores = evaluate_after_raw(args, raw_path, raw_sha, frozen)
    return {"inference_manifest": inference_manifest, "scores": scores}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-dir", type=Path, default=DEFAULT_TASK_DIR)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    p.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    p.add_argument("--v10", type=Path, default=DEFAULT_V10)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--reuse-residual-output", type=Path,
                   help="reuse only prior outputs whose exact model input text SHA and stage match")
    p.add_argument("--freeze", action="store_true", help="freeze task/method/model hashes only; do not infer")
    p.add_argument("--run", action="store_true", help="run CPU inference, persist raw outputs, then evaluate labels")
    args = p.parse_args()
    if args.freeze == args.run:
        p.error("choose exactly one of --freeze or --run")
    if args.freeze:
        result = freeze(args)
    else:
        result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
