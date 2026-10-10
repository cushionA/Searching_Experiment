from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "sku-matching"
sys.path.insert(0, str(EXPERIMENT))
import trial_luna_sku_matching as core
import sku_luna_output_shape as shape


def make_case(au_counts=(1, 1)):
    return {
        "case_id": "shape-synthetic",
        "rakuten_conditions": [{"condition_id": f"R{i}", "axis": f"R軸{i}", "value": f"R値{i}",
                                "choices": ["秘密の候補"]} for i in range(3)],
        "selected_attributes": [{"axis": "単位", "value": 25, "unit": "cm"}],
        "au_rows": [{"row_key": f"row-{i}", "sku_id": f"private-{i}", "row_index": i,
                     "column_index": i,
                     "conditions": [{"condition_id": f"A{i}-{j}", "axis": f"AU軸{j}", "value": f"AU値{j}",
                                     "source": {"quote": f"AU値{j}"}} for j in range(count)]}
                    for i, count in enumerate(au_counts)],
        "sources": [{"source_id": "S0", "kind": "title", "text": "固定商品", "scope": "fixed_product",
                     "source": {"quote": "固定商品"}}],
    }


def standard_schema(schema):
    type_map = {"OBJECT": "object", "ARRAY": "array", "STRING": "string"}
    out = {}
    for key, value in schema.items():
        if key == "type":
            out[key] = type_map.get(value, value)
        elif key in {"properties", "items"}:
            if key == "properties":
                out[key] = {k: standard_schema(v) for k, v in value.items()}
            else:
                out[key] = standard_schema(value)
        else:
            out[key] = value
    return out


class LunaOutputShapeTests(unittest.TestCase):
    def test_rakuten_three_au_one_is_rejected_by_schema_and_smoke_validation(self):
        from jsonschema import ValidationError, validate

        case = make_case((1,))
        req = shape.output_shape_request(case, {})
        schema = standard_schema(req["body"]["generationConfig"]["responseSchema"])
        valid_answer = {"rows": [{"rakuten_checks": [{"status": "unknown", "source_ids": []} for _ in range(3)],
                                   "au_checks": [{"status": "unknown", "source_ids": []}]}]}
        validate(valid_answer, schema)
        malformed = {"rows": [{"rakuten_checks": [{"status": "unknown", "source_ids": []} for _ in range(3)],
                                "au_checks": [{"status": "unknown", "source_ids": []} for _ in range(3)]}]}
        with self.assertRaises(ValidationError):
            validate(malformed, schema)
        self.assertEqual(core.smoke.validate(case, malformed), (False, "condition_coverage_error:row-0:au_checks"))
        prompt = req["body"]["contents"][0]["parts"][0]["text"]
        self.assertLess(prompt.index("OUTPUT_SHAPE="), prompt.index("INPUT="))
        self.assertIn('[{"row_index":0,"rakuten_checks":3,"au_checks":1}]', prompt)

    def test_variable_au_counts_are_prompted_per_row_in_input_order(self):
        case = make_case((1, 2, 1))
        req = shape.output_shape_request(case, {})
        props = req["body"]["generationConfig"]["responseSchema"]["properties"]["rows"]["items"]["properties"]
        self.assertEqual(props["rakuten_checks"]["minItems"], 3)
        self.assertEqual(props["rakuten_checks"]["maxItems"], 3)
        self.assertNotIn("minItems", props["au_checks"])
        self.assertNotIn("maxItems", props["au_checks"])
        prompt = req["body"]["contents"][0]["parts"][0]["text"]
        self.assertIn('[{"row_index":0,"rakuten_checks":3,"au_checks":1},{"row_index":1,"rakuten_checks":3,"au_checks":2},{"row_index":2,"rakuten_checks":3,"au_checks":1}]', prompt)
        self.assertIn("入力AU行の順序を保つ", prompt)

    def test_shape_wrapper_preserves_payload_values_units_and_check_fields(self):
        case = make_case((1,))
        req = shape.output_shape_request(case, {})
        prompt = req["body"]["contents"][0]["parts"][0]["text"]
        self.assertIn('"unit":"cm"', prompt)
        self.assertIn('"value":25', prompt)
        self.assertNotIn("choices", prompt)
        self.assertNotIn('"sku_id"', prompt)
        check_props = req["body"]["generationConfig"]["responseSchema"]["properties"]["rows"]["items"]["properties"]["au_checks"]["items"]["properties"]
        self.assertEqual(set(check_props), {"status", "source_ids"})

    def test_prepare_round_updates_hash_and_v4_manifest_without_touching_v3(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "empty.zip"
            with zipfile.ZipFile(archive, "w"):
                pass
            cases = [make_case((1,))]
            round_dir = root / "v4"
            manifest = shape.prepare_shape_round(round_dir, cases, {"input": "synthetic"}, archive,
                                                 selection={"cohort": "synthetic"})
            self.assertEqual(manifest["mode"], "scoped-v3")
            self.assertEqual(manifest["task_version"], shape.VERSION_V4)
            self.assertEqual(manifest["shape_policy"], shape.SHAPE_POLICY)
            self.assertEqual(core.integrity(round_dir), manifest)
            req = core.read(round_dir / manifest["entries"][0]["request"])
            self.assertIn("OUTPUT_SHAPE=", req["body"]["contents"][0]["parts"][0]["text"])

            v3_dir = root / "v3"
            original = core.prepare(v3_dir, cases, {}, archive, mode="scoped-v3")
            self.assertEqual(original["task_version"], core.VERSION_V3)
            req_v3 = core.read(v3_dir / original["entries"][0]["request"])
            self.assertNotIn("OUTPUT_SHAPE=", req_v3["body"]["contents"][0]["parts"][0]["text"])


if __name__ == "__main__":
    unittest.main()
