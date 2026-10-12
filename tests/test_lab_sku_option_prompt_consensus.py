"""Synthetic checks for label-free group-level prompt consensus."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))

import option_prompt_consensus_v1 as CONSENSUS
import product_condition_sharing_v1 as SHARING


class OptionPromptConsensus(unittest.TestCase):
    def setUp(self):
        self.group = {"group_id": "group:synthetic", "group_sha256": "abc123",
                      "option_values": ["単品", "2個セット", "+780円"]}

    @staticmethod
    def component(name, status="ok", winner="単品", blocks=None, refs=None, block_winners=None):
        return {"component_name": name, "status": status, "winning_value": winner,
                "supporting_block_ids": list(blocks or []), "quote_refs": list(refs or []),
                "block_winners": dict(block_winners or {}), "block_votes": {}}

    def test_each_variant_is_one_vote_even_if_one_has_many_blocks(self):
        components = [
            self.component("original", blocks=["b1", "b2", "b3"],
                           block_winners={"b1": "単品", "b2": "単品", "b3": "単品"}),
            self.component("compound", blocks=["b1", "b2"]),
            self.component("all-options", winner="2個セット", blocks=["b1", "b2", "b3"]),
        ]
        result = CONSENSUS.combine(self.group, components)
        self.assertEqual(result["winning_value"], "単品")
        self.assertEqual(result["vote_counts"], {"単品": 2, "2個セット": 1, "+780円": 0})
        self.assertEqual([item["component_name"] for item in result["component_outcomes"]],
                         ["original", "compound", "all-options"])

    def test_one_vote_or_three_different_votes_abstains(self):
        one_vote = [self.component("a"), self.component("b", "unknown", None),
                    self.component("c", "error", None)]
        self.assertEqual(CONSENSUS.combine(self.group, one_vote)["status"], "unknown")
        three_values = [self.component("a", winner="単品"),
                        self.component("b", winner="2個セット"),
                        self.component("c", winner="+780円")]
        result = CONSENSUS.combine(self.group, three_values)
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["winning_value"])

    def test_unknown_error_and_tie_are_abstentions(self):
        components = [self.component("a", winner=SHARING.UNKNOWN),
                      self.component("b", "error", None),
                      self.component("c", "tie", None)]
        result = CONSENSUS.combine(self.group, components)
        self.assertEqual(result["vote_counts"], {"単品": 0, "2個セット": 0, "+780円": 0})
        self.assertEqual(result["status"], "unknown")
        components = [self.component("a"), self.component("b"), self.component("c", "unknown", None)]
        components[0]["tie"] = True
        components[1]["error_type"] = "inference_error"
        result = CONSENSUS.combine(self.group, components)
        self.assertEqual(sum(result["vote_counts"].values()), 0)
        self.assertEqual(result["status"], "unknown")

    def test_nonoriginal_winner_and_repeated_component_names_reject(self):
        with self.assertRaisesRegex(ValueError, "not an original raw option"):
            CONSENSUS.combine(self.group, [self.component("a", winner="青"),
                                           self.component("b"), self.component("c")])
        with self.assertRaisesRegex(ValueError, "distinct"):
            CONSENSUS.combine(self.group, [self.component("a"), self.component("a"),
                                           self.component("c")])

    def test_only_consensus_support_quotes_and_blocks_are_unioned_and_inputs_unchanged(self):
        supported_ref = {"field": "description", "quote": "本体1点"}
        dissent_ref = {"field": "description", "quote": "青色"}
        components = [self.component("a", blocks=["b1"], refs=[supported_ref]),
                      self.component("b", blocks=["b2"], refs=[supported_ref]),
                      self.component("c", winner="2個セット", blocks=["b3"], refs=[dissent_ref])]
        before = deepcopy(components)
        group_before = deepcopy(self.group)
        result = CONSENSUS.combine(self.group, components)
        self.assertEqual(result["quote_refs"], [supported_ref])
        self.assertEqual(result["supporting_block_ids"], ["b1", "b2"])
        self.assertEqual(components, before)
        self.assertEqual(self.group, group_before)


if __name__ == "__main__":
    unittest.main()
