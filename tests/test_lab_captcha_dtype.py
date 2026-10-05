"""Regression checks for mixed encoder dtypes in GPU evaluation scoring."""
import importlib.util
from pathlib import Path
import unittest

try:
    import torch
except ImportError:
    torch = None


@unittest.skipIf(torch is None, 'requires the model environment with PyTorch')
class SimilarityDtypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'experiments/captcha-small-model/benchmark_public_samples.py'
        spec = importlib.util.spec_from_file_location('public_eval_dtype_test', path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.torch = torch

    def test_moe_float_image_and_half_text_produce_known_scores(self):
        images = torch.eye(2, dtype=torch.float32)
        text = torch.eye(2, dtype=torch.float16)
        with self.assertRaises(RuntimeError):
            images @ text.T
        result = self.module.feature_similarities(images, text)
        self.assertEqual(result.dtype, torch.float32)
        self.assertTrue(torch.equal(result, torch.eye(2)))
        self.assertEqual(result.argmax(dim=1).tolist(), [0, 1])

    def test_matching_half_encoder_outputs_keep_existing_scores(self):
        images = torch.tensor([[0.5, 0.25], [0.25, 0.5]], dtype=torch.float16)
        text = torch.tensor([[0.25, 0.5], [0.5, 0.25]], dtype=torch.float16)
        result = self.module.feature_similarities(images, text)
        self.assertEqual(result.dtype, torch.float16)
        self.assertTrue(torch.equal(result, images @ text.T))


if __name__ == '__main__':
    unittest.main()
