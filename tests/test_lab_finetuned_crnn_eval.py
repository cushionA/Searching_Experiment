"""Integrity/scope checks for portable validation-selected CRNN inference."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1] / "experiments" / "captcha-small-model"
SPEC = importlib.util.spec_from_file_location("selected_crnn_eval_tests", ROOT / "evaluate_finetuned_crnn_text.py")
assert SPEC is not None and SPEC.loader is not None
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def provenance():
    crnn = evaluation.crnn
    files = {name: {"sha256": digest, "bytes": 1} for name, digest in crnn.FILE_SHA256.items()}
    split = {"seed": 17, "by_source": {"numeric": {"train": 14, "validation": 2, "test": 4}}}
    config = {"pretrained": {"repository": crnn.REPOSITORY, "revision": crnn.REVISION, "files": files},
              "manifest_sha256": "full-manifest", "split_sha256": "frozen-split", "seed": 17,
              "partition_counts": split["by_source"], "vocabulary": crnn.VOCABULARY,
              "blank_index": 0, "output_timesteps": 37, "training": {"max_epochs": 20},
              "code_sha256": {"finetune_crnn_text.py": "a" * 64} |
                             {name: crnn.sha256_file(ROOT / name) for name in
                              ("evaluate_crnn_text.py", "evaluate_public_text.py", "split_crnn_text.py")}}
    metadata = {"pretrained_repository": crnn.REPOSITORY, "pretrained_revision": crnn.REVISION,
                "pretrained_sha256": files[crnn.MODEL_FILENAME]["sha256"],
                "manifest_sha256": "full-manifest", "split_sha256": "frozen-split",
                "training_code_sha256": "a" * 64, "vocabulary": crnn.VOCABULARY,
                "selected_epoch": "19"}
    return metadata, config, files, split


class FinetunedCrnnEvaluatorTests(unittest.TestCase):
    def test_changed_checkpoint_is_rejected_by_required_literal_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.safetensors"
            payload = b"not executed; SHA checks happen before any tensor loading"
            path.write_bytes(payload)
            expected = hashlib.sha256(payload).hexdigest()
            self.assertEqual(evaluation.verify_checkpoint_sha(path, expected), expected)
            path.write_bytes(payload + b"corrupted")
            with self.assertRaisesRegex(ValueError, "checkpoint SHA-256 mismatch"):
                evaluation.verify_checkpoint_sha(path, expected)
            with self.assertRaisesRegex(ValueError, "explicit 64-character"):
                evaluation.verify_checkpoint_sha(path, "")

    def test_subset_manifest_or_changed_split_cannot_claim_original_test_scope(self):
        metadata, config, files, split = provenance()
        self.assertEqual(evaluation.validate_provenance(metadata, config, "full-manifest", "frozen-split", files, split), 19)
        for manifest_sha, split_sha in (("subset-manifest", "frozen-split"), ("full-manifest", "changed-split")):
            with self.subTest(manifest=manifest_sha, split=split_sha):
                with self.assertRaisesRegex(ValueError, "manifest/split SHA-256 mismatch"):
                    evaluation.validate_provenance(metadata, config, manifest_sha, split_sha, files, split)

    def test_checkpoint_metadata_must_match_config_and_exclude_continuation_state(self):
        metadata, config, files, split = provenance()
        for corrupt in (metadata | {"training_code_sha256": "b" * 64},
                        metadata | {"selected_epoch": "21"},
                        metadata | {"role": "last training state, not validation winner"}):
            with self.subTest(metadata=corrupt):
                with self.assertRaises(ValueError):
                    evaluation.validate_provenance(corrupt, config, "full-manifest", "frozen-split", files, split)
        bad_config = copy.deepcopy(config)
        bad_config["code_sha256"]["evaluate_crnn_text.py"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "trusted inference source mismatch"):
            evaluation.validate_provenance(metadata, bad_config, "full-manifest", "frozen-split", files, split)
        bad_split_code = copy.deepcopy(config)
        bad_split_code["code_sha256"]["split_crnn_text.py"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "trusted inference source mismatch"):
            evaluation.validate_provenance(metadata, bad_split_code, "full-manifest", "frozen-split", files, split)

    def test_training_fold_and_implicit_auto_device_are_not_supported(self):
        with self.assertRaisesRegex(ValueError, "only frozen test or validation"):
            evaluation.evaluate(SimpleNamespace(fold="train"))
        with self.assertRaisesRegex(ValueError, "device must be explicit"):
            evaluation.evaluate(SimpleNamespace(fold="test", device="auto", batch_size=32))


if __name__ == "__main__":
    unittest.main()
