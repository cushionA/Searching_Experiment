from __future__ import annotations
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_visual", ROOT / "experiments/captcha-small-model/local_visual.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class LocalVisualGeometryTests(unittest.TestCase):
    def test_clip_margin_threshold(self):
        self.assertEqual(module.select_clip_tiles([.5, .2], [.4, .1], .099), [0, 1])
    def test_clip_rejects_nonfinite(self):
        with self.assertRaises(ValueError): module.select_clip_tiles([float("nan")], [0], .01)
    def test_clip_rejects_length_mismatch(self):
        with self.assertRaises(ValueError): module.select_clip_tiles([.5], [], .01)
    def test_box_coverage_and_score(self):
        picked, scores = module.boxes_to_tiles([[0, 0, 50, 50]], [.8], (100, 100), 2, 2, .5, .9)
        self.assertEqual(picked, [0])
        self.assertEqual(scores, [.8, 0., 0., 0.])
    def test_each_box_coverage_is_independent(self):
        picked, _ = module.boxes_to_tiles([[0, 0, 5, 10], [5, 0, 10, 10]], [.9, .9], (10, 10), 1, 1, .5, .8)
        self.assertEqual(picked, [])
    def test_bbox_clips_to_image(self):
        picked, _ = module.boxes_to_tiles([[-5, -5, 5, 5]], [.9], (10, 10), 1, 1, .5, .25)
        self.assertEqual(picked, [0])
    def test_invalid_bbox_nan_and_grid(self):
        with self.assertRaises(ValueError): module.boxes_to_tiles([[0, 0, float("inf"), 1]], [.9], (10, 10), 1, 1, .5, .1)
        with self.assertRaises(ValueError): module.boxes_to_tiles([], [], (10, 10), 0, 1, .5, .1)

    def make_manifest(self, root, *, clip_files=None, clip_path="weights.safetensors", clip_repo="laion/CLIP-ViT-B-32-laion2B-s34B-b79K", clip_revision="1a25a446712ba5ee05982a381eed697ef9b435cf", clip_type="clip"):
        models = {}
        for name, repo, rev, model_type in (("clip", clip_repo, clip_revision, clip_type), ("owl", "google/owlv2-base-patch16-ensemble", "cfd3195ba4ea9592eec887ded089f4c08eff231d", "owlv2")):
            directory = root / name
            directory.mkdir()
            entries = []
            names = ["config.json", clip_path if name == "clip" else "weights.safetensors"]
            for rel in (clip_files if name == "clip" and clip_files is not None else names):
                if ".." not in Path(rel).parts and not Path(rel).is_absolute():
                    target = directory / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(rel.encode())
                    entries.append({"path": rel, "sha256": hashlib.sha256(rel.encode()).hexdigest()})
                else:
                    entries.append({"path": rel, "sha256": "0" * 64})
            models[name] = {"repo_id": repo, "revision": rev, "model_type": model_type, "directory": name, "files": entries}
        (root / "manifest.json").write_text(json.dumps({"format": "local-visual-models-v1", "models": models}))

    def test_manifest_rejects_empty_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.make_manifest(root, clip_files=[])
            with self.assertRaises(ValueError): module._manifest(root)

    def test_manifest_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.make_manifest(root, clip_path="../outside")
            with self.assertRaises(ValueError): module._manifest(root)

    def test_manifest_requires_allowed_repository_and_model_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.make_manifest(root, clip_repo="attacker/model")
            with self.assertRaises(ValueError): module._manifest(root)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.make_manifest(root, clip_type="owlv2")
            with self.assertRaises(ValueError): module._manifest(root)

    def test_manifest_rejects_added_unverified_tokenizer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_manifest(root)
            (root / "clip" / "tokenizer.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "unverified files"):
                module._manifest(root)

    def test_detection_probability_outside_unit_interval_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "detection score"):
            module.boxes_to_tiles([[0, 0, 10, 10]], [1.1], (10, 10), 1, 1, .5, .1)

    def test_constructor_validates_unit_interval_before_loading_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.make_manifest(root)
            with self.assertRaises(ValueError): module.LocalVisualSolver(root, confidence_threshold=1.1)
            with self.assertRaises(ValueError): module.LocalVisualSolver(root, coverage_threshold=-.1)

    def test_scores_are_finite(self):
        picked, scores = module.boxes_to_tiles([[0, 0, 10, 10]], [.7], (10, 10), 1, 1, .5, .1)
        self.assertEqual(picked, [0]); self.assertTrue(all(__import__('math').isfinite(x) for x in scores))

if __name__ == "__main__": unittest.main()
