"""Export short, source-verified relation questions without opening SKU labels."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import sku_integrated_gate_v1 as gate
from run_integrated_sku_gate_v1 import ROOT, dump, read, sha, write_rows

MAX_EVIDENCE_PER_REQUIREMENT = 4
CONTROL_LIMIT_PER_RELATION = 40
CANONICAL_COMPONENT_NOUNS = {
    "lace": "レースカーテン", "drape": "カーテン", "hook": "フック",
    "tassel": "タッセル", "door": "ドア", "panel": "パネル",
    "electric_blanket": "電気毛布", "blanket": "毛布", "armrest": "肘掛け",
    "top_board": "天板", "bookshelf": "本棚", "handle": "持ち手",
    "storage_bag": "収納袋", "duvet_cover": "布団カバー", "futon": "布団",
    "pad": "敷きパッド", "mattress": "マットレス",
}


def noun_for(req, modules):
    component = req.get("component", "")
    if component.startswith("word:"):
        return component[5:]
    return CANONICAL_COMPONENT_NOUNS.get(component)


def hypothesis(req, modules):
    if req["type"] != "component_presence" or not isinstance(req.get("value"), bool):
        return None
    noun = noun_for(req, modules)
    if not noun:
        return None
    return f"この商品には{noun}が付いている。" if req["value"] else f"この商品には{noun}が付いていない。"


def eligible_scope(line):
    scope = line.get("scope_tag", "")
    return scope.startswith("product_page") or scope == "fixed_au_title"


def noun_is_row_invariant(context, noun):
    """Global product prose is safe only when this noun never varies by AU SKU row."""
    return not any(noun in str(axis.get("value", "")) or noun in str(axis.get("axis_name", ""))
                   for row in context.get("au_rows", []) for axis in row.get("axes", []))


def validate_provenance(input_dir, gate_run):
    gate_manifest = json.loads((gate_run / "manifest.json").read_text())
    freeze_path = gate_run / "freeze.json"
    freeze = json.loads(freeze_path.read_text())
    input_manifest_path = input_dir / "manifest.json"
    input_manifest = json.loads(input_manifest_path.read_text())
    input_hashes = {n: sha(input_dir / n) for n in ("cases.jsonl", "products.jsonl", "manifest.json")}
    if gate_manifest.get("files", {}).get("predictions.jsonl") != sha(gate_run / "predictions.jsonl"):
        raise ValueError("Source gate prediction changed")
    if gate_manifest.get("files", {}).get("freeze.json") != sha(freeze_path):
        raise ValueError("Source gate freeze changed")
    if freeze.get("input_sha256") != input_hashes:
        raise ValueError("Source gate was not frozen against these inputs")
    if input_manifest.get("synthetic") is not False or input_manifest.get("labels_read") is not False:
        raise ValueError("Inputs must be real and label-blind")
    if input_manifest.get("fixed_pair_mapping") is not True:
        raise ValueError("Inputs must preserve the fixed AU URL mapping")
    for name in ("cases.jsonl", "products.jsonl"):
        if input_manifest.get("output_sha256", {}).get(f"inputs/{name}") != input_hashes[name]:
            raise ValueError("Prepared input changed: " + name)
    if not isinstance(freeze.get("code_sha256"), dict) or not freeze["code_sha256"]:
        raise ValueError("Source gate freeze has no code hash record")
    return gate_manifest, freeze, input_manifest, input_hashes, input_manifest_path, freeze_path


def evidence_candidates(context, req, modules, store):
    noun = noun_for(req, modules)
    if not noun or not noun_is_row_invariant(context, noun):
        return []
    title = context["au_product"]
    lines = [{"text": title["title"], "span": title.get("title_span"), "scope_tag": "fixed_au_title"}]
    lines += context["au_description_lines"]
    results = []
    for line in lines:
        span = line.get("span")
        if not eligible_scope(line) or not span or not modules.src.verify_span(store, span):
            continue
        if not isinstance(line.get("text"), str) or noun not in line["text"] or span.get("quote") != line["text"]:
            continue
        # Preserve an entire literal line. Token limits are enforced by the model,
        # never by cutting away a clause or its negation.
        results.append({"quote": line["text"], "span": span,
                        "source_scope": {"kind": "fixed_au_product", "scope_tag": line["scope_tag"],
                                         "au_product_id": title["product_id"]}})
    results.sort(key=lambda e: (e["source_scope"]["scope_tag"] != "fixed_au_title", len(e["quote"])))
    return results[:MAX_EVIDENCE_PER_REQUIREMENT]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / ".lab-output/sku-integrated-inputs-20261010-v1/inputs")
    parser.add_argument("--gate-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    manifest, freeze, input_manifest, input_hashes, input_manifest_path, freeze_path = validate_provenance(args.input, args.gate_run)
    modules = gate.load_gate()
    store = modules.src.RawStore(ROOT)
    contexts = {p["dossier_id"]: p for p in read(args.input / "products.jsonl")}
    cases = {c["case_id"]: c for c in read(args.input / "cases.jsonl")}
    for case in cases.values():
        context = contexts.get(case.get("dossier_id"))
        if not context or str(case.get("au_product_id")) != str(context.get("au_product", {}).get("product_id")):
            raise ValueError("Case does not map to its fixed AU product")
    predictions = read(args.gate_run / "predictions.jsonl")
    unique, targets, controls, control_counts = {}, [], [], Counter()
    seen_controls = set()
    def add(req, evidence, case_id, row_key, purpose):
        claim = hypothesis(req, modules)
        if not claim:
            return None
        key = json.dumps([claim, evidence["span"], evidence["source_scope"]], ensure_ascii=False, sort_keys=True)
        task_id = "relation-" + hashlib.sha256(key.encode()).hexdigest()[:20]
        item = {"task_id": task_id, "case_id": case_id, "au_row_key": row_key,
                "requirement": {k: req.get(k) for k in ("type", "value", "component", "axis_label", "axis_value_quote")},
                "hypothesis": claim, "evidence": {**evidence, "evidence_id": "evidence-" + task_id[9:]},
                "source_scope": evidence["source_scope"]}
        if task_id not in unique:
            unique[task_id] = item
        return {"task_id": task_id, "case_id": case_id, "au_row_key": row_key,
                "requirement_id": req["requirement_id"], "purpose": purpose}
    for pred in predictions:
        reqs = {r["requirement_id"]: r for r in pred["requirements"]}
        context = contexts[pred["dossier_id"]]
        for row in pred["rows"]:
            if row["status"] == "conflict":
                continue
            for result in row.get("atom_results", []):
                req = reqs[result["requirement_id"]]
                # Initial experiment is presence verification; absence continues to
                # require the structural gate's explicitly bounded contents policy.
                if pred["decision"] != "accept" and result["status"] == "unknown" and req.get("value") is True:
                    for evidence in evidence_candidates(context, req, modules, store):
                        target = add(req, evidence, pred["case_id"], row["row_key"], "unresolved_presence")
                        if target:
                            targets.append(target)
                if control_counts[result["status"]] >= CONTROL_LIMIT_PER_RELATION:
                    continue
                if req["type"] != "component_presence" or result["status"] not in ("support", "conflict"):
                    continue
                for ev in result.get("evidence", []):
                    span = ev.get("span")
                    if not span or not modules.src.verify_span(store, span) or ev.get("quote") != span.get("quote"):
                        continue
                    evidence = {"quote": ev["quote"], "span": span,
                        "source_scope": {"kind": "structural_relation_control", "scope": ev.get("scope"),
                                         "au_product_id": pred["au_product_id"]}}
                    target = add(req, evidence, pred["case_id"], row["row_key"], "structural_control")
                    if target:
                        control = {**target, "reference_relation": result["status"],
                                   "reference_kind": "structural_source_annotation_not_independent_gold"}
                        control_key = (control["task_id"], control["reference_relation"])
                        if control_key not in seen_controls:
                            control["hypothesis"] = hypothesis(req, modules)
                            control["evidence"] = evidence
                            seen_controls.add(control_key)
                            controls.append(control)
                            control_counts[result["status"]] += 1
                    break
    # Real negative controls also come from the excluded rows. Recompute only a
    # bounded sample, since the compact gate file omits repeated conflict cards.
    checked = 0
    for pred in predictions:
        if control_counts["conflict"] >= CONTROL_LIMIT_PER_RELATION or checked >= 80:
            break
        if not any(r["type"] == "component_presence" for r in pred["requirements"]):
            continue
        checked += 1
        case = cases[pred["case_id"]]
        facts = modules.PairFacts(contexts[pred["dossier_id"]])
        full = modules.run_method("A", case, facts, modules.PRIMARY_CONFIG)
        reqs = {r["requirement_id"]: r for r in full["requirements"]}
        for row in full["rows"]:
            for result in row.get("atom_results", []):
                req = reqs[result["requirement_id"]]
                if req["type"] != "component_presence" or result["status"] != "conflict":
                    continue
                for ev in result.get("evidence", []):
                    span = ev.get("span")
                    if not span or ev.get("quote") != span.get("quote") or not modules.src.verify_span(store, span):
                        continue
                    e = {"quote": ev["quote"], "span": span, "source_scope": {
                        "kind": "structural_relation_control", "scope": ev.get("scope"), "au_product_id": pred["au_product_id"]}}
                    target = add(req, e, pred["case_id"], row["row_key"], "structural_control")
                    if target:
                        item = {**target, "reference_relation": "conflict",
                                "reference_kind": "structural_source_annotation_not_independent_gold"}
                        control_key = (item["task_id"], item["reference_relation"])
                        if control_key not in seen_controls:
                            item["hypothesis"] = hypothesis(req, modules)
                            item["evidence"] = e
                            seen_controls.add(control_key)
                            controls.append(item); control_counts["conflict"] += 1
                    break
                if control_counts["conflict"] >= CONTROL_LIMIT_PER_RELATION:
                    break
            if control_counts["conflict"] >= CONTROL_LIMIT_PER_RELATION:
                break
    args.output.mkdir(parents=True)
    write_rows(args.output / "tasks.jsonl", list(unique.values()))
    write_rows(args.output / "targets.jsonl", targets)
    write_rows(args.output / "structural-controls.jsonl", controls)
    dump(args.output / "manifest.json", {"labels_opened": False, "synthetic_inputs": False,
        "tasks": len(unique), "targets": len(targets), "target_cases": len({t["case_id"] for t in targets}),
        "structural_controls": len(controls), "controls_are_gold": False,
        "target_selection": "unknown component_presence=True only; these references select unresolved targets",
        "model_task_pool": "targets plus structural support/conflict tasks; task records contain no reference labels",
        "source_gate_predictions_sha256": sha(args.gate_run / "predictions.jsonl"),
        "source_gate_freeze_sha256": sha(freeze_path),
        "source_gate_code_sha256": freeze.get("code_sha256", {}),
        "input_manifest_sha256": sha(input_manifest_path),
        "inputs_sha256": {n: sha(args.input / n) for n in ("cases.jsonl", "products.jsonl")},
        "code_sha256": sha(Path(__file__)), "raw_source_sha256": store._sha,
        "files": {p.name: sha(p) for p in args.output.iterdir() if p.is_file()}})
    print(json.dumps({"tasks": len(unique), "targets": len(targets), "target_cases": len({t["case_id"] for t in targets}),
                      "control_counts": dict(control_counts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
