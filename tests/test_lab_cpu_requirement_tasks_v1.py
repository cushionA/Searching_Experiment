import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
import prepare_cpu_requirement_tasks_v1 as exporter
import sku_integrated_gate_v1 as gate


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CpuRequirementTasksV1Tests(unittest.TestCase):
    def test_component_hypotheses_use_generic_canonical_nouns(self):
        modules = gate.load_gate()
        examples = {"lace": "レースカーテン", "drape": "カーテン", "hook": "フック"}
        for component, noun in examples.items():
            with self.subTest(component=component):
                self.assertEqual(exporter.noun_for({"component": component}, modules), noun)
                self.assertEqual(exporter.hypothesis({"type": "component_presence", "component": component,
                                                      "value": True}, modules), f"この商品には{noun}が付いている。")
        self.assertEqual(exporter.noun_for({"component": "word:ミラーレース"}, modules), "ミラーレース")

    def test_literal_spans_keep_negation_and_long_context_but_row_variant_nouns_are_guarded(self):
        modules = gate.load_gate()
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            raw = Path(temp) / "au.json"
            title = "専用バー付き収納ラック"
            description = "バーなし。" + ("補足" * 65) + "バー付きの場合は別売オプションです。"
            write_json(raw, {"itemInfo": {"itemTitle": title, "extraItemComment": description}})
            rel = str(raw.relative_to(ROOT))
            store = modules.src.RawStore(ROOT)
            context = {"au_product": {"product_id": "42", "title": title,
                                       "title_span": modules.src.json_leaf_span(store, rel, "$.itemInfo.itemTitle")},
                       "au_description_lines": [
                           {"text": description, "scope_tag": "product_page_extra_comment",
                            "span": modules.src.json_leaf_span(store, rel, "$.itemInfo.extraItemComment")}]}
            req = {"type": "component_presence", "component": "word:バー", "value": True}
            rows = exporter.evidence_candidates(context, req, modules, store)
            self.assertEqual([row["quote"] for row in rows], [title, description])
            self.assertEqual(rows[0]["source_scope"]["kind"], "fixed_au_product")
            self.assertEqual(rows[0]["source_scope"]["scope_tag"], "fixed_au_title")
            self.assertTrue(modules.src.verify_span(store, rows[0]["span"]))
            variant_context = {**context, "au_rows": [{"axes": [{"value": "バーなし"}]}]}
            self.assertEqual(exporter.evidence_candidates(variant_context, req, modules, store), [])
            axis_name_context = {**context, "au_rows": [{"axes": [{"axis_name": "バー種類", "value": "白"}]}]}
            self.assertEqual(exporter.evidence_candidates(axis_name_context, req, modules, store), [])

    def test_input_hashes_must_match_sourcegate_freeze_and_prediction_manifest(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            root = Path(temp)
            inputs = root / "inputs"
            gate_run = root / "gate"
            inputs.mkdir()
            gate_run.mkdir()
            (inputs / "cases.jsonl").write_text("{}\n", encoding="utf-8")
            (inputs / "products.jsonl").write_text("{}\n", encoding="utf-8")
            input_manifest = {"synthetic": False, "labels_read": False, "fixed_pair_mapping": True,
                              "output_sha256": {"inputs/cases.jsonl": digest(inputs / "cases.jsonl"),
                                                "inputs/products.jsonl": digest(inputs / "products.jsonl")}}
            write_json(inputs / "manifest.json", input_manifest)
            hashes = {n: digest(inputs / n) for n in ("cases.jsonl", "products.jsonl", "manifest.json")}
            write_json(gate_run / "predictions.jsonl", {"run": 1})
            write_json(gate_run / "freeze.json", {"input_sha256": hashes, "code_sha256": {"source.py": "1" * 64}})
            write_json(gate_run / "manifest.json", {"files": {"predictions.jsonl": digest(gate_run / "predictions.jsonl"),
                                                      "freeze.json": digest(gate_run / "freeze.json")}})
            exporter.validate_provenance(inputs, gate_run)
            (inputs / "cases.jsonl").write_text("{\"tampered\":true}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frozen against these inputs"):
                exporter.validate_provenance(inputs, gate_run)


if __name__ == "__main__":
    unittest.main()
