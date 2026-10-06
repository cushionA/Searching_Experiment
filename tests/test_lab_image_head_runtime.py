"""Exported image heads must reject altered code and incompatible tensor shapes."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

try:
    import torch
    from safetensors.torch import save_file
except ImportError:
    torch = None

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments/captcha-small-model"
SPEC = importlib.util.spec_from_file_location("lab_image_head_runtime", EXPERIMENT / "recognize_finetuned_image_head.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


@unittest.skipIf(torch is None, "torch and safetensors are optional inference dependencies")
class ImageHeadRuntimeTests(unittest.TestCase):
    def fixture(self, root, width=768):
        labels = [f"label-{i}" for i in range(16)]
        save_file({"linear.weight": torch.zeros((16, width)), "linear.bias": torch.zeros(16)},
                  str(root / "linear_head.safetensors"))
        head_sha = runtime.file_sha(root / "linear_head.safetensors")
        metadata = {"architecture": {"type": "linear", "input_dim": 768, "output_dim": 16,
                                     "labels": labels, "output": "independent sigmoid logits"},
                    "provenance": {"head_sha256": head_sha},
                    "selection": {"fixed_board_threshold": 0.5, "requested_count_not_used": True}}
        (root / "metadata.json").write_text(json.dumps(metadata))
        deployment = {"schema_version": 1, "encoder": {"name": "MobileCLIP2-S3"},
                      "labels": labels, "head_sha256": head_sha,
                      "metadata_sha256": runtime.file_sha(root / "metadata.json"),
                      "source_sha256": {name: runtime.file_sha(EXPERIMENT / name)
                                        for name in ("recognize_finetuned_image_head.py", "benchmark_embeddings.py")}}
        (root / "deployment.json").write_text(json.dumps(deployment))
        return deployment

    def test_altered_encoder_api_source_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deployment = self.fixture(root)
            deployment["source_sha256"]["benchmark_embeddings.py"] = "0" * 64
            (root / "deployment.json").write_text(json.dumps(deployment))
            with self.assertRaisesRegex(ValueError, "SHA-256 differs"):
                runtime.load_exported_head(root)

    def test_compatible_metadata_cannot_hide_wrong_tensor_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, width=767)
            with self.assertRaisesRegex(ValueError, "incorrect shape"):
                runtime.load_exported_head(root)


if __name__ == "__main__":
    unittest.main()
