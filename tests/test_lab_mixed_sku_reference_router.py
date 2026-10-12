from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

HERE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("mixed_sku_reference_router", HERE / "route_mixed_sku_references_v1.py")
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def put_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def put_jsonl(path, values):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8")


class MixedReferenceRouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prepared = self.root / "prepared"
        self.freeze_dir = self.root / "frozen"
        self.ids = {"prior-a": "prior_errors", "dev-a": "independent_dev",
                    "control-a": "old_structural_control", "control-b": "old_structural_control",
                    "diag-a": "product_pair_diagnostic"}

        self.dev_archive = self.root / "dev-v2.zip"
        answer_member = ".lab-output/sku-independent-real-20261011/eval-v2/labels-token/private/resolved.jsonl"
        case_member = ".lab-output/sku-independent-real-20261011/eval-v2/run-token/inputs/cases.jsonl"
        case_record = {"case_id": "dev-a", "split": "exploration", "group_id": "g1"}
        case_payload = json.dumps(case_record).encode("utf-8") + b"\n"
        answer_payload = (b'{"case_id":"test-only","expected":! this test record must stay opaque\n'
                          b'{"case_id":"dev-a","expected":"accept","au_row_keys":["row-1"],'
                          b'"label_source":"machinegold:fixture","is_independent_human_gold":true}\n')
        with ZipFile(self.dev_archive, "w") as archive:
            archive.writestr(answer_member, answer_payload)
            archive.writestr(case_member, case_payload)
        archive_sha = digest(self.dev_archive)

        membership = []
        for identity, stratum in self.ids.items():
            member = {"stratum": stratum, "primary_score_eligible": stratum != "product_pair_diagnostic"}
            if stratum == "independent_dev":
                member.update({"source_cohort": "independent_real_eval_v2", "split": "dev",
                    "source_archive": str(self.dev_archive), "source_archive_sha256": archive_sha,
                    "source_case_ref": {"member": case_member, "member_sha256": hashlib.sha256(case_payload).hexdigest(),
                                         "byte_offset": 0, "line": 1},
                    "original_case": case_record})
            membership.append({"case_id": identity, "membership_strata": [stratum], "memberships": [member]})
        put_jsonl(self.prepared / "membership.jsonl", membership)
        gpu_cases = [{"case_id": "dev-a", "split": "dev", "group_id": "g1", "dev_group_provenance": {
            "original_group": {"split": "dev", "group_id": "g1", "excluded": None}}}]
        put_jsonl(self.prepared / "gpu-cases.jsonl", gpu_cases)
        for name in ("requests.jsonl", "documents.jsonl", "tasks.jsonl", "row_plans.jsonl", "case_plans.jsonl", "failures.jsonl"):
            put_jsonl(self.prepared / name, [])
        prep_manifest = {"schema_version": "mixed-real-gpu-sku-inputs-v1", "input_ready": True,
            "labels_read": False, "labels_in_model_inputs": False, "case_count": len(membership),
            "input_sha256": {str(self.dev_archive.resolve()): archive_sha},
            "output_sha256": {name: digest(self.prepared / name)
                              for name in ("membership.jsonl", "gpu-cases.jsonl", "requests.jsonl", "documents.jsonl",
                                           "tasks.jsonl", "row_plans.jsonl", "case_plans.jsonl", "failures.jsonl")}}
        put_json(self.prepared / "manifest.json", prep_manifest)

        self.prior = self.root / "prior-refs.jsonl"
        put_jsonl(self.prior, [{"case_id": "prior-a", "label": "unmatched",
                                "scoring_reference_source": {"source_path": "older-source", "source_sha256": "a" * 64}}])
        self.controls = self.root / "controls.zip"
        with ZipFile(self.controls, "w") as archive:
            archive.writestr("legacy.jsonl", b'{"case_id":"control-a","label":"matched"}\n')

        self._make_freeze(prep_manifest)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_freeze(self, prep_manifest):
        self.freeze_dir.mkdir(parents=True, exist_ok=True)
        input_hashes = {
            str((self.prepared / "manifest.json").resolve()): digest(self.prepared / "manifest.json"),
        }
        for name in ("membership.jsonl", "requests.jsonl", "documents.jsonl", "tasks.jsonl", "row_plans.jsonl",
                     "case_plans.jsonl", "gpu-cases.jsonl", "failures.jsonl"):
            input_hashes[str((self.prepared / name).resolve())] = digest(self.prepared / name)
        for variant, name in zip(R.VARIANTS, R.PREDICTIONS, strict=True):
            prediction = self.root / "predictions" / name
            put_jsonl(prediction, [])
            input_hashes[str(prediction.resolve())] = digest(prediction)
            gate = self.freeze_dir / variant
            decision_path = gate / "decisions.jsonl"
            put_jsonl(decision_path, [{"case_id": identity, "decision": "exclude", "top_row_key": None}
                                      for identity in self.ids])
            gate_manifest = {"output_sha256": {"decisions.jsonl": digest(decision_path)}}
            put_json(gate / "manifest.json", gate_manifest)
        gate_hashes = {variant: digest(self.freeze_dir / variant / "manifest.json") for variant in R.VARIANTS}
        put_jsonl(self.freeze_dir / "case-eligibility.jsonl", [{"case_id": identity} for identity in self.ids])
        frozen = {"schema_version": "mixed-gpu-sku-evaluation-v1", "labels_read": False,
            "labels_in_model_inputs": False, "labels_are_loaded_only_after_all_gates_frozen": True,
            "execution_device": "cpu", "input_sha256": input_hashes, "gate_manifest_sha256": gate_hashes,
            "case_eligibility_sha256": digest(self.freeze_dir / "case-eligibility.jsonl"),
            "case_count": prep_manifest["case_count"]}
        put_json(self.freeze_dir / "freeze.json", frozen)
        put_json(self.freeze_dir / "all-gates-frozen.json", {"labels_read": False,
            "all_predictions_and_gates_frozen": True, "gate_manifest_sha256": gate_hashes})

    def test_invalid_freeze_fails_before_opening_any_reference_file(self):
        broken = self.root / "broken-freeze"
        broken.mkdir()
        with self.assertRaises(FileNotFoundError):
            R.route(self.prepared, broken, self.root / "must-not-open.jsonl",
                    self.root / "must-not-open.zip", self.root / "never-written",
                    expected_counts=None)
        self.assertFalse((self.root / "never-written").exists())

    def test_routes_only_selected_ids_and_records_unresolved_controls(self):
        out = self.root / "routed"
        manifest = R.route(self.prepared, self.freeze_dir, self.prior, self.controls, out,
            control_member="legacy.jsonl", allow_unresolved_controls=True,
            expected_counts={"prior_errors": 1, "independent_dev": 1,
                             "old_structural_control": 2, "product_pair_diagnostic": 1})
        dev = list(R.jsonl_rows(out / "independent-dev.jsonl"))
        self.assertEqual([row["case_id"] for row in dev], ["dev-a"])
        route = dev[0]["scoring_reference_source"]
        self.assertEqual(route["reference_kind"], "independent_dev")
        self.assertIn("private/resolved.jsonl", route["member"])
        self.assertEqual(dev[0]["label"], "matched")
        self.assertEqual(dev[0]["decision"], "matched")
        self.assertEqual(dev[0]["source_label_record"]["expected"], "accept")
        self.assertEqual(dev[0]["source_label_record"]["au_row_keys"], ["row-1"])
        self.assertEqual(manifest["routed_case_counts"]["independent_dev"], 1)
        self.assertEqual(manifest["routed_case_counts"]["old_structural_control"], 1)
        self.assertEqual(manifest["unresolved_control_case_ids"], ["control-b"])
        self.assertEqual(R.read_path_references(out / "independent-dev.jsonl", {"diag-a"}, "x"), {})

    def test_independent_review_state_keeps_expected_none_unresolved(self):
        source = {"case_id": "selected", "expected": None, "au_row_keys": ["row-x"]}
        routed = R.normalize_independent_label(source)
        self.assertEqual(routed["label"], "review")
        self.assertEqual(routed["decision"], "review")
        self.assertTrue(routed["reference_uncertain"])
        self.assertIsNone(routed["source_label_record"]["expected"])

    def test_freeze_prediction_hash_mismatch_is_rejected(self):
        hashes = json_file(self.freeze_dir / "freeze.json")["input_sha256"]
        prediction = next(Path(path) for path in hashes if Path(path).name == R.PREDICTIONS[0])
        prediction.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "frozen input SHA256 differs"):
            R.validate_gate_freeze(self.prepared, self.freeze_dir)


def json_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
