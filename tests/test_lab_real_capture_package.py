"""Keep marked synthetic records out of real packages and protect prior files."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "sku-matching"))
import package_real_capture


class RealCapturePackageTests(unittest.TestCase):
    def run_package(self, root, rows, nested_name="products.jsonl"):
        capture = root / ".lab-output" / "sku-real-au-test"
        capture.mkdir(parents=True)
        source = capture / nested_name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        output = root / "real.zip"
        with patch.object(package_real_capture, "ROOT", root), patch.object(package_real_capture, "CODE", ()):
            return package_real_capture.package([capture], [], output)

    def test_rejects_synthetic_path_beneath_allowed_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "Synthetic path"):
                self.run_package(root, [{}], "subdir/synthetic.jsonl")
            self.assertFalse((root / "real.zip").exists())

    def test_rejects_explicit_synthetic_origin_inside_normal_named_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "Synthetic provenance"):
                self.run_package(root, [{"provenance": {"record_kind": "SYNTHETIC_CONTROL"}}])
            self.assertFalse((root / "real.zip").exists())

    def test_preserves_existing_sidecar_when_zip_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sidecar = root / "real.zip.sha256"
            sidecar.write_text("prior digest\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                self.run_package(root, [{"synthetic_data_rows": 0}])
            self.assertEqual(sidecar.read_text(), "prior digest\n")
            self.assertFalse((root / "real.zip").exists())

    def test_zero_synthetic_count_passes_with_verified_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_package(Path(tmp), [{"synthetic_data_rows": 0, "synthetic_data_included": False}])
            self.assertFalse(report["synthetic_data_included"])
            self.assertTrue(report["crc_and_payload_sha256_verified"])


if __name__ == "__main__":
    unittest.main()
