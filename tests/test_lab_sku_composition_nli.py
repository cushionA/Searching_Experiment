"""Inline contracts for title-free composition NLI candidate construction."""
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("composition_nli_v1", HERE / "trial_composition_nli_v1.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def fixture(kind="shared"):
    axes = "色：赤\n容量：500ml" if kind == "paired" else None
    unit = {
        "group_id": "unit-1", "group_sha256": "unit-sha", "kind": kind,
        "axis_name": "セット内容", "option_values": ["本体＋ケース 2個", "本体／替え芯×３"],
        "target_document_id": "doc-1", "target_selected_axes_text": axes,
    }
    choices = [
        {"label": value, "description": f"購入対象の〈セット内容〉は〈{value}〉である。"}
        for value in unit["option_values"]
    ] + [{"label": "unknown", "description": "この記載から購入対象の構成を特定できない。"}]
    passage = "商品1点、付属品：ケース"
    selected_prefix = "対象SKUの選択軸：\n" + axes + "\n" if axes else ""
    request = {
        "id": "unit-1:block-1", "group_id": "unit-1", "group_sha256": "unit-sha",
        "unit_kind": kind, "block_id": "block-1", "block_rank": 1,
        "all_original_option_values": list(unit["option_values"]), "unknown_control_label": "unknown",
        "choices": choices,
        "question": "購入対象の仕様・セット内容について、〈セット内容〉はどの選択肢に該当しますか。",
        "state": selected_prefix + "この商品の記載された仕様・セット内容：\n" + passage,
        "quote_refs": [{"document_id": "doc-1", "field": "description", "quote": "商品1点"}],
    }
    block = {"block_id": "block-1", "target_document_id": "doc-1", "model_passage": passage,
             "quote_refs": request["quote_refs"]}
    return unit, request, block


class CompositionNLIPairTests(unittest.TestCase):
    def test_all_raw_options_become_pairs_and_unknown_stays_only_in_full_choice_hash(self):
        unit, request, block = fixture("shared")
        pairs = M.pairs_for([unit], [request], [block])
        self.assertEqual(len(pairs), 2)
        self.assertEqual([p["option_value"] for p in pairs], unit["option_values"])
        self.assertEqual([p["option_index"] for p in pairs], [0, 1])
        self.assertEqual([p["hypothesis"] for p in pairs], [
            "購入対象の〈セット内容〉は〈本体＋ケース 2個〉である。",
            "購入対象の〈セット内容〉は〈本体／替え芯×３〉である。",
        ])
        for pair in pairs:
            self.assertEqual(pair["premise"], request["state"])
            self.assertNotIn("現在の商品名：", pair["premise"])
            self.assertNotIn("unknown", pair["hypothesis"])
            self.assertEqual(pair["all_original_option_values"], unit["option_values"])
            self.assertEqual(pair["unknown_control_label"], "unknown")
            self.assertEqual(pair["source_choice_sha256"], M.FULL.digest(request["choices"]))
            self.assertEqual(pair["unit_sha256"], "unit-sha")
            self.assertEqual(pair["block_rank"], 1)
            self.assertEqual(pair["quote_refs"], block["quote_refs"])
            self.assertFalse(any(ref.get("field") == "selected_axes" for ref in pair["quote_refs"]))

    def test_paired_option_pairs_add_only_verified_exact_selected_axes_quote(self):
        unit, request, block = fixture("paired")
        pairs = M.pairs_for([unit], [request], [block])
        expected = {"document_id": "doc-1", "field": "selected_axes", "start": 0,
                    "end": len(unit["target_selected_axes_text"]),
                    "quote": unit["target_selected_axes_text"]}
        for pair in pairs:
            self.assertEqual(pair["premise"], request["state"])
            self.assertEqual(pair["quote_refs"][-1], expected)
            self.assertEqual(pair["quote_refs"][:-1], block["quote_refs"])

    def test_malformed_source_choice_order_is_rejected_before_pair_scoring(self):
        unit, request, block = fixture("shared")
        request["choices"][0], request["choices"][1] = request["choices"][1], request["choices"][0]
        with self.assertRaisesRegex(ValueError, "full source choices"):
            M.pairs_for([unit], [request], [block])

    def test_shared_state_rejects_selected_axes_injection(self):
        unit, request, block = fixture("shared")
        request["state"] = "対象SKUの選択軸：\n色：赤\nこの商品の記載された仕様・セット内容：\n" + block["model_passage"]
        with self.assertRaisesRegex(ValueError, "title-free composition rendering"):
            M.pairs_for([unit], [request], [block])


if __name__ == "__main__":
    unittest.main()
