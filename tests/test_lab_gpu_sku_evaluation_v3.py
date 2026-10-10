"""Label-free contracts for the v9 two-mode GPU evaluator."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/evaluate_gpu_sku_trial_v3.py"
spec = importlib.util.spec_from_file_location("evaluate_gpu_sku_trial_v3_test", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GpuSkuEvaluationV3Tests(unittest.TestCase):
    def setUp(self):
        self.case = {"case_id": "smoke-1", "au": {"sku_rows": [
            {"alias": "a000", "row_key": "au:row:one", "sku": "color=blue"},
            {"alias": "a001", "row_key": "au:row:two", "sku": "color=red"}]}}

    def test_simple_mode_maps_input_alias_to_row_key_without_claiming_entailment(self):
        record = {"case_id": "smoke-1", "status": "ok",
            "raw_output": json.dumps({"decision": "matched", "au_row_alias": "a001", "reason": "red option"}),
            "parsed": {"decision": "matched", "au_row_key": "au:row:two", "reason": "red option",
                "evidence": [], "inputrefs": {"kind": "inputrefs_not_entailment_evidence",
                    "row_key": "au:row:two", "sku": "color=red"}}, "resolved_evidence": None}
        checked = module.validate_simple_record(self.case, record)
        self.assertEqual(checked["prediction"], {"decision": "matched", "au_row_key": "au:row:two"})
        self.assertEqual(checked["evidence_semantics"], "input_row_mapping_only_not_model_entailment_evidence")

    def test_simple_nonmatch_requires_null_alias_and_preserves_decision(self):
        record = {"case_id": "smoke-1", "status": "ok",
            "raw_output": '{"decision":"unmatched","au_row_alias":null,"reason":"no option matches"}',
            "parsed": {"decision": "unmatched", "au_row_key": None, "reason": "no option matches",
                "evidence": [], "inputrefs": None}, "resolved_evidence": None}
        self.assertEqual(module.validate_simple_record(self.case, record)["prediction"],
            {"decision": "unmatched", "au_row_key": None})
        record["raw_output"] = '{"decision":"unmatched","au_row_alias":"a000","reason":"no option"}'
        with self.assertRaisesRegex(ValueError, "invalid AU alias"):
            module.validate_simple_record(self.case, record)

    def test_simple_invalid_error_and_oom_rows_are_review(self):
        for status in ("invalid_output", "error", "oom", "invalid_input_over_limit"):
            record = {"case_id": "smoke-1", "status": status, "raw_output": None}
            self.assertEqual(module.validate_simple_record(self.case, record)["prediction"],
                {"decision": "review", "au_row_key": None})

    def test_simple_host_mapping_must_match_model_alias(self):
        record = {"case_id": "smoke-1", "status": "ok",
            "raw_output": '{"decision":"matched","au_row_alias":"a000","reason":"blue option"}',
            "parsed": {"decision": "matched", "au_row_key": "au:row:two", "reason": "blue option",
                "evidence": [], "inputrefs": {"kind": "inputrefs_not_entailment_evidence",
                    "row_key": "au:row:two", "sku": "color=red"}}, "resolved_evidence": None}
        with self.assertRaisesRegex(ValueError, "mapping differs"):
            module.validate_simple_record(self.case, record)

    def test_strict_review_can_be_valid_with_no_cited_evidence(self):
        case = {"case_id": "smoke-1", "au": {"sku_rows": self.case["au"]["sku_rows"]},
            "evidence_registry": [], "source_texts": {"au": [], "rakuten": []}}
        raw = '{"decision":"review","au_row_alias":null,"reason":"not enough evidence","evidence_ids":[]}'
        record = {"case_id": "smoke-1", "mode": "strict", "status": "ok", "raw_output": raw,
            "parsed": {"decision": "review", "au_row_key": None, "reason": "not enough evidence", "evidence": []},
            "resolved_evidence": []}
        self.assertEqual(module.validate_strict_record(case, record)["prediction"],
            {"decision": "review", "au_row_key": None})

    def test_strict_match_requires_rakuten_sku_and_exact_selected_au_row_evidence(self):
        rk = {"id": "rs", "side": "rakuten", "field": "selected_sku", "scope": "selected_option",
            "quote": "color=red", "source_ref": {"source": "input"}}
        au = {"id": "a001", "side": "au", "field": "selected_option", "scope": "sku_row",
            "quote": "color=red", "row_key": "au:row:two", "source_ref": {"row_key": "au:row:two"}}
        case = {"case_id": "smoke-1", "au": {"sku_rows": self.case["au"]["sku_rows"]},
            "evidence_registry": [rk, au], "source_texts": {"au": ["color=red"], "rakuten": ["color=red"]}}
        expected_evidence = [{**{k: v for k, v in rk.items() if k != "id"}, "evidence_id": "rs"},
            {**{k: v for k, v in au.items() if k != "id"}, "evidence_id": "a001"}]
        raw = '{"decision":"matched","au_row_alias":"a001","reason":"same color","evidence_ids":["rs","a001"]}'
        record = {"case_id": "smoke-1", "mode": "strict", "status": "ok", "raw_output": raw,
            "parsed": {"decision": "matched", "au_row_key": "au:row:two", "reason": "same color",
                "evidence": expected_evidence}, "resolved_evidence": expected_evidence}
        self.assertEqual(module.validate_strict_record(case, record)["prediction"]["au_row_key"], "au:row:two")
        raw = '{"decision":"matched","au_row_alias":"a001","reason":"same color","evidence_ids":["rs"]}'
        record["raw_output"] = raw
        with self.assertRaisesRegex(ValueError, "chosen AU row evidence"):
            module.validate_strict_record(case, record)

    def test_frozen_input_hash_is_the_14_case_sample(self):
        self.assertEqual(module.EXPECTED_INPUT_SHA256,
            "6e2a3057dca06e50e1c0c0f421fb9ce234e6964b2f47f668661e664abe1af23d")
        self.assertEqual(module.EXPECTED_SOURCE_CASES_SHA256,
            "a91fca45c574f4d531eca2dc450e81a9adc44fa38c934955109831cf3147e18f")

    def test_actual_summary_schema_uses_quantization_config_and_runtime_for_metadata(self):
        summary = {"model": module.MODEL, "runtime_quantization": "nf4",
            "is_loaded_in_4bit": True, "config_name": module.MODEL["name"],
            "config_revision": module.MODEL["revision"], "attention_implementation": "sdpa",
            "prefill_method": module.PREFILL_METHOD, "prefill_chunk_size": 512,
            "logits_to_keep": 1, "nf4_linear4bit_module_count": 346,
            "nf4_quantized_linear4bit_module_count": 346, "nf4_logical_parameter_count": 100,
            "base_logical_parameter_count": 120, "nf4_coverage_ratio": 0.83,
            "hf_quantization_config": {"load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
                "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True},
            "parameter_devices": ["cuda:0"], "hf_device_map": {"model": "0"}}
        runtime = {"gpu_available": True, "cuda_device_count": 1, "devices": [{"name": "T4"}],
            "prefill_method": module.PREFILL_METHOD, "prefill_chunk_size": 512,
            "decode_strategy": module.DECODE_STRATEGY, "max_new_tokens": 256,
            "runtime_quantization": "nf4", "is_loaded_in_4bit": True,
            "attention_implementation": "sdpa", "parameter_devices": ["cuda:0"],
            "hf_device_map": {"model": "0"},
            "hf_quantization_config": summary["hf_quantization_config"],
            "nf4_linear4bit_module_count": 346, "nf4_quantized_linear4bit_module_count": 346,
            "nf4_coverage_ratio": 0.83}
        module.validate_v9_actual_runtime(summary, runtime)

    def test_wrong_row_audit_only_counts_wrong_accepts_for_known_matches(self):
        scored = [
            {"case_id": "wrong-match", "gold_decision": "matched", "gold_row_keys": ["gold-row"],
                "prediction": {"decision": "matched", "au_row_key": "wrong-row"}},
            {"case_id": "negative-accept", "gold_decision": "unmatched", "gold_row_keys": [],
                "prediction": {"decision": "matched", "au_row_key": "row"}},
            {"case_id": "review-accept", "gold_decision": "review", "gold_row_keys": [],
                "prediction": {"decision": "matched", "au_row_key": "row"}},
        ]
        audit = module.audit_case_ids(scored)
        self.assertEqual(audit["wrong_au_row_accepts"], ["wrong-match"])
        self.assertEqual(audit["false_accept_known_negative"], ["negative-accept"])
        self.assertEqual(audit["gold_review_accepts"], ["review-accept"])


if __name__ == "__main__":
    unittest.main()
