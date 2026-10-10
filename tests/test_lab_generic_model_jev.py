from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generic_model_jev_v1", ROOT / "experiments/sku-matching/trial_generic_model_jev_v1.py")
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def request(request_id="r1", question="この条件に当てはまるか？", state="原文の状況", **metadata):
    return {"id": request_id, "question": question, "state": state,
            "choices": [{"label": "B", "description": "第二の説明"},
                        {"label": "A", "description": "第一の説明"},
                        {"label": "C", "description": "第三の説明"}], **metadata}


def successful_inference(requests, model_dir, threads, batch_size, on_ready):
    pins = RUNNER._pins()
    runtime = {"model_load_seconds": 0.0, "inference_seconds": 0.1}
    on_ready(pins, runtime)
    records = []
    for item in requests:
        labels = [choice["label"] for choice in item["choices"]]
        logits = [float(index) for index in range(len(labels))]
        probabilities = RUNNER._softmax(logits)
        records.append({"cache_key": item["cache_key"], "status": "ok",
                        "logits": dict(zip(labels, logits)),
                        "probabilities": dict(zip(labels, probabilities)),
                        "argmax_label": labels[-1], "argmax_index": len(labels) - 1,
                        "argmax_probability": probabilities[-1],
                        "token_counts": {label: 30 for label in labels},
                        "latency_seconds": 0.1, "note": None, "error_type": None})
    return records, pins, runtime


class Tensor:
    def __init__(self, values):
        self.values = values

    def cpu(self):
        return self

    def detach(self):
        return self

    def tolist(self):
        return self.values


class Tokenizer:
    def __init__(self, specs=None, fails=None):
        self.specs = specs or {}
        self.fails = fails or set()
        self.calls = []
        self.pad_calls = []

    def __call__(self, context, candidate, **options):
        self.calls.append((context, candidate, options))
        if candidate in self.fails:
            raise ValueError("tokenizer failed")
        count, value = self.specs.get(candidate, (10, float(len(self.calls))))
        return {"input_ids": [value] * count, "attention_mask": [1] * count}

    def pad(self, rows, **options):
        self.pad_calls.append((deepcopy(rows), options))
        return {"input_ids": Tensor([row["input_ids"] for row in rows])}


class Model:
    def __init__(self, fails_on=None, bad_rows=None):
        self.calls = []
        self.fails_on = fails_on or set()
        self.bad_rows = bad_rows

    def __call__(self, input_ids):
        values = input_ids.tolist()
        self.calls.append(values)
        if any(row[0] in self.fails_on for row in values):
            raise RuntimeError("candidate inference failed")
        return SimpleNamespace(logits=Tensor(self.bad_rows if self.bad_rows is not None else [[row[0]] for row in values]))


def fake_load(tokenizer=None, model=None):
    return (tokenizer or Tokenizer(), model or Model(),
            SimpleNamespace(inference_mode=nullcontext), RUNNER._pins(),
            {"file_verification_seconds": 0.0, "model_load_seconds": 0.0,
             "inference_seconds": 0.0, "tokenization_seconds": 0.0})


class GenericModelJevTests(unittest.TestCase):
    def run_mock(self, requests, **options):
        with patch.object(RUNNER, "_infer", side_effect=successful_inference):
            return RUNNER.run_choices(requests, **options)

    def test_card_rendering_scalar_softmax_and_supplied_label_order(self):
        item = request(question=" 質問  ", state=" 状況\nそのまま ")
        tokenizer = Tokenizer({"B — 第二の説明": (20, 4.0), "A — 第一の説明": (22, 1.0),
                               "C — 第三の説明": (23, -2.0)})
        model = Model()
        ready = Mock()
        with patch.object(RUNNER, "_load_model", return_value=fake_load(tokenizer, model)):
            result = RUNNER.run_choices([item], batch_size=2, on_ready=ready)
        record = result["records"][0]
        self.assertEqual(record["status"], "ok")
        self.assertEqual(list(record["probabilities"]), ["B", "A", "C"])
        self.assertEqual(record["logits"], {"B": 4.0, "A": 1.0, "C": -2.0})
        expected = RUNNER._softmax([4.0, 1.0, -2.0])
        self.assertEqual(list(record["probabilities"].values()), expected)
        self.assertEqual(record["argmax_label"], "B")
        self.assertEqual(record["argmax_index"], 0)
        self.assertEqual(record["argmax_probability"], expected[0])
        self.assertEqual(record["token_counts"], {"B": 20, "A": 22, "C": 23})
        self.assertEqual([pair[0] for pair in tokenizer.calls], ["質問:  質問  \n状況:  状況\nそのまま "] * 3)
        self.assertEqual([pair[1] for pair in tokenizer.calls], ["B — 第二の説明", "A — 第一の説明", "C — 第三の説明"])
        for _, _, options in tokenizer.calls:
            self.assertEqual(options, {"truncation": False, "padding": False, "add_special_tokens": True})
        self.assertEqual([len(call) for call in model.calls], [2, 1])
        ready.assert_called_once()
        self.assertNotIn("relation", record)
        self.assertNotIn("confidence_threshold", record)

    def test_one_scalar_per_pair_is_not_a_fixed_three_class_head(self):
        item = request(choices=[{"label": "真", "description": ""}, {"label": "偽", "description": "いいえ"}])
        with patch.object(RUNNER, "_load_model", return_value=fake_load()):
            record = RUNNER.run_choices([item])["records"][0]
        self.assertEqual(list(record["logits"]), ["真", "偽"])
        self.assertEqual(sum(record["probabilities"].values()), 1)
        self.assertEqual(RUNNER.render_candidate(item["choices"][0]), "真")

    def test_empty_description_uses_bare_label_without_stripping_raw_strings(self):
        item = request(choices=[{"label": " bare ", "description": ""},
                                {"label": "space", "description": " "}])
        tokenizer = Tokenizer()
        with patch.object(RUNNER, "_load_model", return_value=fake_load(tokenizer)):
            record = RUNNER.run_choices([item])["records"][0]
        self.assertEqual([call[1] for call in tokenizer.calls], [" bare ", "space —  "])
        self.assertEqual(record["choices"], item["choices"])
        self.assertEqual(record["request"], item)
        self.assertEqual(record["pins"]["empty_description_candidate_template"], "{label}")

    def test_nonempty_description_keys_and_pre_fix_cache_remain_compatible(self):
        item = request()
        payload = [RUNNER.MODEL_ID, RUNNER.MODEL_REVISION, "fp32", RUNNER.FORMAT_VERSION,
                   RUNNER.MAX_TOKENS, RUNNER.CONTEXT_TEMPLATE, RUNNER.CANDIDATE_TEMPLATE,
                   item["question"], item["state"],
                   [[choice["label"], choice["description"]] for choice in item["choices"]]]
        old_key = hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(RUNNER.request_key(item["question"], item["state"], item["choices"]), old_key)
        original = self.run_mock([item])["records"][0]
        original["pins"].pop("empty_description_candidate_template")
        with patch.object(RUNNER, "_infer") as infer:
            result = RUNNER.run_choices([request("new")], cache={old_key: original})
        infer.assert_not_called()
        self.assertTrue(result["records"][0]["cache_reused"])
        self.assertEqual(result["pins"]["empty_description_candidate_template"], "{label}")

    def test_old_empty_description_cache_key_cannot_reuse_separator_rendering(self):
        item = request(choices=[{"label": "bare", "description": ""}])
        old_payload = [RUNNER.MODEL_ID, RUNNER.MODEL_REVISION, "fp32", RUNNER.FORMAT_VERSION,
                       RUNNER.MAX_TOKENS, RUNNER.CONTEXT_TEMPLATE, RUNNER.CANDIDATE_TEMPLATE,
                       item["question"], item["state"], [["bare", ""]]]
        old_key = hashlib.sha256(json.dumps(old_payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        new_key = RUNNER.request_key(item["question"], item["state"], item["choices"])
        self.assertNotEqual(old_key, new_key)
        with patch.object(RUNNER, "_infer", side_effect=successful_inference) as infer:
            result = RUNNER.run_choices([item], cache={old_key: {"cache_key": old_key}})
        infer.assert_called_once()
        self.assertFalse(result["records"][0]["cache_reused"])
        self.assertEqual(result["records"][0]["cache_key"], new_key)

    def test_repeated_text_deduplicates_and_keeps_metadata_order(self):
        items = [request("z", row={"number": 1}), request("a", row={"number": 2})]
        before = deepcopy(items)
        with patch.object(RUNNER, "_infer", side_effect=successful_inference) as infer:
            result = RUNNER.run_choices(items)
        self.assertEqual(len(infer.call_args.args[0]), 1)
        self.assertEqual([record["id"] for record in result["records"]], ["z", "a"])
        self.assertEqual([record["row"] for record in result["records"]], [{"number": 1}, {"number": 2}])
        self.assertEqual([record["cache_reused"] for record in result["records"]], [False, True])
        self.assertEqual(result["runtime"]["unique_request_count"], 1)
        self.assertEqual(items, before)
        result["records"][0]["choices"][0]["description"] = "changed"
        self.assertEqual(items, before)
        self.assertEqual(result["records"][1]["choices"], before[1]["choices"])

    def test_metadata_collisions_remain_in_exact_request_snapshot(self):
        item = request(model_id="caller metadata", probabilities={"opaque": True})
        record = self.run_mock([item])["records"][0]
        self.assertEqual(record["request"], item)
        self.assertEqual(record["model_id"], RUNNER.MODEL_ID)

    def test_hash_preserves_raw_text_description_and_choice_order(self):
        item = request()
        variants = [item, {**item, "state": item["state"] + " "},
                    {**item, "question": item["question"] + " "},
                    {**item, "choices": list(reversed(item["choices"]))},
                    {**item, "choices": [{**item["choices"][0], "description": "different"}, *item["choices"][1:]]}]
        keys = {RUNNER.request_key(variant["question"], variant["state"], variant["choices"]) for variant in variants}
        self.assertEqual(len(keys), len(variants))
        other = request("new", row="new metadata")
        self.assertEqual(RUNNER.request_key(item["question"], item["state"], item["choices"]),
                         RUNNER.request_key(other["question"], other["state"], other["choices"]))

    def test_persistent_cache_preserves_new_ids_and_metadata(self):
        original = self.run_mock([request("old", row=1)])["records"][0]
        ready = Mock()
        with patch.object(RUNNER, "_infer") as infer:
            result = RUNNER.run_choices([request("new", row=2)], cache={original["cache_key"]: original}, on_ready=ready)
        infer.assert_not_called()
        ready.assert_called_once()
        record = result["records"][0]
        self.assertEqual(record["id"], "new")
        self.assertEqual(record["row"], 2)
        self.assertEqual(record["request"], request("new", row=2))
        self.assertTrue(record["cache_reused"])
        self.assertEqual(result["runtime"]["cached_unique_request_count"], 1)

    def test_cache_identity_text_pins_and_label_order_are_checked(self):
        original = self.run_mock([request()])["records"][0]
        for fields in ({"model_revision": "other"}, {"format_version": "other"}, {"precision": "int8"},
                       {"state": "different"}, {"choices": list(reversed(original["choices"]))},
                       {"pins": {**original["pins"], "parameter_count": 1}},
                       {"probabilities": dict(reversed(list(original["probabilities"].items())))}):
            with self.subTest(fields=fields), patch.object(RUNNER, "_infer") as infer:
                with self.assertRaises(ValueError):
                    RUNNER.run_choices([request()], cache={original["cache_key"]: {**original, **fields}})
                infer.assert_not_called()

    def test_cached_scores_must_be_finite_softmax_and_consistent_argmax(self):
        original = self.run_mock([request()])["records"][0]
        for fields in ({"logits": {"B": float("nan"), "A": 1, "C": 2}},
                       {"probabilities": {"B": 1, "A": 0, "C": 1}},
                       {"probabilities": {"B": 0.1, "A": 0.8, "C": 0.1}},
                       {"argmax_label": "B"}, {"argmax_index": 0},
                       {"token_counts": {"B": 513, "A": 30, "C": 30}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                RUNNER.run_choices([request()], cache={original["cache_key"]: {**original, **fields}})

    def test_failed_cache_status_and_token_counts_must_be_consistent(self):
        original = self.run_mock([request()])["records"][0]
        failure = {**original, **RUNNER._empty_scores("input_too_long"),
                   "token_counts": {"B": 513, "A": 30, "C": 30}}
        with patch.object(RUNNER, "_infer") as infer:
            record = RUNNER.run_choices([request()], cache={original["cache_key"]: failure})["records"][0]
        infer.assert_not_called()
        self.assertEqual(record["status"], "input_too_long")
        self.assertIsNone(record["probabilities"])
        for fields in ({"status": "unknown"}, {"token_counts": None},
                       {"token_counts": {"B": 30, "A": 30, "C": 30}},
                       {"status": "inference_error"},
                       {"status": "tokenization_error", "token_counts": {"A": 30}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                RUNNER.run_choices([request()], cache={original["cache_key"]: {**failure, **fields}})

    def test_any_long_pair_fails_whole_request_and_counts_every_candidate(self):
        tokenizer = Tokenizer({"B — 第二の説明": (20, 1), "A — 第一の説明": (513, 2), "C — 第三の説明": (512, 3)})
        model = Model()
        with patch.object(RUNNER, "_load_model", return_value=fake_load(tokenizer, model)):
            result = RUNNER.run_choices([request(state=" 長い原文 " * 100)])
        record = result["records"][0]
        self.assertEqual(record["status"], "input_too_long")
        self.assertEqual(record["token_counts"], {"B": 20, "A": 513, "C": 512})
        self.assertIsNone(record["probabilities"])
        self.assertIsNone(record["logits"])
        self.assertIsNone(record["argmax_label"])
        self.assertEqual(record["state"], " 長い原文 " * 100)
        self.assertEqual(len(tokenizer.calls), 3)
        self.assertFalse(model.calls)
        self.assertFalse(tokenizer.pad_calls)
        self.assertEqual(result["runtime"]["input_too_long_count"], 1)

    def test_exactly_512_tokens_is_accepted(self):
        tokenizer = Tokenizer({"B — 第二の説明": (512, 1)})
        with patch.object(RUNNER, "_load_model", return_value=fake_load(tokenizer)):
            record = RUNNER.run_choices([request()])["records"][0]
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["token_counts"]["B"], 512)

    def test_tokenizer_failure_does_not_score_partial_request_or_other_metadata(self):
        tokenizer = Tokenizer(fails={"A — 第一の説明"})
        model = Model()
        other = request("r2", choices=[{"label": "別", "description": "候補"}])
        with patch.object(RUNNER, "_load_model", return_value=fake_load(tokenizer, model)):
            records = RUNNER.run_choices([request(), other])["records"]
        self.assertEqual([record["status"] for record in records], ["tokenization_error", "ok"])
        self.assertIsNone(records[0]["probabilities"])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(model.calls[0]), 1)

    def test_failed_candidate_discards_entire_request_but_other_requests_continue(self):
        tokenizer = Tokenizer({"B — 第二の説明": (10, 1), "A — 第一の説明": (10, 2),
                               "C — 第三の説明": (10, 3), "別 — 候補": (10, 4)})
        model = Model(fails_on={2})
        other = request("r2", choices=[{"label": "別", "description": "候補"}])
        with patch.object(RUNNER, "_load_model", return_value=fake_load(tokenizer, model)):
            records = RUNNER.run_choices([request(), other], batch_size=1)["records"]
        self.assertEqual([record["status"] for record in records], ["inference_error", "ok"])
        self.assertIsNone(records[0]["logits"])
        self.assertIsNone(records[0]["probabilities"])
        self.assertEqual(records[0]["error_type"], "RuntimeError")
        self.assertEqual([rows[0][0] for rows in model.calls], [1, 2, 4])

    def test_invalid_output_shape_and_nonfinite_logits_are_errors(self):
        for rows in ([[1, 2, 3]], [[float("nan")], [1], [2]], [[1], [2]]):
            with self.subTest(rows=rows), patch.object(RUNNER, "_load_model", return_value=fake_load(model=Model(bad_rows=rows))):
                record = RUNNER.run_choices([request()])["records"][0]
            self.assertEqual(record["status"], "inference_error")
            self.assertIsNone(record["probabilities"])

    def test_invalid_requests_are_per_request_errors_and_valid_ones_continue(self):
        invalid = [request("empty", choices=[]), request("nonstring", state={"raw": "JSON"}),
                   request("label", choices=[{"label": "", "description": "x"}]),
                   request("duplicate", choices=[{"label": "A", "description": "x"}, {"label": "A", "description": "y"}]),
                   request("description", choices=[{"label": "A"}]), "not an object"]
        with patch.object(RUNNER, "_infer", side_effect=successful_inference) as infer:
            records = RUNNER.run_choices([*invalid, request("valid")])["records"]
        self.assertEqual([record["status"] for record in records], ["invalid_request"] * 6 + ["ok"])
        self.assertEqual(len(infer.call_args.args[0]), 1)
        for item, record in zip(invalid, records):
            self.assertEqual(record["request"], item)
            self.assertIsNone(record["probabilities"])

    def test_duplicate_ids_and_nonpositive_runtime_settings_are_rejected(self):
        with patch.object(RUNNER, "_infer") as infer:
            with self.assertRaisesRegex(ValueError, "duplicate request id"):
                RUNNER.run_choices([request(), request(state="another")])
            for settings in ({"threads": 0}, {"batch_size": 0}, {"threads": True}, {"batch_size": 1.5}):
                with self.subTest(settings=settings), self.assertRaises(ValueError):
                    RUNNER.run_choices([request()], **settings)
        infer.assert_not_called()

    def test_wrong_model_pin_is_rejected(self):
        def wrong_pin(*args):
            records, pins, runtime = successful_inference(*args)
            pins["parameter_count"] = 123
            return records, pins, runtime
        with patch.object(RUNNER, "_infer", side_effect=wrong_pin), self.assertRaisesRegex(RuntimeError, "pins"):
            RUNNER.run_choices([request()])

    def test_empty_run_loads_no_model_and_softmax_is_stable(self):
        with patch.object(RUNNER, "_infer") as infer:
            result = RUNNER.run_choices([])
        infer.assert_not_called()
        self.assertEqual(result["records"], [])
        self.assertEqual(result["pins"]["parameter_count"], 315203329)
        self.assertEqual(result["pins"]["num_labels"], 1)
        self.assertEqual(RUNNER._softmax([10000.0, 10000.0]), [0.5, 0.5])


class JevPinnedLoaderTests(unittest.TestCase):
    def write_fixture(self, directory):
        path = Path(directory)
        content = {
            "README.md": b"model card",
            "config.json": json.dumps({"model_type": "modernbert", "architectures": ["ModernBertForSequenceClassification"],
                                        "id2label": {"0": "LABEL_0"}}).encode(),
            "jev_modernbert.json": json.dumps({"format_version": RUNNER.FORMAT_VERSION, "max_length": 512}).encode(),
            "tokenizer_config.json": b"{}", "tokenizer.json": b"{}", "model.safetensors": b"fixture weights",
        }
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
        for name, data in content.items():
            (path / name).write_bytes(data)
        manifest = {"schema_version": "generic_model_jev_provenance_v1", "model_id": RUNNER.MODEL_ID,
                    "revision": RUNNER.MODEL_REVISION, "format_version": RUNNER.FORMAT_VERSION,
                    "files": {name: {"sha256": digest,
                                     "source_url": f"https://huggingface.co/{RUNNER.MODEL_ID}/resolve/{RUNNER.MODEL_REVISION}/{name}"}
                              for name, digest in hashes.items()}}
        (path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
        return path, hashes, manifest

    def test_manifest_and_every_pinned_file_are_verified_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            path, hashes, _ = self.write_fixture(directory)
            with patch.object(RUNNER, "JEV_FILE_SHA256", hashes):
                pins = RUNNER.verify_model_files(path)
                self.assertEqual(pins["file_sha256"], hashes)
                self.assertEqual(pins["provenance_manifest_sha256"], RUNNER._sha256(path / "provenance.json"))
                self.assertEqual(set(pins["files"]), set(hashes))
                (path / "tokenizer.json").write_text("changed", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
                    RUNNER.verify_model_files(path)

    def test_missing_manifest_unpinned_revision_or_optional_tokenizer_files_fail(self):
        for alteration in ("missing", "revision", "url", "optional"):
            with self.subTest(alteration=alteration), tempfile.TemporaryDirectory() as directory:
                path, hashes, manifest = self.write_fixture(directory)
                if alteration == "missing":
                    (path / "provenance.json").unlink()
                elif alteration == "revision":
                    manifest["revision"] = "main"
                    (path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
                elif alteration == "url":
                    manifest["files"]["model.safetensors"]["source_url"] = "https://huggingface.co/unpinned"
                    (path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
                else:
                    (path / "added_tokens.json").write_text("{}", encoding="utf-8")
                with patch.object(RUNNER, "JEV_FILE_SHA256", hashes), self.assertRaises((RuntimeError, FileNotFoundError)):
                    RUNNER.verify_model_files(path)

    def test_standard_transformers_loader_enforces_offline_cpu_fp32_scalar_pin(self):
        tokenizer = Mock()
        parameter = SimpleNamespace(device=SimpleNamespace(type="cpu"), dtype="float32", numel=lambda: RUNNER.FP32_PARAMETER_COUNT)
        model = Mock(config=SimpleNamespace(num_labels=1))
        model.to.return_value = model
        model.eval.return_value = model
        model.parameters.return_value = [parameter]
        torch = SimpleNamespace(float32="float32", set_num_threads=Mock())
        transformers = SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=Mock(return_value=tokenizer)),
            AutoModelForSequenceClassification=SimpleNamespace(from_pretrained=Mock(return_value=model)))
        with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}), patch.object(
                RUNNER, "verify_model_files", return_value=RUNNER._pins()):
            result = RUNNER._load_model(Path("/local/pinned"), 4)
        self.assertIs(result[0], tokenizer)
        torch.set_num_threads.assert_called_once_with(4)
        tokenizer_options = transformers.AutoTokenizer.from_pretrained.call_args.kwargs
        model_options = transformers.AutoModelForSequenceClassification.from_pretrained.call_args.kwargs
        for options in (tokenizer_options, model_options):
            self.assertTrue(options["local_files_only"])
            self.assertFalse(options["trust_remote_code"])
            self.assertEqual(options["revision"], RUNNER.MODEL_REVISION)
        self.assertTrue(model_options["use_safetensors"])
        self.assertEqual(model_options["dtype"], "float32")
        self.assertEqual(model_options["attn_implementation"], "eager")
        model.to.assert_called_once_with("cpu")

    def test_loader_rejects_wrong_head_count_device_or_dtype(self):
        for invalid in ("head", "count", "device", "dtype"):
            model = Mock(config=SimpleNamespace(num_labels=3 if invalid == "head" else 1))
            model.to.return_value = model
            model.eval.return_value = model
            parameter = SimpleNamespace(device=SimpleNamespace(type="cuda" if invalid == "device" else "cpu"),
                                        dtype="float16" if invalid == "dtype" else "float32",
                                        numel=lambda: 1 if invalid == "count" else RUNNER.FP32_PARAMETER_COUNT)
            model.parameters.return_value = [parameter]
            torch = SimpleNamespace(float32="float32", set_num_threads=Mock())
            transformers = SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=Mock()),
                                           AutoModelForSequenceClassification=SimpleNamespace(from_pretrained=Mock(return_value=model)))
            with self.subTest(invalid=invalid), patch.dict(sys.modules, {"torch": torch, "transformers": transformers}), patch.object(
                    RUNNER, "verify_model_files", return_value=RUNNER._pins()), self.assertRaises(RuntimeError):
                RUNNER._load_model(Path("/local/pinned"), 4)


if __name__ == "__main__":
    unittest.main()
