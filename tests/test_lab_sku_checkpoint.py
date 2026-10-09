"""A SKU checkpoint must not replace existing data or extract corrupt entries."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sku_checkpoint", ROOT / "experiments/sku-matching/package_data.py")
checkpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkpoint)


class SkuCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous_root = checkpoint.ROOT
        checkpoint.ROOT = self.root

    def tearDown(self):
        checkpoint.ROOT = self.previous_root
        self.temp.cleanup()

    def archive(self, files, hashes=None):
        path = self.root / "checkpoint.zip"
        manifest = {"files": {name: {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                              for name, body in files.items()}}
        for name, digest in (hashes or {}).items():
            manifest["files"][name]["sha256"] = digest
        with zipfile.ZipFile(path, "w") as out:
            for name, body in files.items():
                out.writestr(name, body)
            out.writestr("SKU-DATA-MANIFEST.json", json.dumps(manifest))
        return path

    def test_restore_reuses_equal_files_and_rejects_different_existing_data(self):
        archive = self.archive({"data/a.json": b'{"a":1}'})
        self.assertEqual(checkpoint.restore(archive)["restored_entries"], 1)
        self.assertEqual(checkpoint.restore(archive)["restored_entries"], 0)
        target = self.root / "data/a.json"
        target.write_bytes(b'{"a":2}')
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            checkpoint.restore(archive)
        self.assertEqual(target.read_bytes(), b'{"a":2}')

    def test_all_hashes_are_checked_before_any_new_file_is_restored(self):
        archive = self.archive({"data/a.json": b"good", "data/b.json": b"bad"},
                               {"data/b.json": "0" * 64})
        with self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
            checkpoint.restore(archive)
        self.assertFalse((self.root / "data/a.json").exists())

    def test_paths_outside_checkout_are_rejected(self):
        archive = self.archive({"../escaped.json": b"unsafe"})
        with self.assertRaisesRegex(ValueError, "Unsafe checkpoint path"):
            checkpoint.restore(archive)


if __name__ == "__main__":
    unittest.main()
