import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / ".lab-output/sku-next-dev-coordinate-execution-20261013-v1/"
          "assemble_coordinate_refids_dev20.py")
SPEC = importlib.util.spec_from_file_location("coordinate_assembler", SCRIPT)
ASSEMBLER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ASSEMBLER)


class FrozenMethodPolicyTests(unittest.TestCase):
    def test_complete_flat_bindings_retain_paired_and_contextless_task_rows(self):
        tasks = [{"task_id": "t1"}, {"task_id": "t2"}, {"task_id": "t3"}]
        bindings = [
            {"task_binding": {"task_id": "t1"}, "pair_id": "p1"},
            {"task_binding": {"task_id": "t2"}, "pair_id": None,
             "preparation_status": "contextless_abstention"},
            {"task_binding": {"task_id": "t3"}, "pair_id": "p1"},
        ]
        result = ASSEMBLER.validate_complete_task_bindings(tasks, bindings, {"p1"})
        self.assertEqual(len(result), 3)
        self.assertIsNone(result[1]["pair_id"])
        self.assertEqual(result[1]["preparation_status"], "contextless_abstention")

    def test_complete_flat_bindings_reject_duplicate_task_identity(self):
        tasks = [{"task_id": "t1"}, {"task_id": "t2"}]
        bindings = [{"task_binding": {"task_id": "t1"}, "pair_id": "p1"},
                    {"task_binding": {"task_id": "t1"}, "pair_id": "p1"}]
        with self.assertRaises(ValueError):
            ASSEMBLER.validate_complete_task_bindings(tasks, bindings, {"p1"})

    @staticmethod
    def build_method_fixture(root, packets):
        instruction_rel = ".lab-output/sku-next-dev-coordinate-execution-20261013-v1/worker-inputs/model-worker-instructions.txt"
        instruction = root / instruction_rel
        instruction.parent.mkdir(parents=True, exist_ok=True)
        instruction.write_text("frozen instruction text", encoding="utf-8")
        frozen_files = {instruction_rel: ASSEMBLER.pin(instruction)}
        ledger_sources, all_ids = [], []
        for packet_no, pair_ids in packets.items():
            assignment_rel = (f".lab-output/sku-next-dev-coordinate-execution-20261013-v1/worker-inputs/"
                              f"assignment-{packet_no:03d}.json")
            packet_rel = (f".lab-output/sku-next-dev-coordinate-execution-20261013-v1/worker-inputs/"
                          f"packet-{packet_no:03d}.json")
            audit_rel = (f".lab-output/sku-next-dev-coordinate-execution-20261013-v1/worker-responses/"
                         f"packet-{packet_no:03d}-method-audit.json")
            for rel in (assignment_rel, packet_rel, audit_rel):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
            assignment = {"packet_index": packet_no, "packet": packet_rel,
                          "instruction_file": instruction_rel, "pair_ids": pair_ids}
            (root / assignment_rel).write_text(json.dumps(assignment), encoding="utf-8")
            (root / packet_rel).write_text("{}", encoding="utf-8")
            code = "\n".join(f"pair_id = '{pair_id}'" for pair_id in pair_ids)
            audit = {"original_toolcode": code, "method_finding": "nonconforming authored vector"}
            (root / audit_rel).write_text(json.dumps(audit), encoding="utf-8")
            for rel in (assignment_rel, packet_rel):
                frozen_files[rel] = ASSEMBLER.pin(root / rel)
            ledger_sources.append({"audit_receipt_path": audit_rel,
                "audit_receipt_sha256": ASSEMBLER.sha_file(root / audit_rel),
                "worker_assignment_path": assignment_rel,
                "worker_assignment_sha256": ASSEMBLER.sha_file(root / assignment_rel),
                "packet_input_path": packet_rel,
                "packet_input_sha256": ASSEMBLER.sha_file(root / packet_rel),
                "instruction_sha256": ASSEMBLER.sha_file(instruction),
                "pair_ids": pair_ids})
            all_ids.extend(pair_ids)
        ledger = {"schema_version": ASSEMBLER.METHOD_EXCLUSION_SCHEMA,
                  "method_policy": "exclude_from_primary_when_worker_authored_nonconforming_vector",
                  "excluded_pair_ids": all_ids, "sources": ledger_sources,
                  "labels_read": False, "test_labels_read": False,
                  "semantic_reclassification": False, "raw_response_preserved": True,
                  "source_validation_still_performed": True}
        return ledger, frozen_files, set(all_ids)

    def test_dynamic_multiple_audits_form_exact_unique_exclusion_union(self):
        ids = [f"choice-pair:{letter * 64}" for letter in "abcde"]
        with tempfile.TemporaryDirectory() as temp:
            ledger, frozen, _excluded = self.build_method_fixture(
                Path(temp), {17: ids[:3], 41: ids[3:]})
            actual = ASSEMBLER.validate_method_ledger(
                ledger, Path(temp), set(ids), set(ids), frozen)
            self.assertEqual(actual, set(ids))
            audit_path = Path(temp) / ledger["sources"][1]["audit_receipt_path"]
            audit_path.write_text(json.dumps({"original_toolcode": f"pair_id = '{ids[3]}'",
                "method_finding": "nonconforming authored vector"}), encoding="utf-8")
            ledger["sources"][1]["audit_receipt_sha256"] = ASSEMBLER.sha_file(audit_path)
            with self.assertRaises(ValueError):
                ASSEMBLER.validate_method_ledger(ledger, Path(temp), set(ids), set(ids), frozen)

    def test_instruction_hash_must_match_actual_frozen_worker_instruction(self):
        pair_id = "choice-pair:" + "a" * 64
        with tempfile.TemporaryDirectory() as temp:
            ledger, frozen, _ = self.build_method_fixture(Path(temp), {17: [pair_id]})
            ledger["sources"][0]["instruction_sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                ASSEMBLER.validate_method_ledger(ledger, Path(temp), {pair_id}, {pair_id}, frozen)

    def test_only_root_method_exclusions_are_removed_after_source_validation(self):
        validated = [{"pair_id": "p1", "validated": True},
                     {"pair_id": "p2", "validated": True},
                     {"pair_id": "p3", "validated": True},
                     {"pair_id": "p4", "validated": True}]
        qualified, report = ASSEMBLER.method_qualify_validated_pairs(validated, {"p2", "p3"})
        self.assertEqual([row["pair_id"] for row in qualified], ["p1", "p4"])
        self.assertEqual({row["pair_id"] for row in report if not row["primary_method_qualified"]},
                         {"p2", "p3"})
        self.assertEqual(len(validated), 4)
        self.assertTrue(all(row["validated"] for row in validated))

    def test_nonconforming_option_vector_fails_closed_without_changing_raw_rows(self):
        prepared = {"p1": {"option_values": ["A", "B"], "cache_key": "k1"},
                    "p2": {"option_values": ["C", "D"], "cache_key": "k2"}}
        raw = [{"pair_id": "p1", "cache_key": "k1", "decisions": [
                    {"option_index": 0}, {"option_index": 1}]},
               {"pair_id": "p2", "cache_key": "k2", "decisions": [
                    {"option_index": 1}, {"option_index": 0}]}]
        before = repr(raw)
        audit = ASSEMBLER.audit_raw_transport(raw, prepared)
        self.assertFalse(audit[0]["malformed_transport_fails_closed_to_unknown"])
        self.assertTrue(audit[1]["malformed_transport_fails_closed_to_unknown"])
        self.assertEqual(repr(raw), before)

    def test_source_invalid_method_exclusion_remains_unknown_and_is_still_recorded(self):
        qualified, report = ASSEMBLER.method_qualify_validated_pairs(
            [{"pair_id": "p1"}], {"p2"},
            [{"pair_id": "p2", "validation_status": "invalid_unknown"}])
        self.assertEqual([row["pair_id"] for row in qualified], ["p1"])
        excluded = next(row for row in report if row["pair_id"] == "p2")
        self.assertFalse(excluded["primary_method_qualified"])
        self.assertEqual(excluded["source_validation_status"], "invalid_unknown")


if __name__ == "__main__":
    unittest.main()
