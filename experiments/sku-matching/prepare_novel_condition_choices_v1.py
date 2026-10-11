#!/usr/bin/env python3
"""Prepare untouched real novel SKUs through pinned Claude v5 alignment.

Stage and prepare use the pinned producer's functions without modifying them.
Similarity reproduces its full bidirectional cross-encoder calls on CPU. No
labels, normalization additions, category dictionaries or URL routing are used.
The output is consumable by integrate_claude_description_v1.finish.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import time

from integrate_claude_description_v1 import axis_text, document, encoded, index, norm, rows, sha, verify_outputs, write_rows

ROOT = Path(__file__).resolve().parents[2]
PRODUCER_SHA = "70e75088c40e4fc600c7ed91abaf237842ed7ea6f4d99c58bb40723d751280c3"
MODEL = "hotchpotch/japanese-reranker-small-v2"
REVISION = "e8091d132c23b372e059505edbb9255f346100d3"
UPSTREAM_HEAD = "7b8f5abf20ad5428a9a1dd48f5b67a1a208a1ba8"
VERSION = "novel-pinned-v5-description-prepare-v1"


def load_producer(path):
    if sha(path.read_bytes()) != PRODUCER_SHA:
        raise ValueError("pinned producer SHA differs")
    spec = importlib.util.spec_from_file_location("novel_pinned_claude_v5", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.MODEL != (MODEL, REVISION):
        raise ValueError("pinned producer model differs")
    return module


def freeze_inputs(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("synthetic") is not False or manifest.get("labels_read") is not False:
        raise ValueError("requires real, label-free frozen inputs")
    for name in ("cases.jsonl", "products.jsonl"):
        expected = manifest["output_sha256"].get(name, manifest["output_sha256"].get("inputs/" + name))
        if expected != sha((directory / name).read_bytes()):
            raise ValueError("frozen input output SHA differs")
    return manifest


def raw_references(values):
    result = {}

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("raw_file"), str) and isinstance(value.get("sha256"), str):
                name, digest = value["raw_file"], value["sha256"]
                if name in result and result[name] != digest:
                    raise ValueError("contradictory raw SHA reference")
                result[name] = digest
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(values)
    return result


def stage(generic_dir, integrated_dir, producer, output_dir):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    gm, im = freeze_inputs(generic_dir), freeze_inputs(integrated_dir)
    original_paths = [directory / name for directory in (generic_dir, integrated_dir)
                      for name in ("cases.jsonl", "products.jsonl", "manifest.json")] + [producer]
    source_shas = {str(path.resolve()): sha(path.read_bytes()) for path in original_paths}
    gc = [row for row in rows(generic_dir / "cases.jsonl") if row["cohort"] == "novel"]
    if not gc:
        raise ValueError("no novel real cases")
    cases = index(gc, "case_id", "novel")
    dossier_ids = {case["dossier_id"] for case in gc}
    gp = [row for row in rows(generic_dir / "products.jsonl") if row["dossier_id"] in dossier_ids]
    ic = [row for row in rows(integrated_dir / "cases.jsonl") if row["case_id"] in cases]
    ip = [row for row in rows(integrated_dir / "products.jsonl") if row["dossier_id"] in dossier_ids]
    integrated_cases = index(ic, "case_id", "integrated novel")
    generic_products = index(gp, "dossier_id", "generic products")
    integrated_products = index(ip, "dossier_id", "integrated products")
    if set(cases) != set(integrated_cases):
        raise ValueError("novel case/product identities differ")
    if set(generic_products) != dossier_ids or set(integrated_products) != dossier_ids:
        raise ValueError("novel dossiers not fully covered")
    for identity, case in cases.items():
        original = integrated_cases[identity]
        if (case["rakuten"]["axes"] != original["rakuten_selected"]["axes"]
                or case["dossier_id"] != original["dossier_id"]
                or case["au_product_id"] != original["au_product_id"]):
            raise ValueError("whole original case/axis identity differs")
    for identity, product in generic_products.items():
        if product["au"]["rows"] != integrated_products[identity]["au_rows"]:
            raise ValueError("whole original fixed AU row array differs")
    raw = raw_references([gc, gp, ic, ip])
    for name, digest in raw.items():
        if gm["source_raw_sha256"].get(name) != digest or sha((ROOT / name).read_bytes()) != digest:
            raise ValueError("novel source raw evidence SHA differs")
    pinned = load_producer(producer)
    output_dir.mkdir(parents=True, exist_ok=False)
    for subdirectory, inputs in (("generic", (gc, gp)), ("inputs", (ic, ip))):
        target = output_dir / subdirectory
        target.mkdir()
        for name, values in zip(("cases.jsonl", "products.jsonl"), inputs):
            write_rows(target / name, values)
        (target / "manifest.json").write_bytes(encoded({"synthetic": False, "labels_read": False,
            "source_raw_sha256": raw, "output_sha256": {name: sha((target / name).read_bytes())
             for name in ("cases.jsonl", "products.jsonl")}}) + b"\n")
    _, _, rakuten, au = pinned.load(output_dir)
    options = [{"dossier_id": identity, "rakuten": rakuten[identity], "au": au[identity]}
               for identity in sorted(rakuten)]
    write_rows(output_dir / "option_inputs.jsonl", options)
    if any(sha(path.read_bytes()) != source_shas[str(path.resolve())] for path in original_paths):
        raise ValueError("source changed during staging")
    manifest = {"schema_version": VERSION, "phase": "stage", "case_count": len(gc), "product_count": len(gp),
        "synthetic": False, "labels_read": False, "upstream_head": UPSTREAM_HEAD,
        "input_sha256": source_shas, "source_raw_sha256": raw, "model": MODEL, "revision": REVISION,
        "producer_sha256": PRODUCER_SHA, "adapter_sha256": sha(Path(__file__).read_bytes()),
        "pairs_to_score": sum(2 * len(row["rakuten"]) * len(row["au"]) for row in options),
        "output_sha256": {"option_inputs.jsonl": sha((output_dir / "option_inputs.jsonl").read_bytes())},
        "production_adoption_allowed": False}
    (output_dir / "manifest.json").write_bytes(encoded(manifest) + b"\n")
    return manifest


def similarity(stage_dir, model_dir, output_dir, threads):
    import torch
    import transformers
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    if output_dir.exists():
        raise FileExistsError(output_dir)
    meta = json.loads((stage_dir / "manifest.json").read_text(encoding="utf-8"))
    verify_outputs(stage_dir, meta, ("option_inputs.jsonl",))
    options = rows(stage_dir / "option_inputs.jsonl")
    provenance_path = model_dir.parent / "download-provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if (provenance["model"], provenance["revision"]) != (MODEL, REVISION):
        raise ValueError("model download provenance differs")
    for name, entry in provenance["files"].items():
        if sha((model_dir / name).read_bytes()) != entry["sha256"]:
            raise ValueError("model download file changed")
    torch.set_num_threads(threads)
    tok = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir, local_files_only=True).eval()

    def scores(query, docs):
        enc = tok([query] * len(docs), docs, padding=True, truncation=True, max_length=512, return_tensors="pt")
        with torch.inference_mode():
            return model(**enc).logits[:, 0].float().tolist()

    start, similarities = time.perf_counter(), []
    for entry in options:
        r_text = [f"{label}: {value}" for _, label, value in entry["rakuten"]]
        a_text = [f"{name}: {value}" for name, value in entry["au"]]
        similarities.append({**entry, "forward": [scores(query, a_text) for query in r_text],
                             "reverse": [scores(query, r_text) for query in a_text]})
    output_dir.mkdir(parents=True, exist_ok=False)
    write_rows(output_dir / "similarities.jsonl", similarities)
    manifest = {"schema_version": VERSION, "phase": "similarity", "model": MODEL, "revision": REVISION,
        "threads": threads, "seconds": time.perf_counter() - start, "pairs_scored": meta["pairs_to_score"],
        "input_sha256": {"option_inputs.jsonl": sha((stage_dir / "option_inputs.jsonl").read_bytes()),
                         "stage_manifest": sha((stage_dir / "manifest.json").read_bytes())},
        "weights_sha256": provenance["files"], "download_provenance_sha256": sha(provenance_path.read_bytes()),
        "adapter_sha256": sha(Path(__file__).read_bytes()), "producer_sha256": PRODUCER_SHA,
        "torch": torch.__version__, "transformers": transformers.__version__, "python": platform.python_version(),
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "labels_read": False, "production_adoption_allowed": False,
        "output_sha256": {"similarities.jsonl": sha((output_dir / "similarities.jsonl").read_bytes())}}
    (output_dir / "manifest.json").write_bytes(encoded(manifest) + b"\n")
    return manifest


def prepare(stage_dir, similarity_dir, producer, output_dir):
    if output_dir.exists():
        raise FileExistsError(output_dir)
    pinned = load_producer(producer)
    meta = json.loads((stage_dir / "manifest.json").read_text(encoding="utf-8"))
    sim_meta = json.loads((similarity_dir / "manifest.json").read_text(encoding="utf-8"))
    verify_outputs(similarity_dir, sim_meta, ("similarities.jsonl",))
    if sim_meta["input_sha256"]["stage_manifest"] != sha((stage_dir / "manifest.json").read_bytes()):
        raise ValueError("similarity bound to different staging")
    gd = stage_dir / "generic"
    freeze_inputs(gd)
    freeze_inputs(stage_dir / "inputs")
    cases = index(rows(gd / "cases.jsonl"), "case_id", "cases")
    products = index(rows(gd / "products.jsonl"), "dossier_id", "products")
    product_lines = {identity: number for number, identity in enumerate(products, 1)}
    case_lines = {identity: number for number, identity in enumerate(cases, 1)}
    contexts, producer_cases, _, _ = pinned.load(stage_dir)
    pcases = index(producer_cases, "case_id", "producer cases")
    sims = index(rows(similarity_dir / "similarities.jsonl"), "dossier_id", "similarities")
    tables = {identity: pinned.value_pairs(entry) for identity, entry in sims.items()}
    axes = {identity: pinned.axis_pairs(tables[identity], entry["au"]) for identity, entry in sims.items()}
    documents, requests, tasks, row_plans, case_plans, decisions, condition_tables = {}, {}, [], [], [], [], []
    counts = Counter()
    generic_shas = {name: sha((gd / name).read_bytes()) for name in ("cases.jsonl", "products.jsonl")}

    def reference(name, line, json_path=None):
        value = {"path": str((gd / name).resolve()), "sha256": generic_shas[name], "line": line}
        if json_path is not None:
            value["json_path"] = json_path
        return value

    def add_task(case, product, row, direction, raw_axis, alternatives, origin):
        if not alternatives or raw_axis["value"] not in alternatives or any(not isinstance(value, str) for value in alternatives):
            raise ValueError("whole original selected value absent from alternatives")
        side = "au" if direction == "rakuten_to_au" else "rakuten"
        key = product["dossier_id"], side
        if key not in documents:
            documents[key] = document(product, side, reference("products.jsonl", product_lines[product["dossier_id"]]))
        doc = documents[key]
        target_axes = ([{"axis_name": a["axis_name"], "value": a["value"]} for a in row["axes"]] if side == "au" else
                       [{"axis_name": a["axis_label"], "value": a["value"]} for a in case["rakuten"]["axes"]])
        semantic = {"direction": direction, "axis_name": raw_axis["axis_label"] if side == "au" else raw_axis["axis_name"],
            "selected_value": raw_axis["value"], "option_values": copy.deepcopy(alternatives),
            "target_document_id": doc["document_id"], "target_document_sha256": doc["document_sha256"],
            "target_selected_axes": target_axes, "target_selected_axes_text": axis_text(target_axes)}
        digest = sha(encoded(semantic))
        request_id = "generic-description:" + digest
        requests.setdefault(request_id, {"request_id": request_id, "request_sha256": digest, **semantic,
            "selected_value_is_binding_only": True, "applicability_required": True, "production_adoption_allowed": False})
        identity = {"case_id": case["case_id"], "au_row_key": row["row_key"], "direction": direction,
                    "axis_key": raw_axis.get("axis_key", raw_axis.get("axis_name"))}
        task_id = "condition:" + sha(encoded(identity))
        tasks.append({"task_id": task_id, **identity, "request_id": request_id, "dossier_id": case["dossier_id"],
            "au_product_id": case["au_product_id"], "source_sku_key": case["rakuten"]["source_sku_key"],
            "raw_condition": copy.deepcopy(raw_axis), "option_values": copy.deepcopy(alternatives), "origin": origin,
            "source_ref": {"path": str((similarity_dir / "similarities.jsonl").resolve()),
                           "sha256": sim_meta["output_sha256"]["similarities.jsonl"]},
            "selected_au_row_ref": reference("products.jsonl", product_lines[product["dossier_id"]],
                                              f"$.au.rows[{product['au']['rows'].index(row)}]"),
            "selected_rakuten_sku_ref": reference("cases.jsonl", case_lines[case["case_id"]], "$.rakuten"),
            "status": "not_evaluated", "verification_required": True, "production_adoption_allowed": False})
        counts["task:" + direction] += 1
        counts["origin:" + origin] += 1
        return {"kind": "model", "task_id": task_id, "request_id": request_id, "direction": direction,
                "axis_key": identity["axis_key"]}

    for identity, case in cases.items():
        dossier = case["dossier_id"]
        product = products[dossier]
        conds = pinned.conditions(pcases[identity], contexts[dossier], tables[dossier], *axes[dossier])
        states = pinned.row_states(conds)
        decision, top, reason = pinned.decide(states)
        counts["upstream_reason:" + reason] += 1
        state_counts = states.value_counts().to_dict()
        upstream = {"case_id": identity, "dossier_id": dossier, "decision": decision, "top_row_key": top,
                    "reason": reason, "row_states": state_counts}
        decisions.append(upstream)
        condition_tables.extend({"case_id": identity, "dossier_id": dossier, **record} for record in
                                json.loads(conds.to_json(orient="records", force_ascii=False)))
        if reason == "pending_description_check":
            selected_keys = list(states.index[states == "pending_description_check"])
        elif reason == "all_conditions_aligned_on_one_row":
            selected_keys = [top]
        else:
            selected_keys = []
        omitted = len(states) - state_counts.get("contradiction", 0) - len(selected_keys)
        fixed_rows = index(product["au"]["rows"], "row_key", "fixed rows")
        if set(states.index) != set(fixed_rows):
            raise ValueError("producer row states do not cover the fixed original array")
        case_plans.append({"case_id": identity, "dossier_id": dossier, "cohort": "novel",
            "au_product_id": case["au_product_id"], "source_sku_key": case["rakuten"]["source_sku_key"],
            "upstream_decision": upstream, "candidate_row_keys": selected_keys, "total_fixed_au_row_count": len(fixed_rows),
            "upstream_contradiction_row_count": state_counts.get("contradiction", 0),
            "unresolved_omitted_row_count": omitted, "all_pending_rows_received": True,
            "production_adoption_allowed": False})
        for row_key in selected_keys:
            row = fixed_rows[row_key]
            rak_axes = index(case["rakuten"]["axes"], "axis_key", "Rakuten axes")
            au_axes = index(row["axes"], "axis_name", "AU axes")
            represented_rak, represented_au, bindings = set(), set(), []
            for condition in json.loads(conds[conds["row_key"] == row_key].to_json(orient="records", force_ascii=False)):
                if condition["kind"] == "rakuten" and condition["status"] == "aligned":
                    rak_axis, au_axis = rak_axes[condition["axis_key"]], au_axes[condition["au_axis"]]
                    if (rak_axis["axis_key"] in represented_rak or au_axis["axis_name"] in represented_au
                            or norm(rak_axis["value"]) != norm(au_axis["value"])):
                        raise ValueError("producer aligned correspondence not uniquely whole-value identical")
                    represented_rak.add(rak_axis["axis_key"])
                    represented_au.add(au_axis["axis_name"])
                    bindings.append({"kind": "upstream_identical", "rakuten_axis_key": rak_axis["axis_key"],
                        "au_axis_name": au_axis["axis_name"], "raw_condition": copy.deepcopy(rak_axis),
                        "mapped_au_condition": copy.deepcopy(au_axis), "independent_semantic_gold": False})
                    counts["upstream_identical_links"] += 1
            for key, raw_axis in rak_axes.items():
                if key not in represented_rak:
                    bindings.append(add_task(case, product, row, "rakuten_to_au", raw_axis, raw_axis["family_values"], "upstream_residual"))
                    represented_rak.add(key)
            for name, raw_axis in au_axes.items():
                if name not in represented_au:
                    options = list(dict.fromkeys(a["value"] for candidate in product["au"]["rows"]
                                               for a in candidate["axes"] if a["axis_name"] == name))
                    bindings.append(add_task(case, product, row, "au_to_rakuten", raw_axis, options, "unrepresented_au_axis"))
                    represented_au.add(name)
            row_plans.append({"case_id": identity, "au_row_key": row_key, "upstream_state": states[row_key],
                "condition_bindings": bindings, "rakuten_axis_count": len(rak_axes), "au_axis_count": len(au_axes),
                "all_original_axes_covered": represented_rak == set(rak_axes) and represented_au == set(au_axes),
                "production_adoption_allowed": False})
    if len({task["task_id"] for task in tasks}) != len(tasks):
        raise ValueError("duplicate condition task")
    output_dir.mkdir(parents=True, exist_ok=False)
    outputs = {"requests.jsonl": list(requests.values()), "documents.jsonl": list(documents.values()),
        "tasks.jsonl": tasks, "row_plans.jsonl": row_plans, "case_plans.jsonl": case_plans,
        "upstream_decisions.jsonl": decisions, "upstream_conditions.jsonl": condition_tables,
        "upstream_value_pairs.jsonl": [{"dossier_id": identity, **record} for identity, table in tables.items()
                                       for record in json.loads(table.to_json(orient="records", force_ascii=False))],
        "upstream_axes.jsonl": [{"dossier_id": identity, "symmetric": symmetric, "au_only": au_only}
                                for identity, (symmetric, au_only) in axes.items()]}
    for name, values in outputs.items():
        write_rows(output_dir / name, values)
    manifest = {"schema_version": VERSION, "phase": "prepare", "upstream_head": UPSTREAM_HEAD,
        "input_sha256": {str((directory / "manifest.json").resolve()): sha((directory / "manifest.json").read_bytes())
                         for directory in (stage_dir, similarity_dir)},
        "code_sha256": sha(Path(__file__).read_bytes()), "producer_sha256": PRODUCER_SHA,
        "labels_read": False, "synthetic": False, "model_inference_run": True,
        "production_adoption_allowed": False, "scope_applicability_proved": False,
        "whole_values_and_alternatives_preserved": True, "candidate_url_routing": False,
        "upstream_row_state_contract": "pinned v5 conditions/row_states/decide; per-row condition evidence preserved",
        "case_count": len(case_plans), "cohort_case_counts": {"novel": len(case_plans)},
        "input_case_count": len(cases), "input_cases_outside_received_cohorts": 0,
        "unreceived_cohort_cases_are_not_processed_or_adopted": True,
        "baseline_matched_count": sum(plan["upstream_decision"]["decision"] == "matched" for plan in case_plans),
        "row_count": len(row_plans), "task_count": len(tasks), "request_count": len(requests),
        "document_count": len(documents), "counts": dict(counts), "source_raw_sha256": meta["source_raw_sha256"],
        "model_input_contract": "selected_value is binding only; use whole original choices and fixed target title/row",
        "output_sha256": {name: sha((output_dir / name).read_bytes()) for name in outputs}}
    (output_dir / "manifest.json").write_bytes(encoded(manifest) + b"\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    st = commands.add_parser("stage")
    st.add_argument("--generic-dir", type=Path, required=True)
    st.add_argument("--integrated-dir", type=Path, required=True)
    st.add_argument("--producer", type=Path, required=True)
    st.add_argument("--output-dir", type=Path, required=True)
    sim = commands.add_parser("similarity")
    sim.add_argument("--stage-dir", type=Path, required=True)
    sim.add_argument("--model-dir", type=Path, required=True)
    sim.add_argument("--output-dir", type=Path, required=True)
    sim.add_argument("--threads", type=int, default=4)
    prep = commands.add_parser("prepare")
    prep.add_argument("--stage-dir", type=Path, required=True)
    prep.add_argument("--similarity-dir", type=Path, required=True)
    prep.add_argument("--producer", type=Path, required=True)
    prep.add_argument("--output-dir", type=Path, required=True)
    options = vars(parser.parse_args())
    command = options.pop("command")
    result = {"stage": stage, "similarity": similarity, "prepare": prepare}[command](**options)
    print(json.dumps({key: value for key, value in result.items() if key not in
                     {"input_sha256", "output_sha256", "source_raw_sha256", "weights_sha256"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
