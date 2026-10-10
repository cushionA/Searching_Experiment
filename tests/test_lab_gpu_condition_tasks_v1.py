import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/sku-matching/prepare_gpu_condition_tasks_v1.py"
SPEC = importlib.util.spec_from_file_location("prepare_gpu_condition_tasks_v1", SCRIPT)
prep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prep)


class GPUConditionTaskPreparerTests(unittest.TestCase):
    def test_emits_label_blind_host_pools_and_bound_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = prep.prepare(Path(tmp) / "out")
            out = Path(tmp) / "out"
            packets = [json.loads(x) for x in (out / "model/packets.jsonl").read_text().splitlines()]
            pools = [json.loads(x) for x in (out / "host/fullpools.jsonl").read_text().splitlines()]
        self.assertEqual(manifest["case_count"], 14)
        self.assertEqual(len(pools), 14)
        self.assertEqual(manifest["au_row_count_total"], 798)
        self.assertTrue(all(p["au_rows"] for p in pools))
        self.assertTrue(all(p["au_row_count"] == len(p["au_rows"]) for p in pools))
        self.assertTrue(all(p["au_row_key"] in {r["row_key"] for h in pools for r in h["au_rows"]}
                            for p in packets))
        by_case = {p["case_id"] for p in packets}
        for host in pools:
            if host["focus_row_key"]:
                case_packets = [p for p in packets if p["case_id"] == host["case_id"]]
                self.assertEqual(sum(p["condition"]["side"] == "rakuten" for p in case_packets),
                                 len(host["required_conditions"]))
                self.assertTrue(all(p["au_row_key"] == host["focus_row_key"] for p in case_packets))
        self.assertTrue(by_case)
        self.assertFalse(manifest["labels_read"])

    def test_evidence_is_current_scope_and_has_verified_raw_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep.prepare(Path(tmp) / "out")
            packets = [json.loads(x) for x in (Path(tmp) / "out/model/packets.jsonl").read_text().splitlines()]
        self.assertTrue(packets)
        for packet in packets:
            self.assertEqual(set(packet), {"packet_id", "case_id", "au_row_key", "condition", "context", "evidence"})
            self.assertIn(packet["condition"]["side"], ("rakuten", "au"))
            self.assertTrue(packet["evidence"])
            for block in packet["evidence"]:
                self.assertNotRegex(block["scope"], r"series|sibling|link|search_keyword|recommend")
                self.assertTrue(block["leaf_offset"]["verified"])
                self.assertRegex(block["source_sha256"], r"^[0-9a-f]{64}$")
                expected_source_side = "au" if packet["condition"]["side"] == "rakuten" else "rakuten"
                self.assertEqual(block["source_ref"]["source_side"], expected_source_side)
                self.assertNotRegex(block["quote"], r"\d+円|クーポン|\d+%OFF")

    def test_host_plan_records_support_conflict_and_unknown_by_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep.prepare(Path(tmp) / "out")
            pools = [json.loads(x) for x in (Path(tmp) / "out/host/fullpools.jsonl").read_text().splitlines()]
        self.assertEqual(sum(len(x["row_condition_statuses"]) for x in pools), 798)
        first = pools[0]
        focus = next(r for r in first["row_condition_statuses"] if r["row_key"] == first["focus_row_key"])
        statuses = [x["status"] for x in focus["condition_status"]]
        self.assertIn("supported_by_AU_row", statuses)
        self.assertIn("unknown_or_missing", statuses)  # Rakuten-only lace selection
        other = next(r for r in first["row_condition_statuses"] if r["row_key"] != first["focus_row_key"])
        self.assertIn("typed_conflict", [x["status"] for x in other["condition_status"]])

    def test_unmatched_compound_options_remain_host_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep.prepare(Path(tmp) / "out")
            pools = [json.loads(x) for x in (Path(tmp) / "out/host/fullpools.jsonl").read_text().splitlines()]
        by_id = {x["case_id"]: x for x in pools}
        for case_id in ("case-03eb5704d3bb159f757b", "case-103636fa17ca1d28173e",
                        "case-074029bf6afff3c98c8c", "case-590326f9f840ed0ef9b4",
                        "case-da88a18f3fd0a6ebcfc6"):
            self.assertIsNone(by_id[case_id]["focus_row_key"])
            self.assertEqual(by_id[case_id]["focus_status"], "host_review_nonunique_or_unmatched")


if __name__ == "__main__":
    unittest.main()
