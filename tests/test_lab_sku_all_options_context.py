"""Contract checks for exposing the full literal option set per candidate."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import all_options_context_v1 as ALL_OPTIONS
import compound_product_choices_v1 as COMPOUND
import test_lab_sku_product_condition_models as FIXTURE
import product_condition_sharing_v1 as SHARING


class AllOptionsContext(unittest.TestCase):
    def setUp(self):
        data, contract = FIXTURE.fixtures()
        self.plan = FIXTURE.plan_for(data, contract)
        self.groups = self.plan["groups"]
        self.blocks = {block["block_id"]: block for block in data[4]}
        self.rankings = {self.groups[0]["group_id"]: {
            "ordered_block_ids": list(self.blocks)}}

    def test_each_candidate_question_contains_complete_ordered_raw_options(self):
        compound = COMPOUND.make_classification_requests(self.groups, self.blocks, self.rankings)
        requests = ALL_OPTIONS.make_classification_requests(self.groups, self.blocks, self.rankings)
        self.assertEqual(len(requests), len(compound))
        for request, original in zip(requests, compound, strict=True):
            encoded = request["question"].split("元の選択肢（順序保持JSON）：", 1)[1]
            self.assertEqual(json.loads(encoded), self.groups[0]["option_values"] + [SHARING.UNKNOWN])
            self.assertIn("実際の仕様・セット内容", request["question"])
            self.assertIn("「" + self.groups[0]["axis_name"] + "」", request["question"])
            self.assertIn("特定できなければ不明", request["question"])
            self.assertNotIn("色：青", request["question"])
            self.assertNotIn("色：赤", request["question"])
            self.assertEqual(request["state"], original["state"])
            self.assertEqual(request["choices"], original["choices"])
            for key in ("id", "group_id", "group_sha256", "block_id", "block_rank",
                        "unknown_control_label", "all_original_option_values"):
                self.assertEqual(request[key], original[key])

    def test_json_preserves_tricky_literal_raw_strings_without_rewriting(self):
        group = deepcopy(self.groups[0])
        values = ['色:"紺"\n限定', "数量 -2個", "12", "+780円", "セット：本体のみ"]
        group["option_values"] = values
        group["model_request"]["choices"] = [
            {"label": value, "description": "source"} for value in values
        ] + [{"label": SHARING.UNKNOWN, "description": "source"}]
        request = ALL_OPTIONS.make_classification_requests([group], self.blocks, self.rankings)[0]
        encoded = request["question"].split("元の選択肢（順序保持JSON）：", 1)[1]
        self.assertEqual(json.loads(encoded), values + [SHARING.UNKNOWN])
        self.assertIn("数量 -2個", encoded)
        self.assertIn('"12"', encoded)
        self.assertIn("+780円", encoded)
        self.assertEqual([choice["label"] for choice in request["choices"]], values + [SHARING.UNKNOWN])
        self.assertIn(group["axis_name"] + "：数量 -2個", request["choices"][1]["description"])

    def test_duplicate_or_reserved_unknown_raw_option_fails_closed(self):
        group = deepcopy(self.groups[0])
        group["option_values"] = ["同じ", "同じ"]
        group["model_request"]["choices"] = [
            {"label": value, "description": "source"} for value in group["option_values"]
        ] + [{"label": SHARING.UNKNOWN, "description": "source"}]
        with self.assertRaisesRegex(ValueError, "ordered raw source option"):
            ALL_OPTIONS.make_classification_requests([group], self.blocks, self.rankings)

        group = deepcopy(self.groups[0])
        group["option_values"][0] = SHARING.UNKNOWN
        group["model_request"]["choices"][0]["label"] = SHARING.UNKNOWN
        with self.assertRaisesRegex(ValueError, "collides"):
            ALL_OPTIONS.make_classification_requests([group], self.blocks, self.rankings)


if __name__ == "__main__":
    unittest.main()
