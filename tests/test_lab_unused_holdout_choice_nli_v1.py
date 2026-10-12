from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / ".lab-output/sku-unused-holdout-choice-20261013-v1"
SOURCE = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/test"
DEV_SOURCE = ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev"
OLD_MEMBERSHIP = ROOT / ".lab-output/sku-final-composition-subset-20261013-v1/plan/subset-membership.jsonl"
NLI_PATH = ROOT / "experiments/sku-matching/sku_directional_choice_nli_v1.py"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def raw_rows(path):
    return [line for line in Path(path).read_bytes().splitlines(keepends=True) if line.strip()]


class UnusedHoldoutChoiceNliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(NLI_PATH.parent))
        spec = importlib.util.spec_from_file_location("test_unused_holdout_choice_nli", NLI_PATH)
        cls.nli = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.nli
        spec.loader.exec_module(cls.nli)
        cls.prepared = ARTIFACT / "prepared-choice-nli"

    def test_frozen_policy_precedes_and_binds_exact_20_by_2_membership(self):
        policy_doc = read_json(ARTIFACT / "selection-policy.json")
        policy = {k: v for k, v in policy_doc.items()
                  if k not in {"policy_sha256", "dev_test_product_separation_audit", "status"}}
        self.assertEqual(sha_bytes(json.dumps(policy, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()), policy_doc["policy_sha256"])
        members = read_jsonl(ARTIFACT / "selection-membership.jsonl")
        product_counts = {}
        for row in members:
            product_counts[row["au_product_id"]] = product_counts.get(row["au_product_id"], 0) + 1
        self.assertEqual(len(members), 40)
        self.assertEqual(len(product_counts), 20)
        self.assertEqual(set(product_counts.values()), {2})
        old_products = {str(x["au_product_id"]) for x in read_jsonl(OLD_MEMBERSHIP)}
        dev_products = {str(x["au_product_id"]) for x in read_jsonl(DEV_SOURCE / "case-plans.jsonl")}
        self.assertFalse(set(product_counts) & old_products)
        self.assertFalse(set(product_counts) & dev_products)
        self.assertEqual(policy_doc["dev_test_product_separation_audit"]["test_dev_product_overlap_count"], 0)

    def test_input_rows_are_exact_source_lines_with_full_case_row_task_coverage(self):
        inputs = ARTIFACT / "source-inputs"
        membership = read_jsonl(ARTIFACT / "selection-membership.jsonl")
        selected_cases = {x["case_id"] for x in membership}
        source_cases = {json.loads(line)["case_id"]: line for line in raw_rows(SOURCE / "case-plans.jsonl")}
        source_rows = {json.loads(line)["case_id"]: [] for line in raw_rows(SOURCE / "case-plans.jsonl")}
        selected_row_keys = set()
        for line in raw_rows(SOURCE / "row-plans.jsonl"):
            row = json.loads(line)
            if row["case_id"] in selected_cases:
                source_rows[row["case_id"]].append(line)
                selected_row_keys.add((row["case_id"], row["au_row_key"]))
        for name, selected in (("case-plans.jsonl", selected_cases),):
            expected = [source_cases[c] for c in source_cases if c in selected]
            self.assertEqual(raw_rows(inputs / name), expected)
        self.assertEqual(len(raw_rows(inputs / "row-plans.jsonl")), sum(map(len, source_rows.values())))
        task_ids = set()
        for line in raw_rows(inputs / "row-plans.jsonl"):
            row = json.loads(line)
            self.assertIn(row["case_id"], selected_cases)
            self.assertEqual(len(row["bindings"]), row["rakuten_axis_count"] + row["au_axis_count"])
            task_ids.update(x["task_id"] for x in row["bindings"])
        input_tasks = raw_rows(inputs / "tasks.jsonl")
        self.assertEqual(len(input_tasks), len(task_ids))
        self.assertTrue(all(json.loads(x)["task_id"] in task_ids for x in input_tasks))
        source_task_rows = raw_rows(SOURCE / "tasks.jsonl")
        expected_tasks = [line for line in source_task_rows if json.loads(line)["task_id"] in task_ids]
        self.assertEqual(input_tasks, expected_tasks)
        request_ids = {json.loads(line)["request_id"] for line in expected_tasks}
        expected_requests = [line for line in raw_rows(SOURCE / "requests.jsonl")
                             if json.loads(line)["request_id"] in request_ids]
        self.assertEqual(raw_rows(inputs / "requests.jsonl"), expected_requests)
        document_ids = {json.loads(line)["target_document_id"] for line in expected_requests}
        expected_documents = [line for line in raw_rows(SOURCE / "documents.jsonl")
                              if json.loads(line)["document_id"] in document_ids]
        self.assertEqual(raw_rows(inputs / "documents.jsonl"), expected_documents)
        self.assertEqual(len(read_jsonl(inputs / "case-plans.jsonl")), 40)
        manifest = read_json(inputs / "manifest.json")
        self.assertEqual(manifest["split"], "test")
        self.assertTrue(manifest["independent_test"])
        self.assertTrue(manifest["test_split_read"])
        self.assertFalse(manifest["labels_read"])

    def test_prepared_scope_and_model_payload_exclude_hidden_task_identity_and_labels(self):
        freeze = read_json(self.prepared / "freeze.json")
        manifest = read_json(ARTIFACT / "manifest.json")
        self.assertEqual(freeze["split"], "test")
        self.assertTrue(freeze["test_split_read"])
        self.assertEqual(freeze["selected_case_count"], 40)
        self.assertTrue(freeze["all_original_tasks_retained"])
        self.assertFalse(freeze["labels_read"])
        self.assertFalse(manifest["classification_or_scoring_run"])
        pairs = self.nli.read_rows(self.prepared / "model-pairs.jsonl")
        payloads = self.nli.build_model_payloads(self.prepared,
            [p["pair_id"] for p in pairs if p["preparation_status"] == "ready"])
        self.assertEqual(len(payloads), freeze["ready_pair_count"])
        forbidden = {"source_selected_fact", "source_selected_value", "task_id", "request_id",
                     "case_id", "au_row_key", "source_sku_key", "gold", "label",
                     "prediction", "score", "desired_answer", "producer_status"}
        def keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)
        for payload in payloads:
            self.assertFalse(forbidden & set(keys(payload)))

    def test_all_prepared_raw_bodies_and_validator_fact_spans_reverify(self):
        source_root = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"
        store = self.nli.SOURCES.RawStore(source_root)
        docs = self.nli.read_rows(self.prepared / "source-documents.jsonl")
        body_count = 0
        for document in docs:
            for block in document.get("blocks", []):
                self.nli._verify_body_block(store, block)
                body_count += 1
        binding_count = 0
        for binding in self.nli.read_rows(self.prepared / "validation-bindings.jsonl"):
            fact = binding.get("source_selected_fact") or {}
            for span_key, value_key in (("value_span", "value"), ("axis_name_span", "axis_name")):
                span = fact.get(span_key)
                if span is not None:
                    self.nli._verify_span(store, span, fact[value_key], "test source fact")
                    binding_count += 1
        self.assertEqual(len(docs), 40)
        self.assertEqual(body_count, 326)
        self.assertEqual(binding_count, 2452)


if __name__ == "__main__":
    unittest.main()
