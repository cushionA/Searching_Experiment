"""Synthetic-only pinned-v7 adapter fixtures; no real cases or labels are read."""
from __future__ import annotations

from collections import Counter
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(EXPERIMENTS))
spec = importlib.util.spec_from_file_location("test_claude_v7_adapter", EXPERIMENTS / "prepare_claude_alignment_v7.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
from integrate_claude_description_v1 import finish, rows
from prepare_product_condition_share_v1 import prepare as prepare_sharing
from product_condition_sharing_v1 import SEMANTIC_FIELDS, build_groups, digest


class SyntheticV7Fixture:
    """Build a self-contained synthetic archive matching the received schema."""

    code = b"# synthetic producer marker; must never execute\nraise RuntimeError('do not execute')\n"

    def __init__(self, root, mutate=None):
        self.root, self.input_dir = root, root / "synthetic-inputs"
        self.input_dir.mkdir()
        self.archive, self.output = root / "synthetic.zip", root / "prepared"
        self.registry, self.products, self.cases, self.handoffs, self.decisions = {}, [], [], {}, {}
        for cohort in adapter.COHORTS:
            rak_file, au_file = f"synthetic/{cohort}-rak.txt", f"synthetic/{cohort}-au.json"
            rak_sha, au_sha = adapter.sha(rak_file.encode()), adapter.sha(au_file.encode())
            self.registry.update({rak_file: rak_sha, au_file: au_sha})

            def span(value, side):
                return {"raw_file": rak_file if side == "rakuten" else au_file, "sha256": rak_sha if side == "rakuten" else au_sha,
                        "start": 0, "end": len(value), "quote": value, "locator": {"kind": "synthetic_leaf", "path": value}}

            pending = cohort == "legacy"
            value, au_value = ("blue", "blue(extra)") if pending else ("Ａ", "A")
            selected_axis = {"axis_index": 0, "axis_key": "synthetic-color", "axis_label": "Color", "value": value,
                             "family_values": [value, "other"], "value_span": span(value, "rakuten"),
                             "axis_label_span": span("Color", "rakuten")}
            axes = [selected_axis]
            if pending:
                axes.append({"axis_index": 1, "axis_key": "synthetic-feature", "axis_label": "Feature", "value": "small",
                             "family_values": ["small", "large"], "value_span": span("small", "rakuten")})
            product = {"dossier_id": f"synthetic-{cohort}",
                "rakuten": {"url": f"https://synthetic.example/{cohort}/rak", "title": "Synthetic Rakuten page",
                    "title_source": {"raw_file": rak_file, "sha256": rak_sha}, "descriptions": [{"text": "blue extra small", "source_ref": span("blue extra small", "rakuten")}]},
                "au": {"product_id": f"synthetic-au-{cohort}", "url": f"https://synthetic.example/{cohort}/au", "title": "Synthetic AU page",
                    "title_source": {"raw_file": au_file, "sha256": au_sha}, "descriptions": [{"text": "blue extra small", "source_ref": span("blue extra small", "au")}],
                    "rows": [{"row_key": f"synthetic-row-{cohort}-1", "axes": [{"axis_name": "Color", "value": au_value, "value_span": span(au_value, "au")}]},
                             {"row_key": f"synthetic-row-{cohort}-2", "axes": [{"axis_name": "Color", "value": "other", "value_span": span("other", "au")}]}]}}
            case = {"case_id": f"synthetic-case-{cohort}", "dossier_id": product["dossier_id"], "cohort": cohort,
                "au_product_id": product["au"]["product_id"], "rakuten": {"url": product["rakuten"]["url"],
                    "source_sku_key": f"synthetic-sku-{cohort}", "sku_record_key": f"synthetic-record-{cohort}", "variant_id": f"synthetic-variant-{cohort}", "axes": axes}}
            self.products.append(product)
            self.cases.append(case)
            decision = {"case_id": case["case_id"], "dossier_id": case["dossier_id"], "decision": "unmatched" if pending else "matched",
                "top_row_key": None if pending else product["au"]["rows"][0]["row_key"],
                "reason": "pending_description_check" if pending else "all_conditions_aligned_on_one_row",
                "row_states": {"contradiction": 1, "pending_description_check" if pending else "aligned": 1}}
            self.decisions[cohort] = [decision]
            self.handoffs[cohort] = []
            if pending:
                decomposition = adapter.decomposition(value, au_value)
                condition = {"kind": "rakuten", "axis_key": selected_axis["axis_key"], "axis_label": selected_axis["axis_label"],
                    "value": value, "au_axis": "Color", "au_value": au_value, "status": "residual", **decomposition}
                one_sided = {"kind": "rakuten", "axis_key": axes[1]["axis_key"], "axis_label": axes[1]["axis_label"], "value": "small",
                    "au_axis": None, "au_value": float("nan"), "status": "one_sided", "core": None, "residual": None}
                self.handoffs[cohort] = [{"case_id": case["case_id"], "dossier_id": case["dossier_id"], "au_product_id": case["au_product_id"],
                    "au_row_key": product["au"]["rows"][0]["row_key"], "au_product_source": product["au"]["title_source"],
                    "rakuten_sku": {**{key: case["rakuten"][key] for key in ("url", "source_sku_key", "sku_record_key", "variant_id")}, "raw_file": rak_file, "sha256": rak_sha},
                    "conditions": [condition, one_sided]}]
        if mutate:
            mutate(self)
        # Bind the complete redundant original-value tables after fixture edits.
        for cohort, handoffs in self.handoffs.items():
            case = next(c for c in self.cases if c["cohort"] == cohort)
            product = next(p for p in self.products if p["dossier_id"] == case["dossier_id"])
            for handoff in handoffs:
                handoff["rakuten_axes"] = {axis["axis_key"]: {key: axis[key] for key in ("axis_label", "value", "family_values", "value_span")}
                                            for axis in case["rakuten"]["axes"]}
                handoff["au_row_axes"] = copy.deepcopy(product["au"]["rows"][0]["axes"])
                handoff["au_axis_options"] = adapter.au_options(product)
        for name, values in (("cases.jsonl", self.cases), ("products.jsonl", self.products)):
            (self.input_dir / name).write_bytes(self.encode_rows(values))
        manifest = {"synthetic": True, "labels_read": False, "source_raw_sha256": self.registry,
            "output_sha256": {name: adapter.sha((self.input_dir / name).read_bytes()) for name in ("cases.jsonl", "products.jsonl")}}
        (self.input_dir / "manifest.json").write_bytes(adapter.encoded(manifest) + b"\n")
        self.make_archive()

    @staticmethod
    def encode_rows(values):
        # The received producer uses NaN as the unmapped AU marker.
        return b"".join((json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode() for value in values)

    def make_archive(self):
        self.payload, self.runs = {adapter.CODE_MEMBER: self.code}, []
        for cohort in adapter.COHORTS:
            parent = f".lab-output/sku-align-20261012-v7/{cohort}/align/"
            handoff = self.encode_rows(self.handoffs[cohort])
            decisions = self.encode_rows(self.decisions[cohort])
            condition_counts = dict(Counter(f"{c['kind']}:{c['status']}" for h in self.handoffs[cohort] for c in h["conditions"] if c["status"] != "aligned"))
            summary = {"labels_read": False, "code_sha256": adapter.sha(self.code), "similarities_sha256": adapter.sha(cohort.encode()),
                "decisions": dict(Counter(d["decision"] for d in self.decisions[cohort])), "decision_reasons": dict(Counter(d["reason"] for d in self.decisions[cohort])),
                "handoff_rows": len(self.handoffs[cohort]), "handoff_conditions": condition_counts,
                "outputs_sha256": {"handoff.jsonl": adapter.sha(handoff), "decisions.jsonl": adapter.sha(decisions)}}
            self.payload.update({parent + "handoff.jsonl": handoff, parent + "decisions.jsonl": decisions, parent + "summary.json": adapter.encoded(summary)})
            run = {"version": "v7", "run": cohort, "commit": "7fd8e17", "code_sha256": adapter.sha(self.code), "code_in_zip": adapter.CODE_MEMBER,
                "out_dir": f".lab-output/sku-align-20261012-v7/{cohort}", "inputs_sha256": {}, "similarities_sha256": summary["similarities_sha256"],
                "decision_reasons": summary["decision_reasons"], "handoff_rows": summary["handoff_rows"], "handoff_conditions": condition_counts,
                "files": [{"path": parent + name, "bytes": len(self.payload[parent + name]), "sha256": adapter.sha(self.payload[parent + name])}
                          for name in ("handoff.jsonl", "decisions.jsonl", "summary.json")]}
            self.runs.append(run)
        self.payload[adapter.INNER_MEMBER] = adapter.encoded({"archive": self.archive.name, "runs": self.runs})
        self.write_zip()

    def write_zip(self):
        with ZipFile(self.archive, "w") as zipped:
            for name, data in self.payload.items():
                zipped.writestr(name, data)
        self.digest = adapter.sha(self.archive.read_bytes())
        external = {"archive": self.archive.name, "sha256": self.digest, "size_bytes": self.archive.stat().st_size,
                    "file_count": len(self.payload), "uncompressed_bytes": sum(map(len, self.payload.values())), "runs": self.runs}
        self.archive.with_suffix(".manifest.json").write_bytes(adapter.encoded(external))
        Path(str(self.archive) + ".sha256").write_text(f"{self.digest}  {self.archive.name}\n")

    def prepare(self):
        # Only the synthetic producer code pin changes; archive and all member
        # digests, input refs and semantic validations still run normally.
        with patch.object(adapter, "CODE_SHA256", adapter.sha(self.code)):
            return adapter.prepare(self.input_dir, self.archive, self.output, expected_sha256=self.digest)


class ClaudeV7PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-v7-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, mutate=None):
        return SyntheticV7Fixture(self.root, mutate)

    def test_residual_routes_both_whole_values_and_preserves_options_and_spans(self):
        fixture = self.fixture()
        manifest = fixture.prepare()
        tasks = [task for task in rows(fixture.output / "tasks.jsonl") if task["origin"] == "v7_residual"]
        self.assertEqual({task["direction"] for task in tasks}, {"rakuten_to_au", "au_to_rakuten"})
        self.assertEqual({task["raw_condition"]["value"] for task in tasks}, {"blue", "blue(extra)"})
        self.assertTrue(all(task["focused_verification_hints"] == [{"side": "au", "text": "extra", "source_value": "blue(extra)", "verify_with": "rakuten"}] for task in tasks))
        forward = next(task for task in tasks if task["direction"] == "rakuten_to_au")
        reverse = next(task for task in tasks if task["direction"] == "au_to_rakuten")
        self.assertEqual(forward["raw_condition"], fixture.cases[0]["rakuten"]["axes"][0])
        self.assertEqual(forward["option_values"], ["blue", "other"])
        self.assertEqual(reverse["option_values"], ["blue(extra)", "other"])
        requests = rows(fixture.output / "requests.jsonl")
        self.assertTrue(all(request["selected_value"] in request["option_values"] for request in requests))
        self.assertTrue(all(request["focused_verification_hints"] == forward["focused_verification_hints"] for request in requests if request["origin"] == "v7_residual"))
        self.assertEqual(forward["source_handoff"]["rakuten_axes"], fixture.handoffs["legacy"][0]["rakuten_axes"])
        raw_line = forward["source_handoff"]["raw_source_line"]
        self.assertEqual(adapter.sha(raw_line.encode()), forward["source_handoff"]["line_sha256"])
        self.assertIn("NaN", raw_line)
        self.assertFalse(manifest["production_adoption_allowed"])
        self.assertFalse(manifest["labels_read"])
        self.assertFalse(manifest["model_inference_run"])
        self.assertTrue(manifest["synthetic"])
        self.assertTrue(all(plan["all_original_axes_covered"] for plan in rows(fixture.output / "row_plans.jsonl")))

    def test_empty_predictions_drop_unresolved_and_exact_direct_case_still_works(self):
        fixture = self.fixture()
        fixture.prepare()
        empty = self.root / "empty-predictions.jsonl"
        empty.write_bytes(b"")
        output = self.root / "finished"
        manifest = finish(fixture.output, empty, output)
        decisions = {row["cohort"]: row for row in rows(output / "decisions.jsonl")}
        self.assertEqual(decisions["legacy"]["decision"], "drop")
        self.assertEqual(decisions["family"]["decision"], "accept")
        self.assertEqual(manifest["decision_counts"], {"drop": 1, "accept": 1})
        self.assertFalse(manifest["production_adoption_allowed"])

    def test_sharing_compatibility_deduplicates_semantics_but_preserves_mixed_task_proof(self):
        def mutate(f):
            product, original_case, original_handoff = f.products[0], f.cases[0], f.handoffs["legacy"][0]
            # Synthetic numeric AU identity satisfies the real URL contract;
            # this fixture never retrieves that URL or enters a real dataset.
            product["au"].update({"product_id": "999000001", "url": "https://wowma.jp/item/999000001"})
            original_case["au_product_id"] = original_handoff["au_product_id"] = "999000001"
            case, handoff, decision = copy.deepcopy(original_case), copy.deepcopy(original_handoff), copy.deepcopy(f.decisions["legacy"][0])
            case["case_id"] = handoff["case_id"] = decision["case_id"] = "synthetic-case-legacy-second"
            for key in ("source_sku_key", "sku_record_key", "variant_id"):
                case["rakuten"][key] += "-second"
                handoff["rakuten_sku"][key] = case["rakuten"][key]
            # Same complete model input, different genuine producer status.
            # The shared Color request must keep both bindings and become
            # ineligible; the Feature request has only one_sided bindings.
            handoff["conditions"][0].update({"status": "one_sided", "au_axis": None,
                "au_value": float("nan"), "core": None, "residual": None})
            f.cases.append(case)
            f.handoffs["legacy"].append(handoff)
            f.decisions["legacy"].append(decision)
        fixture = self.fixture(mutate)
        fixture.prepare()
        requests, tasks = rows(fixture.output / "requests.jsonl"), rows(fixture.output / "tasks.jsonl")
        documents = rows(fixture.output / "documents.jsonl")
        for request in requests:
            self.assertEqual(request["request_sha256"], digest({field: request[field] for field in SEMANTIC_FIELDS}))
            self.assertTrue(request["task_metadata_is_authoritative"])
            self.assertTrue(request["focused_verification_hints_are_binding_metadata_only"])
        mixed = next(r for r in requests if r["direction"] == "rakuten_to_au" and r["axis_name"] == "Color")
        self.assertEqual(mixed["origin"], "mixed_task_provenance")
        self.assertEqual(set(mixed["origins"]), {"v7_residual", "one_sided"})
        self.assertEqual(len(mixed["task_provenance_hints"]), 2)
        mixed_tasks = [task for task in tasks if task["request_id"] == mixed["request_id"]]
        self.assertEqual(len(mixed_tasks), 2)
        self.assertEqual({task["origin"] for task in mixed_tasks}, {"v7_residual", "one_sided"})
        contract = prepare_sharing(fixture.output, fixture.root / "sharing")
        feature = next(r for r in requests if r["direction"] == "rakuten_to_au" and r["axis_name"] == "Feature")
        self.assertEqual(contract["eligible_forward_request_ids"], [feature["request_id"]])
        bindings = {request["request_id"]: [task for task in tasks if task["request_id"] == request["request_id"]] for request in requests}
        kwargs = {"model_identity": {"model_id": "synthetic-model-not-executed", "revision": "synthetic-pin"},
                  "content_version": "synthetic-render-v1", "invariance_contract": contract}
        plan = build_groups(requests, documents, bindings, **kwargs)
        self.assertEqual(len(plan["groups"]), 1)
        self.assertNotIn(mixed["request_id"], plan["request_to_group"])
        altered = copy.deepcopy(requests)
        for request in altered:
            request["origin"] = "auxiliary metadata only"
            request["focused_verification_hints"] = [{"text": "auxiliary metadata only"}]
        changed = build_groups(altered, documents, bindings, **kwargs)
        self.assertEqual(changed["groups"], plan["groups"])
        self.assertEqual(changed["model_requests"], plan["model_requests"])

    def test_wrong_residual_side_source_or_cross_target_rejected(self):
        for field, value in (("side", "rakuten"), ("source_value", "blue"), ("verify_with", "au"), ("text", "invented")):
            with self.subTest(field=field), tempfile.TemporaryDirectory(prefix="synthetic-v7-invalid-") as directory:
                fixture = SyntheticV7Fixture(Path(directory), lambda f: f.handoffs["legacy"][0]["conditions"][0]["residual"][0].update({field: value}))
                with self.assertRaisesRegex(ValueError, "core/residual"):
                    fixture.prepare()
                self.assertFalse(fixture.output.exists())

    def test_changed_core_rejected_even_with_consistent_archive_hashes(self):
        fixture = self.fixture(lambda f: f.handoffs["legacy"][0]["conditions"][0]["core"].update({"rakuten": ["red"]}))
        with self.assertRaisesRegex(ValueError, "core/residual"):
            fixture.prepare()

    def test_fixed_url_row_and_original_provenance_bindings_rejected(self):
        mutations = [
            lambda f: f.handoffs["legacy"][0]["rakuten_sku"].update({"url": "https://alternate.example/"}),
            lambda f: f.handoffs["legacy"][0].update({"au_row_key": "different-row"}),
            lambda f: f.handoffs["legacy"][0]["au_product_source"].update({"sha256": "0" * 64}),
            lambda f: f.cases[0]["rakuten"]["axes"][0]["value_span"].update({"quote": "invented"}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="synthetic-v7-binding-") as directory:
                fixture = SyntheticV7Fixture(Path(directory), mutation)
                with self.assertRaises(ValueError):
                    fixture.prepare()
                self.assertFalse(fixture.output.exists())

    def test_model_candidate_is_bidirectional_and_never_one_sided_share_origin(self):
        def mutate(f):
            condition = f.handoffs["legacy"][0]["conditions"][0]
            condition["status"] = "model_candidate"
        fixture = self.fixture(mutate)
        fixture.prepare()
        tasks = [task for task in rows(fixture.output / "tasks.jsonl") if task["origin"] == "v7_model_candidate"]
        self.assertEqual({task["direction"] for task in tasks}, {"rakuten_to_au", "au_to_rakuten"})
        self.assertTrue(all(task["producer_condition"]["status"] == "model_candidate" for task in tasks))
        one_sided = [task for task in rows(fixture.output / "tasks.jsonl") if task["origin"] == "one_sided"]
        self.assertEqual(len(one_sided), 1)
        self.assertEqual(one_sided[0]["producer_condition"]["status"], "one_sided")
        self.assertIsNone(one_sided[0]["producer_condition"]["au_axis"])

    def test_numerical_nonidentity_is_not_adopted_from_direct_decision(self):
        def mutate(f):
            rak = f.cases[1]["rakuten"]["axes"][0]
            rak["value"], rak["family_values"] = "15C", ["15C", "other"]
            rak["value_span"].update({"quote": "15C", "end": 3})
            au = f.products[1]["au"]["rows"][0]["axes"][0]
            au["value"] = "-15C"
            au["value_span"].update({"quote": "-15C", "end": 4})
        fixture = self.fixture(mutate)
        fixture.prepare()
        tasks = [task for task in rows(fixture.output / "tasks.jsonl") if task["case_id"] == "synthetic-case-family"]
        self.assertEqual({task["direction"] for task in tasks}, {"rakuten_to_au", "au_to_rakuten"})
        empty = self.root / "empty.jsonl"
        empty.write_bytes(b"")
        finish(fixture.output, empty, self.root / "finished")
        self.assertTrue(all(row["decision"] == "drop" for row in rows(self.root / "finished/decisions.jsonl")))

    def test_sorted_slash_alignment_requires_both_whole_directions(self):
        def mutate(f):
            rak = f.cases[0]["rakuten"]["axes"][0]
            rak["value"], rak["family_values"] = "blue/red", ["blue/red", "other"]
            rak["value_span"].update({"quote": "blue/red", "end": 8})
            au = f.products[0]["au"]["rows"][0]["axes"][0]
            au["value"] = "red/blue"
            au["value_span"].update({"quote": "red/blue", "end": 8})
            condition = f.handoffs["legacy"][0]["conditions"][0]
            condition.update({"value": "blue/red", "au_value": "red/blue", "status": "aligned", **adapter.decomposition("blue/red", "red/blue")})
        fixture = self.fixture(mutate)
        fixture.prepare()
        tasks = [task for task in rows(fixture.output / "tasks.jsonl") if task["origin"] == "v7_aligned_nonidentical"]
        self.assertEqual({task["direction"] for task in tasks}, {"rakuten_to_au", "au_to_rakuten"})
        self.assertEqual({task["raw_condition"]["value"] for task in tasks}, {"blue/red", "red/blue"})

    def test_pending_exact_alignment_is_basic_but_uncovered_au_axes_stay_reverse(self):
        def mutate(f):
            product = f.products[0]
            au = product["au"]["rows"][0]["axes"][0]
            au["value"] = "blue"
            au["value_span"].update({"quote": "blue", "end": 4})
            condition = f.handoffs["legacy"][0]["conditions"][0]
            condition.update({"au_value": "blue", "status": "aligned", "core": None, "residual": None})
            for number, row in enumerate(product["au"]["rows"]):
                value = "extra" if number == 0 else "alternative extra"
                span = copy.deepcopy(au["value_span"])
                span.update({"quote": value, "end": len(value)})
                row["axes"].append({"axis_name": "Unrepresented", "value": value, "value_span": span})
        fixture = self.fixture(mutate)
        fixture.prepare()
        plan = next(p for p in rows(fixture.output / "row_plans.jsonl") if p["case_id"] == "synthetic-case-legacy")
        self.assertEqual(sum(binding["kind"] == "upstream_identical" for binding in plan["condition_bindings"]), 1)
        self.assertTrue(plan["all_original_axes_covered"])
        tasks = [task for task in rows(fixture.output / "tasks.jsonl") if task["case_id"] == "synthetic-case-legacy"]
        self.assertEqual(len(tasks), 2)
        reverse = next(task for task in tasks if task["direction"] == "au_to_rakuten")
        self.assertEqual(reverse["origin"], "v7_unrepresented_au_axis")
        self.assertEqual(reverse["option_values"], ["extra", "alternative extra"])

    def test_all_pending_rows_must_be_received(self):
        fixture = self.fixture(lambda f: f.decisions["legacy"][0].update({"row_states": {"pending_description_check": 2}}))
        with self.assertRaisesRegex(ValueError, "pending count"):
            fixture.prepare()

    def test_symmetric_unresolved_and_contradiction_cases_drop_without_invented_rows(self):
        for reason, states in (("symmetric_condition_unresolved", {"symmetric_unresolved": 2}),
                              ("contradiction_on_every_row", {"contradiction": 2}), ("several_rows_aligned", {"aligned": 2})):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory(prefix="synthetic-v7-omitted-") as directory:
                def mutate(f):
                    f.decisions["family"][0].update({"decision": "unmatched", "top_row_key": None, "reason": reason, "row_states": states})
                fixture = SyntheticV7Fixture(Path(directory), mutate)
                fixture.prepare()
                empty = fixture.root / "empty.jsonl"
                empty.write_bytes(b"")
                finish(fixture.output, empty, fixture.root / "finished")
                self.assertTrue(all(row["decision"] == "drop" for row in rows(fixture.root / "finished/decisions.jsonl")))
                family = next(plan for plan in rows(fixture.output / "case_plans.jsonl") if plan["cohort"] == "family")
                self.assertEqual(family["candidate_row_keys"], [])

    def test_extra_directory_archive_entry_rejected(self):
        fixture = self.fixture()
        fixture.payload[adapter.CODE_MEMBER + "/"] = b""
        fixture.write_zip()
        with self.assertRaisesRegex(ValueError, "unsafe or duplicate"):
            fixture.prepare()

    def test_duplicate_json_key_rejected_despite_resealed_hashes(self):
        fixture = self.fixture()
        name = ".lab-output/sku-align-20261012-v7/legacy/align/handoff.jsonl"
        data = fixture.payload[name].replace(b'"case_id":', b'"case_id":"forged", "case_id":', 1)
        fixture.payload[name] = data
        run = fixture.runs[0]
        next(spec for spec in run["files"] if spec["path"] == name).update({"bytes": len(data), "sha256": adapter.sha(data)})
        summary_name = ".lab-output/sku-align-20261012-v7/legacy/align/summary.json"
        summary = json.loads(fixture.payload[summary_name])
        summary["outputs_sha256"]["handoff.jsonl"] = adapter.sha(data)
        fixture.payload[summary_name] = adapter.encoded(summary)
        next(spec for spec in run["files"] if spec["path"] == summary_name).update({"bytes": len(fixture.payload[summary_name]), "sha256": adapter.sha(fixture.payload[summary_name])})
        fixture.payload[adapter.INNER_MEMBER] = adapter.encoded({"archive": fixture.archive.name, "runs": fixture.runs})
        fixture.write_zip()
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            fixture.prepare()

    def test_archive_and_embedded_member_hash_corruption_rejected(self):
        fixture = self.fixture()
        fixture.archive.write_bytes(fixture.archive.read_bytes() + b"corruption")
        with self.assertRaisesRegex(ValueError, "archive SHA256"):
            fixture.prepare()
        fixture.make_archive()
        name = ".lab-output/sku-align-20261012-v7/legacy/align/handoff.jsonl"
        fixture.payload[name] += b"corruption"
        fixture.write_zip()
        with self.assertRaisesRegex(ValueError, "embedded size/SHA256"):
            fixture.prepare()

    def test_generic_output_hash_and_pending_coverage_rejected(self):
        fixture = self.fixture()
        (fixture.input_dir / "cases.jsonl").write_bytes((fixture.input_dir / "cases.jsonl").read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "frozen output SHA"):
            fixture.prepare()

    def test_row_count_must_cover_complete_fixed_array(self):
        fixture = self.fixture(lambda f: f.decisions["legacy"][0].update({"row_states": {"pending_description_check": 1}}))
        with self.assertRaisesRegex(ValueError, "fixed AU row array"):
            fixture.prepare()


if __name__ == "__main__":
    unittest.main()
