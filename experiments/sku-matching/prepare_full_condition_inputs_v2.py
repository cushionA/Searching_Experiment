#!/usr/bin/env python3
"""Build split-specific full-condition baseline and composition input bindings.

This is a label-blind, no-inference adapter for the pinned full v2/v3 source
bundle. It retains every captured AU row (including sold-out rows), every AU
axis, every selected Rakuten axis and full original option order. It writes
preparation files only; it does not classify or score.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parents[2]
TASK_ACCUM = []
REQUEST_ACCUM = {}
OUT_DEFAULT = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2"
PREP_DEFAULT = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1"
GENERIC_PATH = ROOT / "experiments/sku-matching/prepare_generic_model_sku_v1.py"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def canonical(x) -> bytes:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(x) -> str:
    return sha_bytes(canonical(x))


def rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_rows(path: Path, values):
    with path.open("xb") as f:
        for value in values:
            f.write(canonical(value) + b"\n")


def write_json(path: Path, value):
    with path.open("xb") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b"\n")


def recursive_spans(value):
    if isinstance(value, dict):
        if all(k in value for k in ("raw_file", "sha256", "locator", "start", "end", "quote")):
            yield value
        for child in value.values():
            yield from recursive_spans(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_spans(child)


def prep_inputs(prep_dir: Path, source_root: Path, split: str):
    inv = json.loads((prep_dir / "inventory.json").read_text(encoding="utf-8"))
    if inv.get("status") != "source_bundle_ready" or inv["source_provenance"].get("missing_referenced_sources"):
        raise ValueError("verified source bundle is not ready")
    groupmaps, cases, products, failures = {}, [], [], []
    hashes = {}
    for version in ("v2", "v3"):
        entry = inv["splits"][version]
        root = source_root / ".lab-output/sku-independent-real-20261011"
        input_dir = root / f"eval-{version}/run-token/inputs"
        group_path = root / f"eval-{version}/groups-token/groups.jsonl"
        for name in ("cases.jsonl", "products.jsonl", "failures.jsonl", "manifest.json"):
            path = input_dir / name
            expected = entry["input_members"][name]["sha256"]
            if sha_path(path) != expected:
                raise ValueError("frozen input SHA mismatch: " + path.name)
            hashes[str(path.relative_to(source_root))] = expected
        group_hash = sha_path(group_path)
        # group-map SHA is pinned by the prior source-index; recheck its file SHA there.
        index = json.loads((prep_dir / "source-index.json").read_text(encoding="utf-8"))
        indexed = {x["path"]: x["sha256"] for x in index}
        rel_group = str(group_path.relative_to(source_root))
        if indexed.get(rel_group) != group_hash:
            raise ValueError("frozen split group-map SHA differs")
        hashes[rel_group] = group_hash
        gm = rows(group_path)
        groupmap = {}
        for group in gm:
            # The frozen map key is dossier_id; do not trust case.split (exploration).
            dossier = str(group.get("dossier_id", group.get("group", {}).get("dossier_id", "")))
            obj = group.get("group", group)
            if not dossier:
                dossier = str(obj.get("dossier_id", ""))
            if dossier:
                if dossier in groupmap:
                    raise ValueError("duplicate frozen dossier split binding")
                groupmap[dossier] = obj
        groupmaps[version] = groupmap
        version_cases = rows(input_dir / "cases.jsonl")
        version_products = rows(input_dir / "products.jsonl")
        version_failures = rows(input_dir / "failures.jsonl")
        product_index = {(str(p["dossier_id"]), str(p["au_product"]["product_id"])): p for p in version_products}
        if len(product_index) != len(version_products):
            raise ValueError("duplicate product source identity")
        for case in version_cases:
            dossier = str(case["dossier_id"])
            grp = groupmap.get(dossier)
            if not grp or grp.get("excluded") or grp.get("split") not in {"dev", "test"}:
                raise ValueError("case lacks frozen eligible split binding")
            c = dict(case)
            c["_source_version"] = version
            c["_split"] = grp["split"]
            cases.append(c)
        for p in version_products:
            q = dict(p)
            q["_source_version"] = version
            products.append(q)
        for f in version_failures:
            # Keep full failure source row in coverage file; never merge into successful cases.
            failures.append({"source_version": version, "failure": f})
    selected_cases = [c for c in cases if c["_split"] == split]
    case_product = {(str(p["dossier_id"]), str(p["au_product"]["product_id"])): p for p in products}
    return inv, selected_cases, products, case_product, failures, hashes


def normalize_paths(value):
    if isinstance(value, dict):
        return {k: (v.replace("\\", "/") if k == "raw_file" and isinstance(v, str) else normalize_paths(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_paths(v) for v in value]
    return value


def verify_raw_spans(source_root: Path, values, sources_module):
    store = sources_module.RawStore(source_root)
    count = 0
    for value in values:
        for span in recursive_spans(value):
            count += 1
            try:
                normalized = dict(span)
                normalized["raw_file"] = str(span["raw_file"]).replace("\\", "/")
                good = sources_module.verify_span(store, normalized)
            except Exception as exc:
                raise ValueError("source-span verification failed: " + str(span.get("raw_file", "unknown"))) from exc
            if not good:
                raise ValueError("source-span verification failed: " + str(span.get("raw_file", "unknown")))
    return count


def prepare(prep_dir: Path, output_dir: Path):
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite " + str(output_dir))
    source_root = prep_dir / "source"
    # The source-only adapter preserves original workspace-relative paths below source/.
    source_root = source_root.resolve()
    sys.path.insert(0, str(ROOT / "experiments/sku-matching/claude_v9_gate"))
    import sku_gate_sources as gate_sources
    spec = importlib.util.spec_from_file_location("generic_sku_adapter", GENERIC_PATH)
    generic = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(generic)
    generic.ROOT = source_root
    source_dir = source_root / ".lab-output/sku-independent-real-20261011"
    inv = json.loads((prep_dir / "inventory.json").read_text(encoding="utf-8"))
    allcases, allproducts, allfailures = [], [], []
    input_hashes = {}
    product_index = {}
    for version in ("v2", "v3"):
        ent = inv["splits"][version]
        d = source_dir / f"eval-{version}/run-token/inputs"
        for name, specrow in ent["input_members"].items():
            path = d / name
            if sha_path(path) != specrow["sha256"]:
                raise ValueError("input SHA mismatch: " + path.name)
            input_hashes[str(path.relative_to(source_root))] = specrow["sha256"]
        cases = rows(d / "cases.jsonl")
        products = rows(d / "products.jsonl")
        fails = rows(d / "failures.jsonl")
        group_path = source_dir / f"eval-{version}/groups-token/groups.jsonl"
        group_map = {}
        for record in rows(group_path):
            obj = record.get("group", record)
            dossier = str(obj.get("dossier_id", record.get("dossier_id", "")))
            if not dossier or dossier in group_map:
                raise ValueError("frozen group-map dossier binding invalid")
            group_map[dossier] = obj
        for product in products:
            key = (str(product["dossier_id"]), str(product["au_product"]["product_id"]))
            if key in product_index:
                raise ValueError("duplicate frozen product identity")
            product_index[key] = (version, product)
        for case in cases:
            group = group_map.get(str(case["dossier_id"]))
            if not group or group.get("excluded") or group.get("split") not in {"dev", "test"}:
                raise ValueError("case lacks frozen group split")
            case = dict(case)
            case["_source_version"] = version
            case["_split"] = group["split"]
            case["_case_sha256"] = sha_path(d / "cases.jsonl")
            allcases.append(case)
        product_sha = sha_path(d / "products.jsonl")
        for product in products:
            product = dict(product)
            product["_source_version"] = version
            product["_product_sha256"] = product_sha
            allproducts.append(product)
        allfailures.extend({"source_version": version, "failure": f} for f in fails)
    expected_splits = {"dev": 1370, "test": 1292}
    if Counter(c["_split"] for c in allcases) != expected_splits:
        raise ValueError("frozen group split population differs")
    # Verify all original typed source spans before generating requests.
    span_count = verify_raw_spans(source_root, allcases + allproducts, gate_sources)

    out = {"dev": output_dir / "dev", "test": output_dir / "test"}
    for p in out.values():
        p.mkdir(parents=True, exist_ok=False)
    (output_dir / "coverage").mkdir()
    write_rows(output_dir / "coverage" / "input-failures.jsonl", allfailures)
    manifests, aggregate = {}, {}
    global TASK_ACCUM, REQUEST_ACCUM
    for split in ("dev", "test"):
        TASK_ACCUM = []
        REQUEST_ACCUM = {}
        requests = REQUEST_ACCUM
        tasks = TASK_ACCUM
        cases = [c for c in allcases if c["_split"] == split]
        dossiers = {str(c["dossier_id"]) for c in cases}
        source_products = [p for p in allproducts if str(p["dossier_id"]) in dossiers]
        products = {}
        product_line = {}
        for n, source_product in enumerate(source_products, 1):
            # Raw HTML is read and mechanically parsed into exact source-bound lines; never printed.
            product = generic.make_product(source_root, normalize_paths(source_product))
            products[product["dossier_id"]] = product
            product_line[product["dossier_id"]] = n
        case_rows = [generic.make_case(c, split) for c in cases]
        case_source = {c["case_id"]: c for c in cases}
        docs, requests, tasks, rows_out, case_out = {}, {}, [], [], []
        counters = Counter()
        request_bindings = defaultdict(list)
        model_inputs = {}

        def document_for(dossier, side):
            key = (dossier, side)
            if key not in docs:
                pref = {"path": str((output_dir / split / "products.jsonl").resolve()),
                        "source_path": str((source_dir / f"eval-{case_source[next(c['case_id'] for c in cases if c['dossier_id']==dossier)]['_source_version']}/run-token/inputs/products.jsonl").resolve()),
                        "source_line": next(i for i, p in enumerate(source_products, 1) if p["dossier_id"] == dossier),
                        "source_sha256": input_hashes[str((source_dir / f"eval-{case_source[next(c['case_id'] for c in cases if c['dossier_id']==dossier)]['_source_version']}/run-token/inputs/products.jsonl").relative_to(source_root))]}
                docs[key] = generic_doc(products[dossier], side, pref)
            return docs[key]

        for case in cases:
            case = normalize_paths(case)
            c = generic.make_case(case, split)
            c.update({"_source_version": case["_source_version"], "_case_sha256": case["_case_sha256"],
                      "_product_sha256": next(p["_product_sha256"] for p in allproducts if p["dossier_id"] == case["dossier_id"] and p["_source_version"] == case["_source_version"])})
            product = products[c["dossier_id"]]
            if product["au"]["product_id"] != c["au_product_id"]:
                raise ValueError("case fixed AU product binding differs")
            case["_product_sha256"] = next(p["_product_sha256"] for p in allproducts if p["dossier_id"] == case["dossier_id"] and p["_source_version"] == case["_source_version"])
            raw_case_sha = sha_path(source_root / case["rakuten_selected"]["raw_file"])
            if raw_case_sha != case["rakuten_selected"]["sha256"]:
                raise ValueError("selected Rakuten source SHA differs")
            au_rows = product["au"]["rows"]
            if not au_rows:
                raise ValueError("product has no captured AU rows")
            rak_axes = c["rakuten"]["axes"]
            if not rak_axes:
                raise ValueError("selected Rakuten SKU has no captured axes")
            all_au_options = ordered_au_options(au_rows)
            # Keep all whole-valued axes. No alignment core/residual or equality is promoted to proof.
            case_bindings = []
            for row_index, row in enumerate(au_rows):
                row_key = row["row_key"]
                row_axes = [{"axis_name": a["axis_name"], "value": a["value"]} for a in row["axes"]]
                row_bindings = []
                # Rakuten -> AU: each complete Rakuten axis, against this complete target AU row.
                for ra in rak_axes:
                    choices = list(ra["family_values"])
                    if not choices or len(set(choices)) != len(choices) or ra["value"] not in choices:
                        raise ValueError("original Rakuten whole option list invalid")
                    doc = document_for(c["dossier_id"], "au")
                    add_task(c, row, "rakuten_to_au", ra, choices, doc, row_axes, row_index, row_bindings, requests, tasks)
                # AU -> Rakuten: every row axis against the complete selected Rakuten SKU axes.
                for au_axis_index, aa in enumerate(row["axes"]):
                    choices = all_au_options.get(aa["axis_name"], [])
                    if not choices or aa["value"] not in choices:
                        raise ValueError("AU full product option list missing selected row value")
                    doc = document_for(c["dossier_id"], "rakuten")
                    rak_target = [{"axis_name": a["axis_label"], "value": a["value"]} for a in rak_axes]
                    indexed_axis = {**aa, "axis_key": "au-axis-index:" + str(au_axis_index)}
                    add_task(c, row, "au_to_rakuten", indexed_axis, choices, doc, rak_target, row_index, row_bindings, requests, tasks)
                row_out = {"case_id": c["case_id"], "source_version": case["_source_version"], "split": split,
                           "dossier_id": c["dossier_id"], "au_product_id": c["au_product_id"], "au_row_key": row_key,
                           "au_row_index": row_index, "au_row_sku_id": row.get("sku_id"),
                           "au_axis_count": len(row_axes), "rakuten_axis_count": len(rak_axes),
                           "au_row_axis_source_spans": [a.get("value_span") for a in row["axes"]],
                           "bindings": row_bindings, "row_retained_regardless_of_stock": True,
                           "strict_typed_proof_count": 0}
                rows_out.append(row_out)
                case_bindings.extend(row_bindings)
                counters["au_rows"] += 1
            case_out.append({"case_id": c["case_id"], "source_version": case["_source_version"], "split": split,
                             "dossier_id": c["dossier_id"], "au_product_id": c["au_product_id"],
                             "source_sku_key": c["rakuten"]["source_sku_key"], "sku_record_key": c["rakuten"]["sku_record_key"],
                             "variant_id": c["rakuten"]["variant_id"], "raw_selected_sku_source_sha256": raw_case_sha,
                             "total_au_rows": len(au_rows), "rakuten_axis_count": len(rak_axes),
                             "total_task_bindings": len(case_bindings), "row_keys": [r["row_key"] for r in au_rows],
                             "all_original_rakuten_axes_covered": True, "all_original_au_rows_covered": len(case_bindings)>0,
                             "strict_core_residual_proof_used": False, "typed_proof_used": False})
        # Build exact model-input identities without the wanted selected value.
        # Original request/task bindings remain separate so fan-out is lossless.
        model_input_links = []
        for rid, request in requests.items():
            model_semantic = {k: request[k] for k in (
                "direction", "axis_name", "option_values", "target_document_id",
                "target_document_sha256", "target_selected_axes", "target_selected_axes_text")}
            mid = "condition-model-input:" + digest(model_semantic)
            request["model_input_id"] = mid
            model_inputs.setdefault(mid, {"model_input_id": mid, **model_semantic,
                "model_input_sha256": digest(model_semantic), "selected_value_in_model_input": False,
                "model_input_version": "fixed-condition-whole-options-v2"})
            model_input_links.append({"request_id": rid, "model_input_id": mid})
        for task in tasks:
            task["model_input_id"] = requests[task["request_id"]]["model_input_id"]
            request_bindings[task["request_id"]].append(task["task_id"])
        comp_units = []
        comp_bindings = []
        for rid, request in requests.items():
            comp_units.append({"group_id": rid, "kind": "paired", "axis_name": request["axis_name"],
                "option_values": list(request["option_values"]), "target_document_id": request["target_document_id"],
                "target_selected_axes_text": request["target_selected_axes_text"],
                "source_request": {"request_id": rid, "target_document_id": request["target_document_id"],
                    "axis_name": request["axis_name"], "option_values": request["option_values"],
                    "selected_value": request["selected_value"],
                    "target_selected_axes_text": request["target_selected_axes_text"]},
                "model_input_id": request["model_input_id"], "production_adoption_allowed": False})
            comp_bindings.append({"group_id": rid, "model_input_id": request["model_input_id"],
                                  "task_ids": request_bindings[rid]})
        write_rows(out[split] / "documents.jsonl", [docs[k] for k in sorted(docs)])
        write_rows(out[split] / "requests.jsonl", list(requests.values()))
        write_rows(out[split] / "tasks.jsonl", tasks)
        write_rows(out[split] / "row-plans.jsonl", rows_out)
        write_rows(out[split] / "case-plans.jsonl", case_out)
        write_rows(out[split] / "baseline-model-inputs.jsonl", list(model_inputs.values()))
        write_rows(out[split] / "request-model-input-map.jsonl", model_input_links)
        write_rows(out[split] / "composition-units.jsonl", comp_units)
        write_rows(out[split] / "composition-unit-bindings.jsonl", comp_bindings)
        names = ("documents.jsonl", "requests.jsonl", "tasks.jsonl", "row-plans.jsonl", "case-plans.jsonl",
                 "baseline-model-inputs.jsonl", "request-model-input-map.jsonl", "composition-units.jsonl",
                 "composition-unit-bindings.jsonl")
        manifest = {"schema_version": "full-condition-inputs-v2", "split": split,
                    "independent_test": split == "test", "labels_read": False, "model_inference_run": False,
                    "source_inputs": input_hashes, "source_archive_sha256": inv["archive_verification"],
                    "preparer_code_sha256": sha_path(Path(__file__)), "source_span_count_verified": span_count,
                    "case_count": len(case_out), "product_count": len({x["dossier_id"] for x in case_out}),
                    "row_plan_count": len(rows_out), "task_binding_count": len(tasks),
                    "unique_request_count": len(requests), "unique_baseline_model_input_count": len(model_inputs),
                    "composition_unit_count": len(comp_units), "composition_task_binding_count": sum(len(x["task_ids"]) for x in comp_bindings),
                    "counts": dict(counters), "outputs_sha256": {n: sha_path(out[split] / n) for n in names},
                    "all_AU_rows_including_stock_states_retained": True,
                    "all_original_option_order_preserved": True,
                    "shared_one_sided_units": 0,
                    "shared_one_sided_blocker": "No full-population source alignment artifact/API has been verified to bind genuine one-sided Rakuten axes. No axis is inferred one-sided from missing string-name matches; per-unit shared spec inputs are therefore withheld pending explicit alignment semantics.",
                    "strict_core_residual_proofs": 0, "whole_raw_equal_identity_proofs": 0,
                    "typed_proofs": 0, "no_dev_prediction_reuse": True,
                    "model_stages": {"baseline": ["fixed-JEV", "fixed-NLI", "strict-typed-proof"],
                                     "hosted_composition": "full-context composition unit per paired row-condition; no inference"},
                    "model_pins": model_pins(), "failure_rows": len(allfailures),
                    "failure_rows_are_separate_coverage": True, "production_adoption_allowed": False}
        write_json(out[split] / "manifest.json", manifest)
        manifests[split] = manifest
        aggregate[split] = {k: manifest[k] for k in ("case_count", "product_count", "row_plan_count", "task_binding_count", "unique_request_count",
                                                       "unique_baseline_model_input_count", "composition_unit_count", "composition_task_binding_count")}
    write_json(output_dir / "coverage" / "failure-manifest.json", {"failure_count": len(allfailures), "sha256": sha_path(output_dir / "coverage" / "input-failures.jsonl"), "labels_read": False})
    root_manifest = {"schema_version": "full-condition-adapter-v2", "splits": aggregate,
                     "source_span_count_verified": span_count, "input_failures": len(allfailures),
                     "missing_referenced_sources": [], "labels_read": False, "model_inference_run": False,
                     "limitations": ["One-sided shared product-spec units are withheld because no full-population alignment artifact proves which Rakuten conditions are genuinely one-sided.",
                                     "Strict core/residual and typed identity shortcuts are all disabled; every condition remains an inference task."],
                     "independent_test_ready_for_frozen_protocol_only": True}
    write_json(output_dir / "manifest.json", root_manifest)
    return root_manifest


def generic_doc(product, side, product_ref):
    # Use the canonical document constructor without importing or changing it.
    import importlib.util
    path = ROOT / "experiments/sku-matching/integrate_claude_description_v1.py"
    spec = importlib.util.spec_from_file_location("integrate_full_sku", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # It imports another module by file-local name.
    sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
    spec.loader.exec_module(mod)
    return mod.document(product, side, product_ref)


def ordered_au_options(rows_in):
    result = defaultdict(list)
    for row in rows_in:
        for axis in row["axes"]:
            if axis["value"] not in result[axis["axis_name"]]:
                result[axis["axis_name"]].append(axis["value"])
    return dict(result)


def add_task(case, row, direction, raw_axis, options, doc, target_axes, row_index, bindings, requests, tasks):
    axis_name = raw_axis["axis_label"] if direction == "rakuten_to_au" else raw_axis["axis_name"]
    value = raw_axis["value"]
    if value not in options:
        raise ValueError("raw selected value is missing from ordered whole options")
    from importlib.util import spec_from_file_location, module_from_spec
    integrate_path = ROOT / "experiments/sku-matching/integrate_claude_description_v1.py"
    key = "_full_integrate"
    mod = sys.modules.get(key)
    if mod is None:
        spec = spec_from_file_location(key, integrate_path)
        mod = module_from_spec(spec)
        sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
        spec.loader.exec_module(mod)
        sys.modules[key] = mod
    side_sem = {"direction": direction, "axis_name": axis_name, "selected_value": value,
                "option_values": list(options), "target_document_id": doc["document_id"],
                "target_document_sha256": doc["document_sha256"], "target_selected_axes": target_axes,
                "target_selected_axes_text": mod.axis_text(target_axes)}
    rh = digest(side_sem)
    request_id = "generic-description:" + rh
    request = {"request_id": request_id, "request_sha256": rh, **side_sem,
               "selected_value_is_binding_only": True, "applicability_required": True,
               "production_adoption_allowed": False, "strict_typed_proof": False}
    requests.setdefault(request_id, request)
    identity = {"case_id": case["case_id"], "au_row_key": row["row_key"], "direction": direction,
                "axis_key": raw_axis.get("axis_key", raw_axis.get("axis_name"))}
    task_id = "full-condition:" + digest(identity)
    source_refs = [raw_axis.get("value_span")]
    if raw_axis.get("axis_label_span"):
        source_refs.append(raw_axis["axis_label_span"])
    provenance = {"source_version": case["_source_version"], "case_source_sha256": case["_case_sha256"],
                  "product_source_sha256": case["_product_sha256"], "source_condition_spans": source_refs,
                  "row_array_source": row.get("array_source"), "row_key": row["row_key"]}
    raw_condition_ref = {"axis_key": identity["axis_key"], "axis_name": axis_name, "value": value,
                         "value_span": raw_axis.get("value_span")}
    if direction == "rakuten_to_au":
        raw_condition_ref["axis_label"] = axis_name
        raw_condition_ref["axis_label_span"] = raw_axis.get("axis_label_span")
    task = {"task_id": task_id, **identity, "request_id": request_id, "dossier_id": case["dossier_id"],
            "au_product_id": case["au_product_id"], "source_sku_key": case["rakuten"]["source_sku_key"],
            "raw_condition_ref": raw_condition_ref, "provenance": provenance,
            "origin": "full_raw_condition_unaligned", "status": "not_evaluated", "verification_required": True,
            "strict_core_residual_proof": False, "strict_typed_proof": False, "production_adoption_allowed": False}
    tasks.append(task)
    binding = {"task_id": task_id, "request_id": request_id, "direction": direction,
               "axis_key": identity["axis_key"]}
    bindings.append(binding)
    return binding


def model_pins():
    # Source metadata only. This reads static code/config pins and does not load weights.
    import importlib.util
    path = ROOT / "experiments/sku-matching/trial_product_condition_models_v1.py"
    spec = importlib.util.spec_from_file_location("full_condition_model_pin_metadata", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
    spec.loader.exec_module(mod)
    return {"fixed_JEV": mod.JEV._pins(),
            "fixed_NLI": {"model_id": mod.NLI.MODEL_ID, "revision": mod.NLI.MODEL_REVISION,
                          "parameter_count": mod.NLI.FP32_PARAMETER_COUNT, "files_sha256": mod.NLI.NLI_FILE_SHA256},
            "pin_use": "metadata only; no model weights loaded or inference run"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prep-dir", type=Path, default=PREP_DEFAULT)
    ap.add_argument("--output-dir", type=Path, default=OUT_DEFAULT)
    args = ap.parse_args(argv)
    result = prepare(args.prep_dir.resolve(), args.output_dir.resolve())
    # Counts and coverage only; never emit case IDs, values, task contents, or predictions.
    print(json.dumps({"schema_version": result["schema_version"], "splits": result["splits"],
                      "source_span_count_verified": result["source_span_count_verified"],
                      "input_failures": result["input_failures"], "labels_read": False,
                      "model_inference_run": False, "limitations": result["limitations"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # Lists are initialized on function objects to keep all identities deterministic.
    add_task.requests = {}
    add_task.tasks = []
    add_task.counter = Counter()
    raise SystemExit(main())
