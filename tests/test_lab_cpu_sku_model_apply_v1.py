import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, "experiments/sku-matching")
import apply_cpu_requirement_relations_v1 as apply
import sku_integrated_gate_v1 as adapter


class CpuSkuModelApplyV1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.au = {"title": "ハンガー", "description": "便利なフックとバー付きなので整理できます。",
                   "blue": "ブルー", "red": "レッド", "cover": "カバー付き"}
        self.rak = {"bar": "バー付き", "blue": "ブルー", "red": "レッド"}
        for name, value in (("au.json", self.au), ("rak.json", self.rak)):
            (self.root / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.store = apply.modules.src.RawStore(self.root)
        def span(name, field):
            text = (self.au if name == "au.json" else self.rak)[field]
            return apply.modules.src.make_span(self.store, name,
                {"kind": "json_leaf", "json_path": "$." + field}, 0, len(text), text)
        self.span = span
        rows = [{"row_key": key, "row_index": i, "column_index": 0, "sku_id": 1,
                 "axes": [{"axis_name": "カラー", "value": self.au[color],
                           "value_span": span("au.json", color), "axis_name_span": None}],
                 "array_source": {}} for i, (key, color) in enumerate((("row-a", "blue"), ("row-b", "red")))]
        vocab, families, tokens = apply.modules.derive_vocabulary(
            [(a["axis_name"], a["value"]) for r in rows for a in r["axes"]],
            [{"key": "タイプ", "label": "タイプ", "values": ["バー付き", "バーなし"]},
             {"key": "カラー", "label": "カラー", "values": ["ブルー", "レッド"]}])
        self.context = {"dossier_id": "dossier", "pair_ref": "pair",
            "au_product": {"product_id": "1", "raw_file": "au.json", "sha256": self.store.sha("au.json"),
                           "title": self.au["title"], "title_span": span("au.json", "title"), "axis_names": {}},
            "au_rows": rows,
            "rakuten_product": {"url": "rak-fixture", "raw_file": "rak.json", "sha256": self.store.sha("rak.json"),
                "encoding": "utf-8", "title": "", "title_span": None, "selector_families": families},
            "color_vocab": sorted(vocab), "variant_tokens": list(tokens),
            "au_description_lines": [{"text": self.au["description"], "span": span("au.json", "description"), "scope_tag": "product_page"}],
            "rakuten_description_lines": [], "au_purchase_option_lines": [], "size_code_crosswalks": []}
        self.case = {"case_id": "case-a", "dossier_id": "dossier", "au_product_id": "1",
            "rakuten_selected": {"url": "rak-fixture", "raw_file": "rak.json", "sha256": self.store.sha("rak.json"),
                "variant_id": "v", "source_row_key": "v", "source_sku_key": "v", "sku_record_key": "v",
                "source_row_index": 0, "variant_attributes": [],
                "axes": [{"axis_index": i, "axis_key": key, "axis_label": key, "axis_label_span": None,
                          "value": self.rak[field], "value_span": span("rak.json", field), "family_values": values}
                         for i, (key, field, values) in enumerate((
                             ("タイプ", "bar", ["バー付き", "バーなし"]),
                             ("カラー", "blue", ["ブルー", "レッド"])))]}}
        self.facts = apply.modules.PairFacts(self.context)
        self.baseline = adapter.predict_case(self.case, self.facts, store=self.store)
        self.req = next(r for r in self.baseline["requirements"] if r.get("component") == "word:バー")
        self.task = {"task_id": "task", "case_id": "case-a", "au_row_key": "row-a",
            "requirement": {k: self.req.get(k) for k in apply.TASK_FIELDS},
            "hypothesis": "この商品にはバーが付いている。",
            "evidence": {"evidence_id": "evidence", "quote": self.au["description"],
                         "span": span("au.json", "description")},
            "source_scope": {"kind": "fixed_au_product", "scope_tag": "product_page", "au_product_id": "1"}}
        self.task["evidence"]["source_scope"] = self.task["source_scope"]
        self.target = {"task_id": "task", "case_id": "case-a", "au_row_key": "row-a",
                       "requirement_id": self.req["requirement_id"], "purpose": "unresolved_presence"}
        self.record = apply.trial.make_record(self.task, {"support": .95, "conflict": .02, "unknown": .03},
                                             "support", .95, .01, 32, "nli")

    def bind(self, task=None, record=None, targets=None, cases=None, baseline=None):
        task = task or self.task
        return apply.bind_proposals({task["task_id"]: task}, targets or [self.target],
            {task["task_id"]: record or self.record}, cases or {"case-a": self.case},
            {"dossier": self.context}, baseline or {"case-a": self.baseline}, self.store, {"model_id": "test-fixture"})

    def test_verified_quote_completes_unknown_and_keeps_all_rows(self):
        self.assertEqual(self.baseline["decision"], "drop")
        proposals = self.bind()
        result = adapter.predict_case(self.case, self.facts,
            evaluator=apply.ProposalEvaluator(self.facts, "case-a", proposals), store=self.store)
        self.assertEqual(result["decision"], "accept", result)
        self.assertEqual(result["au_row_key"], "row-a")
        self.assertEqual({r["row_key"] for r in result["rows"]}, {"row-a", "row-b"})
        self.assertEqual(result["requirements"], self.baseline["requirements"])

    def test_forged_quote_and_wrong_hypothesis_are_rejected(self):
        task = copy.deepcopy(self.task)
        task["evidence"]["span"]["start"] += 1
        record = apply.trial.make_record(task, {"support": .95, "conflict": .02, "unknown": .03}, "support", .95, 0, 32, "nli")
        with self.assertRaisesRegex(ValueError, "literal source quote"):
            self.bind(task=task, record=record)
        record = copy.deepcopy(self.record)
        record["hypothesis"] = "別商品にはバーが付いている。"
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            self.bind(record=record)

    def test_unproven_competitor_cannot_be_adopted(self):
        context = copy.deepcopy(self.context)
        context["au_rows"][1]["axes"] = copy.deepcopy(context["au_rows"][0]["axes"])
        facts = apply.modules.PairFacts(context)
        result = adapter.predict_case(self.case, facts,
            evaluator=apply.ProposalEvaluator(facts, "case-a", self.bind()), store=self.store)
        self.assertEqual(result["decision"], "drop")
        self.assertEqual(result["reason"], "competing_row_not_excluded")
        self.assertIsNone(result["au_row_key"])

    def test_known_conflict_cannot_be_overridden_and_case_cache_is_isolated(self):
        proposals = self.bind()
        color = next(r for r in apply.modules.selected_atoms(self.case, self.facts) if r["type"] == "color")
        row = self.facts.rows[1]
        proposals[("case-a", "row-b", color["requirement_id"])] = [
            {"relation": "support", "target_requirement_semantic": apply.semantic(color)}]
        evaluator = apply.ProposalEvaluator(self.facts, "case-a", proposals)
        self.assertEqual(evaluator.evaluate_cached(color, row, evaluator.au_facts_for(row))["status"], "conflict")
        other = apply.ProposalEvaluator(self.facts, "case-b", proposals)
        req = next(r for r in apply.modules.selected_atoms(self.case, self.facts) if r.get("component") == "word:バー")
        self.assertEqual(other.evaluate_cached(req, self.facts.rows[0], other.au_facts_for(self.facts.rows[0]))["status"], "unknown")

    def test_disagreeing_model_quotes_remain_unknown(self):
        proposals = self.bind()
        key = ("case-a", "row-a", self.req["requirement_id"])
        proposals[key].append({**proposals[key][0], "relation": "conflict", "task_id": "second-quote"})
        evaluator = apply.ProposalEvaluator(self.facts, "case-a", proposals)
        result = adapter.predict_case(self.case, self.facts, evaluator=evaluator, store=self.store)
        self.assertEqual(result["decision"], "drop")
        self.assertIn("disagreeing_quotes_unknown", evaluator.completions.values())

    def test_shared_task_binds_each_target_case_without_requiring_same_case_id(self):
        second = copy.deepcopy(self.case)
        second["case_id"] = "case-b"
        second["rakuten_selected"]["axes"][1].update(value="レッド", value_span=self.span("rak.json", "red"))
        pred = adapter.predict_case(second, self.facts, store=self.store)
        targets = [self.target, {**self.target, "case_id": "case-b", "au_row_key": "row-b"}]
        proposals = self.bind(targets=targets, cases={"case-a": self.case, "case-b": second},
                              baseline={"case-a": self.baseline, "case-b": pred})
        result = adapter.predict_case(second, self.facts,
            evaluator=apply.ProposalEvaluator(self.facts, "case-b", proposals), store=self.store)
        self.assertEqual(result["decision"], "accept")
        self.assertEqual(result["au_row_key"], "row-b")

    def test_page_quote_cannot_complete_condition_varying_between_au_rows(self):
        context = copy.deepcopy(self.context)
        context["au_rows"][0]["axes"].append({"axis_name": "付属品", "value": "バー付き"})
        self.assertFalse(apply.fixed_evidence(self.task, context, self.store))
        task = copy.deepcopy(self.task)
        task["source_scope"]["au_product_id"] = "2"
        self.assertFalse(apply.fixed_evidence(task, self.context, self.store))

    def test_nonfinite_probability_and_forged_threshold_proposal_are_rejected(self):
        for change in ({"probabilities": {"support": float("nan"), "conflict": .02, "unknown": .03}},
                       {"confidence_threshold": .5},
                       {"probabilities": {"support": .8, "conflict": .1, "unknown": .1}, "predicted_confidence": .8},
                       {"token_count_untruncated": 513}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    apply.validated_record(self.task, {**self.record, **change})

    def test_changed_prediction_file_is_rejected_by_manifest(self):
        path = self.root / "predictions.jsonl"
        path.write_text("original\n")
        manifest = {"files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}}
        path.write_text("changed\n")
        with self.assertRaisesRegex(ValueError, "Changed artifact"):
            apply.verify_files(self.root, manifest, {path.name})


if __name__ == "__main__":
    unittest.main()
