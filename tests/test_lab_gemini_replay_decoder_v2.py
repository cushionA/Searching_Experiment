import copy
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(EXP))
SPEC = importlib.util.spec_from_file_location(
    "gemini_replay_decoder_v2_under_test", EXP / "trial_gemini_luna50_replay_v2.py"
)
decoder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(decoder)


class CompatibleSchemaTests(unittest.TestCase):
    def test_removes_input_dependent_constraints_without_mutating_or_weakening_status(self):
        schema = {
            "type": "OBJECT", "minItems": 1, "maxItems": 50,
            "properties": {
                "status": {"type": "STRING", "enum": ["support", "contradiction", "unknown"]},
                "source_ids": {
                    "type": "ARRAY", "minItems": 0, "maxItems": 12,
                    "items": {"type": "STRING", "enum": ["S001", "S002"]},
                },
                "note": {"type": "STRING", "enum": ["商品A", "商品B"]},
            },
            "required": ["status", "source_ids"],
        }
        original = copy.deepcopy(schema)

        adapted = decoder.compatible_schema(schema)

        self.assertEqual(schema, original)
        self.assertIsNot(adapted, schema)
        self.assertIsNot(adapted["properties"], schema["properties"])
        self.assertNotIn("minItems", adapted)
        self.assertNotIn("maxItems", adapted)
        self.assertEqual(adapted["properties"]["status"]["enum"], ["support", "contradiction", "unknown"])
        self.assertEqual(adapted["properties"]["status"]["type"], "STRING")
        self.assertEqual(adapted["properties"]["source_ids"]["items"], {"type": "STRING"})
        self.assertEqual(adapted["properties"]["note"], {"type": "STRING"})
        self.assertEqual(adapted["properties"].keys(), schema["properties"].keys())
        self.assertEqual(adapted["required"], ["status", "source_ids"])


class AdaptRequestTests(unittest.TestCase):
    def test_only_response_schema_changes_and_manifest_hashes_are_rebound(self):
        schema = {
            "type": "OBJECT",
            "properties": {
                "status": {"type": "STRING", "enum": ["support", "contradiction", "unknown"]},
                "source_ids": {"type": "ARRAY", "maxItems": 4,
                               "items": {"type": "STRING", "enum": ["S-17", "S-18"]}},
            },
            "required": ["status", "source_ids"],
        }
        # Deliberately include selected values and non-ASCII prompt text; these
        # bytes represent the frozen task content that adaptation must preserve.
        selected_prompt = "選択商品: 青 / 幅 120 cm / 2枚組。本文の選択値をそのまま使う。"
        original = {
            "metadata": {"case_id": "fixture-case", "selection": ["青", "120 cm", "2枚組"]},
            "body": {
                "contents": [{"role": "user", "parts": [{"text": selected_prompt}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                     "responseSchema": schema},
            },
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            request_path = root / "requests/fixture.json"
            request_path.parent.mkdir(parents=True)
            decoder.stages.core.save(request_path, original)
            before_bytes = request_path.read_bytes()
            before = decoder.base.read(request_path)
            expected_schema = decoder.compatible_schema(schema)
            entry = {"request": "requests/fixture.json", "request_sha256": "stale", "case_id": "fixture-case"}

            decoder.adapt_request(root, entry)

            after_bytes = request_path.read_bytes()
            after = decoder.base.read(request_path)
            self.assertEqual(after["body"]["contents"][0]["parts"][0]["text"].encode("utf-8"),
                             selected_prompt.encode("utf-8"))
            self.assertEqual(after["metadata"], before["metadata"])
            self.assertEqual(after["body"]["contents"], before["body"]["contents"])
            self.assertEqual(after["body"]["generationConfig"]["temperature"],
                             before["body"]["generationConfig"]["temperature"])
            self.assertEqual(after["body"]["generationConfig"]["responseMimeType"],
                             before["body"]["generationConfig"]["responseMimeType"])
            self.assertEqual(after["body"]["generationConfig"]["responseSchema"], expected_schema)
            self.assertEqual(entry["original_request_sha256"], hashlib.sha256(before_bytes).hexdigest())
            self.assertEqual(entry["request_sha256"], decoder.base.sha(request_path))
            self.assertNotEqual(entry["request_sha256"], entry["original_request_sha256"])
            self.assertEqual(entry["request_body_sha256"], decoder.stages._body_sha(after_bytes))
            self.assertEqual(entry["decoder_adapter"], "input-dependent decoder constraints validated locally")
            self.assertEqual(set(entry), {"request", "request_sha256", "case_id", "original_request_sha256",
                                          "request_body_sha256", "decoder_adapter"})


if __name__ == "__main__":
    unittest.main()
