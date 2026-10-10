"""Artificial V4 handoff contracts; fixtures are separate from real samples."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import import_claude_alignment_handoff_v2 as importer

spec = importlib.util.spec_from_file_location("v4_artificial_fixture_helpers", ROOT / "tests/test_lab_claude_handoff_v2.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
FIXTURE = helpers.ClaudeFlatHandoffV2Tests()
V4_RUN = importer.PINNED_RUNS[importer.UPSTREAM_V4_HEAD][0]


class ClaudeFlatHandoffV4Tests(unittest.TestCase):
    def fixture(self, root):
        archive, case, product, registry, handoff = FIXTURE.fixture(root)
        handoff["conditions"][0]["status"] = "model_candidate"
        FIXTURE.write_archive(archive, handoff, run=V4_RUN)
        return archive, case, product, registry, handoff

    def run_import(self, archive, root, output="out", **kwargs):
        return importer.prepare(archive, root, root / output,
                                expected_sha256=importer.sha256(archive.read_bytes()),
                                expected_head=kwargs.pop("expected_head", importer.UPSTREAM_V4_HEAD), **kwargs)

    @staticmethod
    def rows(path):
        return FIXTURE.rows(path)

    @staticmethod
    def rewrite_payload(archive, edit):
        with ZipFile(archive) as zipped:
            payload = {name: zipped.read(name) for name in zipped.namelist() if name != importer.CHECKPOINT_MEMBER}
            checkpoint = json.loads(zipped.read(importer.CHECKPOINT_MEMBER))
        edit(payload, checkpoint)
        checkpoint.update(entries={name: {"size_bytes": len(body), "sha256": importer.sha256(body)} for name, body in payload.items()},
                          payload_file_count=len(payload), uncompressed_payload_bytes=sum(map(len, payload.values())))
        with ZipFile(archive, "w") as zipped:
            for name, body in payload.items():
                zipped.writestr(name, body)
            zipped.writestr(importer.CHECKPOINT_MEMBER, json.dumps(checkpoint))

    def test_model_candidate_preserves_whole_values_in_both_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, case, product, _, handoff = self.fixture(root)
            manifest = self.run_import(archive, root)
            card = self.rows(root / "out/cards.jsonl")[0]
            reverse = self.rows(root / "out/reverse_conditions.jsonl")[0]
            self.assertEqual(card["raw_condition"], case["rakuten"]["axes"][0])
            self.assertEqual(card["selected_value"], "本棚付き / 天然木")
            self.assertEqual(card["option_values"], case["rakuten"]["axes"][0]["family_values"])
            self.assertEqual(card["producer_status"], "model_candidate")
            self.assertFalse(card["model_alignment_is_proof"])
            self.assertTrue(card["verification_required"])
            self.assertFalse(card["automatic_adoption_allowed"])
            self.assertEqual(reverse["raw_condition"], product["au"]["rows"][0]["axes"][0])
            self.assertEqual(reverse["selected_value"], "天然木（無塗装）")
            self.assertEqual(reverse["origin"], "model_candidate")
            self.assertEqual(reverse["parent_condition_ref"]["condition_id"], card["condition_id"])
            self.assertEqual(card["reverse_condition_ref"]["line"], 1)
            self.assertFalse(reverse["automatic_adoption_allowed"])
            self.assertNotIn("matched_by", handoff["conditions"][0])
            self.assertEqual(manifest["condition_counts"]["rakuten:model_candidate"], 1)
            self.assertFalse(manifest["model_candidate_is_proof"])
            self.assertFalse(manifest["upstream_alignment_is_proof"])

    def test_all_model_candidate_mapped_au_values_have_reverse_obligations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _, _, handoff = self.fixture(root)
            handoff["conditions"][1]["status"] = "model_candidate"
            FIXTURE.write_archive(archive, handoff, run=V4_RUN)
            manifest = self.run_import(archive, root)
            candidates = [row for row in self.rows(root / "out/cards.jsonl") if row["producer_status"] == "model_candidate"]
            reverse = self.rows(root / "out/reverse_conditions.jsonl")
            self.assertEqual(len(candidates), 2)
            self.assertEqual(len(reverse), 2)
            for card in candidates:
                reverse_row = reverse[card["reverse_condition_ref"]["line"] - 1]
                self.assertEqual(reverse_row["selected_value"], card["producer_condition"]["au_value"])
            self.assertTrue(manifest["all_model_candidate_mapped_au_whole_values_require_reverse_verification"])

    def test_v4_au_only_stays_a_reverse_whole_value_obligation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, case, product, registry, handoff = self.fixture(root)
            axis = copy.deepcopy(product["au"]["rows"][0]["axes"][0])
            axis.update(axis_name="追加構成", value="本体＋付属品")
            product["au"]["rows"][0]["axes"].append(axis)
            FIXTURE.write_inputs(root, case, product, registry)
            handoff["conditions"].append({"kind": "au_only", "axis_key": None, "axis_label": "追加構成", "value": None,
                                         "au_axis": "追加構成", "au_value": "本体＋付属品", "status": "one_sided"})
            FIXTURE.write_archive(archive, handoff, run=V4_RUN)
            self.run_import(archive, root)
            reverse = self.rows(root / "out/reverse_conditions.jsonl")[-1]
            self.assertEqual(reverse["origin"], "au_only")
            self.assertEqual(reverse["selected_value"], "本体＋付属品")
            self.assertEqual(reverse["raw_condition"], axis)

    def test_status_schemas_are_version_isolated(self):
        for head, status in ((importer.UPSTREAM_V4_HEAD, "extra_in_value"),
                             (importer.UPSTREAM_HEAD, "model_candidate"),
                             (importer.UPSTREAM_V3_HEAD, "model_candidate")):
            with self.subTest(head=head, status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, _, _, _, handoff = self.fixture(root)
                handoff["conditions"][0]["status"] = status
                if head == importer.UPSTREAM_V3_HEAD:
                    for condition, method in zip(handoff["conditions"], ("model", "identical", None)):
                        condition["matched_by"] = method
                FIXTURE.write_archive(archive, handoff, run=importer.PINNED_RUNS[head][0])
                with self.assertRaises(ValueError):
                    self.run_import(archive, root, expected_head=head)
                self.assertFalse((root / "out").exists())

    def test_v3_still_requires_alignment_method_and_v4_rejects_retired_field(self):
        for head, add_method in ((importer.UPSTREAM_V3_HEAD, False), (importer.UPSTREAM_V4_HEAD, True)):
            with self.subTest(head=head), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, _, _, _, handoff = self.fixture(root)
                if head == importer.UPSTREAM_V3_HEAD:
                    handoff["conditions"][0]["status"] = "extra_in_value"
                if add_method:
                    handoff["conditions"][0]["matched_by"] = "model"
                FIXTURE.write_archive(archive, handoff, run=importer.PINNED_RUNS[head][0])
                with self.assertRaises(ValueError):
                    self.run_import(archive, root, expected_head=head)

    def test_v4_aligned_requires_identical_full_values_but_preserves_raw_normalization_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _, _, handoff = self.fixture(root)
            handoff["conditions"][0]["status"] = "aligned"
            FIXTURE.write_archive(archive, handoff, run=V4_RUN)
            with self.assertRaises(ValueError):
                self.run_import(archive, root)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, case, product, registry, handoff = self.fixture(root)
            product["au"]["rows"][0]["axes"][1]["value"] = "青　"
            span = product["au"]["rows"][0]["axes"][1]["value_span"]
            span.update(quote="青　", end=2)
            handoff["conditions"][1]["au_value"] = "青　"
            FIXTURE.write_inputs(root, case, product, registry)
            FIXTURE.write_archive(archive, handoff, run=V4_RUN)
            self.run_import(archive, root)
            aligned = self.rows(root / "out/upstream_context.jsonl")[0]["aligned_conditions"][0]
            self.assertEqual(aligned["mapped_au_condition"]["value"], "青　")
            self.assertEqual(aligned["raw_condition"]["value"], "青")

    def test_mapped_au_value_and_span_mutations_are_rejected(self):
        for change in ("whole_value", "axis_name", "span"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, _, product, _, handoff = self.fixture(root)
                condition = handoff["conditions"][0]
                if change == "whole_value":
                    condition["au_value"] = "天然木"
                elif change == "axis_name":
                    condition["au_axis"] = "別の軸"
                else:
                    condition["au_value_span"] = copy.deepcopy(product["au"]["rows"][0]["axes"][0]["value_span"])
                    condition["au_value_span"]["start"] = 1
                FIXTURE.write_archive(archive, handoff, run=V4_RUN)
                with self.assertRaises(ValueError):
                    self.run_import(archive, root)
                self.assertFalse((root / "out").exists())

    def test_missing_one_sided_nan_normalized_only_in_derived_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _, _, _ = self.fixture(root)
            self.run_import(archive, root)
            cards = self.rows(root / "out/cards.jsonl")
            self.assertIsNone(cards[-1]["producer_condition"]["au_value"])
            self.assertTrue(cards[-1]["producer_condition"]["producer_missing_au_value_was_nan"])
            with ZipFile(archive) as zipped:
                original = b"".join(zipped.read(f"{V4_RUN}/{cohort}/align/handoff.jsonl") for cohort in importer.COHORTS)
            self.assertEqual((root / "out/original_handoff.jsonl").read_bytes(), original)
            self.assertIn(b"NaN", original)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _, _, handoff = self.fixture(root)
            handoff["conditions"][0]["au_value"] = float("nan")
            FIXTURE.write_archive(archive, handoff, run=V4_RUN)
            with self.assertRaises(ValueError):
                self.run_import(archive, root)

    def test_label_informed_upstream_and_absent_candidate_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _, _, _ = self.fixture(root)

            def edit(payload, checkpoint):
                for cohort in importer.COHORTS:
                    member = f"{V4_RUN}/{cohort}/align/summary.json"
                    summary = json.loads(payload[member])
                    summary["label_informed_changes"] = "artificial fixture: changed after machine reference"
                    payload[member] = json.dumps(summary).encode()
                payload[f"{V4_RUN}/candidate/align/summary.json"] = json.dumps({"handoff_rows": 999,
                    "outputs_sha256": {"handoff.jsonl": "f" * 64}}).encode()
                checkpoint["label_derived_content"] = "artificial aggregate disclosure"

            self.rewrite_payload(archive, edit)
            manifest = self.run_import(archive, root)
            self.assertFalse(manifest["labels_read"])
            self.assertTrue(manifest["upstream_method_label_informed"])
            self.assertIn("artificial fixture", manifest["upstream_label_informed_changes"]["legacy"])
            self.assertEqual(manifest["upstream_label_derived_content_disclosure"], "artificial aggregate disclosure")
            candidate = manifest["unreceived_cohorts"]["candidate"]
            self.assertEqual(candidate["declared_handoff_row_count"], 999)
            self.assertTrue(candidate["summary_row_count_is_unverified_declaration"])
            self.assertFalse(candidate["handoff_in_archive"])
            self.assertFalse(candidate["processed_by_this_importer"])
            self.assertEqual(manifest["received_cohorts"], ["legacy", "family"])

    def test_singleton_au_axes_omitted_by_producer_are_warned_not_invented(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, case, product, registry, handoff = self.fixture(root)
            axis = copy.deepcopy(product["au"]["rows"][0]["axes"][0])
            axis.update(axis_name="固定構成", value="単一の構成")
            product["au"]["rows"][0]["axes"].append(axis)
            FIXTURE.write_inputs(root, case, product, registry)
            manifest = self.run_import(archive, root)
            self.assertEqual(manifest["unrepresented_au_axis_counts"]["single_valued_au_axis_occurrences"], 1)
            self.assertEqual(manifest["reverse_condition_count"], 1)
            self.assertIn("singleton", manifest["producer_au_only_export_limitation"])

    def test_duplicate_json_and_embedded_hash_mutations_remain_rejected(self):
        for duplicate in (True, False):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, _, _, _, _ = self.fixture(root)
                member = f"{V4_RUN}/legacy/align/handoff.jsonl"
                if duplicate:
                    def edit(payload, _):
                        payload[member] = payload[member].replace(b'"case_id": "case1"', b'"case_id": "case1", "case_id": "case1"', 1)
                        summary_member = f"{V4_RUN}/legacy/align/summary.json"
                        summary = json.loads(payload[summary_member])
                        summary["outputs_sha256"]["handoff.jsonl"] = importer.sha256(payload[member])
                        payload[summary_member] = json.dumps(summary).encode()
                    self.rewrite_payload(archive, edit)
                else:
                    with ZipFile(archive) as zipped:
                        payload = {name: zipped.read(name) for name in zipped.namelist()}
                    payload[member] += b"\n"
                    with ZipFile(archive, "w") as zipped:
                        for name, body in payload.items():
                            zipped.writestr(name, body)
                with self.assertRaises(ValueError):
                    self.run_import(archive, root)

    def test_v4_external_pin_and_versioned_member_binding_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _, _, _ = self.fixture(root)
            with self.assertRaises(ValueError):
                importer.prepare(archive, root, root / "out", expected_head=importer.UPSTREAM_V4_HEAD)
            with self.assertRaises(ValueError):
                self.run_import(archive, root, expected_head=importer.UPSTREAM_V3_HEAD)


if __name__ == "__main__":
    unittest.main()
