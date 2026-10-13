"""Synthetic route packetizer guards; no cohort/task/label data is read."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("route_packetizer_test", HERE / "prepare_body_indexed_route_dev_packets_v1.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def rows(path: Path, values: list[dict]) -> None:
    path.write_bytes(b"".join(P.canon(value) + b"\n" for value in values))


def source_fixture(root: Path):
    scoped, preflight = root / "scoped", root / "preflight"
    scoped.mkdir(); preflight.mkdir()
    pairs = [{"pair_id": f"p{i}", "cache_key": f"k{i}", "option_values": ["x", "y"],
              "candidate_vector": ["x", "y"]} for i in range(4)]
    payloads = [{"pair_id": f"p{i}", "cache_key": f"k{i}", "candidate_vector": ["x", "y"]}
                for i in range(4)]
    (scoped / "manifest.json").write_bytes(P.canon({"prepared_pair_count": 4, "payload_count": 4}))
    rows(scoped / "model-pairs-v3.jsonl", pairs)
    rows(scoped / "model-payloads-v3.jsonl", payloads)
    refs = [{"pair_ref": i, "pair_id": f"p{i}", "cache_key": f"k{i}", "option_count": 2,
             "dispatch_status": "dispatched" if i in (0, 2, 3) else "literal_protected_not_dispatched"}
            for i in range(4)]
    rows(preflight / "pair-ref-index.jsonl", refs)
    rows(preflight / "dispatch-pair-ids.jsonl", [{"pair_id": f"p{i}"} for i in (0, 2, 3)])
    rows(preflight / "literal-protected-pair-ids.jsonl", [{"pair_id": "p1"}])
    receipt = {"schema_version": "indexed-dev-source-partition-v1",
        "status": "complete_source_derived_pre_model_partition", "prepared_pair_count": 4,
        "dispatch_pair_count": 3, "literal_protected_pair_count": 1,
        "dispatch_pair_ids_sha256": P.RUNNER.digest_canonical(["p0", "p2", "p3"]),
        "literal_protected_pair_ids_sha256": P.RUNNER.digest_canonical(["p1"]),
        "labels_read": False, "inference_run": False, "scores_created": False}
    (preflight / "source-partition-receipt.json").write_bytes(P.canon(receipt))
    return scoped, preflight


class RoutePopulationTests(unittest.TestCase):
    def test_dynamic_partition_keeps_global_refs_and_only_dispatches_marked_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            scoped, preflight = source_fixture(Path(tmp))
            result = P._source_pair_files(scoped, preflight)
            self.assertEqual(len(result[0]), 4)
            self.assertEqual(result[4][2]["pair_id"], "p2")
            self.assertEqual(result[5], ["p0", "p2", "p3"])
            self.assertEqual(result[6], ["p1"])

    def test_duplicate_global_ref_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            scoped, preflight = source_fixture(Path(tmp))
            refs = P.read_jsonl(preflight / "pair-ref-index.jsonl")
            refs[1]["pair_ref"] = refs[0]["pair_ref"]
            rows(preflight / "pair-ref-index.jsonl", refs)
            with self.assertRaises(ValueError):
                P._source_pair_files(scoped, preflight)

    def test_payload_identity_or_candidate_vector_substitution_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            scoped, preflight = source_fixture(Path(tmp))
            payloads = P.read_jsonl(scoped / "model-payloads-v3.jsonl")
            payloads[0]["candidate_vector"] = ["y", "x"]
            rows(scoped / "model-payloads-v3.jsonl", payloads)
            pair_rows = P.read_jsonl(scoped / "model-pairs-v3.jsonl")
            pair_by_id = {row["pair_id"]: row for row in pair_rows}
            payload_by_id = {row["pair_id"]: row for row in payloads}
            with self.assertRaises(ValueError):
                P._public_payload(pair_by_id["p0"], payload_by_id["p0"], 0)


if __name__ == "__main__":
    unittest.main()
