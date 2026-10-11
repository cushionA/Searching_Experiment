"""GPU-free failure-path checks; synthetic fixtures are not evaluation data."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("kaggle_full_choices", HERE / "kaggle_full_condition_choices_v1.py")
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)


def bundle_manifest(directory):
    manifest = {"labels_included": False, "files": {
        str(p.relative_to(directory)): {"sha256": G.TASK.sha(p), "size_bytes": p.stat().st_size}
        for p in directory.rglob("*") if p.is_file() and p.name != "BUNDLE_MANIFEST.json"}}
    (directory / "BUNDLE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")


def choice(identity="first", labels=None):
    return {"id": identity, "question": "which whole specification?", "state": "current original product",
            "choices": [{"label": label, "description": "complete original " + label}
                        for label in (labels or ["a", "b", G.TASK.UNKNOWN])]}


class Tokenizer:
    def __call__(self, first, second, **options):
        assert options["truncation"] is False and options["padding"] is False
        if "token-error" in second:
            raise ValueError("a single candidate failed tokenization")
        return {"input_ids": [1] * (513 if "long" in second else 5)}


def scorer(logits=None):
    result = object.__new__(G.CudaScorer)
    result.load = lambda _: (Tokenizer(), None, {"device": "cuda"}, {})
    result.logits = logits or (lambda _, __, encodings, columns: [[float(columns - i) for i in range(columns)] for _ in encodings])
    return result


class KaggleFullChoiceContracts(unittest.TestCase):
    def test_bundle_inventory_verifies_all_files_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "source.txt").write_text("original🙂", encoding="utf-8")
            bundle_manifest(root)
            self.assertEqual(G.verify_bundle(root)["file_count"], 1)
            (root / "source.txt").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                G.verify_bundle(root)

    def test_unlisted_bundle_file_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "source.txt").write_text("original", encoding="utf-8")
            bundle_manifest(root)
            (root / "extra.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inventory differs"):
                G.verify_bundle(root)
            (root / "extra.txt").unlink()
            (root / "source.txt").unlink()
            (root / "source.txt").symlink_to("BUNDLE_MANIFEST.json")
            with self.assertRaisesRegex(ValueError, "ordinary contained"):
                G.verify_bundle(root)

    def test_unsafe_bundle_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "BUNDLE_MANIFEST.json").write_text(json.dumps({"labels_included": False, "files": {"../outside": {}}}))
            with self.assertRaisesRegex(ValueError, "unsafe"):
                G.verify_bundle(root)

    def test_gpu_request_cache_preserves_distinct_logical_ids_and_exact_text(self):
        engine = scorer()
        with patch.object(engine, "infer_jev", wraps=engine.infer_jev) as infer:
            first = engine.run_choices([choice("first"), choice("second")])
            self.assertEqual(len(infer.call_args.args[0]), 1)
            self.assertEqual([r["id"] for r in first["records"]], ["first", "second"])
            self.assertTrue(first["records"][1]["cache_reused"])
            cached = {r["cache_key"]: r for r in first["records"]}
            engine.run_choices([choice("third")], cache=cached)
            self.assertEqual(infer.call_count, 1)
            cached[next(iter(cached))]["state"] += "changed"
            with self.assertRaisesRegex(ValueError, "cache text"):
                engine.run_choices([choice("fourth")], cache=cached)

    def test_gpu_cache_never_accepts_cpu_arithmetic(self):
        engine = scorer()
        result = engine.run_choices([choice()])
        row = result["records"][0]
        row["device"] = "cpu"
        with self.assertRaisesRegex(ValueError, "cache identity"):
            engine.run_choices([choice()], cache={row["cache_key"]: row})

    def test_any_jev_overlong_candidate_invalidates_entire_request_without_truncation(self):
        engine = scorer(logits=lambda *_: self.fail("overlong request must never enter the model"))
        result = engine.run_choices([choice(labels=["a", "long"])])["records"][0]
        self.assertEqual(result["status"], "input_too_long")
        self.assertEqual(result["token_counts"], {"a": 5, "long": 513})
        self.assertIsNone(result["probabilities"])

    def test_single_jev_candidate_tokenization_failure_invalidates_request(self):
        engine = scorer(logits=lambda *_: self.fail("failed request must never enter model"))
        result = engine.run_choices([choice(labels=["a", "token-error", "b"])])["records"][0]
        self.assertEqual(result["status"], "tokenization_error")
        self.assertEqual(result["token_counts"], {"a": 5})
        self.assertIsNone(result["argmax_label"])

    def test_batch_inference_failure_invalidates_all_candidates_for_each_affected_request(self):
        def fail(*_):
            raise RuntimeError("simulated GPU out of memory")
        engine = scorer(logits=fail)
        result = engine.run_choices([choice("first"), choice("second", ["c", "d"])])
        self.assertEqual([r["status"] for r in result["records"]], ["inference_error"] * 2)
        self.assertTrue(all(r["probabilities"] is None for r in result["records"]))

    def test_nli_class_mapping_and_duplicate_pair_binding_are_preserved(self):
        engine = scorer()
        pairs = [{"id": identity, "premise": "actual full title row", "hypothesis": "complete option"}
                 for identity in ("first", "second")]
        result = engine.run_pairs(pairs)
        self.assertEqual([r["id"] for r in result["records"]], ["first", "second"])
        self.assertEqual(result["runtime"]["inferred_unique_pair_count"], 1)
        probabilities = result["records"][0]["probabilities"]
        self.assertGreater(probabilities["support"], probabilities["unknown"])
        self.assertGreater(probabilities["unknown"], probabilities["conflict"])
        cache = {r["cache_key"]: r for r in result["records"]}
        self.assertEqual(engine.run_pairs(pairs, cache=cache)["runtime"]["inferred_unique_pair_count"], 0)
        cache[next(iter(cache))]["device"] = "cpu"
        with self.assertRaisesRegex(ValueError, "cache identity"):
            engine.run_pairs(pairs, cache=cache)

    def test_overlong_and_failed_nli_pairs_return_no_probabilities(self):
        engine = scorer(logits=lambda *_: self.fail("failed pairs must not enter model"))
        pairs = [{"id": "too-long", "premise": "original", "hypothesis": "long"},
                 {"id": "token-error", "premise": "original", "hypothesis": "token-error"}]
        result = engine.run_pairs(pairs)["records"]
        self.assertEqual(result[0]["token_count_untruncated"], 513)
        self.assertEqual(result[1]["error_type"], "ValueError")
        self.assertTrue(all(r["relation"] == "unknown" and r["probabilities"] is None for r in result))

    def test_nli_pinned_readme_and_config_mapping_are_checked_before_loading(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # The pinned README omits the final quote in its declaration.
            (root / "README.md").write_text('{0: "entailment", 1: "neutral", 2: "contradiction}')
            (root / "config.json").write_text(json.dumps({"id2label": {"0": "LABEL_0", "1": "LABEL_1", "2": "LABEL_2"}}))
            files = {p.name: G.TASK.sha(p) for p in root.iterdir()}
            with patch.object(G.NLI, "NLI_FILE_SHA256", files):
                self.assertEqual(G.verify_nli_files(root)["device"], "cuda")
                (root / "README.md").write_text("an incompatible class order")
                files["README.md"] = G.TASK.sha(root / "README.md")
                with self.assertRaisesRegex(RuntimeError, "README label order"):
                    G.verify_nli_files(root)

    def test_missing_pinned_weights_cannot_download_when_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "download disabled"):
                G.acquire_weights(Path(td), Path(td) / "unused-provenance.json", download=False)

    def test_mock_full_run_keeps_source_bindings_and_exact_quotes_for_all_logical_requests(self):
        # Exercise the output contract without torch, GPU or model inference.
        task = G.TASK
        document = {"document_id": "synthetic-document", "title": {"text": "原商品🙂"},
                    "description": {"text": "全文。別商品への説明も保存。", "complete": True}}
        document["document_sha256"] = task.digest(document)
        requests, tasks = [], []
        for index, value in enumerate(["a", "b"]):
            semantic = {"direction": "rakuten_to_au", "axis_name": "販売仕様", "selected_value": value,
                        "option_values": ["a", "b"], "target_document_id": document["document_id"],
                        "target_document_sha256": document["document_sha256"],
                        "target_selected_axes": [{"axis_name": "色", "value": "青🙂"}],
                        "target_selected_axes_text": "色：青🙂"}
            identity = task.digest(semantic)
            request = {**semantic, "request_sha256": identity, "request_id": "generic-description:" + identity}
            requests.append(request)
            tasks.append({"task_id": "synthetic-task-" + str(index), "request_id": request["request_id"],
                          "direction": request["direction"], "option_values": ["a", "b"],
                          "raw_condition": {"axis_label": "販売仕様", "value": value}})
        engine = scorer(logits=lambda _, __, encodings, columns:
                        [[float(3 - i)] if columns == 1 else [3., 1., 0.] for i, _ in enumerate(encodings)])
        engine.runtime = {"device": "cuda", "gpu_name": "synthetic-test-only", "precision": "fp32"}
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            bundle = base / "bundle"
            input_dir = bundle / "input" / "full"
            input_dir.mkdir(parents=True)
            for name, rows in (("requests.jsonl", requests), ("documents.jsonl", [document]), ("tasks.jsonl", tasks)):
                task.write_rows(input_dir / name, rows)
            task.write_json(input_dir / "manifest.json", {"labels_read": False,
                            "output_sha256": {n: task.sha(input_dir / n) for n in ("requests.jsonl", "documents.jsonl", "tasks.jsonl")}})
            bundle_manifest(bundle)
            output_dir = base / "output"
            with patch("builtins.print"):
                result = G.run(input_dir, output_dir, bundle, engine)
            self.assertEqual(result["request_count"], 2)
            self.assertEqual(result["unique_jev_request_count"], 1)
            self.assertEqual(result["device"], "cuda")
            self.assertFalse(result["production_adoption_allowed"])
            self.assertFalse(result["labels_read"])
            predictions = task.read(output_dir / "predictions.jsonl")
            self.assertEqual([p["request_id"] for p in predictions], [r["request_id"] for r in requests])
            self.assertEqual([p["state"] for p in predictions], ["support", "unknown"])
            self.assertEqual([p["source_task_bindings"][0]["task_id"] for p in predictions], [t["task_id"] for t in tasks])
            self.assertTrue(all(task.current_refs(requests[0], {document["document_id"]: document}) == p["quote_refs"]
                                for p in predictions if p["state"] == "support"))
            self.assertEqual(task.read(output_dir / "source-documents.jsonl"), [document])
            for name, expected in result["output_sha256"].items():
                self.assertEqual(task.sha(output_dir / name), expected)


if __name__ == "__main__":
    unittest.main()
