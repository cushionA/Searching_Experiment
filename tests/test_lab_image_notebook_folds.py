"""The GPU notebook must use the exact frozen split bytes selected locally."""
from __future__ import annotations

import importlib.util
import ast
import json
from pathlib import Path
import tempfile
import sys
import unittest
import zipfile
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "experiments/captcha-small-model/build_public_eval_notebook.py"
SPEC = importlib.util.spec_from_file_location("lab_image_notebook_folds", SCRIPT)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class ImageNotebookFoldTests(unittest.TestCase):
    def test_default_notebook_keeps_legacy_models_without_requiring_split_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps({"samples": [{"path": "image.png"}]}))
            output = root / "legacy.ipynb"
            with patch.object(sys, "argv", [str(SCRIPT), "--manifest", str(manifest_path),
                                          "--dataset-ref", "owner/public-fixture", "--output", str(output)]):
                helper.main()
            notebook = json.loads(output.read_bytes())
            run = ast.parse("".join(notebook["cells"][3]["source"]))
            models = next(item.value for item in run.body if isinstance(item, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "models" for target in item.targets))
            self.assertEqual(ast.literal_eval(models), list(helper.MODEL_NAMES))

    def fixture(self, root):
        manifest = json.dumps({"samples": [{"path": "image.png"}]}).encode()
        path = root / "public_full.json"
        path.write_bytes(manifest)
        index = {"parent_manifest_sha256": helper.sha256(manifest), "split_manifests": {}}
        for fold in ("train", "validation", "test"):
            name = f"grouped-{fold}.json"
            raw = json.dumps({"split": {"fold": fold, "parent_manifest_sha256": helper.sha256(manifest)}}).encode()
            (root / name).write_bytes(raw)
            index["split_manifests"][fold] = {"path": name, "sha256": helper.sha256(raw)}
        (root / "grouped-splits.json").write_text(json.dumps(index))
        return path, manifest

    def test_all_folds_are_pinned_and_archive_must_match_each_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, manifest = self.fixture(root)
            assets = helper.split_assets(path, manifest)
            self.assertEqual(set(assets), set(helper.SPLIT_NAMES))
            bundle = root / "evaluation_bundle.bin"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("manifest.json", manifest)
                archive.writestr("image.png", b"fixture image bytes")
                for name, raw in assets.items():
                    archive.writestr(name, raw if name != "grouped-test.json" else raw + b" ")
            with self.assertRaisesRegex(ValueError, "fold bytes differ"):
                helper.validate_bundle(bundle, manifest, {"image.png"}, assets)

    def test_split_from_different_parent_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, manifest = self.fixture(root)
            fold = root / "grouped-test.json"
            data = json.loads(fold.read_bytes())
            data["split"]["parent_manifest_sha256"] = "0" * 64
            fold.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "fold identity"):
                helper.split_assets(path, manifest)

    def test_stale_index_rejects_changed_fold_even_with_matching_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, manifest = self.fixture(root)
            fold = root / "grouped-train.json"
            fold.write_bytes(fold.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "index hash/path"):
                helper.split_assets(path, manifest)


if __name__ == "__main__":
    unittest.main()
