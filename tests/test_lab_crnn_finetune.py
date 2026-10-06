"""Guardrails for the OCR pilot's frozen grouped split and checkpoint policy."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1] / "experiments" / "captcha-small-model"


def load(name):
    spec = importlib.util.spec_from_file_location(f"test_{name}", ROOT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


splitter = load("split_crnn_text")
training = load("finetune_crnn_text")


def samples(count=20):
    return [{"id": f"{source}:{index}", "source": source,
             "sha256": hashlib.sha256(f"{source}:{index}".encode()).hexdigest(), "label": "a11B"}
            for source in ("digits", "alphanumeric") for index in range(count)]


class CrnnFrozenSplitTests(unittest.TestCase):
    def test_split_is_permutation_invariant_and_source_stratified(self):
        rows = samples()
        frozen = splitter.make_split(rows, "manifest-hash", seed=17)
        self.assertEqual(frozen, splitter.make_split(list(reversed(rows)), "manifest-hash", seed=17))
        self.assertEqual(frozen["by_source"], {source: {"train": 14, "validation": 2, "test": 4}
                                               for source in ("digits", "alphanumeric")})
        partitions = splitter.validate_split(rows, frozen, "manifest-hash")
        self.assertEqual(sum(map(len, partitions.values())), len(rows))
        digests = {fold: {row["sha256"] for row in group} for fold, group in partitions.items()}
        self.assertFalse(digests["train"] & digests["validation"])
        self.assertFalse(digests["train"] & digests["test"])
        self.assertFalse(digests["validation"] & digests["test"])

    def test_identical_image_aliases_stay_together_and_cross_fold_edit_fails(self):
        rows = samples()
        rows.append(rows[0] | {"id": "duplicate-archive-alias"})
        frozen = splitter.make_split(rows, "manifest-hash")
        duplicates = [row for row in frozen["assignments"] if row["sha256"] == rows[0]["sha256"]]
        self.assertEqual(len(duplicates), 2)
        self.assertEqual(len({row["fold"] for row in duplicates}), 1)
        corrupt = copy.deepcopy(frozen)
        edited = next(row for row in corrupt["assignments"] if row["id"] == "duplicate-archive-alias")
        edited["fold"] = "test" if edited["fold"] != "test" else "train"
        with self.assertRaisesRegex(ValueError, "crosses"):
            splitter.validate_split(rows, corrupt, "manifest-hash")

    def test_changed_manifest_conflicting_labels_and_missing_assignment_fail(self):
        rows = samples()
        frozen = splitter.make_split(rows, "manifest-hash")
        with self.assertRaisesRegex(ValueError, "manifest SHA-256 mismatch"):
            splitter.validate_split(rows, frozen, "changed-manifest")
        conflict = rows + [rows[0] | {"id": "conflict", "label": "different"}]
        with self.assertRaisesRegex(ValueError, "conflicting labels"):
            splitter.make_split(conflict, "manifest-hash")
        corrupt = copy.deepcopy(frozen)
        corrupt["assignments"].pop()
        with self.assertRaisesRegex(ValueError, "assign every image"):
            splitter.validate_split(rows, corrupt, "manifest-hash")

    def test_unique_image_swap_is_rejected_even_when_source_counts_are_unchanged(self):
        rows = samples()
        frozen = splitter.make_split(rows, "manifest-hash")
        corrupt = copy.deepcopy(frozen)
        train = next(row for row in corrupt["assignments"] if row["source"] == "digits" and row["fold"] == "train")
        validation = next(row for row in corrupt["assignments"] if row["source"] == "digits" and row["fold"] == "validation")
        train["fold"], validation["fold"] = validation["fold"], train["fold"]
        with self.assertRaisesRegex(ValueError, "frozen seed-ranked"):
            splitter.validate_split(rows, corrupt, "manifest-hash")

    def test_ctc_repeats_need_blank_frames_and_unknown_charset_is_rejected(self):
        training.validate_targets([{"id": "valid", "label": "aaB12"}], timesteps=6)
        with self.assertRaisesRegex(ValueError, "cannot fit CTC"):
            training.validate_targets([{"id": "too-short", "label": "aaB12"}], timesteps=5)
        with self.assertRaisesRegex(ValueError, "outside fixed pretrained vocabulary"):
            training.validate_targets([{"id": "unknown", "label": "ab!"}])

    def test_validation_selection_prefers_exact_then_cer_and_keeps_ties(self):
        high_exact = {"exact_count": 9, "character_error_rate": 0.4}
        low_exact = {"exact_count": 8, "character_error_rate": 0.01}
        self.assertGreater(training.selection_key(high_exact), training.selection_key(low_exact))
        better_cer = high_exact | {"character_error_rate": 0.3}
        self.assertGreater(training.selection_key(better_cer), training.selection_key(high_exact))
        self.assertFalse(training.selection_key(high_exact) > training.selection_key(high_exact))

    def test_extended_budget_reaches_output_guard_without_gpu_or_training(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(max_epochs=100, max_seconds=1200, batch_size=32,
                                   learning_rate=1e-4, patience=5, train_contrast=0.0,
                                   output=Path(folder))
            with self.assertRaisesRegex(FileExistsError, 'refusing to overwrite'):
                training.train(args)

    def test_over_budget_is_rejected_before_data_or_gpu_access(self):
        for epochs, seconds in ((101, 1200), (100, 1201), (0, 1200)):
            with self.subTest(epochs=epochs, seconds=seconds):
                with self.assertRaisesRegex(ValueError, 'training budget'):
                    training.train(SimpleNamespace(max_epochs=epochs, max_seconds=seconds))


if __name__ == "__main__":
    unittest.main()
