"""Synthetic input-contract checks for compound product choice prompts."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compound_product_choices_v1 as C
import test_lab_sku_product_condition_models as FIXTURE
import product_condition_sharing_v1 as SHARING


class CompoundProductChoices(unittest.TestCase):
    def setUp(self):
        data, contract = FIXTURE.fixtures()
        self.data = data
        self.plan = FIXTURE.plan_for(data, contract)
        self.groups = self.plan["groups"]
        self.blocks = {block["block_id"]: block for block in data[4]}
        self.rankings = {self.groups[0]["group_id"]: {
            "ordered_block_ids": list(self.blocks)}}

    def test_all_raw_options_keep_literal_labels_order_and_single_unknown(self):
        requests = C.make_classification_requests(self.groups, self.blocks, self.rankings)
        self.assertEqual(len(requests), 3)
        for request in requests:
            self.assertEqual([choice["label"] for choice in request["choices"]],
                             self.groups[0]["option_values"] + [SHARING.UNKNOWN])
            self.assertEqual(request["all_original_option_values"], self.groups[0]["option_values"])
            self.assertIn("楽天側", request["question"])
            self.assertIn("実際の仕様・セット内容", request["question"])
            self.assertIn("価格", request["question"])

    def test_description_is_compound_and_wanted_row_residue_never_enters_inputs(self):
        requests = C.make_classification_requests(self.groups, self.blocks, self.rankings)
        for request in requests:
            for value, choice in zip(self.groups[0]["option_values"], request["choices"], strict=False):
                self.assertEqual(choice["label"], value)
                self.assertIn("実際の仕様・セット内容", choice["description"])
                self.assertIn(self.groups[0]["axis_name"] + "：" + value, choice["description"])
            self.assertIn("特定できない", request["choices"][-1]["description"])
            self.assertNotIn("色：青", request["state"])
            self.assertNotIn("色：赤", request["state"])
            self.assertNotIn("選択行", request["state"])
            self.assertEqual(request["state"], C.PRODUCT.state_for(self.groups[0], self.blocks[request["block_id"]]))

    def test_price_like_raw_values_are_preserved_without_semantic_rewriting(self):
        group = deepcopy(self.groups[0])
        values = ["単品", "+780円"]
        group["option_values"] = values
        group["model_request"]["choices"] = [{"label": value, "description": "source"} for value in values]
        group["model_request"]["choices"].append({"label": SHARING.UNKNOWN, "description": "source"})
        requests = C.make_classification_requests([group], self.blocks, self.rankings)
        self.assertEqual([item["label"] for item in requests[0]["choices"]], values + [SHARING.UNKNOWN])
        self.assertIn(group["axis_name"] + "：+780円", requests[0]["choices"][1]["description"])

    def test_unknown_label_collision_fails_closed(self):
        group = deepcopy(self.groups[0])
        group["option_values"][0] = SHARING.UNKNOWN
        group["model_request"]["choices"][0]["label"] = SHARING.UNKNOWN
        with self.assertRaisesRegex(ValueError, "collides"):
            C.make_classification_requests([group], self.blocks, self.rankings)


if __name__ == "__main__":
    unittest.main()
