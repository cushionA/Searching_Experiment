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


def rows_of(*values, axes):
    return {"au_rows": [{"row_key": f"r{i}", "axes": [{"axis_name": n, "value": v} for n, v in zip(axes, row)]}
                        for i, row in enumerate(values)]}


def selected(table, *values):
    return {"rakuten_selected": {"axes": [{"axis_key": k, "axis_label": label, "value": v,
                                           "family_values": list(table.loc[table["axis_key"] == k, "value"])}
                                          for k, label, v in values]}}


def run(table, au, context, *values):
    symmetric, au_only = al.axis_pairs(table, au)
    case = selected(table, *values)
    states = al.row_states(al.conditions(case, context, table, symmetric, au_only))
    return states.to_dict(), al.decide(states)


class TableTests(unittest.TestCase):
    def setUp(self):
        self.table = al.value_pairs(entry(RAKUTEN, AU, FORWARD, REVERSE))

    def test_identical_strings_rank_first_and_pairs_are_mutual(self):
        got = {(r.value, r.au_value): (r.paired, r.matched_by) for r in self.table.itertuples()}
        self.assertEqual(got[("ブラック", "ブラック")], (True, "identical"))
        self.assertEqual(got[("グレー", "グレイ")], (True, "model"))
        self.assertEqual(got[("10個", "ホワイト")], (True, "model"))
        self.assertFalse(got[("ネイビー", "グレイ")][0])

    def test_axes_pair_one_to_one(self):
        # Model pairs join axes too: サイズ has no identical value. 10個-ホワイト also reaches カラー,
        # so カラー has two Rakuten axes and pairs with neither.
        self.assertEqual(al.axis_pairs(self.table, AU), ({"size": "サイズ"}, ["カラー"]))
        # Two Rakuten axes reaching one AU axis are not paired.
        t = al.value_pairs(entry([["a", "カラー", "グレー"], ["b", "タイプ", "ブラック"]],
                                 [["カラー", "グレー"], ["カラー", "ブラック"]], [[0, 0], [0, 0]], [[0, 0], [0, 0]]))
        self.assertEqual(al.axis_pairs(t, [["カラー", "グレー"], ["カラー", "ブラック"]])[0], {})

    def test_axis_without_identical_values_pairs_through_model_pairs(self):
        # Curtain sizes: no string is identical, but the model pairs them mutually, so the axes pair.
        rakuten = [["size", "サイズ", "100×80cm"], ["size", "サイズ", "100×105cm"], ["colour", "カラー", "ブラック"]]
        au = [["サイズ", "幅100×丈80cm(2枚)"], ["サイズ", "幅100×丈105cm(2枚)"], ["カラー", "ブラック"]]
        table = al.value_pairs(entry(rakuten, au, [[5, 3, 0], [3, 5, 0], [0, 0, 0]], [[5, 3, 0], [3, 5, 0], [0, 0, 0]]))
        self.assertEqual(set(table.loc[table["axis_key"] == "size", "matched_by"]), {"model"})
        self.assertEqual(al.axis_pairs(table, au), ({"size": "サイズ", "colour": "カラー"}, []))
        # The other size's row is a contradiction; the model pair's row still goes to the description check.
        context = rows_of(("幅100×丈80cm(2枚)", "ブラック"), ("幅100×丈105cm(2枚)", "ブラック"), axes=("サイズ", "カラー"))
        states, decision = run(table, au, context, ("size", "サイズ", "100×80cm"), ("colour", "カラー", "ブラック"))
        self.assertEqual(states, {"r0": "pending_description_check", "r1": "contradiction"})
        self.assertEqual(decision, ("unmatched", None, "pending_description_check"))

    def test_wrong_axis_link_is_neither_aligned_nor_adopted(self):
        # 10個 and ホワイト are each other's best match, so the set axis pairs with the AU colour axis.
        rakuten = [["size", "サイズ", "S"], ["size", "サイズ", "M"], ["set", "セット数", "10個"]]
        au = [["サイズ", "S"], ["サイズ", "M"], ["カラー", "ホワイト"], ["カラー", "ブラック"]]
        table = al.value_pairs(entry(rakuten, au, [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 3, 1]],
                                     [[0, 0, 0], [0, 0, 0], [0, 0, 3], [0, 0, 1]]))
        symmetric, au_only = al.axis_pairs(table, au)
        self.assertEqual((symmetric, au_only), ({"size": "サイズ", "set": "カラー"}, []))
        context = rows_of(("S", "ホワイト"), ("S", "ブラック"), ("M", "ホワイト"), ("M", "ブラック"), axes=("サイズ", "カラー"))
        case = selected(table, ("size", "サイズ", "S"), ("set", "セット数", "10個"))
        conds = al.conditions(case, context, table, symmetric, au_only)
        self.assertEqual(set(conds.loc[conds["axis_key"] == "set", "status"]), {"model_candidate", "contradiction"})
        states = al.row_states(conds)
        self.assertNotIn("aligned", set(states))
        self.assertEqual(al.decide(states), ("unmatched", None, "pending_description_check"))

    def test_rows_and_decision(self):
        # Without 10個 the colour axis pairs; the size axis pairs through its model pair.
        table = al.value_pairs(entry(RAKUTEN[:4], AU, FORWARD[:4], [r[:4] for r in REVERSE]))
        context = rows_of(*[("幅100×丈80cm(2枚)", c) for c in ["ブラック", "グレイ", "ホワイト"]], axes=("サイズ", "カラー"))

        def run_here(*values):
            return run(table, AU, context, *values)

        black, grey = ("colour", "カラー", "ブラック"), ("colour", "カラー", "グレー")
        self.assertEqual(run_here(black)[1], ("matched", "r0", "all_conditions_aligned_on_one_row"))
        # A model pair is only a candidate: its row goes to the description check, the others are excluded.
        self.assertEqual(run_here(grey)[0], {"r0": "contradiction", "r1": "pending_description_check", "r2": "contradiction"})
        self.assertEqual(run_here(black, ("size", "サイズ", "100×80cm"))[0]["r0"], "pending_description_check")
        # Conditions on one-sided axes also go to the description check.
        self.assertEqual(run_here(black, ("set", "セット数", "10個"))[0]["r0"], "pending_description_check")
        # A value of a symmetric axis with no AU counterpart is excluded, not handed off.
        self.assertEqual(run_here(("colour", "カラー", "ネイビー"))[1][2], "symmetric_condition_unresolved")


def left(c):
    return [(r["side"], r["text"], r["verify_with"]) for r in c["residual"]]


class CoreTests(unittest.TestCase):
    def test_compare(self):
        self.assertEqual(left(al.compare("ベージュ", "ショート/ベージュ")), [("au", "ショート", "rakuten")])
        self.assertEqual(left(al.compare("ホワイト(長袖)", "ホワイト")), [("rakuten", "長袖", "au")])
        self.assertEqual(al.compare("グレー", "グレー(パイル)")["residual"][0]["source_value"], "グレー(パイル)")
        # A sign, unit or qualifier is never dropped: the cores differ, and only bracketed parts are split off.
        for rakuten, au in [("15℃", "-15℃"), ("100cm", "最大100cm"), ("21.0", "21.0cm"), ("1", "1/2サイズ"),
                            ("100×220cm", "幅100×丈220cm(2枚)"), ("01.ブラック", "01.ブラックポケットあり"), ("S", "XS")]:
            self.assertFalse(al.compare(rakuten, au)["core"]["identical"], (rakuten, au))
        self.assertEqual(left(al.compare("100×220cm", "幅100×丈220cm(2枚)")), [("au", "2枚", "rakuten")])

    def conds(self, rakuten, au, forward, reverse, *values):
        table = al.value_pairs(entry(rakuten, au, forward, reverse))
        symmetric, au_only = al.axis_pairs(table, au)
        context = rows_of(*[(v,) for _, v in au], axes=("カラー",))
        return al.conditions(selected(table, *values), context, table, symmetric, au_only)

    def test_every_row_sharing_the_core_keeps_its_residual(self):
        # The model picks グレー(フェイクファー) only; グレー(フォックス) shares the core and is not contradicted.
        rakuten = [["c", "カラー", "グレー"], ["c", "カラー", "ブラック"]]
        au = [["カラー", "グレー(フェイクファー)"], ["カラー", "グレー(フォックス)"], ["カラー", "ブラック"]]
        conds = self.conds(rakuten, au, [[3, 1, 0], [0, 0, 0]], [[3, 0], [1, 0], [0, 0]], ("c", "カラー", "グレー"))
        self.assertEqual(list(conds["status"]), ["residual", "residual", "contradiction"])
        self.assertEqual([[(r["side"], r["text"]) for r in x] for x in list(conds["residual"])[:2]],
                         [[("au", "フェイクファー")], [("au", "フォックス")]])
        self.assertEqual(al.decide(al.row_states(conds))[2], "pending_description_check")

    def test_siblings_of_an_unconfirmed_pick_stay_candidates(self):
        # 100×80cm is not provably 幅100×丈80cm: the pick and its (4枚組) sibling stay candidates with their parts split off.
        rakuten = [["s", "サイズ", "100×80cm"], ["s", "サイズ", "100×90cm"]]
        au = [["カラー", "幅100×丈80cm(2枚)"], ["カラー", "幅100×丈80cm(4枚組)"], ["カラー", "幅100×丈90cm(2枚)"]]
        conds = self.conds(rakuten, au, [[3, 2, 0], [0, 0, 3]], [[3, 0], [2, 0], [0, 3]], ("s", "サイズ", "100×80cm"))
        self.assertEqual(list(conds["status"]), ["model_candidate", "model_candidate", "contradiction"])
        self.assertEqual([c["identical"] for c in list(conds["core"])[:2]], [False, False])
        self.assertEqual([x[0]["text"] for x in list(conds["residual"])[:2]], ["2枚", "4枚組"])

    def test_au_value_identical_to_another_rakuten_value_is_not_shared(self):
        rakuten = [["c", "カラー", "グレー"], ["c", "カラー", "グレー(メッシュ)"]]
        au = [["カラー", "グレー"], ["カラー", "ブラック"]]
        conds = self.conds(rakuten, au, [[0, 0], [3, 0]], [[0, 3], [0, 0]], ("c", "カラー", "グレー(メッシュ)"))
        self.assertEqual(set(conds["status"]), {"symmetric_unresolved"})

    def test_numbers_alone_do_not_align(self):
        rakuten = [["t", "温度", "15℃"], ["t", "温度", "20℃"]]
        au = [["カラー", "-15℃"], ["カラー", "20℃以下"]]
        conds = self.conds(rakuten, au, [[1, 0], [0, 1]], [[1, 0], [0, 1]], ("t", "温度", "15℃"))
        self.assertNotIn("aligned", set(conds["status"]))
        self.assertEqual(al.decide(al.row_states(conds))[0], "unmatched")


if __name__ == "__main__":
    unittest.main()
