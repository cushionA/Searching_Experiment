"""Archive safety and label-preservation fixtures for OCR manifest recovery."""

from __future__ import annotations

import importlib.util
import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "experiments" / "captcha-small-model" / "prepare_text_manifest.py"
SPEC = importlib.util.spec_from_file_location("prepare_text_manifest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
manifest_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manifest_tool)


def png_bytes() -> bytes:
    image = Image.new("RGB", (3, 2), (20, 40, 60))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def jpeg_bytes() -> bytes:
    image = Image.new("RGB", (3, 2), (20, 40, 60))
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


class TextManifestArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_identical_kaggle_entries_deduplicate_and_keep_aliases(self) -> None:
        archive = self.root / "kaggle.zip"
        payload = png_bytes()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("dataset/captcha-version-2-images/abcde.png", payload)
            bundle.writestr("mirror/captcha-version-2-images/abcde.png", payload)
        source = {
            "key": "kaggle_fournierp_captcha_version_2",
            "count": 1,
            "archive_image_entries": 2,
        }

        records = manifest_tool.write_source_images(archive, self.root / "output", source, set())

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["label"], "abcde")
        self.assertEqual(records[0]["archive_member"], "dataset/captcha-version-2-images/abcde.png")
        self.assertEqual(
            records[0]["archive_aliases"],
            [
                "dataset/captcha-version-2-images/abcde.png",
                "mirror/captcha-version-2-images/abcde.png",
            ],
        )
        self.assertEqual((self.root / "output" / records[0]["path"]).read_bytes(), payload)

    def test_identical_kaggle_bytes_with_conflicting_labels_fail(self) -> None:
        archive = self.root / "conflict.zip"
        payload = png_bytes()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("a/abcde.png", payload)
            bundle.writestr("b/fghij.png", payload)
        source = {"key": "kaggle_fournierp_captcha_version_2", "count": 1}

        with self.assertRaisesRegex(ValueError, "conflicting labels"):
            manifest_tool.write_source_images(archive, self.root / "output", source, set())

    def test_sloth_label_keeps_leading_zeroes(self) -> None:
        archive = self.root / "sloth.tar.gz"
        payload = jpeg_bytes()
        with tarfile.open(archive, "w:gz") as bundle:
            info = tarfile.TarInfo("test/001251.5a12ece127743d6023d5fd52728d2084.jpg")
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
        source = {"key": "project_sloth_captcha_images_test", "count": 1}

        records = manifest_tool.write_source_images(archive, self.root / "output", source, set())

        self.assertEqual(records[0]["label"], "001251")
        self.assertEqual(records[0]["archive_member"], "test/001251.5a12ece127743d6023d5fd52728d2084.jpg")

    def test_zip_path_traversal_is_rejected(self) -> None:
        archive = self.root / "traversal.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("../escape.png", png_bytes())
        source = {"key": "kaggle_fournierp_captcha_version_2", "count": 1}

        with self.assertRaisesRegex(ValueError, "Unsafe archive member path"):
            manifest_tool.write_source_images(archive, self.root / "output", source, set())

    def test_prepare_refuses_existing_output_directory(self) -> None:
        output = self.root / "already-there"
        output.mkdir()
        with self.assertRaises(FileExistsError):
            manifest_tool.prepare(self.root / "missing.tar.gz", self.root / "missing.zip", output)

    def test_prepare_rejects_unpinned_archives_without_leaving_output(self) -> None:
        sloth = self.root / "untrusted.tar.gz"
        kaggle = self.root / "untrusted.zip"
        sloth.write_bytes(b"untrusted archive bytes")
        kaggle.write_bytes(b"untrusted archive bytes")
        output = self.root / "new-output"
        with self.assertRaisesRegex(ValueError, "archive SHA-256 differs"):
            manifest_tool.prepare(sloth, kaggle, output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".new-output.staging-*")), [])


if __name__ == "__main__":
    unittest.main()
