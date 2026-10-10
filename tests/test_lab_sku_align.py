"""Model-free parts of the SKU condition alignment: answer matching and mapping checks."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/sku-matching"))
import align_sku_conditions as al  # noqa: E402

OPTIONS = [{"axis_name": "サイズ", "value": "幅100×丈80cm(2枚)"}, {"axis_name": "サイズ", "value": "幅100×丈90cm(2枚)"},
           {"axis_name": "カラー", "value": "グレー"}, {"axis_name": "カラー", "value": "ダークグレー"}]


class ParseTests(unittest.TestCase):
    def test_reply_must_equal_one_listed_option(self):
        self.assertEqual(al.parse("- 幅 100×丈 80cm(2 枚)", OPTIONS), 0)
        self.assertEqual(al.parse("カラー: ダークグレー", OPTIONS), 3)
        self.assertEqual(al.parse("グレー", OPTIONS), 2)
        self.assertEqual(al.parse("該当なし", OPTIONS), "none")
        # A near option or a partial string is not an answer.
        self.assertEqual(al.parse("ライトグレー", OPTIONS), "invalid")
        self.assertEqual(al.parse("幅100×丈80cm", OPTIONS), "invalid")


class MappingTests(unittest.TestCase):
    def q(self, qid, value):
        return {"question_id": qid, "dossier_id": "d", "axis_key": "size", "value": value, "au_options": OPTIONS}

    def test_order_disagreement_and_shared_targets_stay_unresolved(self):
        qs = [self.q("q0", "100×80cm"), self.q("q1", "100×90cm"), self.q("q2", "100×100cm"), self.q("q3", "S")]
        answers = [{"question_id": "q0", "order": "forward", "choice": 0}, {"question_id": "q0", "order": "reverse", "choice": 0},
                   {"question_id": "q1", "order": "forward", "choice": 1}, {"question_id": "q1", "order": "reverse", "choice": 0},
                   {"question_id": "q2", "order": "forward", "choice": "none"}, {"question_id": "q2", "order": "reverse", "choice": "none"},
                   {"question_id": "q3", "order": "forward", "choice": 1}, {"question_id": "q3", "order": "reverse", "choice": 1}]
        m = al.mappings(qs, answers)
        self.assertEqual(m[("d", "size", "100×80cm")]["status"], "mapped")
        self.assertEqual(m[("d", "size", "100×90cm")]["status"], "order_inconsistent")
        self.assertEqual(m[("d", "size", "100×100cm")]["status"], "no_same_au_option")
        self.assertEqual(m[("d", "size", "S")]["status"], "mapped")
        answers[7]["choice"] = answers[6]["choice"] = 0
        m = al.mappings(qs, answers)
        self.assertEqual(m[("d", "size", "100×80cm")]["status"], "not_one_to_one")
        self.assertEqual(m[("d", "size", "S")]["status"], "not_one_to_one")

    def test_two_rakuten_axes_cannot_share_one_au_axis(self):
        colour = {"question_id": "c", "dossier_id": "d", "axis_key": "colour", "value": "マーブルホワイト", "au_options": OPTIONS}
        size = {"question_id": "s", "dossier_id": "d", "axis_key": "size", "value": "80cm(直径)", "au_options": OPTIONS}
        answers = [{"question_id": q, "order": o, "choice": c} for q, c in (("c", 2), ("s", 3)) for o in ("forward", "reverse")]
        m = al.mappings([colour, size], answers)
        self.assertEqual({v["status"] for v in m.values()}, {"axis_shared"})

    def test_identical_strings_and_mutual_best_match(self):
        grey = {"question_id": "g", "dossier_id": "d", "axis_key": "colour", "value": "グレー", "au_options": OPTIONS}
        dark = {"question_id": "k", "dossier_id": "d", "axis_key": "colour", "value": "チャコール", "au_options": OPTIONS}
        m = al.mappings([grey, dark], None)
        self.assertEqual((m[("d", "colour", "グレー")]["status"], m[("d", "colour", "グレー")]["option"]["value"]),
                         ("exact_string", "グレー"))
        self.assertEqual(m[("d", "colour", "チャコール")]["status"], "no_identical_string")
        # A model answer cannot move an identical string to another option when exact_first is set.
        answers = [{"question_id": "g", "order": o, "choice": 3} for o in ("forward", "reverse")] +                   [{"question_id": "k", "order": "forward", "choice": 3}, {"question_id": "k", "order": "reverse", "choice": "not_mutual"}]
        m = al.mappings([grey, dark], answers, exact_first=True)
        self.assertEqual(m[("d", "colour", "グレー")]["option"]["value"], "グレー")
        self.assertEqual(m[("d", "colour", "チャコール")]["status"], "not_mutual_best")

    def test_value_contained_in_the_other_is_not_the_same_option(self):
        options = [{"axis_name": "タイプ", "value": "グレー"}, {"axis_name": "タイプ", "value": "グレイ"}]
        qs = [{"question_id": "a", "dossier_id": "d", "axis_key": "t", "value": "スリム / グレー", "au_options": options},
              {"question_id": "b", "dossier_id": "e", "axis_key": "t", "value": "グレー", "au_options": options[1:]}]
        answers = [{"question_id": "a", "order": o, "choice": 0} for o in ("forward", "reverse")] +                   [{"question_id": "b", "order": o, "choice": 0} for o in ("forward", "reverse")]
        m = al.mappings(qs, answers)
        self.assertEqual(m[("d", "t", "スリム / グレー")]["status"], "one_side_has_more")
        self.assertEqual(m[("e", "t", "グレー")]["status"], "mapped")

    def test_axis_split_pairs_axes_and_leaves_unmatched_axes_one_sided(self):
        maps = {("d", "size", "100×80cm"): {"status": "mapped", "option": OPTIONS[0]},
                ("d", "size", "100×300cm"): {"status": "not_mutual_best"},
                ("d", "lace", "あり"): {"status": "not_mutual_best"},
                ("d", "lace", "なし"): {"status": "not_mutual_best"},
                ("e", "colour", "グレー"): {"status": "mapped", "option": OPTIONS[2]},
                ("e", "type", "グレー系"): {"status": "one_side_has_more", "option": OPTIONS[3]}}
        axes = al.axis_split(maps)
        self.assertEqual(axes[("d", "size")], {"kind": "symmetric", "au_axis": "サイズ", "votes": {"サイズ": 1}})
        self.assertEqual(axes[("d", "lace")]["kind"], "one_sided")
        # Two Rakuten axes claiming one AU axis are not guessed.
        self.assertEqual({axes[("e", "colour")]["reason"], axes[("e", "type")]["reason"]},
                         {"au_axis_claimed_by_several_rakuten_axes"})


if __name__ == "__main__":
    unittest.main()
