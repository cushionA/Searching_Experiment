import hashlib
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

BASE = Path(__file__).resolve().parents[1]
EXPERIMENT = BASE / "experiments/captcha-small-model"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


recognize = load_module("recognize_local_visual", EXPERIMENT / "recognize_local_visual.py")
evaluate = load_module("evaluate_local_visual", EXPERIMENT / "evaluate_local_visual.py")


class FakeSolver:
    calls = []
    invalid_case = None
    fail_case = None

    def __init__(self, *args, **kwargs):
        pass

    def _result(self, target, count):
        if target == self.fail_case:
            raise RuntimeError("intentional solver failure")
        if target == self.invalid_case:
            return {"selection": [count], "scores": [0.0] * count, "elapsed_ms": 1}
        return {
            "selection": [1 if count == 9 else 0],
            "scores": [0.0] * count,
            "model": {"fake": True},
            "elapsed_ms": 1,
        }

    def predict_type_a(self, paths, target):
        self.calls.append(("A", list(paths), target))
        return self._result(target, 9)

    def predict_type_b(self, path, target, rows=4, columns=4):
        with Image.open(path) as image:
            pixels = image.convert("RGB")
            observed = [
                pixels.getpixel((column * 3 + 1, row * 2 + 1))
                for row in range(4)
                for column in range(4)
            ]
            dimensions = pixels.size
        self.calls.append(("B", observed, dimensions, target, path))
        result = self._result(target, 16)
        result["boxes"] = [[0.0, 0.0, 3.0, 2.0]]
        result["detection_scores"] = [0.75]
        return result


class LocalEvalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.images = self.root / "images"
        self.images.mkdir()
        self.model_root = self.root / "model"
        self.model_root.mkdir()
        (self.model_root / "manifest.json").write_text("{}")
        FakeSolver.calls = []
        FakeSolver.invalid_case = None
        FakeSolver.fail_case = None

    def tearDown(self):
        self.temp.cleanup()

    def make_case(self, identifier, challenge_type, color):
        tile_count = 9 if challenge_type == "Type A" else 16
        paths = []
        hashes = []
        for index in range(tile_count):
            name = f"{identifier}-{index}.png"
            path = self.images / name
            Image.new("RGB", (3, 2), (color + index, 0, 0)).save(path)
            paths.append(name)
            hashes.append(sha256(path))
        return {
            "id": identifier,
            "source_type": challenge_type,
            "target": "car",
            "original_keyword": "cars",
            "tile_paths": paths,
            "tile_sha256": hashes,
            "correct_answers": [1] if challenge_type == "Type A" else [0],
        }

    def run_eval(self, cases, output, challenge_type="all", limit=None):
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps({"cases": cases}))
        argv = [
            "--manifest", str(manifest_path),
            "--image-root", str(self.images),
            "--output-dir", str(output),
            "--type", challenge_type,
            "--model-root", str(self.model_root),
        ]
        if limit is not None:
            argv.extend(["--limit", str(limit)])
        fake_module = types.SimpleNamespace(LocalVisualSolver=FakeSolver)
        with patch.dict("sys.modules", {"local_visual": fake_module}):
            return evaluate.main(argv)

    def test_routes_types_metrics_and_prompt_exclude_gold(self):
        type_a = self.make_case("a", "Type A", 10)
        type_b = self.make_case("b", "Type B", 30)
        output = self.root / "output"
        self.run_eval([type_a, type_b], output)
        self.assertEqual([call[0] for call in FakeSolver.calls], ["A", "B"])
        self.assertEqual(FakeSolver.calls[0][2], "cars")
        metrics = json.loads((output / "metrics.json").read_text())
        self.assertEqual(metrics["count"], 2)
        self.assertEqual(metrics["by_type"]["Type A"]["count"], 1)
        self.assertEqual(metrics["by_type"]["Type B"]["count"], 1)
        protocol = json.loads((output / "protocol.json").read_text())
        self.assertEqual(protocol["status"], "completed")
        prediction = json.loads((output / "predictions.jsonl").read_text().splitlines()[0])
        self.assertNotIn("correct_answers", prediction)

    def test_type_b_reconstruction_preserves_all_row_major_tile_positions(self):
        case = self.make_case("board", "Type B", 20)
        output = self.root / "output"
        self.run_eval([case], output, "B")
        _, observed, dimensions, target, derived_path = FakeSolver.calls[0]
        expected = [(20 + index, 0, 0) for index in range(16)]
        self.assertEqual(dimensions, (12, 8))
        self.assertEqual(observed, expected)
        self.assertEqual(target, "cars")
        row = json.loads((output / "predictions.jsonl").read_text())
        self.assertEqual(row["derived_image_path"], derived_path.name)
        self.assertEqual(row["derived_image_sha256"], sha256(derived_path))
        self.assertEqual(row["boxes"], [[0.0, 0.0, 3.0, 2.0]])
        self.assertEqual(row["detection_scores"], [0.75])

    def test_limit_leaves_unselected_images_unneeded(self):
        selected = self.make_case("selected", "Type A", 10)
        unselected = self.make_case("unselected", "Type A", 30)
        for relative in unselected["tile_paths"]:
            (self.images / relative).unlink()
        output = self.root / "output"
        self.run_eval([selected, unselected], output, "A", 1)
        self.assertEqual(len(FakeSolver.calls), 1)
        protocol = json.loads((output / "protocol.json").read_text())
        self.assertIn("first N", protocol["limit_semantics"])

    def test_hash_mismatch_rejected_before_model_or_output_creation(self):
        case = self.make_case("board", "Type A", 10)
        case["tile_sha256"][0] = "0" * 64
        output = self.root / "output"
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.run_eval([case], output, "A")
        self.assertFalse(output.exists())
        self.assertEqual(FakeSolver.calls, [])

    def test_invalid_solver_selection_marks_run_failed(self):
        case = self.make_case("bad", "Type A", 10)
        FakeSolver.invalid_case = "cars"
        output = self.root / "output"
        with self.assertRaisesRegex(ValueError, "out of bounds"):
            self.run_eval([case], output, "A")
        protocol = json.loads((output / "protocol.json").read_text())
        self.assertEqual(protocol["status"], "failed")
        self.assertEqual(protocol["completed_count"], 0)

    def test_failure_preserves_prior_prediction_and_partial_metrics(self):
        first = self.make_case("first", "Type A", 10)
        second = self.make_case("second", "Type A", 30)
        FakeSolver.fail_case = "cars"
        # Make the second target fail while retaining the first target as valid.
        second["original_keyword"] = "fail"
        FakeSolver.fail_case = "fail"
        output = self.root / "output"
        with self.assertRaisesRegex(RuntimeError, "intentional"):
            self.run_eval([first, second], output, "A")
        rows = (output / "predictions.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1)
        protocol = json.loads((output / "protocol.json").read_text())
        self.assertEqual(protocol["status"], "failed")
        self.assertEqual(protocol["completed_count"], 1)
        self.assertEqual(json.loads((output / "metrics.json").read_text())["count"], 1)

    def test_reference_alias_disagreement_rejected(self):
        case = self.make_case("board", "Type A", 10)
        case["reference_selection"] = [2]
        output = self.root / "output"
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.run_eval([case], output, "A")
        self.assertFalse(output.exists())

    def test_existing_output_rejected(self):
        output = self.root / "output"
        output.mkdir()
        with self.assertRaises(SystemExit):
            self.run_eval([], output)


if __name__ == "__main__":
    unittest.main()
