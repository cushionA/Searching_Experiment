"""The table steps of the SKU condition alignment, without a model."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/sku-matching"))
import align_sku_conditions as al  # noqa: E402


def entry(rakuten, au, forward, reverse):
    return {"rakuten": rakuten, "au": au, "forward": forward, "reverse": reverse}


RAKUTEN = [["size", "サイズ", "100×80cm"], ["colour", "カラー", "グレー"], ["colour", "カラー", "ブラック"],
           ["colour", "カラー", "ネイビー"], ["set", "セット数", "10個"]]
AU = [["サイズ", "幅100×丈80cm(2枚)"], ["カラー", "グレイ"], ["カラー", "ブラック"], ["カラー", "ホワイト"]]
# forward: Rakuten x AU, reverse: AU x Rakuten. ブラック leans on グレイ but is identical to ブラック;
# ネイビー leans on グレイ but loses it to グレー; 10個 and ホワイト are each other's best match.
FORWARD = [[9, 0, 0, 0], [0, 6, 3, 2], [0, 5, 1, 0], [0, 4, 3, 1], [0, 1, 1, 3]]
REVERSE = [[9, 0, 0, 0, 0], [0, 6, 2, 5, 0], [0, 5, 1, 0, 0], [0, 2, 1, 1, 3]]


class TableTests(unittest.TestCase):
    def setUp(self):
        self.table = al.value_pairs(entry(RAKUTEN, AU, FORWARD, REVERSE))

    def test_identical_strings_rank_first_and_pairs_are_mutual(self):
        got = {(r.value, r.au_value): (r.paired, r.matched_by) for r in self.table.itertuples()}
        self.assertEqual(got[("ブラック", "ブラック")], (True, "identical"))
        self.assertEqual(got[("グレー", "グレイ")], (True, "model"))
        self.assertEqual(got[("10個", "ホワイト")], (True, "model"))
        self.assertFalse(got[("ネイビー", "グレイ")][0])

    def test_axes_join_only_through_identical_values(self):
        # The model pairs 10個 with ホワイト; that link alone does not make the set axis symmetric.
        self.assertEqual(al.axis_pairs(self.table, AU), ({"colour": "カラー"}, []))
        # Two Rakuten axes reaching one AU axis are not paired.
        t = al.value_pairs(entry([["a", "カラー", "グレー"], ["b", "タイプ", "ブラック"]],
                                 [["カラー", "グレー"], ["カラー", "ブラック"]], [[0, 0], [0, 0]], [[0, 0], [0, 0]]))
        self.assertEqual(al.axis_pairs(t, [["カラー", "グレー"], ["カラー", "ブラック"]])[0], {})

    def test_rows_and_decision(self):
        context = {"au_rows": [{"row_key": f"r{i}", "axes": [{"axis_name": "サイズ", "value": "幅100×丈80cm(2枚)"},
                                                             {"axis_name": "カラー", "value": c}]}
                               for i, c in enumerate(["ブラック", "グレイ", "ホワイト"])]}
        symmetric, au_only = al.axis_pairs(self.table, AU)

        def run(*values):
            case = {"rakuten_selected": {"axes": [{"axis_key": k, "axis_label": label, "value": v}
                                                  for k, label, v in values]}}
            states = al.row_states(al.conditions(case, context, self.table, symmetric, au_only))
            return states.to_dict(), al.decide(states)

        black, grey = ("colour", "カラー", "ブラック"), ("colour", "カラー", "グレー")
        self.assertEqual(run(black)[1], ("matched", "r0", "all_conditions_aligned_on_one_row"))
        # A model pair is only a candidate: its row goes to the description check, the others are excluded.
        self.assertEqual(run(grey)[0], {"r0": "contradiction", "r1": "pending_description_check", "r2": "contradiction"})
        # Conditions on one-sided axes also go to the description check.
        self.assertEqual(run(black, ("size", "サイズ", "100×80cm"))[0]["r0"], "pending_description_check")
        self.assertEqual(run(black, ("set", "セット数", "10個"))[0]["r0"], "pending_description_check")
        # A value of a symmetric axis with no AU counterpart is excluded, not handed off.
        self.assertEqual(run(("colour", "カラー", "ネイビー"))[1][2], "symmetric_condition_unresolved")


if __name__ == "__main__":
    unittest.main()
