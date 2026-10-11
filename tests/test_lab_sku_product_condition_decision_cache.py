"""Eight CPU-free cache contracts; synthetic fixtures are not SKU datasets."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import product_condition_decision_cache_v1 as C
import product_condition_sharing_v1 as S
from test_lab_sku_product_condition_sharing import build, fixture, PIN, result


VARIANT = "synthetic-top1"


def decisions(plan, winner="なし", variant=VARIANT):
    values = result(plan, winner)
    for identity, value in values.items():
        value.update({"group_id": identity, "variant": variant})
    return values


def rehash_cache(cache):
    original = {}
    for entry in cache["entries"].values():
        entry["original_group_result_sha256"] = S.digest(entry["original_group_result"])
        entry["entry_sha256"] = S.digest({k: v for k, v in entry.items() if k != "entry_sha256"})
        original[entry["source_group_id"]] = entry["original_group_result"]
    cache["source_group_results_sha256"] = S.digest(original)
    cache["cache_sha256"] = S.digest({k: v for k, v in cache.items() if k != "cache_sha256"})


def changed_plan(*, document_version=False, reverse_options=False):
    values, docs, bindings, contract, _ = fixture()
    if document_version:
        document = docs[values[0]["target_document_id"]]
        document["description"]["text"] += " 原本の新しい版。"
        document["document_sha256"] = S.digest({k: v for k, v in document.items() if k != "document_sha256"})
    new_values, new_bindings, eligible = [], {}, []
    for value in values:
        changed = copy.deepcopy(value)
        changed["target_document_sha256"] = docs[changed["target_document_id"]]["document_sha256"]
        if reverse_options and changed["direction"] == "rakuten_to_au":
            changed["option_values"].reverse()
        checksum = S.digest({k: changed[k] for k in S.SEMANTIC_FIELDS})
        changed["request_sha256"] = checksum
        changed["request_id"] = "generic-description:" + checksum
        new_values.append(changed)
        new_bindings[changed["request_id"]] = copy.deepcopy(bindings[value["request_id"]])
        for task in new_bindings[changed["request_id"]]:
            task["request_id"] = changed["request_id"]
            task["option_values"] = copy.deepcopy(changed["option_values"])
        if value["request_id"] in contract["eligible_forward_request_ids"]:
            eligible.append(changed["request_id"])
    return build(new_values, docs, new_bindings, {**contract, "eligible_forward_request_ids": eligible})


class ProductConditionDecisionCacheContracts(unittest.TestCase):
    def test_variant_namespace_is_effective_key_and_other_variant_is_a_miss(self):
        plan = build()
        cache = C.build_cache(plan, decisions(plan), VARIANT)
        hits, misses = C.lookup_cache(cache, plan, VARIANT)
        self.assertEqual(set(hits), {g["group_id"] for g in plan["groups"]})
        self.assertEqual(misses, {})
        key = next(iter(cache["entries"]))
        self.assertEqual(key, C.effective_key(VARIANT, plan["groups"][0]["model_cache_key"]))
        hits, misses = C.lookup_cache(cache, plan, "synthetic-majority")
        self.assertEqual(hits, {})
        self.assertTrue(all(v["reason"] == "variant_namespace_mismatch" for v in misses.values()))
        with self.assertRaisesRegex(ValueError, "variant must equal"):
            C.build_cache(plan, decisions(plan), "synthetic-majority")

    def test_wanted_values_and_fanout_count_change_hit_without_source_plan_sha_constraint(self):
        source = build()
        cache = C.build_cache(source, decisions(source, "なし"), VARIANT)
        values, docs, binds, contract, baseline = fixture()
        remaining = values[2:]
        identities = {r["request_id"] for r in remaining}
        changed = build(remaining, docs, {k: v for k, v in binds.items() if k in identities},
                        {**contract, "eligible_forward_request_ids": [r["request_id"] for r in values[2:4]]})
        self.assertNotEqual(source["plan_sha256"], changed["plan_sha256"])
        hits, misses = C.lookup_cache(cache, changed, VARIANT)
        self.assertEqual(misses, {})
        self.assertEqual(len(hits), 1)
        output = S.fan_out(changed, hits, {k: v for k, v in baseline.items() if k in identities})
        self.assertEqual([p["state"] for p in output[:2]], ["support", "support"])
        self.assertEqual(next(iter(hits.values()))["source_decision_cache"]["source_plan_sha256"], source["plan_sha256"])

    def test_model_document_option_order_and_content_version_changes_are_misses(self):
        original = build()
        cache = C.build_cache(original, decisions(original), VARIANT)
        variants = [build(pin={**PIN, "revision": "next-model-policy"}), build(version="next-context-policy"),
                    changed_plan(document_version=True), changed_plan(reverse_options=True)]
        for changed in variants:
            hits, misses = C.lookup_cache(cache, changed, VARIANT)
            self.assertEqual(hits, {})
            self.assertEqual(set(misses), {g["group_id"] for g in changed["groups"]})
            self.assertTrue(all(v["reason"] == "exact_model_input_not_cached" for v in misses.values()))

    def test_negative_unknown_and_tied_group_outcomes_all_hit_with_frozen_semantics(self):
        plan = build()
        for winner in ["なし", S.UNKNOWN]:
            cache = C.build_cache(plan, decisions(plan, winner), VARIANT)
            hits, misses = C.lookup_cache(cache, plan, VARIANT)
            self.assertEqual(misses, {})
            output = S.fan_out(plan, hits, fixture()[4])
            expected = ["unknown", "unknown", "support", "support"] if winner == "なし" else ["unknown"] * 4
            self.assertEqual([p["state"] for p in output[:4]], expected)
            self.assertEqual(cache["actual_saved_inference_count"], 0)
        tied = decisions(plan, None)
        first = tied[plan["groups"][0]["group_id"]]
        first["scores"] = {"あり": 1, "なし": 1, S.UNKNOWN: 0}
        hits, misses = C.lookup_cache(C.build_cache(plan, tied, VARIANT), plan, VARIANT)
        self.assertEqual(misses, {})
        self.assertTrue(all(p["state"] == "unknown" for p in S.fan_out(plan, hits, fixture()[4])[:4]))

    def test_lookup_rebinds_saved_group_alias_without_mutating_original_result_or_cache(self):
        plan = build()
        cache = C.build_cache(plan, decisions(plan), VARIANT)
        # A saved legacy identifier may differ while the exact model-input key remains identical.
        entry = next(iter(cache["entries"].values()))
        entry["source_group_id"] = "synthetic-saved-legacy-group-alias"
        entry["original_group_result"]["group_id"] = entry["source_group_id"]
        rehash_cache(cache)
        before = copy.deepcopy(cache)
        hits, misses = C.lookup_cache(cache, plan, VARIANT)
        self.assertEqual(misses, {})
        identity = plan["groups"][0]["group_id"]
        self.assertEqual(hits[identity]["group_id"], identity)
        self.assertEqual(hits[identity]["group_sha256"], plan["groups"][0]["group_sha256"])
        self.assertEqual(hits[identity]["source_decision_cache"]["source_group_id"], entry["source_group_id"])
        self.assertEqual(cache, before)

    def test_invalid_whole_value_literal_quote_and_cache_integrity_rejected(self):
        plan = build()
        for change in ["invalid-winner", "row-quote", "partial-scores"]:
            values = decisions(plan)
            first = next(iter(values.values()))
            if change == "invalid-winner": first["winning_value"] = "not-an-original-option"
            elif change == "row-quote": first["quote_refs"][0]["field"] = "selected_axes"
            else: first["scores"] = {"なし": 1}
            with self.assertRaises(ValueError):
                C.build_cache(plan, values, VARIANT)
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            C.build_cache(plan, {}, VARIANT)
        cache = C.build_cache(plan, decisions(plan), VARIANT)
        next(iter(cache["entries"].values()))["original_group_result"]["winning_value"] = "あり"
        with self.assertRaisesRegex(ValueError, "cache SHA"):
            C.lookup_cache(cache, plan, VARIANT)

    def test_exact_original_results_plan_and_semantic_hashes_preserved(self):
        plan = build()
        values = decisions(plan)
        before = copy.deepcopy((plan, values))
        cache = C.build_cache(plan, values, VARIANT)
        self.assertEqual((plan, values), before)
        self.assertEqual(cache["cache_sha256"], S.digest({k: v for k, v in cache.items() if k != "cache_sha256"}))
        self.assertEqual(cache["source_group_results_sha256"], S.digest(values))
        for entry in cache["entries"].values():
            self.assertEqual(entry["original_group_result"], values[entry["source_group_id"]])
            self.assertEqual(entry["original_group_result_sha256"], S.digest(entry["original_group_result"]))
        self.assertEqual(cache["actual_saved_inference_count"], 0)
        self.assertFalse(cache["labels_read"])
        self.assertFalse(cache["model_inference_run"])

    def test_cli_new_json_only_manifest_raw_shas_and_output_overwrite_rejection(self):
        plan = build()
        values = decisions(plan)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            plan_path, results_path, output = root / "plan.json", root / "results.jsonl", root / "cache.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            results_path.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values.values()), encoding="utf-8")
            before = (plan_path.read_bytes(), results_path.read_bytes())
            with contextlib.redirect_stdout(io.StringIO()):
                code = C.main(["--plan", str(plan_path), "--group-results", str(results_path),
                               "--variant", VARIANT, "--output", str(output)])
            self.assertEqual(code, 0)
            artifact = json.loads(output.read_text())
            self.assertEqual(artifact["artifact_sha256"], S.digest({k: v for k, v in artifact.items() if k != "artifact_sha256"}))
            self.assertEqual(artifact["manifest"]["manifest_sha256"], S.digest({k: v for k, v in artifact["manifest"].items() if k != "manifest_sha256"}))
            self.assertTrue(artifact["manifest"]["input_file_sha256_unchanged"])
            self.assertEqual(before, (plan_path.read_bytes(), results_path.read_bytes()))
            with self.assertRaises(FileExistsError):
                C.export_cache(plan_path, results_path, VARIANT, output)


if __name__ == "__main__":
    unittest.main()
