"""GPU contracts tested with mocks; these tests make no speed/GPU claims."""
from contextlib import nullcontext
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("product_gpu_runner", HERE / "kaggle_product_condition_gpu_v1.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
import test_lab_sku_product_condition_models as PRODUCT
import test_lab_sku_scoped_html_evidence as STRUCTURE


class Device:
    def __init__(self, kind):
        self.type = kind

    def __str__(self):
        return self.type + (":0" if self.type == "cuda" else "")


class Tensor:
    def __init__(self, values, device="cpu", dtype="fp32"):
        self.values, self.device, self.dtype = values, Device(device), dtype

    def to(self, target):
        return Tensor(self.values, target, self.dtype) if target in {"cpu", "cuda"} else Tensor(self.values, self.device.type, target)

    def cpu(self):
        return self.to("cpu")

    def detach(self):
        return self

    def tolist(self):
        return self.values


class Tokenizer:
    def __init__(self, specs=None, fail=None):
        self.specs, self.fail, self.calls = specs or {}, fail, []

    def __call__(self, first, second, **options):
        self.calls.append((deepcopy(first), deepcopy(second), options))
        if isinstance(second, list):
            return {"input_ids": Tensor([[self.specs.get(text, (12, 1))[1]] for text in second])}
        if second == self.fail:
            raise ValueError("mock tokenizer failure")
        count, value = self.specs.get(second, (12, 1))
        return {"input_ids": [value] * count}

    def pad(self, encodings, **options):
        return {"input_ids": Tensor([row["input_ids"] for row in encodings])}


class Model:
    def __init__(self, columns=1, fail=False, parameter_device="cuda", parameter_dtype="fp32"):
        self.columns, self.fail, self.calls = columns, fail, []
        self.config = SimpleNamespace(num_labels=columns, _attn_implementation="eager")
        count = R.JEV.FP32_PARAMETER_COUNT if columns == 1 else R.NLI.FP32_PARAMETER_COUNT
        self.parameter = SimpleNamespace(device=Device(parameter_device), dtype=parameter_dtype, numel=lambda: count)

    def __call__(self, **features):
        self.calls.append(features)
        if self.fail:
            raise RuntimeError("mock forward failure")
        values = features["input_ids"].values
        rows = [[float(row[0])] if self.columns == 1 else [float(row[0]), 0., -1.] for row in values]
        return SimpleNamespace(logits=Tensor(rows, "cuda"))

    def parameters(self):
        return iter([self.parameter])

    def to(self, device):
        return self

    def eval(self):
        return self


def torch_mock(available=True):
    cuda = SimpleNamespace(is_available=lambda: available, current_device=lambda: 0,
        get_device_properties=lambda _: SimpleNamespace(total_memory=12345),
        get_device_name=lambda _: "mock GPU", get_device_capability=lambda _: (7, 5),
        synchronize=Mock(), memory_allocated=lambda: 100, memory_reserved=lambda: 200,
        max_memory_allocated=lambda: 300, max_memory_reserved=lambda: 400)
    return SimpleNamespace(cuda=cuda, float32="fp32", float64="fp64", version=SimpleNamespace(cuda="mock"),
        backends=SimpleNamespace(cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=True)),
                                 cudnn=SimpleNamespace(allow_tf32=True)),
        set_num_threads=Mock(), set_float32_matmul_precision=Mock(),
        inference_mode=nullcontext, autocast=lambda *_args, **_kwargs: nullcontext())


def scorer(tokenizer=None, model=None, kind="jev"):
    with patch.dict(sys.modules, torch=torch_mock()):
        result = R.CudaScorer(Path("mock-jev"), Path("mock-nli"))
    pins = R.cuda_jev_pins() if kind == "jev" else {
        "model_id": R.NLI.MODEL_ID, "revision": R.NLI.MODEL_REVISION,
        "parameter_count": R.NLI.FP32_PARAMETER_COUNT, "device": "cuda", "precision": "fp32"}
    result.loaded[kind] = (tokenizer or Tokenizer(), model or Model(1 if kind == "jev" else 3), pins)
    return result


def choice(identifier="a"):
    return {"id": identifier, "question": " 質問 ", "state": "原文\nそのまま",
            "choices": [{"label": " 元の値 ", "description": ""},
                        {"label": "別の値", "description": "説明"},
                        {"label": "特定できない", "description": "不明"}]}


def pair(identifier="p", hypothesis="条件"):
    return {"id": identifier, "premise": " 原文 ", "hypothesis": hypothesis, "metadata": {"opaque": identifier}}


class ProductGpuContracts(unittest.TestCase):
    def test_cuda_required_tf32_disabled_and_no_cpu_fallback(self):
        with patch.dict(sys.modules, torch=torch_mock(available=False)):
            with self.assertRaisesRegex(RuntimeError, "real CUDA"):
                R.CudaScorer(Path("jev"), Path("nli"))
        torch = torch_mock()
        with patch.dict(sys.modules, torch=torch):
            loaded = R.CudaScorer(Path("jev"), Path("nli"))
        self.assertFalse(torch.backends.cuda.matmul.allow_tf32)
        self.assertFalse(torch.backends.cudnn.allow_tf32)
        torch.set_float32_matmul_precision.assert_called_once_with("highest")
        self.assertTrue(loaded.runtime["synchronized_inference_timing"])

    def test_loader_verifies_actual_fp32_cuda_parameters_and_file_pins(self):
        for device, dtype in (("cpu", "fp32"), ("cuda", "fp16"), ("cuda", "fp32")):
            with self.subTest(device=device, dtype=dtype):
                model = Model(parameter_device=device, parameter_dtype=dtype)
                transformer = SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=Mock(return_value=Tokenizer())),
                    AutoModelForSequenceClassification=SimpleNamespace(from_pretrained=Mock(return_value=model)))
                with patch.dict(sys.modules, torch=torch_mock(), transformers=transformer), patch.object(
                        R.JEV, "verify_model_files", return_value=R.JEV._pins()) as verify:
                    loaded = R.CudaScorer(Path("jev"), Path("nli"))
                    if device != "cuda" or dtype != "fp32":
                        with self.assertRaisesRegex(RuntimeError, "FP32 on CUDA"):
                            loaded.load("jev")
                    else:
                        _, _, pins, _ = loaded.load("jev")
                        self.assertEqual(pins["actual_parameter_devices"], ["cuda:0"])
                        self.assertEqual(pins["actual_parameter_dtypes"], ["fp32"])
                        loaded.load("jev")
                        self.assertEqual(loaded.telemetry["jev"]["model_load_count"], 1)
                    verify.assert_called_once()
                options = transformer.AutoModelForSequenceClassification.from_pretrained.call_args.kwargs
                self.assertEqual(options["attn_implementation"], "eager")
                self.assertEqual(options["revision"], R.JEV.MODEL_REVISION)
                self.assertFalse(options["trust_remote_code"])

    def test_jev_exact_whole_options_and_gpu_dedup_cache_keep_cpu_helpers_unchanged(self):
        item = choice()
        tokenizer = Tokenizer({" 元の値 ": (512, 4), "別の値 — 説明": (21, 1), "特定できない — 不明": (20, -2)})
        model = Model()
        runner = scorer(tokenizer, model)
        original_infer, original_pins = R.JEV._infer, R.JEV._pins
        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "0"}):
            result = runner.run_choices([item, {**item, "id": "repeat"}])
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "0")
        self.assertIs(R.JEV._infer, original_infer)
        self.assertIs(R.JEV._pins, original_pins)
        records = result["records"]
        self.assertEqual([r["id"] for r in records], ["a", "repeat"])
        self.assertEqual(records[0]["choices"], item["choices"])
        self.assertEqual(list(records[0]["logits"]), [" 元の値 ", "別の値", "特定できない"])
        self.assertEqual(records[0]["argmax_label"], " 元の値 ")
        self.assertEqual(records[0]["cache_key"], R.JEV.request_key(item["question"], item["state"], item["choices"]))
        self.assertTrue(records[1]["cache_reused"])
        self.assertEqual(len(model.calls), 1)
        self.assertTrue(all(tensor.device.type == "cuda" for tensor in model.calls[0].values()))
        self.assertGreaterEqual(runner.torch.cuda.synchronize.call_count, 2)
        for _, _, options in tokenizer.calls:
            self.assertEqual(options, {"truncation": False, "padding": False, "add_special_tokens": True})
        cached = runner.run_choices([item], cache={records[0]["cache_key"]: records[0]})
        self.assertEqual(cached["records"][0]["logits"], records[0]["logits"])
        self.assertEqual(len(model.calls), 1)
        altered = deepcopy(records[0])
        altered["device"] = "cpu"
        with self.assertRaisesRegex(ValueError, "not CPU output"):
            runner.run_choices([item], cache={altered["cache_key"]: altered})

    def test_jev_overlong_invalid_and_forward_error_remain_unknown(self):
        item = choice()
        for tokenizer, model, expected in ((Tokenizer({" 元の値 ": (513, 1)}), Model(), "input_too_long"),
                                            (Tokenizer(), Model(fail=True), "inference_error")):
            result = scorer(tokenizer, model).run_choices([item])["records"][0]
            self.assertEqual(result["status"], expected)
            self.assertIsNone(result["argmax_label"])
        invalid = scorer().run_choices([{**item, "choices": []}])["records"][0]
        self.assertEqual(invalid["status"], "invalid_request")
        self.assertIsNone(invalid["probabilities"])

    def test_nli_exact_double_tokenization_limit_order_mapping_and_threshold(self):
        tokenizer = Tokenizer({"条件": (512, 4), "長文": (513, 4)})
        model = Model(3)
        runner = scorer(tokenizer, model, "nli")
        inputs = [pair("long", "長文"), pair("short"), pair("repeat")]
        result = runner.run_pairs(inputs)
        records = result["records"]
        self.assertEqual([r["id"] for r in records], ["long", "short", "repeat"])
        self.assertIsNone(records[0]["probabilities"])
        self.assertEqual(records[0]["relation"], "unknown")
        self.assertEqual(records[1]["token_count_untruncated"], 512)
        self.assertEqual(records[1]["probabilities"], R.NLI.relation_probabilities([4., 0., -1.])[0])
        self.assertEqual(records[1]["confidence_threshold"], .90)
        self.assertEqual(records[1]["relation"], "support")
        self.assertTrue(records[2]["cache_reused"])
        self.assertEqual(len(model.calls), 1)
        individual = [c for c in tokenizer.calls if isinstance(c[1], str)]
        batch = [c for c in tokenizer.calls if isinstance(c[1], list)]
        self.assertEqual(len(individual), 2)
        self.assertEqual(len(batch), 1)
        self.assertTrue(all(c[2] == {"truncation": False, "add_special_tokens": True} for c in individual))
        self.assertEqual(batch[0][2], {"padding": True, "truncation": False, "return_tensors": "pt"})
        cached = runner.run_pairs(inputs, cache={r["cache_key"]: r for r in records})
        self.assertEqual([r["relation"] for r in cached["records"]], [r["relation"] for r in records])
        self.assertEqual(len(model.calls), 1)

    def test_nli_verified_tokenizer_chunk_error_is_cacheable_unknown(self):
        runner = scorer(Tokenizer(fail="bad"), Model(3), "nli")
        pairs = [pair("a", "bad"), pair("b", "good")]
        result = R.FULL.score_nli_chunk(runner.run_pairs, pairs, Path("mock-nli"), 4, 16)
        self.assertEqual([r["relation"] for r in result["records"]], ["unknown", "unknown"])
        self.assertTrue(all(r["error_type"] == "ValueError" for r in result["records"]))
        cached = runner.run_pairs(pairs, cache={r["cache_key"]: r for r in result["records"]})
        self.assertEqual([r["relation"] for r in cached["records"]], ["unknown", "unknown"])

    def test_nli_batch_failure_keeps_all_failed_pairs_unknown(self):
        runner = scorer(Tokenizer(), Model(3, fail=True), "nli")
        result = runner.run_pairs([pair("a", "first"), pair("b", "second")])
        self.assertEqual(result["runtime"]["inference_error_count"], 2)
        self.assertTrue(all(r["probabilities"] is None and r["relation"] == "unknown" for r in result["records"]))

    def test_honest_mixed_development_guard_keeps_sha_checks_and_rejects_test(self):
        data, _ = PRODUCT.fixtures()
        with tempfile.TemporaryDirectory() as td:
            prepared = Path(td) / "prep"
            STRUCTURE.prepare(prepared, data)
            manifest = json.loads((prepared / "manifest.json").read_text())
            manifest["errors_only"] = False
            (prepared / "manifest.json").write_text(json.dumps(manifest))
            self.assertFalse(R.development_loader()(prepared)[-1]["errors_only"])
            with self.assertRaises(ValueError):
                R.V1.load_inputs(prepared)
            manifest["independent_test"] = True
            (prepared / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                R.development_loader()(prepared)
            manifest["independent_test"] = False
            (prepared / "manifest.json").write_text(json.dumps(manifest))
            with (prepared / "requests.jsonl").open("a") as handle:
                handle.write("{}\n")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                R.development_loader()(prepared)

    def test_placeholder_baseline_cannot_be_silently_replayed(self):
        data, contract = PRODUCT.fixtures()
        with tempfile.TemporaryDirectory() as td:
            prepared, eligible, output = Path(td) / "prep", Path(td) / "eligible.json", Path(td) / "out"
            STRUCTURE.prepare(prepared, data)
            manifest = json.loads((prepared / "manifest.json").read_text())
            manifest.update(errors_only=False, baseline_predictions_are_unevaluated=True,
                            baseline_must_be_regenerated_for_all_requests=True)
            (prepared / "manifest.json").write_text(json.dumps(manifest))
            contract["source_sha256"] = {"prepared": {name: R.TASK.sha(prepared / name)
                for name in ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl")}}
            R.TASK.write_json(eligible, contract)
            with self.assertRaisesRegex(ValueError, "refresh-baseline"):
                R.run(prepared, eligible, output)
            self.assertFalse(output.exists())

    def test_end_to_end_refreshes_all_requests_preserves_bindings_and_freezes_before_inference(self):
        data, contract = PRODUCT.fixtures()
        requests, documents = data[:2]
        choices = R.FULL.choice_requests(requests, {d["document_id"]: d for d in documents})
        specs = {R.JEV.render_candidate(c): (30, float(len(row["choices"]) - i))
                 for row in choices for i, c in enumerate(row["choices"])}
        runner = scorer(Tokenizer(specs), Model())
        runner.loaded["nli"] = (Tokenizer(), Model(3), {
            "model_id": R.NLI.MODEL_ID, "revision": R.NLI.MODEL_REVISION,
            "parameter_count": R.NLI.FP32_PARAMETER_COUNT, "precision": "fp32", "device": "cuda"})
        with tempfile.TemporaryDirectory() as td:
            prepared, eligible, output = Path(td) / "prep", Path(td) / "eligible.json", Path(td) / "out"
            STRUCTURE.prepare(prepared, data)
            manifest = json.loads((prepared / "manifest.json").read_text())
            manifest.update(errors_only=False, baseline_predictions_are_unevaluated=True,
                            baseline_must_be_regenerated_for_all_requests=True)
            (prepared / "manifest.json").write_text(json.dumps(manifest))
            contract["source_sha256"] = {"prepared": {name: R.TASK.sha(prepared / name)
                for name in ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl")}}
            R.TASK.write_json(eligible, contract)
            received = {name: R.TASK.sha(prepared / name) for name in R.SOURCE_NAMES}
            original = runner.run_choices

            def frozen_first(*args, **kwargs):
                self.assertTrue((output / "execution-freeze.json").is_file())
                return original(*args, **kwargs)

            with patch.object(runner, "run_choices", side_effect=frozen_first):
                summary = R.run(prepared, eligible, output, scorer=runner, refresh=True,
                                reranker_factory=STRUCTURE.FakeReranker)
            self.assertEqual(summary["stage_devices"], {"reranker": "cpu", "jev": "cuda", "nli": "cuda"})
            self.assertFalse(summary["errors_only"])
            self.assertEqual(summary["baseline_refresh_summary"]["request_count"], len(requests))
            fresh = R.FULL.read(output / "baseline-refresh/prepared/baseline-predictions.jsonl")
            self.assertEqual([p["source_task_bindings"] for p in fresh], [p["source_task_bindings"] for p in data[3]])
            self.assertEqual([p["source_request"] for p in fresh], requests)
            self.assertEqual(received, {name: R.TASK.sha(prepared / name) for name in R.SOURCE_NAMES})
            self.assertTrue(summary["received_source_and_code_verified_after_inference"])
            self.assertEqual(R.JEV._pins()["device"], "cpu")
            fresh_manifest = json.loads((output / "baseline-refresh/prepared/manifest.json").read_text())
            self.assertFalse(fresh_manifest["baseline_predictions_are_unevaluated"])
            for variant in R.TASK.VARIANTS:
                result = R.FULL.read(output / (variant + "-predictions.jsonl"))
                self.assertEqual(len(result), len(requests))
                self.assertEqual(result[-1], fresh[-1])

    def test_device_only_replay_preserves_cpu_model_inputs_and_prediction_states(self):
        data, contract = PRODUCT.fixtures()
        runner = scorer()

        def jev_mock(inputs, model_dir, threads, batch_size, on_ready):
            records, _, runtime = STRUCTURE.fake_jev_infer(inputs, model_dir, threads, batch_size, lambda *_: None)
            pins = R.cuda_jev_pins()
            on_ready(pins, runtime)
            return records, pins, runtime

        def nli_mock(inputs, model_dir, threads, batch_size, on_ready):
            records, pins, runtime = STRUCTURE.fake_nli_infer(inputs, model_dir, threads, batch_size, lambda *_: None)
            pins = {**pins, "device": "cuda", "precision": "fp32"}
            on_ready(pins, runtime)
            return records, pins, runtime

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ):
            prepared, eligible = Path(td) / "prep", Path(td) / "eligible.json"
            cpu_output, gpu_output = Path(td) / "cpu", Path(td) / "gpu"
            STRUCTURE.prepare(prepared, data)
            contract["source_sha256"] = {"prepared": {name: R.TASK.sha(prepared / name)
                for name in ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl")}}
            R.TASK.write_json(eligible, contract)
            with patch.object(R.JEV, "_infer", side_effect=STRUCTURE.fake_jev_infer), patch.object(
                    R.NLI, "_infer", side_effect=STRUCTURE.fake_nli_infer):
                R.TASK.run(prepared, eligible, cpu_output, reranker_factory=STRUCTURE.FakeReranker,
                           jev_runner=R.JEV.run_choices, nli_runner=R.NLI.run_pairs)
            with patch.object(runner, "_infer_jev", side_effect=jev_mock), patch.object(
                    runner, "_infer_nli", side_effect=nli_mock):
                summary = R.run(prepared, eligible, gpu_output, scorer=runner,
                                reranker_factory=STRUCTURE.FakeReranker)
            for name in ("model-requests.jsonl", "model-nli-pairs.jsonl", "product-groups.jsonl"):
                self.assertEqual((cpu_output / name).read_bytes(), (gpu_output / name).read_bytes())
            for variant in R.TASK.VARIANTS:
                cpu = R.TASK.read(cpu_output / (variant + "-predictions.jsonl"))
                gpu = R.TASK.read(gpu_output / (variant + "-predictions.jsonl"))
                self.assertEqual(cpu, gpu)
            self.assertEqual(summary["baseline_mode"], "received-baseline-parity-replay")
            self.assertTrue(summary["saved_prediction_fanout_and_literal_references_verified"])

    def test_bootstrap_reuses_revision_pinned_existing_acquisition(self):
        with tempfile.TemporaryDirectory() as td:
            roots = {"jev": Path(td) / "jev", "nli": Path(td) / "nli"}
            with patch.object(R.WEIGHTS, "acquire_weights", return_value=roots) as acquire, patch(
                    "download_candidate.download_candidate", return_value=Path(td) / "reranker") as reranker:
                result = R.acquire_models(Path(td))
            provenance = json.loads((Path(td) / "jev-provenance.json").read_text())
            self.assertEqual(provenance["revision"], R.JEV.MODEL_REVISION)
            self.assertEqual(set(provenance["files"]), set(R.JEV.JEV_FILE_SHA256))
            for name, entry in provenance["files"].items():
                self.assertEqual(entry["sha256"], R.JEV.JEV_FILE_SHA256[name])
                self.assertIn("/resolve/" + R.JEV.MODEL_REVISION + "/", entry["source_url"])
            acquire.assert_called_once_with(Path(td), Path(td) / "jev-provenance.json", download=True)
            reranker.assert_called_once_with("reranker", Path(td) / "reranker")
            self.assertEqual(set(result), {"jev", "nli", "reranker"})


if __name__ == "__main__":
    unittest.main()
