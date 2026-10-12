import sys
import unittest
import json
import copy
import tempfile
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE, *HERE.parents)
            if (parent / "experiments/sku-matching").is_dir())
EXPERIMENTS = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(EXPERIMENTS))
import sku_directional_choice_nli_au_coordinate_v1 as adapter


class AUCoordinateFamilyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = adapter.base.SOURCES.RawStore(
            ROOT / ".lab-output/sku-full-evaluation-prepare-20261013-v1/source")
        cls.raw_file = ".lab-output/sku-independent-real-20261011/multishop/selection-v1/au/raw/586201628-item.json"
        cls.sha = "98bf4e4da5fc81dc94d7b4c8373531211f1b64194670bc6f2d097812461d06d9"
        cls.dev_tasks = [json.loads(line) for line in
                         (ROOT / ".lab-output/sku-full-condition-adapter-20261013-v2/dev/tasks.jsonl").read_text(encoding="utf-8").splitlines()
                         if line.strip()]

    def fact(self, kind="row", caption="選択"):
        raw = self.store.json(self.raw_file)
        value = raw["itemInfo"]["skuInfo"][kind + "Names"][0]
        label_span = adapter.base.SOURCES.json_leaf_span(
            self.store, self.raw_file, f"$.itemInfo.skuInfo.optionName.{kind}",
            raw["itemInfo"]["skuInfo"]["optionName"][kind])
        value_span = adapter.base.SOURCES.json_leaf_span(
            self.store, self.raw_file, f"$.itemInfo.skuInfo.{kind}Names[0]", value)
        return {"axis_name": caption, "axis_name_span": label_span, "value": value,
                "value_span": value_span}

    def test_duplicate_caption_uses_exact_value_coordinate_and_keeps_family(self):
        raw = self.store.json(self.raw_file)
        sku = raw["itemInfo"]["skuInfo"]
        # This fixture has the same caption on both axes.
        self.assertEqual(sku["optionName"]["row"], sku["optionName"]["column"])
        fact = self.fact("row", sku["optionName"]["row"])
        result = adapter._au_family(self.store, fact)
        self.assertEqual(result["axis_kind"], "row")
        self.assertEqual(result["family_values"], sku["rowNames"])
        self.assertEqual(result["members"][0]["role"], "selected")
        self.assertEqual(result["axis_name_span"]["locator"]["json_path"],
                         "$.itemInfo.skuInfo.optionName.row")

    def test_wrong_source_sha_fails_closed(self):
        fact = self.fact()
        fact["value_span"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            adapter._au_family(self.store, fact)

    def test_caption_mismatch_fails_closed(self):
        fact = self.fact(caption="wrong caption")
        with self.assertRaises(ValueError):
            adapter._au_family(self.store, fact)


    def test_fact_record_does_not_replace_a_supplied_mismatched_au_label_span(self):
        task = copy.deepcopy(next(t for t in self.dev_tasks
                    if t.get("direction") == "au_to_rakuten"
                    and "586201628-item.json" in ((t.get("raw_condition_ref") or {}).get("value_span") or {}).get("raw_file", "")))
        ref = task["raw_condition_ref"]
        value_path = ref["value_span"]["locator"]["json_path"]
        kind = "column" if ".columnNames[" in value_path else "row"
        wrong_kind = "column" if kind == "row" else "row"
        raw_file = ref["value_span"]["raw_file"]
        source = self.store.json(raw_file)
        wrong_caption = source["itemInfo"]["skuInfo"]["optionName"][wrong_kind]
        self.assertEqual(wrong_caption, ref["axis_name"])
        ref["axis_label_span"] = adapter.base.SOURCES.json_leaf_span(
            self.store, raw_file, f"$.itemInfo.skuInfo.optionName.{wrong_kind}", wrong_caption)
        with self.assertRaises(ValueError):
            adapter._fact_record(self.store, task)

    def test_equal_caption_with_mismatched_existing_name_path_fails_closed(self):
        sku = self.store.json(self.raw_file)["itemInfo"]["skuInfo"]
        self.assertEqual(sku["optionName"]["row"], sku["optionName"]["column"])
        fact = self.fact("column", sku["optionName"]["column"])
        fact["axis_name_span"] = adapter.base.SOURCES.json_leaf_span(
            self.store, self.raw_file, "$.itemInfo.skuInfo.optionName.row",
            sku["optionName"]["row"])
        with self.assertRaises(ValueError):
            adapter._au_family(self.store, fact)

    def test_inexact_coordinate_fails_closed(self):
        fact = self.fact()
        fact["value_span"]["locator"]["json_path"] = "$.itemInfo.skuInfo.rowNames"
        with self.assertRaises(ValueError):
            adapter._au_family(self.store, fact)


    def rakuten_fact(self):
        task = next(t for t in self.dev_tasks if t.get("direction") == "rakuten_to_au"
                    and (t.get("raw_condition_ref") or {}).get("axis_key") == "horizontal-key"
                    and "35279092-492358d1.html" in ((t.get("raw_condition_ref") or {}).get("value_span") or {}).get("raw_file", "")
                    and ((t.get("raw_condition_ref") or {}).get("axis_label_span") or {}).get("locator", {}).get("path") == "variantSelectors[0].label")
        task = copy.deepcopy(task)
        fact = adapter.base._fact_from_task(self.store, task)
        fact["axis_key"] = task["axis_key"]
        fact["axis_name_span"] = task["raw_condition_ref"]["axis_label_span"]
        return fact

    def test_rakuten_duplicate_caption_uses_exact_selector_coordinate(self):
        fact = self.rakuten_fact()
        result = adapter._rakuten_family(self.store, fact)
        self.assertEqual(result["family_values"], ["当日出荷", "通常配送5日以内"])
        self.assertEqual(result["axis_key"], "horizontal-key")

    def test_rakuten_wrong_value_coordinate_and_wrong_sha_fail_closed(self):
        fact = self.rakuten_fact()
        fact["value_span"]["locator"]["path"] = "sku[variantId=35279092ZZ].selectorValues[1]"
        with self.assertRaises(ValueError):
            adapter._rakuten_family(self.store, fact)
        fact = self.rakuten_fact()
        fact["axis_name_span"]["locator"]["path"] = "variantSelectors[1].label"
        with self.assertRaises(ValueError):
            adapter._rakuten_family(self.store, fact)
        fact = self.rakuten_fact()
        fact["axis_name_span"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            adapter._rakuten_family(self.store, fact)
        fact = self.rakuten_fact()
        del fact["axis_name_span"]["locator"]["path"]
        with self.assertRaises(ValueError):
            adapter._rakuten_family(self.store, fact)

    def test_prepare_restores_base_module_hooks(self):
        original = (adapter.base._au_family, adapter.base._rakuten_family,
                    adapter.base._fact_record, adapter.base._load_manifest)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "prepared"
            def fake_prepare(_input, _source, out):
                out.mkdir()
                (out / "freeze.json").write_text("{}\n", encoding="utf-8")
                (out / "summary.json").write_text("{}\n", encoding="utf-8")
                return {}
            with mock.patch.object(adapter.base, "prepare", side_effect=fake_prepare):
                adapter.prepare(Path(temp) / "input", Path(temp) / "source", output)
        self.assertEqual((adapter.base._au_family, adapter.base._rakuten_family,
                          adapter.base._fact_record, adapter.base._load_manifest), original)


if __name__ == "__main__":
    unittest.main()
