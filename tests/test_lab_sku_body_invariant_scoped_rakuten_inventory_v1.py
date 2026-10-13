import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import sku_body_invariant_scoped_rakuten_inventory_v1 as SCOPED


class ScopedRakutenInventoryTests(unittest.TestCase):
    def _parent(self, *, rows=None, selectors=None, description=None):
        if selectors is None:
            selectors = [
                {"key": "shade", "label": "Shade", "values": [
                    {"value": "red"}, {"value": "blue"}]},
                {"key": "size", "label": "Size", "values": [
                    {"value": "small"}, {"value": "large"}]},
            ]
        if rows is None:
            # Intentionally sparse: these are the two actual native rows, not
            # the four possible Cartesian combinations.
            rows = [
                {"variantId": "v-red-small", "selectorValues": ["red", "small"]},
                {"variantId": "v-blue-large", "selectorValues": ["blue", "large"]},
            ]
        pc = {"itemNumber": "native-item-number"}
        if description is not None:
            pc["productDescription"] = description
        return {"itemId": "native-item", "shopId": "native-shop",
            "manageNumber": "native-manage", "pcFields": pc,
            "variantSelectors": selectors, "sku": rows}

    def _source(self, directory, api=None, mirror=None, prefix=""):
        api = api or self._parent()
        mirror = copy.deepcopy(api) if mirror is None else mirror
        data = {"api": {"data": {"itemInfoSku": api}},
            "newApi": {"itemInfoSku": mirror}}
        raw = (prefix + '<script id="item-page-app-data" type="application/json">'
            + json.dumps(data, ensure_ascii=True, separators=(",", ":"))
            + "</script><script id='noise' type='application/json'>"
            + '{"variantId":"placeholder-only","newPurchaseSku":{}}'
            + "</script>\n{\"variantId\":\"outside-script\"}")
        path = Path(directory) / "capture.html"
        path.write_text(raw, encoding="utf-8")
        digest = SCOPED.BASE.sha_file(path)
        title_ref = {"raw_file": "capture.html", "sha256": digest,
            "locator": {"encoding": "utf-8", "kind": "captured_document"}}
        doc = {"document_id": "doc:raku", "document_sha256": "d" * 64,
            "side": "rakuten", "title": {"source_refs": [title_ref]},
            "description": {"segments": []}}
        return doc, raw, digest

    def test_main_native_parent_ignores_bare_placeholders_and_preserves_sparse_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            doc, _raw, _digest = self._source(temp)
            store = SCOPED.BASE.NLI.SOURCES.RawStore(Path(temp))
            inventory = SCOPED._scoped_rakuten_inventory(store, doc)
            self.assertEqual(inventory["status"], "verified_native_axes")
            self.assertEqual(len(inventory["all_option_states"]), 2)
            self.assertEqual([s["variant_id"] for s in inventory["all_option_states"]],
                ["v-red-small", "v-blue-large"])
            self.assertEqual(len(inventory["source_row_lexeme_refs"]), 2)
            for row in inventory["source_row_lexeme_refs"]:
                self.assertTrue(SCOPED.BASE.NLI.SOURCES.verify_span(store, row["variant_id_span"]))
                for span in row["coordinate_spans"]:
                    self.assertTrue(SCOPED.BASE.NLI.SOURCES.verify_span(store, span))
            self.assertTrue(all(SCOPED.BASE.NLI.SOURCES.verify_span(store, ref)
                for ref in inventory["source_parent_lexeme_refs"]))

    def test_missing_product_description_does_not_invalidate_native_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            doc, _raw, _digest = self._source(temp, api=self._parent(description=None))
            inventory = SCOPED._scoped_rakuten_inventory(
                SCOPED.BASE.NLI.SOURCES.RawStore(Path(temp)), doc)
            self.assertEqual(inventory["status"], "verified_native_axes")
            self.assertEqual(len(inventory["all_option_states"]), 2)

    def test_mirror_with_same_ids_but_different_coordinates_abstains(self):
        with tempfile.TemporaryDirectory() as temp:
            api = self._parent()
            mirror = copy.deepcopy(api)
            mirror["sku"][0]["selectorValues"] = ["red", "large"]
            doc, _raw, _digest = self._source(temp, api=api, mirror=mirror)
            inventory = SCOPED._scoped_rakuten_inventory(
                SCOPED.BASE.NLI.SOURCES.RawStore(Path(temp)), doc)
            self.assertEqual(inventory["status"], "unknown_native_target_sku_inventory")

    def test_incomplete_actual_row_family_abstains_without_cartesian_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            api = self._parent(rows=[{"variantId": "v-red-small",
                "selectorValues": ["red", "small"]}])
            doc, _raw, _digest = self._source(temp, api=api)
            inventory = SCOPED._scoped_rakuten_inventory(
                SCOPED.BASE.NLI.SOURCES.RawStore(Path(temp)), doc)
            self.assertEqual(inventory["status"], "unknown_native_target_sku_inventory")

    def test_missing_row_coordinate_tuple_abstains(self):
        with tempfile.TemporaryDirectory() as temp:
            api = self._parent()
            api["sku"][0].pop("selectorValues")
            mirror = copy.deepcopy(api)
            doc, _raw, _digest = self._source(temp, api=api, mirror=mirror)
            inventory = SCOPED._scoped_rakuten_inventory(
                SCOPED.BASE.NLI.SOURCES.RawStore(Path(temp)), doc)
            self.assertEqual(inventory["status"], "unknown_native_target_sku_inventory")

    def test_inventory_hook_restores_on_exception_and_au_uses_frozen_reader(self):
        original = SCOPED.BASE._target_sku_inventory
        sentinel = lambda store, doc: {"sentinel": True}
        SCOPED.BASE._target_sku_inventory = sentinel
        try:
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                with SCOPED._installed_inventory_hook():
                    self.assertIsNot(SCOPED.BASE._target_sku_inventory, sentinel)
                    raise RuntimeError("synthetic")
            self.assertIs(SCOPED.BASE._target_sku_inventory, sentinel)
        finally:
            SCOPED.BASE._target_sku_inventory = original
        self.assertIs(SCOPED.BASE_TARGET_INVENTORY, original)
        prior = SCOPED.BASE_TARGET_INVENTORY
        SCOPED.BASE_TARGET_INVENTORY = sentinel
        try:
            self.assertEqual(SCOPED._scoped_rakuten_inventory(None, {"side": "au"}),
                {"sentinel": True})
        finally:
            SCOPED.BASE_TARGET_INVENTORY = prior


if __name__ == "__main__":
    unittest.main()
