import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "experiments/captcha-small-model/benchmark_pe_core.py"
SPEC = importlib.util.spec_from_file_location("lab_pe_source_test_helper", SCRIPT)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class PESourceIntegrityTests(unittest.TestCase):
    def source_spec(self, data):
        return {"core/example.py": (len(data), hashlib.sha256(data).hexdigest())}

    def test_download_checks_bytes_and_uses_pinned_commit(self):
        data = b"# pinned test source\n"
        with tempfile.TemporaryDirectory() as directory, patch.dict(helper.SOURCE_FILES, self.source_spec(data), clear=True), \
                patch.object(helper.urllib.request, "urlopen", return_value=io.BytesIO(data)) as request:
            root = helper.ensure_source(Path(directory))
            self.assertEqual((root / "core/example.py").read_bytes(), data)
            self.assertIn(helper.SOURCE_COMMIT, request.call_args.args[0])
            self.assertFalse((root / "core/example.py.partial").exists())

    def test_corrupt_cached_source_is_rejected_without_network(self):
        data = b"expected"
        with tempfile.TemporaryDirectory() as directory, patch.dict(helper.SOURCE_FILES, self.source_spec(data), clear=True), \
                patch.object(helper.urllib.request, "urlopen") as request:
            path = Path(directory) / "core/example.py"
            path.parent.mkdir()
            path.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "cached source"):
                helper.ensure_source(Path(directory))
            request.assert_not_called()

    def test_truncated_source_is_not_promoted_to_finished_cache(self):
        data = b"expected bytes"
        with tempfile.TemporaryDirectory() as directory, patch.dict(helper.SOURCE_FILES, self.source_spec(data), clear=True), \
                patch.object(helper.urllib.request, "urlopen", return_value=io.BytesIO(data[:-1])):
            with self.assertRaisesRegex(ValueError, "download hash/size"):
                helper.ensure_source(Path(directory))
            self.assertFalse((Path(directory) / "core/example.py").exists())
            self.assertFalse((Path(directory) / "core/example.py.partial").exists())

    def test_loaded_unrelated_core_package_is_rejected(self):
        unrelated = types.ModuleType("core")
        unrelated.__file__ = "/unrelated/site-packages/core/__init__.py"
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"core": unrelated}):
            with self.assertRaisesRegex(RuntimeError, "namespace conflicts"):
                helper.import_source(Path(directory))


if __name__ == "__main__":
    unittest.main()
