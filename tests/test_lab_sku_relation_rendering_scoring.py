"""Contract tests for generic relation rendering and frozen-output scoring.

Every fixture here is invented for the contract. No captured SKU, labels,
model, prediction artifact, or production data is read.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TRAINED = load_module("prepare_trained_relation_probe_contract", "experiments/sku-matching/prepare_trained_relation_probe_v1.py")
NOUN = load_module("prepare_noun_phrase_relation_probe_contract", "experiments/sku-matching/prepare_noun_phrase_relation_probe_v1.py")
SCORER = load_module("score_trained_relation_probe_contract", "experiments/sku-matching/score_trained_relation_probe_v1.py")
QUOTE = load_module("prepare_quote_relation_probe_contract", "experiments/sku-matching/prepare_quote_relation_probe_v1.py")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RelationRenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "artificial-source"
        self.source.mkdir()
        # Composite values and title deliberately exercise raw preservation.
        self.source_request = {
            "id": "invented-request-01", "state": "架空の商品名：サンプル　型（特別）\n原文保持",
            "choices": [{"label": "unknown", "description": "不明"},
                        {"label": "option_0", "description": "黒（つや消し）・限定版"},
                        {"label": "option_1", "description": "白（艶あり）/予備セット"}],
            "provenance": {
                "arm": "natural_structure", "task_id": "invented-task", "case_id": "invented-case",
                "au_row_key": "artificial-row", "fixed_au_product_ref": "fixture://product/1",
                "axis_name": "色・付属品", "selected_value": "黒（つや消し）・限定版",
                "option_values": ["黒（つや消し）・限定版", "白（艶あり）/予備セット"],
                "scope_proven": False,
                "window": {"field_kind": "plain_title", "quote": "架空の商品名：サンプル　型（特別）"},
                "selected_au_row": {"axes": [{"axis_name": "素材", "value": "人工革（試験用）"},
                                                   {"axis_name": "サイズ", "value": "小型 / 2個"}]},
            },
        }
        self._freeze_source()

    def _freeze_source(self):
        requests = self.source / "requests.jsonl"
        requests.write_text(json.dumps(self.source_request, ensure_ascii=False) + "\n", encoding="utf-8")
        (self.source / "manifest.json").write_text(json.dumps({"output_sha256": {"requests.jsonl": digest(requests)}}), encoding="utf-8")

    def rows(self, directory):
        return [json.loads(line) for line in (directory / "requests.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_trained_rendering_keeps_all_raw_alternatives_and_exact_jnli_contract(self):
        output = self.root / "rendered"
        manifest = TRAINED.prepare(self.source, output)
        rows = self.rows(output)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["provenance"]["candidate_option_value"] for r in rows], self.source_request["provenance"]["option_values"])
        expected_choices = [
            {"label": "entailment", "description": "含意：前提から仮説が正しいと必ず言える"},
            {"label": "contradiction", "description": "矛盾：前提から仮説が誤りだと必ず言える"},
            {"label": "neutral", "description": "中立：前提だけでは仮説が正しいとも誤りとも判断できない"},
        ]
        for index, row in enumerate(rows):
            p = row["provenance"]
            value = self.source_request["provenance"]["option_values"][index]
            hypothesis = f"この商品の「色・付属品」は「{value}」です。"
            self.assertEqual(row["choices"], expected_choices)
            self.assertEqual(row["state"], json.dumps({"前提": self.source_request["state"], "仮説": hypothesis}, ensure_ascii=False))
            self.assertEqual(row["premise"], self.source_request["state"])
            self.assertEqual(row["hypothesis"], hypothesis)
            self.assertEqual(p["candidate_option_value"], value)
            self.assertEqual(p["selected_value"], self.source_request["provenance"]["selected_value"])
            self.assertFalse(p["desired_value_injected_into_premise"])
            self.assertEqual(p["source_request_id"], self.source_request["id"])
        self.assertTrue(manifest["all_original_alternatives_retained"])
        self.assertFalse(manifest["labels_read"])

    def test_unknown_label_is_not_rendered_as_raw_alternative(self):
        output = self.root / "rendered"
        TRAINED.prepare(self.source, output)
        rows = self.rows(output)
        self.assertEqual(len(rows), 2)
        self.assertNotIn("不明", [r["provenance"]["candidate_option_value"] for r in rows])

    def test_source_manifest_tamper_and_existing_output_are_refused(self):
        req = self.source / "requests.jsonl"
        req.write_text(req.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source request SHA"):
            TRAINED.prepare(self.source, self.root / "bad")
        occupied = self.root / "occupied"
        occupied.mkdir()
        marker = occupied / "keep.txt"
        marker.write_text("retained")
        with self.assertRaises(FileExistsError):
            TRAINED.prepare(self.source, occupied)
        self.assertEqual(marker.read_text(), "retained")

    def test_noun_phrase_uses_one_title_full_row_and_whole_axis_value(self):
        trained_dir = self.root / "trained"
        TRAINED.prepare(self.source, trained_dir)
        output = self.root / "noun"
        manifest = NOUN.prepare(trained_dir, output)
        rows = self.rows(output)
        self.assertEqual(len(rows), 2)
        for row in rows:
            p = row["provenance"]
            title = self.source_request["provenance"]["window"]["quote"]
            self.assertEqual(row["premise"].count(title), 1)
            self.assertIn("選択された素材は「人工革（試験用）」です。", row["premise"])
            self.assertIn("選択されたサイズは「小型 / 2個」です。", row["premise"])
            self.assertEqual(row["hypothesis"], f"この商品は色・付属品{p['candidate_option_value']}の商品です。")
            self.assertEqual(row["state"], json.dumps({"前提": row["premise"], "仮説": row["hypothesis"]}, ensure_ascii=False))
            self.assertEqual(p["hypothesis_style"], "noun_phrase")
            self.assertTrue(p["single_title_occurrence"])
        self.assertFalse(manifest["domain_rules"])
        self.assertFalse(manifest["production_eligible"])


class RelationScoringTests(unittest.TestCase):
    def record(self, value, probs, ident=None):
        return {"id": ident or value, "probabilities": probs,
                "provenance": {"case_id": "case-x", "au_row_key": "row-x", "axis_name": "軸（複合）",
                               "selected_value": "選択値（全部）", "candidate_option_value": value,
                               "hypothesis_style": "axis", "task_id": "task-x",
                               "fixed_au_product_ref": "fixture://fixed/1"}}

    def test_aggregation_requires_selected_support_and_conflict_or_other_support_yields_unknown(self):
        support = {"support": 0.95, "conflict": 0.02, "unknown": 0.03}
        conflict = {"support": 0.02, "conflict": 0.95, "unknown": 0.03}
        unknown = {"support": 0.05, "conflict": 0.05, "unknown": 0.90}
        selected = "選択値（全部）"
        other = "別値（全体）"
        cases = [
            ([self.record(selected, support), self.record(other, unknown)], "support"),
            ([self.record(selected, support), self.record(selected, conflict)], "unknown"),
            ([self.record(selected, support), self.record(other, support)], "unknown"),
            ([self.record(selected, conflict), self.record(other, support)], "conflict"),
            ([self.record(selected, unknown), self.record(other, support)], "unknown"),
        ]
        for records, expected in cases:
            with self.subTest(expected=expected, records=len(records)):
                self.assertEqual(SCORER.aggregate(records, 0.8)[0]["relation"], expected)

    def test_alternative_support_alone_never_creates_selected_conflict(self):
        support = {"support": 0.95, "conflict": 0.02, "unknown": 0.03}
        records = [self.record("選択値（全部）", {"support": 0.01, "conflict": 0.01, "unknown": 0.98}),
                   self.record("別値（全体）", support)]
        result = SCORER.aggregate(records, 0.8)[0]
        self.assertEqual(result["relation"], "unknown")
        self.assertEqual(result["conflict_evidence_ids"], [])
        self.assertEqual(result["other_supported_evidence_ids"], ["別値（全体）"])

    def test_support_precision_is_undefined_when_predictions_are_all_unknown(self):
        rows = [{"case_id": "case-x", "au_row_key": "row-x", "axis_name": "軸（複合）",
                 "selected_value": "選択値（全部）", "relation": "unknown"}]
        key = ("case-x", "row-x", "軸（複合）", "選択値（全部）")
        metrics = SCORER.metrics(rows, {key: {"relation": "unknown"}})
        self.assertIsNone(metrics["support_precision"])
        self.assertFalse(metrics["support_precision_defined"])

    def _write_frozen_run(self, run, alter_metadata=False):
        run.mkdir()
        (run / "code").mkdir()
        req = {"id": "request-x", "question": "fixture question", "state": "fixture state",
               "choices": [{"label": "support"}], "provenance": self.record("選択値（全部）", {}).get("provenance")}
        prediction = {**deepcopy(req), "probabilities": {"support": 0.9, "conflict": 0.05, "unknown": 0.05}}
        if alter_metadata:
            prediction["provenance"]["selected_value"] = "tampered value"
        (run / "requests.jsonl").write_text(json.dumps(req, ensure_ascii=False) + "\n", encoding="utf-8")
        (run / "predictions.jsonl").write_text(json.dumps(prediction, ensure_ascii=False) + "\n", encoding="utf-8")
        code = run / "code" / "scorer.py"
        code.write_text("fixture source")
        (run / "summary.json").write_text(json.dumps({"output_sha256": {
            "requests.jsonl": digest(run / "requests.jsonl"), "predictions.jsonl": digest(run / "predictions.jsonl")},
            "primary_threshold": 0.9, "thresholds_for_diagnostics": [0.9]}), encoding="utf-8")
        (run / "freeze.json").write_text(json.dumps({"code_sha256": {"scorer.py": digest(code)}}), encoding="utf-8")

    def test_prediction_and_request_binding_tamper_fails_before_annotation_read(self):
        for tamper in ("prediction_hash", "metadata"):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                run = root / "run"
                self._write_frozen_run(run, alter_metadata=(tamper == "metadata"))
                if tamper == "prediction_hash":
                    with (run / "predictions.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(" ")
                # Invalid JSON proves scoring rejects the frozen output first.
                labels = root / "labels.jsonl"
                labels.write_text("not JSON", encoding="utf-8")
                with self.assertRaises(ValueError):
                    SCORER.score(run, [labels], root / "scores.json")
                self.assertFalse((root / "scores.json").exists())


class QuoteRelationRenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        base = self.root / "artificial-source"
        base.mkdir()
        row = {
            "id": "invented-quote-source", "state": "artificial original premise",
            "premise": "artificial original premise", "hypothesis": "artificial original hypothesis",
            "choices": [{"label": "option_0", "description": "青（限定）・大容量"}],
            "provenance": {"arm": "natural_structure", "case_id": "invented", "task_id": "invented-task",
                "axis_name": "色・容量（複合）", "option_values": ["青（限定）・大容量"], "scope_proven": False,
                "candidate_option_value": "青（限定）・大容量", "selected_value": "青（限定）・大容量",
                "window": {"field_kind": "plain_title", "quote": "架空の引用文：材質は合成繊維です。"},
                "selected_au_row": {"axes": [{"axis_name": "本体名", "value": "架空タイトル"}]},
                "fixed_au_product_ref": "fixture://never-real"},
        }
        requests = base / "requests.jsonl"
        requests.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        (base / "manifest.json").write_text(json.dumps({"output_sha256": {"requests.jsonl": digest(requests)}}), encoding="utf-8")
        self.source = self.root / "relation-source"
        TRAINED.prepare(base, self.source)

    def test_quote_hypothesis_keeps_compound_axis_value_and_uses_only_literal_quote(self):
        output = self.root / "quoted"
        QUOTE.prepare(self.source, output)
        rows = [json.loads(line) for line in (output / "requests.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        quote = "架空の引用文：材質は合成繊維です。"
        self.assertEqual(row["premise"], quote)
        self.assertEqual(row["hypothesis"], "色・容量（複合）は青（限定）・大容量です。")
        self.assertNotIn("架空タイトル", row["premise"])
        self.assertNotIn("本体名", row["premise"])
        self.assertEqual(row["state"], json.dumps({"前提": quote, "仮説": row["hypothesis"]}, ensure_ascii=False))
        self.assertEqual(row["provenance"]["relation_task_scope"], "quoted_text_only_not_fixed_row_applicability")
        self.assertFalse(row["provenance"]["scope_proven"])

    def test_whitespace_only_quote_is_retained_as_unknown_without_inference(self):
        request_path = self.source / "requests.jsonl"
        source_rows = [json.loads(line) for line in request_path.read_text(encoding="utf-8").splitlines()]
        source_rows[0]["provenance"]["window"]["quote"] = " \t\n　 "
        request_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in source_rows) + "\n", encoding="utf-8")
        manifest_path = self.source / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["output_sha256"]["requests.jsonl"] = digest(request_path)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        output = self.root / "empty-quote"
        result = QUOTE.prepare(self.source, output)
        self.assertEqual(result["request_count"], 0)
        self.assertEqual(result["empty_literal_quote_request_count"], 1)
        empty = [json.loads(line) for line in (output / "empty-quote-requests.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(empty), 1)
        self.assertEqual(empty[0]["provenance"]["window"]["quote"], " \t\n　 ")
        self.assertEqual(empty[0]["relation"], "unknown")
        self.assertEqual(empty[0]["not_inferred_reason"], "literal_quote_contains_only_whitespace")

    def test_source_hash_tamper_and_existing_output_are_refused(self):
        requests = self.source / "requests.jsonl"
        requests.write_text(requests.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "input SHA mismatch"):
            QUOTE.prepare(self.source, self.root / "tampered")
        occupied = self.root / "occupied"
        occupied.mkdir()
        marker = occupied / "keep.txt"
        marker.write_text("preserve")
        with self.assertRaises(FileExistsError):
            QUOTE.prepare(self.source, occupied)
        self.assertEqual(marker.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
