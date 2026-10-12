from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import sku_directional_choice_nli_v1 as protocol  # noqa: E402

INPUT = ROOT / ".lab-output/sku-next-dev-v7-adapter-20261013-v1/full-gate-inputs-v8"
SOURCE_ROOT = ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source"


class DirectionalChoiceNliSourceValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="sku-choice-nli-test-")
        cls.prepared = Path(cls.temp.name) / "prepared"
        protocol.prepare(INPUT, SOURCE_ROOT, cls.prepared)
        _, cls.pairs, cls.contexts, cls.source_docs, cls.bindings = protocol._load_prepared(cls.prepared)
        cls.pair_id = cls._find_exact_axis_pair()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def _find_exact_axis_pair(cls):
        for pair_id in sorted(cls.pairs):
            pair = cls.pairs[pair_id]
            binding = cls.bindings[pair_id][0]
            if binding["exact_name_binding_status"] != "unique":
                continue
            context = cls.contexts[pair["context_id"]]
            has_body = any(cls.source_docs.get(ref["document_sha256"], {}).get("blocks")
                            for ref in context.get("source_body_refs", []))
            if len(context["target_selected_facts"]) > 1 and has_body:
                return pair_id
        raise AssertionError("frozen DEV source has no exact-name multi-axis paired example")

    @classmethod
    def _raw_citation(cls, evidence_id, span, document_sha256=None):
        citation = {"evidence_id": evidence_id, "source_sha256": span["sha256"],
                    "start": span["start"], "end": span["end"], "quote": span["quote"]}
        if document_sha256 is not None:
            citation["document_sha256"] = document_sha256
        return citation

    @classmethod
    def _valid_response(cls, pair_id=None):
        pair_id = pair_id or cls.pair_id
        pair = cls.pairs[pair_id]
        context = cls.contexts[pair["context_id"]]
        binding = cls.bindings[pair_id][0]
        target_id = binding["exact_name_target_fact_ids"][0]
        target_fact = next(f for f in context["target_selected_facts"] if f["fact_id"] == target_id)
        judgments = []
        citation_table = []
        citation_ids_by_canonical = {}

        def add_citation(citation):
            key = protocol.digest(citation)
            if key not in citation_ids_by_canonical:
                citation_id = "cite:" + key
                citation_ids_by_canonical[key] = citation_id
                citation_table.append({"citation_id": citation_id, **citation})
            return citation_ids_by_canonical[key]

        for candidate in pair["candidate_vector"]:
            judgment = {
                "option_index": candidate["option_index"],
                "whole_option": candidate["whole_option"],
                "option_evidence_id": candidate["option_evidence_id"],
                "whole_value_assessed": True,
                "state": "unknown",
                "scope_assessment": "uncertain",
                "scope_and_coordinate_explanation": "Fixture leaves condition scope and relation unresolved.",
                "semantic_relation": "uncertain",
                
                "exclusivity_assessment": "uncertain",
                "option_relation_explanation": "Fixture leaves the complete option unresolved.",
                "target_fact_ids": [], "citation_ids": [],
            }
            if candidate["option_index"] == 0:
                member_span = protocol._context_raw_spans(context)[candidate["option_evidence_id"]]
                judgment.update({
                    "state": "support", "scope_assessment": "same_condition",
                    "scope_and_coordinate_explanation": "Fixture assesses scope and coordinate relation for the complete option.",
                    "semantic_relation": "support",
                    "exclusivity_assessment": "source_family",
                    "option_relation_explanation": "Fixture compares this complete value with the cited target fact.",
                    "target_fact_ids": [target_id],
                    "citation_ids": [add_citation(cls._raw_citation(protocol._span_id(target_fact["axis_name_span"]), target_fact["axis_name_span"])),
                                     add_citation(cls._raw_citation(target_fact["fact_id"], target_fact["value_span"])),
                                     add_citation(cls._raw_citation(candidate["option_evidence_id"], member_span))],
                })
            judgments.append(judgment)
        body_payload = protocol.build_model_payload(cls.prepared, pair_id)["context"]["eligible_source_bodies"]
        first_block = next(block for body in body_payload for block in body["blocks"] if block["support_text"])
        body_citation = {"evidence_id": first_block["evidence_id"],
                         "source_sha256": first_block["source_sha256"],
                         "document_sha256": first_block["document_sha256"],
                         "start": 0, "end": len(first_block["support_text"]),
                         "quote": first_block["support_text"]}
        judgments[0]["citation_ids"].append(add_citation(body_citation))
        return {"protocol": protocol.PROTOCOL, "pair_id": pair["pair_id"],
                "cache_key": pair["cache_key"], "scope_status": "model_assessed_unproved",
                "sourceproof_certified": False, "production_adoption_allowed": False,
                "citation_table": citation_table, "judgments": judgments}

    def test_valid_raw_target_and_whole_option_citations_pass_as_unproved_semantics(self):
        result = protocol.validate_response(self.prepared, self._valid_response(), SOURCE_ROOT)
        self.assertTrue(result["source_citations_verified"])
        self.assertFalse(result["semantic_truth_verified"])
        self.assertFalse(result["sourceproof_certified"])

    def test_model_payload_hides_source_selected_record_but_keeps_unmarked_family(self):
        pair = self.pairs[self.pair_id]
        context = self.contexts[pair["context_id"]]
        binding = self.bindings[self.pair_id][0]
        hidden = binding["source_selected_fact"]
        self.assertIn(hidden["value"], context["hypothesis"]["family_values"])
        self.assertNotIn("source_selected_fact", context)
        self.assertNotIn("value_span", context["hypothesis"])
        self.assertTrue(all("role" not in m and "selected" not in m for m in context["hypothesis"]["family_members"]))
        for forbidden in ("task_binding", "request_binding", "source_sku_key", "case_id", "task_id"):
            self.assertNotIn(forbidden, pair)
            self.assertNotIn(forbidden, context)
        rendered = protocol.build_model_payload(self.prepared, self.pair_id)
        self.assertEqual(protocol.build_model_payloads(self.prepared, [self.pair_id])[0], rendered)
        self.assertEqual(rendered["candidate_vector"], pair["candidate_vector"])
        self.assertEqual(rendered["context"]["target_selected_facts"], context["target_selected_facts"])
        self.assertTrue(all("support_text" in b for d in rendered["context"]["eligible_source_bodies"]
                            for b in d["blocks"]))
        self.assertNotIn("source_selected_fact", rendered["context"])
        self.assertNotIn("task_id", json.dumps(rendered))

    def test_mutating_hidden_selected_fact_to_other_verified_family_member_does_not_change_payload(self):
        task_binding = self.bindings[self.pair_id][0]["task_binding"]
        task_id = task_binding["task_id"]
        bundle = Path(self.temp.name) / "input-mutated"
        shutil.copytree(INPUT, bundle)
        tasks = protocol.read_rows(bundle / "tasks.jsonl")
        target_task = next(t for t in tasks if t["task_id"] == task_id)
        original = protocol._fact_record(protocol.SOURCES.RawStore(SOURCE_ROOT), target_task)
        alternate = next(m for m in original["family"]["members"] if m["value"] != original["value"])
        target_task["raw_condition_ref"]["value"] = alternate["value"]
        target_task["raw_condition_ref"]["value_span"] = alternate["span"]
        with (bundle / "tasks.jsonl").open("w", encoding="utf-8") as f:
            for task in tasks:
                f.write(json.dumps(task, ensure_ascii=False, separators=(",", ":")) + "\n")
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        manifest["outputs_sha256"]["tasks.jsonl"] = hashlib.sha256((bundle / "tasks.jsonl").read_bytes()).hexdigest()
        (bundle / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        mutated_prepared = Path(self.temp.name) / "mutated-prepared"
        protocol.prepare(bundle, SOURCE_ROOT, mutated_prepared)
        _, original_pairs, _, _, original_bindings = protocol._load_prepared(self.prepared)
        _, mutated_pairs, _, _, mutated_bindings = protocol._load_prepared(mutated_prepared)
        original = next(b for values in original_bindings.values() for b in values
                        if b["task_binding"]["task_id"] == task_id)
        changed = next(b for values in mutated_bindings.values() for b in values
                       if b["task_binding"]["task_id"] == task_id)
        self.assertEqual(original["source_selected_fact"]["value_span"]["quote"], original["source_selected_fact"]["value"])
        self.assertEqual(changed["source_selected_fact"]["value"], alternate["value"])
        self.assertEqual(original_pairs[original["pair_id"]], mutated_pairs[changed["pair_id"]])

    def test_source_hypothesis_member_cannot_be_misrepresented_as_target_axis_fact(self):
        response = self._valid_response()
        pair = self.pairs[response["pair_id"]]
        context = self.contexts[pair["context_id"]]
        candidate = pair["candidate_vector"][0]
        hypothesis_span = protocol._context_raw_spans(context)[candidate["option_evidence_id"]]
        claim = response["judgments"][0]
        claim["target_fact_ids"] = [candidate["option_evidence_id"]]
        citation = self._raw_citation(candidate["option_evidence_id"], hypothesis_span)
        response["citation_table"][0] = {"citation_id": claim["citation_ids"][0], **citation}
        with self.assertRaisesRegex(ValueError, "outside the exact paired source context"):
            protocol.validate_response(self.prepared, response, SOURCE_ROOT)

    def test_cross_pair_target_fact_cannot_be_reused(self):
        response = self._valid_response()
        pair = self.pairs[response["pair_id"]]
        binding = self.bindings[response["pair_id"]][0]
        other = next(p for pid, p in self.pairs.items() if pid != response["pair_id"]
                     and p["context_id"] != pair["context_id"])
        foreign_context = self.contexts[other["context_id"]]
        foreign = foreign_context["target_selected_facts"][0]
        response["judgments"][0]["target_fact_ids"] = [foreign["fact_id"]]
        citation = self._raw_citation(foreign["fact_id"], foreign["value_span"])
        response["citation_table"][0] = {"citation_id": response["judgments"][0]["citation_ids"][0], **citation}
        with self.assertRaisesRegex(ValueError, "outside the exact paired source context"):
            protocol.validate_response(self.prepared, response, SOURCE_ROOT)

    def test_full_ordered_vector_and_exact_raw_offsets_are_required(self):
        response = self._valid_response()
        response["judgments"].pop()
        with self.assertRaisesRegex(ValueError, "every original whole option"):
            protocol.validate_response(self.prepared, response, SOURCE_ROOT)
        response = self._valid_response()
        response["judgments"][0]["whole_option"] += " (stripped)"
        with self.assertRaisesRegex(ValueError, "exact complete original option"):
            protocol.validate_response(self.prepared, response, SOURCE_ROOT)
        response = self._valid_response()
        citation_id = response["judgments"][0]["citation_ids"][0]
        next(c for c in response["citation_table"] if c["citation_id"] == citation_id)["start"] += 1
        with self.assertRaisesRegex(ValueError, "citation"):
            protocol.validate_response(self.prepared, response, SOURCE_ROOT)
        response = self._valid_response()
        citation_id = response["judgments"][0]["citation_ids"][0]
        response["citation_table"][0] = {"citation_id": citation_id,
            "evidence_id": "metadata.selected_value", "source_sha256": "0" * 64,
            "start": 0, "end": 1, "quote": self.pairs[response["pair_id"]]["option_values"][0]}
        with self.assertRaisesRegex(ValueError, "citation"):
            protocol.validate_response(self.prepared, response, SOURCE_ROOT)


if __name__ == "__main__":
    unittest.main()
