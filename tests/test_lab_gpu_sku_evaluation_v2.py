"""Focused contract tests for the v6/v7 alias and evidence-ID evaluator."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/evaluate_gpu_sku_trial_v2.py"
spec = importlib.util.spec_from_file_location("evaluate_gpu_sku_trial_v2_test", SCRIPT)
assert spec and spec.loader
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


def evidence(eid: str, side: str, quote: str, *, row_key: str | None = None,
             scope: str = "product_page") -> dict:
    item = {"id": eid, "side": side, "field": "selected_option" if row_key else "title",
            "scope": scope, "quote": quote, "source_ref": {"quote_verbatim": True}}
    if row_key:
        item["row_key"] = row_key
        item["source_ref"]["row_key"] = row_key
    return item


def prediction_case(decision="matched", evidence_ids=None, alias="a000", scope="product_page"):
    row = {"alias": "a000", "row_key": "au:row:0", "sku": "color=blue / size=large"}
    other = {"alias": "a001", "row_key": "au:row:1", "sku": "color=red / size=large"}
    reg = [evidence("rt", "rakuten", "raw Rakuten title"),
           evidence("at", "au", "raw AU title"),
           evidence("rs", "rakuten", row_key=None, quote="color=blue / size=large"),
           evidence("a000", "au", row_key=row["row_key"], quote=row["sku"], scope=scope),
           evidence("a001", "au", row_key=other["row_key"], quote=other["sku"])]
    ids = evidence_ids or (["rs", "a000"] if decision == "matched" else ["rs", "a001"])
    case = {"case_id": "case-1", "au": {"sku_rows": [row, other]},
            "evidence_registry": reg,
            "source_texts": {"rakuten": [x["quote"] for x in reg if x["side"] == "rakuten"],
                             "au": [x["quote"] for x in reg if x["side"] == "au"]}}
    raw_obj = {"decision": decision, "au_row_alias": alias if decision == "matched" else None,
               "evidence_ids": ids, "reason": "selected row matches the stated color and size"}
    raw = json.dumps(raw_obj, ensure_ascii=False)
    registry = {x["id"]: x for x in reg}
    resolved = []
    for eid in ids:
        e = registry[eid]
        resolved.append({k: e[k] for k in ("side", "field", "scope", "source_ref", "quote", "row_key") if k in e} | {"evidence_id": eid})
    parsed = {"decision": decision, "au_row_key": row["row_key"] if decision == "matched" else None,
              "reason": raw_obj["reason"], "evidence": resolved}
    return case, {"case_id": "case-1", "status": "ok", "raw_output": raw,
                  "parsed": parsed, "resolved_evidence": resolved}


class GpuSkuEvaluationV2Tests(unittest.TestCase):
    def test_matching_requires_selected_rakuten_sku_and_exact_au_row_evidence(self):
        case, record = prediction_case()
        self.assertEqual(evaluator.validate_prediction_record(case, record)["prediction"],
                         {"decision": "matched", "au_row_key": "au:row:0"})
        case, record = prediction_case(evidence_ids=["rt", "at"], alias="a000")
        # The raw schema lacks the mandatory selected SKU and selected candidate row citations.
        with self.assertRaisesRegex(ValueError, "selected Rakuten SKU"):
            evaluator.validate_prediction_record(case, record)

    def test_wrong_selected_row_evidence_and_alias_are_rejected(self):
        case, record = prediction_case(evidence_ids=["rs", "a001"], alias="a000")
        with self.assertRaisesRegex(ValueError, "exact selected AU row"):
            evaluator.validate_prediction_record(case, record)

    def test_short_nonempty_reason_is_valid(self):
        case, record = prediction_case()
        p = json.loads(record["raw_output"])
        p["reason"] = "SKU matches"
        record["raw_output"] = json.dumps(p)
        record["parsed"]["reason"] = p["reason"]
        self.assertEqual(evaluator.validate_prediction_record(case, record)["prediction"]["decision"], "matched")
        case, record = prediction_case(evidence_ids=["rs", "a000"], alias="a001")
        with self.assertRaisesRegex(ValueError, "exact selected AU row"):
            evaluator.validate_prediction_record(case, record)

    def test_unmatched_requires_explicit_candidate_row_conflict_evidence(self):
        case, record = prediction_case(decision="unmatched", evidence_ids=["rs", "at"])
        with self.assertRaisesRegex(ValueError, "explicit AU candidate-row conflict"):
            evaluator.validate_prediction_record(case, record)
        case, record = prediction_case(decision="unmatched", evidence_ids=["rs", "a001"])
        self.assertEqual(evaluator.validate_prediction_record(case, record)["prediction"]["decision"], "unmatched")

    def test_decisive_decisions_reject_series_or_sibling_context(self):
        case, record = prediction_case(scope="series_or_sibling_context")
        with self.assertRaisesRegex(ValueError, "non-authoritative"):
            evaluator.validate_prediction_record(case, record)

    def test_duplicate_evidence_id_registry_is_rejected(self):
        case, record = prediction_case()
        case["evidence_registry"].append(dict(case["evidence_registry"][0]))
        with self.assertRaisesRegex(ValueError, "does not resolve"):
            evaluator.validate_prediction_record(case, record)

    def test_invalid_rows_are_retained_as_review(self):
        case, record = prediction_case()
        record.update(status="oom", parsed=None, resolved_evidence=None, raw_output=None)
        result = evaluator.validate_prediction_record(case, record)
        self.assertEqual(result["status"], "oom")
        self.assertEqual(result["prediction"], {"decision": "review", "au_row_key": None})

    def test_nf4_gate_checks_runtime_quantizer_module_counts_and_gpu_placement(self):
        summary = {"model": evaluator.MODEL,
            "config_name": evaluator.MODEL["name"], "config_revision": evaluator.MODEL["revision"],
            "runtime_quantization": "nf4", "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True,
            "is_loaded_in_4bit": True, "nf4_linear4bit_module_count": 25,
            "nf4_quantized_linear4bit_module_count": 25, "base_logical_parameter_count": 1000,
            "nf4_logical_parameter_count": 800, "nf4_coverage_ratio": .8,
            "hf_quantization_config": {"load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
                "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True},
            "attention_implementation": "sdpa", "logits_to_keep_supported": True,
            "parameter_devices": ["cuda:0", "cuda:1"],
            "hf_device_map": {"model.visual": "0", "model.language_model": "1"}}
        evaluator.validate_actual_nf4(summary)
        summary["nf4_quantized_linear4bit_module_count"] = 24
        with self.assertRaisesRegex(ValueError, "complete actual NF4"):
            evaluator.validate_actual_nf4(summary)

    def test_v8_memory_stats_read_per_device_allocated_peaks(self):
        stats = evaluator.latency_memory_stats([
            {"latency_seconds": 1.0, "input_tokens": 10, "output_tokens": 4,
             "peak_allocated_bytes_by_device": [100, 200]},
            {"latency_seconds": 2.0, "input_tokens": 20, "output_tokens": 5,
             "peak_allocated_bytes_by_device": [150, 180]},
            {"latency_seconds": None, "input_tokens": 30, "output_tokens": None},
        ])
        self.assertEqual(stats["peak_allocated_bytes_by_device_max_over_run"], [150, 200])
        self.assertEqual(stats["peak_allocated_bytes_total_max_over_run"], 350)
        self.assertEqual(stats["count"], 2)


if __name__ == "__main__":
    unittest.main()
