"""Apply source-bound CPU relation proposals through the fixed-pair SKU gate."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import sku_integrated_gate_v1 as gate
from prepare_cpu_requirement_tasks_v1 import hypothesis, eligible_scope, noun_for
from run_integrated_sku_gate_v1 import DEFAULT_INPUT, HERE, ROOT, compact, dump, read, sha, write_rows
import trial_cpu_requirement_relations_v1 as trial

THRESHOLD = 0.90
RELATIONS = ("support", "conflict", "unknown")
TASK_FIELDS = ("type", "value", "component", "axis_label", "axis_value_quote")
modules = gate.load_gate()


def indexed(rows, key):
    result = {}
    for row in rows:
        ident = row.get(key)
        if not isinstance(ident, str) or not ident or ident in result:
            raise ValueError("Invalid or duplicate " + key)
        result[ident] = row
    return result


def semantic(req):
    """Deduplicated tasks share a claim, while each target keeps its own axis proof."""
    return {k: req.get(k) for k in ("type", "value", "component")}


def eligible(req):
    return req.get("type") == "component_presence" and req.get("value") is True


def verify_files(directory, manifest, required):
    files = manifest.get("files", manifest.get("artifacts"))
    if not isinstance(files, dict) or not required <= files.keys():
        raise ValueError("Incomplete file manifest: " + str(directory))
    for name, entry in files.items():
        path = Path(name)
        if path.is_absolute() or len(path.parts) != 1 or name in (".", ".."):
            raise ValueError("Manifest file is not a local basename")
        digest = entry.get("sha256") if isinstance(entry, dict) else entry
        if (sha(directory / name) != digest
                or isinstance(entry, dict) and (directory / name).stat().st_size != entry.get("size_bytes")):
            raise ValueError("Changed artifact: " + str(directory / name))


def fixed_evidence(task, context, store):
    """A model may cite only a complete literal line from this fixed AU product."""
    evidence = task.get("evidence", {})
    span, scope = evidence.get("span"), task.get("source_scope")
    product = context["au_product"]
    if not isinstance(span, dict) or not isinstance(scope, dict):
        return False
    if evidence.get("source_scope") != scope:
        return False
    if (scope.get("kind") != "fixed_au_product"
            or scope.get("au_product_id") != product["product_id"]
            or span.get("raw_file") != product["raw_file"]
            or span.get("sha256") != product["sha256"]
            or span.get("quote") != evidence.get("quote")):
        return False
    if not modules.src.verify_span(store, span):
        return False
    lines = [{"text": product["title"], "span": product.get("title_span"),
              "scope_tag": "fixed_au_title"}] + context["au_description_lines"]
    noun = noun_for(task["requirement"], modules)
    # A page-level statement cannot fill a component that changes between AU
    # selectable rows. For those components, require a future row-scoped task.
    if noun and any(noun in str(axis.get("value", "")) or noun in str(axis.get("axis_name", ""))
                    for row in context["au_rows"] for axis in row["axes"]):
        return False
    return bool(noun) and noun in evidence["quote"] and any(
        eligible_scope(line) and line.get("span") == span
        and line.get("text") == evidence["quote"]
        and line.get("scope_tag") == scope.get("scope_tag") for line in lines)


def validated_record(task, record):
    """Recompute the frozen threshold decision; reject edited prompts or bindings."""
    for name in ("task_id", "case_id", "au_row_key", "requirement", "hypothesis"):
        if record.get(name) != task.get(name):
            raise ValueError("Model/task binding mismatch: " + name)
    binding = {k: task["evidence"].get(k) for k in ("evidence_id", "quote", "span")}
    binding["source_scope"] = task["source_scope"]
    if record.get("evidence_binding") != binding:
        raise ValueError("Model evidence binding mismatch")
    if record.get("confidence_threshold") != THRESHOLD:
        raise ValueError("Model threshold differs from the frozen policy")
    probs = record.get("probabilities")
    predicted, confidence = record.get("predicted_class"), record.get("predicted_confidence")
    if probs is None:
        if predicted != "unknown" or confidence is not None or record.get("proposal") != "unknown":
            raise ValueError("Unscored model record must remain unknown")
        return "unknown"
    if not isinstance(probs, dict) or set(probs) != set(RELATIONS):
        raise ValueError("Model probabilities need exactly three relation classes")
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or not 0 <= v <= 1 for v in probs.values()):
        raise ValueError("Non-finite or invalid probability")
    if not math.isclose(sum(probs.values()), 1.0, abs_tol=1e-5):
        raise ValueError("Model probabilities do not sum to one")
    if (predicted not in probs or probs[predicted] != max(probs.values())
            or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not math.isclose(confidence, probs[predicted], abs_tol=1e-6)):
        raise ValueError("Model winning class/confidence disagrees with probabilities")
    relation = predicted if confidence >= THRESHOLD and predicted != "unknown" else "unknown"
    if record.get("proposal") != relation:
        raise ValueError("Model proposal disagrees with the frozen threshold")
    if relation != "unknown":
        count = record.get("token_count_untruncated")
        if (isinstance(count, bool) or not isinstance(count, int)
                or not 0 < count <= trial.MAX_TOKENS or record.get("error_type")):
            raise ValueError("Scored proposal lacks a complete, successful model input")
    return relation


def bind_proposals(tasks, targets, records, cases, contexts, baseline, store, model_metadata):
    """Validate origin and target independently so shared tasks cannot reroute a SKU."""
    bound = {}
    for task_id, task in tasks.items():
        trial.validate_task(task)
        origin = cases.get(task["case_id"])
        if origin is None:
            raise ValueError("Task origin case is absent")
        pred = baseline[task["case_id"]]
        if task["au_row_key"] not in {r["row_key"] for r in pred["rows"]}:
            raise ValueError("Task origin AU row is absent")
        origin_reqs = pred["requirements"]
        if not any({k: r.get(k) for k in TASK_FIELDS} == task["requirement"] for r in origin_reqs):
            raise ValueError("Task origin requirement does not exist")
        if task["hypothesis"] != hypothesis(task["requirement"], modules):
            raise ValueError("Task hypothesis differs from the frozen template")
        evidence = task["evidence"]
        if evidence.get("span", {}).get("quote") != evidence.get("quote") or not modules.src.verify_span(store, evidence["span"]):
            raise ValueError("Task evidence is not a literal source quote")
        validated_record(task, records[task_id])
    seen = set()
    for target in targets:
        if target.get("purpose") != "unresolved_presence":
            raise ValueError("Only unresolved presence targets can alter SKU evaluation")
        task_id, case_id = target.get("task_id"), target.get("case_id")
        task, case = tasks.get(task_id), cases.get(case_id)
        if task is None or case is None:
            raise ValueError("Target references an absent task or case")
        key = (case_id, target.get("au_row_key"), target.get("requirement_id"))
        if (*key, task_id) in seen:
            raise ValueError("Duplicate completion target")
        seen.add((*key, task_id))
        pred = baseline[case_id]
        row = next((r for r in pred["rows"] if r["row_key"] == key[1]), None)
        req = next((r for r in pred["requirements"] if r["requirement_id"] == key[2]), None)
        atom_result = next((r for r in (row or {}).get("atom_results", []) if r["requirement_id"] == key[2]), None)
        if (pred["decision"] != "drop" or row is None or row["status"] == "conflict"
                or req is None or not eligible(req) or atom_result is None or atom_result["status"] != "unknown"):
            raise ValueError("Completion target is not an unknown positive presence condition")
        if (semantic(req) != semantic(task["requirement"])
                or task["hypothesis"] != hypothesis(req, modules)):
            raise ValueError("Target and task requirements differ")
        context = contexts[case["dossier_id"]]
        origin_context = contexts[cases[task["case_id"]]["dossier_id"]]
        if not fixed_evidence(task, context, store) or not fixed_evidence(task, origin_context, store):
            raise ValueError("Completion evidence is outside the origin or target fixed AU page")
        if not modules.src.verify_span(store, req.get("span", {})) or req["span"].get("quote") != req.get("quote"):
            raise ValueError("Target requirement lacks its literal selected SKU source")
        relation = validated_record(task, records[task_id])
        if relation == "unknown":
            continue
        record = records[task_id]
        item = {"task_id": task_id, "relation": relation,
                "source": "cpu_model_quote", "scope": "fixed_au_product",
                "quote": task["evidence"]["quote"], "span": task["evidence"]["span"],
                "source_scope": task["source_scope"], "hypothesis": task["hypothesis"],
                "confidence": record["predicted_confidence"], "model": model_metadata,
                "target_requirement_semantic": semantic(req), "evidence_id": task["evidence"]["evidence_id"]}
        bound.setdefault(key, []).append(item)
    return bound


class ProposalEvaluator(modules.Evaluator):
    """Case-local completion; original proven relations always retain precedence."""
    def __init__(self, facts, case_id, proposals):
        super().__init__(facts, modules.SOURCE_CONFIGS[modules.PRIMARY_CONFIG], require_verified=False)
        self.case_id, self.proposals = case_id, proposals
        self.completions = {}

    def evaluate_cached(self, req, row, facts):
        # The upstream cache intentionally omits case/requirement provenance.
        # Completion must retain that provenance, so use a case-local full card key.
        key = (self.case_id, req["requirement_id"], row["row_key"], modules.canonical_json(req))
        if key not in self._eval_cache:
            self._eval_cache[key] = self.evaluate(req, row, facts)
        return self._eval_cache[key]

    def evaluate(self, req, row, product_facts):
        result = super().evaluate(req, row, product_facts)
        if result["status"] != "unknown" or not eligible(req):
            return result
        key = (self.case_id, row["row_key"], req["requirement_id"])
        proposals = [p for p in self.proposals.get(key, []) if p.get("target_requirement_semantic") == semantic(req)]
        relations = {p["relation"] for p in proposals}
        if not relations or relations == {"support", "conflict"}:
            if len(relations) > 1:
                self.completions[key] = "disagreeing_quotes_unknown"
            return result
        relation = next(iter(relations))
        self.completions[key] = relation
        return {"status": relation, "evidence": proposals,
                "note": "source_bound_cpu_model_completion_of_unknown_presence",
                "structural_result_before_completion": result}


def verify_model_freeze(freeze, model_run):
    if (freeze.get("threshold_frozen_before_inference") != THRESHOLD
            or freeze.get("relation_classes") != list(RELATIONS)
            or freeze.get("max_tokens") != trial.MAX_TOKENS
            or freeze.get("outputs_are_proposals_only") is not True
            or freeze.get("cpu_enforced") is not True
            or freeze.get("device") != "cpu" or freeze.get("gpu_used") is not False
            or freeze.get("runner_sha256") != sha(model_run / "runner.py")):
        raise ValueError("Model runner or frozen decision policy changed")
    backend, pin = freeze.get("backend"), freeze.get("model", {})
    expected = {"nli": (trial.MODEL_ID, trial.MODEL_REVISION),
                "gliner-decide": (trial.GLINER_ID, trial.GLINER_REVISION)}
    if backend not in expected or (pin.get("model_id"), pin.get("revision")) != expected[backend]:
        raise ValueError("Unsupported or changed model pin")
    if backend == "nli":
        if {name: value.get("sha256") for name, value in pin.get("files", {}).items()} != trial.NLI_FILE_SHA256:
            raise ValueError("NLI model file pin changed")
    else:
        source = trial.DEFAULT_GLINER / "source-manifest.json"
        source_manifest = json.loads(source.read_text())
        if (pin.get("source_manifest_sha256") != sha(source)
                or pin.get("files") != source_manifest["files"]):
            raise ValueError("GLiNER model files changed")


def verify_gliner_schema(freeze, tasks):
    if freeze["backend"] != "gliner-decide":
        return
    mode = freeze.get("gliner_task")
    schema = freeze.get("backend_metadata_before_inference", {}).get("task_schema")
    if (mode not in ("relation", "component") or not isinstance(schema, dict)
            or schema.get("mode") != mode or freeze["model"].get("task_mode") != mode):
        raise ValueError("GLiNER task mode is not a supported frozen schema")
    if mode == "relation":
        expected = {"mode": "relation", "task_name": "relation",
            "instruction": "Classify whether the Japanese requirement is supported, contradicted, or unstated by the literal evidence quote.",
            "candidate_labels": list(trial.GLINER_CHOICES),
            "candidate_label_mapping": trial.GLINER_LABEL_TO_RELATION,
            "model_text_template": "quote plus hypothesis"}
        if schema != expected:
            raise ValueError("GLiNER relation schema differs from the declared template")
        return
    specs = indexed(schema.get("per_task_schema", []), "task_id")
    if (set(specs) != set(tasks) or schema.get("candidate_labels") != list(trial.COMPONENT_CHOICES)
            or schema.get("model_text_template") != "literal quote only; hypothesis appears only in schema instruction"):
        raise ValueError("GLiNER component schema has missing tasks or changed labels")
    for task_id, task in tasks.items():
        noun = noun_for(task["requirement"], modules)
        if not noun:
            raise ValueError("GLiNER component schema has no source requirement noun")
        expected = {"task_id": task_id, "component_noun": noun,
            "expected_present": task["requirement"]["value"], "task_name": noun + "の有無",
            "hypothesis": task["hypothesis"],
            "instruction": trial.component_schema_instruction(task["hypothesis"], noun),
            "candidate_labels": list(trial.COMPONENT_CHOICES),
            "candidate_label_mapping": trial.component_relation_map(task["requirement"]["value"])}
        if specs[task_id] != expected:
            raise ValueError("GLiNER component schema differs from the source-bound task")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--gate-run", type=Path, required=True)
    parser.add_argument("--tasks-run", type=Path, required=True)
    parser.add_argument("--model-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    input_meta = json.loads((args.input / "manifest.json").read_text())
    for name, digest in input_meta["output_sha256"].items():
        if sha(args.input.parent / name) != digest:
            raise ValueError("Prepared input changed: " + name)
    for directory, required in ((args.gate_run, {"freeze.json", "predictions.jsonl", "summary.json"}),
                                (args.tasks_run, {"tasks.jsonl", "targets.jsonl", "structural-controls.jsonl"}),
                                (args.model_run, {"freeze.json", "predictions.jsonl", "summary.json", "runner.py"})):
        verify_files(directory, json.loads((directory / "manifest.json").read_text()), required)
    gate_freeze = json.loads((args.gate_run / "freeze.json").read_text())
    task_manifest = json.loads((args.tasks_run / "manifest.json").read_text())
    model_freeze = json.loads((args.model_run / "freeze.json").read_text())
    input_sha = {name: sha(args.input / name) for name in ("cases.jsonl", "products.jsonl", "manifest.json")}
    if gate_freeze["input_sha256"] != input_sha:
        raise ValueError("Source gate used different fixed inputs")
    for name, digest in gate_freeze["code_sha256"].items():
        if sha(ROOT / name) != digest:
            raise ValueError("Source gate code changed: " + name)
    if (task_manifest["inputs_sha256"] != {k: input_sha[k] for k in ("cases.jsonl", "products.jsonl")}
            or task_manifest["source_gate_predictions_sha256"] != sha(args.gate_run / "predictions.jsonl")
            or task_manifest["code_sha256"] != sha(HERE / "prepare_cpu_requirement_tasks_v1.py")):
        raise ValueError("Task export inputs, source gate, or exporter changed")
    if model_freeze["input_sha256"] != sha(args.tasks_run / "tasks.jsonl"):
        raise ValueError("Model used a different task input")
    verify_model_freeze(model_freeze, args.model_run)
    cases = indexed(read(args.input / "cases.jsonl"), "case_id")
    contexts = indexed(read(args.input / "products.jsonl"), "dossier_id")
    baseline = indexed(read(args.gate_run / "predictions.jsonl"), "case_id")
    tasks = indexed(read(args.tasks_run / "tasks.jsonl"), "task_id")
    records = indexed(read(args.model_run / "predictions.jsonl"), "task_id")
    if set(baseline) != set(cases) or set(records) != set(tasks) or model_freeze["task_count"] != len(tasks):
        raise ValueError("Missing or added cases/tasks/predictions")
    verify_gliner_schema(model_freeze, tasks)
    for case_id, case in cases.items():
        context, pred = contexts[case["dossier_id"]], baseline[case_id]
        expected_rows = {r["row_key"] for r in context["au_rows"]}
        rows = indexed(pred["rows"], "row_key")
        if (set(rows) != expected_rows or pred["dossier_id"] != case["dossier_id"]
                or case["au_product_id"] != context["au_product"]["product_id"]
                or pred["au_product_id"] != case["au_product_id"]):
            raise ValueError("Baseline does not preserve the fixed AU candidate pool")
    store = modules.src.RawStore(ROOT)
    model_metadata = {"backend": model_freeze["backend"], "model_id": model_freeze["model"]["model_id"],
                      "revision": model_freeze["model"]["revision"], "threshold": THRESHOLD,
                      "task_mode": model_freeze.get("gliner_task"),
                      "model_run_freeze_sha256": sha(args.model_run / "freeze.json")}
    if any(r.get("backend") != model_metadata["backend"] for r in records.values()):
        raise ValueError("Prediction backend differs from the frozen model")
    proposals = bind_proposals(tasks, read(args.tasks_run / "targets.jsonl"), records,
                              cases, contexts, baseline, store, model_metadata)
    code_paths = set(gate_freeze["code_sha256"]) | {
        str(Path(__file__).relative_to(ROOT)), "experiments/sku-matching/prepare_cpu_requirement_tasks_v1.py",
        "experiments/sku-matching/trial_cpu_requirement_relations_v1.py"}
    frozen_code = {name: sha(ROOT / name) for name in sorted(code_paths)}
    artifacts = {str(directory / name): sha(directory / name) for directory, names in (
        (args.input, ("cases.jsonl", "products.jsonl", "manifest.json")),
        (args.gate_run, ("freeze.json", "predictions.jsonl", "manifest.json")),
        (args.tasks_run, ("tasks.jsonl", "targets.jsonl", "structural-controls.jsonl", "manifest.json")),
        (args.model_run, ("freeze.json", "predictions.jsonl", "manifest.json", "runner.py"))) for name in names}
    args.output.mkdir(parents=True)
    with (args.output / "runner.py").open("xb") as stream:
        stream.write(Path(__file__).read_bytes())
    dump(args.output / "freeze.json", {"at_utc": datetime.now(timezone.utc).isoformat(),
        "labels_opened": False, "development_inputs": True, "model": model_metadata,
        "contract": "one priced Rakuten SKU, all rows of one fixed AU URL; binary accept/drop",
        "policy": "complete unknown positive component presence only; literal fixed AU quote; threshold 0.90; competing quotes stay unknown; no rerouting",
        "code_sha256": frozen_code, "artifact_sha256": artifacts})
    facts = {k: modules.PairFacts(c) for k, c in contexts.items()}
    source_evaluators = {k: modules.make_evaluator("A", fact, modules.PRIMARY_CONFIG)
                         for k, fact in facts.items()}
    targeted_cases = {key[0] for key in proposals}
    predictions, changes, completed = [], [], Counter()
    started = time.perf_counter()
    for case_id, case in cases.items():
        fact = facts[case["dossier_id"]]
        source_result = compact(gate.predict_case(case, fact,
            evaluator=source_evaluators[case["dossier_id"]], store=store))
        if source_result != baseline[case_id]:
            raise ValueError("Recomputed source gate disagrees with baseline: " + case_id)
        completions = {}
        if case_id in targeted_cases:
            evaluator = ProposalEvaluator(fact, case_id, proposals)
            result = compact(gate.predict_case(case, fact, evaluator=evaluator, store=store))
            completions = evaluator.completions
        else:
            # A case with no usable proposal has exactly the source-only gate.
            result = dict(source_result)
        if {r["row_key"] for r in result["rows"]} != {r["row_key"] for r in fact.rows}:
            raise ValueError("Model completion lost an AU row")
        if source_result["decision"] == "accept" and (
                result["decision"] != "accept" or result["au_row_key"] != source_result["au_row_key"]):
            raise ValueError("Model completion altered a structurally accepted SKU")
        completed.update(completions.values())
        result["cpu_model_completion"] = {"model": model_metadata,
            "conditions_completed": sum(v in ("support", "conflict") for v in completions.values()),
            "disagreeing_quotes": sum(v == "disagreeing_quotes_unknown" for v in completions.values())}
        predictions.append(result)
        if (source_result["decision"], source_result["au_row_key"]) != (result["decision"], result["au_row_key"]):
            changes.append({"case_id": case_id, "dossier_id": case["dossier_id"],
                "au_product_id": case["au_product_id"], "before": source_result["decision"],
                "after": result["decision"], "au_row_key": result["au_row_key"], "reason": result["reason"]})
    for name, digest in frozen_code.items():
        if sha(ROOT / name) != digest:
            raise ValueError("Code changed during application: " + name)
    for name, digest in artifacts.items():
        if sha(Path(name)) != digest:
            raise ValueError("Input artifact changed during application: " + name)
    write_rows(args.output / "predictions.jsonl", predictions)
    write_rows(args.output / "changes.jsonl", changes)
    by_id = {r["case_id"]: r for r in predictions}
    groups = {name: {"cases": len(bundle["case_ids"]),
                    "counts": dict(Counter(by_id[c]["decision"] for c in bundle["case_ids"])),
                    "changed_cases": sum(c in {r["case_id"] for r in changes} for c in bundle["case_ids"])}
              for name, bundle in input_meta["bundle_inputs"].items()}
    summary = {"groups": groups, "elapsed_seconds": time.perf_counter() - started,
        "case_count": len(cases), "all_au_row_references": sum(len(p["rows"]) for p in predictions),
        "completion_counts": dict(completed), "bound_proposal_count": sum(map(len, proposals.values())),
        "changed_cases": len(changes), "labels_opened": False, "accuracy_claim": False,
        "raw_source_sha256": store._sha}
    dump(args.output / "summary.json", summary)
    dump(args.output / "manifest.json", {"files": {p.name: sha(p) for p in args.output.iterdir() if p.is_file()}})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
