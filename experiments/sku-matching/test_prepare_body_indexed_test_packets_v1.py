"""Synthetic TEST packet index checks; no TEST cohort data is opened."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("indexed_test_packetizer", HERE / "prepare_body_indexed_test_packets_v1.py")
PACKETS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKETS)


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_bytes(b"".join(PACKETS.canon(row) + b"\n" for row in rows))


class DynamicPopulationTests(unittest.TestCase):
    def test_partition_counts_follow_frozen_rows_not_dev_constants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scoped = root / "scoped"
            preflight = root / "preflight"
            scoped.mkdir()
            preflight.mkdir()
            pairs = [{"pair_id": f"p{i}", "cache_key": f"k{i}", "option_values": ["a", "b"],
                      "candidate_vector": ["a", "b"]} for i in range(3)]
            payloads = [{"pair_id": f"p{i}", "cache_key": f"k{i}", "candidate_vector": ["a", "b"]}
                        for i in range(3)]
            (scoped / "manifest.json").write_text(json.dumps({"prepared_pair_count": 3}), encoding="utf-8")
            write_rows(scoped / "model-pairs-v3.jsonl", pairs)
            write_rows(scoped / "model-payloads-v3.jsonl", payloads)
            refs = [{"pair_ref": i, "pair_id": f"p{i}", "cache_key": f"k{i}", "option_count": 2,
                     "dispatch_status": "dispatched" if i < 2 else "literal_protected_not_dispatched"}
                    for i in range(3)]
            dispatch = [{"pair_id": "p0"}, {"pair_id": "p1"}]
            protected = [{"pair_id": "p2"}]
            write_rows(preflight / "pair-ref-index.jsonl", refs)
            write_rows(preflight / "dispatch-pair-ids.jsonl", dispatch)
            write_rows(preflight / "literal-protected-pair-ids.jsonl", protected)
            receipt = {"schema_version": "indexed-test-source-partition-v1",
                "status": "complete_source_derived_pre_model_partition", "prepared_pair_count": 3,
                "dispatch_pair_count": 2, "literal_protected_pair_count": 1,
                "dispatch_pair_ids_sha256": PACKETS.RUNNER.digest_canonical(["p0", "p1"]),
                "literal_protected_pair_ids_sha256": PACKETS.RUNNER.digest_canonical(["p2"]),
                "labels_read": False, "inference_run": False, "scores_created": False,
                "independent_test": True, "test_split_read": True}
            (preflight / "source-partition-receipt.json").write_bytes(PACKETS.canon(receipt))
            result = PACKETS._source_pair_files(scoped, preflight)
            self.assertEqual(len(result[0]), 3)
            self.assertEqual(result[-1], ["p0", "p1"])

    def test_partition_receipt_rejects_unbound_test_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scoped = root / "scoped"
            preflight = root / "preflight"
            scoped.mkdir()
            preflight.mkdir()
            (scoped / "manifest.json").write_text(json.dumps({"prepared_pair_count": 1}), encoding="utf-8")
            write_rows(scoped / "model-pairs-v3.jsonl", [{"pair_id": "p", "cache_key": "k",
                "option_values": ["a"], "candidate_vector": ["a"]}])
            write_rows(scoped / "model-payloads-v3.jsonl", [{"pair_id": "p", "cache_key": "k",
                "candidate_vector": ["a"]}])
            write_rows(preflight / "pair-ref-index.jsonl", [{"pair_ref": 0, "pair_id": "p",
                "cache_key": "k", "option_count": 1, "dispatch_status": "dispatched"}])
            write_rows(preflight / "dispatch-pair-ids.jsonl", [{"pair_id": "p"}])
            write_rows(preflight / "literal-protected-pair-ids.jsonl", [])
            receipt = {"schema_version": "indexed-test-source-partition-v1",
                "status": "complete_source_derived_pre_model_partition", "prepared_pair_count": 1,
                "dispatch_pair_count": 1, "literal_protected_pair_count": 0,
                "dispatch_pair_ids_sha256": PACKETS.RUNNER.digest_canonical(["p"]),
                "literal_protected_pair_ids_sha256": PACKETS.RUNNER.digest_canonical([]),
                "labels_read": False, "inference_run": False, "scores_created": False,
                "independent_test": False, "test_split_read": False}
            (preflight / "source-partition-receipt.json").write_bytes(PACKETS.canon(receipt))
            with self.assertRaises(ValueError):
                PACKETS._source_pair_files(scoped, preflight)


if __name__ == "__main__":
    unittest.main()
