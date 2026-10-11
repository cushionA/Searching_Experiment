"""Artificial V5 contract fixtures; no fixture enters a real-data experiment."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import import_claude_alignment_handoff_v2 as importer

spec = importlib.util.spec_from_file_location("v5_artificial_fixture_helpers", ROOT / "tests/test_lab_claude_handoff_v4.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
FIXTURE = helpers.FIXTURE
V5_RUN = importer.PINNED_RUNS[importer.UPSTREAM_V5_HEAD][0]


class ClaudeFlatHandoffV5Tests(unittest.TestCase):
    def fixture(self, root):
        archive, case, product, registry, handoff = FIXTURE.fixture(root)
        handoff["conditions"][0]["status"] = "model_candidate"
        FIXTURE.write_archive(archive, handoff, run=V5_RUN)
        return archive, case, product, registry, handoff

    def run_import(self, archive, root, **kwargs):
        return importer.prepare(archive, root, root / "out",
                                expected_sha256=importer.sha256(archive.read_bytes()),
                                expected_head=kwargs.pop("expected_head", importer.UPSTREAM_V5_HEAD), **kwargs)

    def test_v5_preserves_complete_model_candidate_and_reverse_obligation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, case, product, _, _ = self.fixture(root)
            manifest = self.run_import(archive, root)
            card = FIXTURE.rows(root / "out/cards.jsonl")[0]
            reverse = FIXTURE.rows(root / "out/reverse_conditions.jsonl")[0]
            self.assertEqual(card["raw_condition"], case["rakuten"]["axes"][0])
            self.assertEqual(card["selected_value"], "本棚付き / 天然木")
            self.assertEqual(card["option_values"], case["rakuten"]["axes"][0]["family_values"])
            self.assertEqual(reverse["raw_condition"], product["au"]["rows"][0]["axes"][0])
            self.assertEqual(reverse["selected_value"], "天然木（無塗装）")
            self.assertEqual(reverse["parent_condition_ref"]["condition_id"], card["condition_id"])
            self.assertFalse(card["automatic_adoption_allowed"])
            self.assertFalse(reverse["automatic_adoption_allowed"])
            self.assertEqual(manifest["upstream_flat_version"], 5)
            self.assertTrue(manifest["upstream_axis_links_can_use_model_candidates"])
            self.assertFalse(manifest["upstream_model_axis_link_is_value_correspondence_proof"])

    def test_v5_model_link_cannot_be_exported_as_aligned_whole_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _, _, handoff = self.fixture(root)
            handoff["conditions"][0]["status"] = "aligned"
            FIXTURE.write_archive(archive, handoff, run=V5_RUN)
            with self.assertRaisesRegex(ValueError, "identical whole-value"):
                self.run_import(archive, root)
            self.assertFalse((root / "out").exists())

    def test_v5_identical_whole_link_retains_original_normalization_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, case, product, registry, handoff = self.fixture(root)
            product["au"]["rows"][0]["axes"][1]["value"] = "青　"
            product["au"]["rows"][0]["axes"][1]["value_span"].update(quote="青　", end=2)
            handoff["conditions"][1]["au_value"] = "青　"
            FIXTURE.write_inputs(root, case, product, registry)
            FIXTURE.write_archive(archive, handoff, run=V5_RUN)
            self.run_import(archive, root)
            context = FIXTURE.rows(root / "out/upstream_context.jsonl")[0]
            self.assertEqual(context["aligned_conditions"][0]["mapped_au_condition"]["value"], "青　")
            self.assertFalse(context["upstream_alignment_is_proof"])
            self.assertEqual(context["final_sku_adoption"], "not_decided")

    def test_v5_rejects_retired_fields_and_non_pending_states(self):
        for change in ("matched_by", "extra_in_value", "contradiction", "symmetric_unresolved"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, _, _, _, handoff = self.fixture(root)
                if change == "matched_by":
                    handoff["conditions"][0][change] = "model"
                else:
                    handoff["conditions"][0]["status"] = change
                FIXTURE.write_archive(archive, handoff, run=V5_RUN)
                with self.assertRaises(ValueError):
                    self.run_import(archive, root)

    def test_v5_fixed_page_sku_and_raw_options_are_bound(self):
        for change in ("fixed_page", "sku", "whole_value", "options", "span"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, _, _, _, handoff = self.fixture(root)
                if change == "fixed_page":
                    handoff["au_product_id"] = "200"
                elif change == "sku":
                    handoff["rakuten_sku"]["source_sku_key"] += "other"
                elif change == "whole_value":
                    handoff["conditions"][0]["value"] = "天然木"
                elif change == "options":
                    handoff["rakuten_axes"]["kind"]["family_values"].pop()
                else:
                    handoff["rakuten_axes"]["kind"]["value_span"]["start"] = 1
                FIXTURE.write_archive(archive, handoff, run=V5_RUN)
                with self.assertRaises(ValueError):
                    self.run_import(archive, root)

    def test_v5_missing_singleton_au_axis_is_not_silently_satisfied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, case, product, registry, _ = self.fixture(root)
            axis = copy.deepcopy(product["au"]["rows"][0]["axes"][0])
            axis.update(axis_name="固定構成", value="単一セット")
            product["au"]["rows"][0]["axes"].append(axis)
            FIXTURE.write_inputs(root, case, product, registry)
            manifest = self.run_import(archive, root)
            self.assertEqual(manifest["unrepresented_au_axis_counts"]["single_valued_au_axis_occurrences"], 1)
            self.assertIn("singleton", manifest["producer_au_only_export_limitation"])

    def test_v5_label_informed_method_is_disclosed_without_reading_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _, _, _ = self.fixture(root)
            def edit(payload, checkpoint):
                for cohort in importer.COHORTS:
                    name = f"{V5_RUN}/{cohort}/align/summary.json"
                    summary = json.loads(payload[name])
                    summary["label_informed_changes"] = "artificial fixture: v5 reviewed earlier references"
                    payload[name] = json.dumps(summary).encode()
                checkpoint["label_derived_content"] = "artificial aggregate only"
            helpers.ClaudeFlatHandoffV4Tests.rewrite_payload(archive, edit)
            manifest = self.run_import(archive, root)
            self.assertTrue(manifest["upstream_method_label_informed"])
            self.assertFalse(manifest["labels_read"])
            self.assertFalse(manifest["model_inference_run"])
            self.assertFalse(manifest["unreceived_cohorts"]["candidate"]["processed_by_this_importer"])

    def test_v5_archive_run_and_external_digest_are_version_specific(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, _, _, _, _ = self.fixture(root)
            with self.assertRaises(ValueError):
                importer.prepare(archive, root, root / "out", expected_head=importer.UPSTREAM_V5_HEAD)
            with self.assertRaises(ValueError):
                self.run_import(archive, root, expected_head=importer.UPSTREAM_V4_HEAD)


if __name__ == "__main__":
    unittest.main()
