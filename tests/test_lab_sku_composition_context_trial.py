"""Temporary-file tests for context assembly-freeze reconciliation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location(
    "trial_composition_context_v1", HERE / "trial_composition_context_v1.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CompositionContextAssemblyFreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / "context-run"
        self.run.mkdir()
        self.code_history = self.root / ".lab-output/sku-composition-context-resume-20261013-v1/code-history"
        self.code_history.mkdir(parents=True)
        self.test_files = [self.root / "tests/test_lab_sku_composition_context.py",
                           self.root / "tests/test_lab_sku_composition_context_policy.py"]
        for path in self.test_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("temporary test pin\n", encoding="utf-8")
        self.runner = self.root / "trial_composition_context_v1.py"
        self.policy = self.root / "composition_context_policy_v1.py"
        self.packet = self.root / "packets.jsonl"
        self.source = self.root / "source-blocks.jsonl"
        self.runner.write_text("old runner\n", encoding="utf-8")
        self.policy.write_text("old policy\n", encoding="utf-8")
        self.packet.write_text('{"unit_id":"u","blocks":[]}\n', encoding="utf-8")
        self.source.write_text('{"block_id":"b","text":"source"}\n', encoding="utf-8")
        self.freeze = {
            "policies": list(M.POLICIES),
            "source_snapshot": {
                str(path): {"path": str(path.resolve()), "sha256": sha(path)}
                for path in (self.runner, self.policy, self.packet, self.source)
            },
        }
        self.freeze_path = self.run / "freeze.json"
        self.freeze_path.write_text(json.dumps(self.freeze, sort_keys=True), encoding="utf-8")
        self.original_freeze_bytes = self.freeze_path.read_bytes()
        self.patchers = [
            patch.object(M, "__file__", str(self.runner)),
            patch.object(M.POLICY, "__file__", str(self.policy)),
            patch.object(M, "ROOT", self.root),
        ]
        for value in self.patchers:
            value.start()

    def tearDown(self):
        for value in reversed(self.patchers):
            value.stop()
        self.temp.cleanup()

    def reconcile(self):
        self.runner.write_text("assembly runner revision\n", encoding="utf-8")
        self.policy.write_text("assembly policy revision\n", encoding="utf-8")
        return M.reconcile_assembly(self.run)

    def test_allowed_assembly_changes_are_receipted_without_rewriting_original_freeze(self):
        self.reconcile()
        receipt_path = self.run / "assembly-freeze.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(self.freeze_path.read_bytes(), self.original_freeze_bytes)
        self.assertTrue(receipt["original_inference_freeze_preserved"])
        self.assertEqual({Path(item["path"]).name
                          for item in receipt["assembly_only_changed_paths"]},
                         {"trial_composition_context_v1.py", "composition_context_policy_v1.py"})
        snapshot = M.verified_assembly_snapshot(self.run, self.freeze)
        self.assertEqual(snapshot[str(self.packet)], self.freeze["source_snapshot"][str(self.packet)])
        self.assertEqual(snapshot[str(self.source)], self.freeze["source_snapshot"][str(self.source)])
        self.assertEqual(snapshot[str(self.runner)]["sha256"], sha(self.runner))
        self.assertEqual(snapshot[str(self.policy)]["sha256"], sha(self.policy))

    def test_packet_or_source_mutation_before_reconciliation_is_rejected(self):
        self.runner.write_text("assembly runner revision\n", encoding="utf-8")
        self.packet.write_text('{"unit_id":"tampered"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "model input/source changed"):
            M.reconcile_assembly(self.run)
        self.assertFalse((self.run / "assembly-freeze.json").exists())

    def test_original_freeze_tampering_after_receipt_is_rejected(self):
        self.reconcile()
        self.freeze_path.write_text('{"policies":[]}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "original inference freeze changed"):
            M.verified_assembly_snapshot(self.run, self.freeze)

    def test_new_source_mutation_after_receipt_is_rejected(self):
        self.reconcile()
        self.source.write_text('{"block_id":"b","text":"changed"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frozen execution input changed"):
            M.verified_assembly_snapshot(self.run, self.freeze)

    def test_repeated_reconciliation_receipt_is_refused(self):
        self.reconcile()
        with self.assertRaises(FileExistsError):
            M.reconcile_assembly(self.run)

    def test_tampered_reconciliation_does_not_replace_original_packet_pin(self):
        self.reconcile()
        receipt_path = self.run / "assembly-freeze.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["source_snapshot"][str(self.packet)]["sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "original model source pin was replaced"):
            M.verified_assembly_snapshot(self.run, self.freeze)


if __name__ == "__main__":
    unittest.main()
