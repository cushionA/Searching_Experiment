import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import sku_embedded_json_source_spans_v1 as ADAPTER
from claude_v9_gate import sku_gate_sources as OLD


def fixture_json(values=None):
    values = values or ["Ａ．5色×2足 = 10足セット", 'quote " slash \\ smile 😀']
    obj = {"newApi": {"itemInfoSku": {"variantSelectors": [
        {"key": "size", "label": "Size", "values": [{"value": "same"}]},
        {"key": "color", "label": "カラー", "values": [{"value": v} for v in values]},
        {"key": "other", "label": "Other", "values": [{"value": "same"}]},
    ]}}}
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class EmbeddedJsonSourceSpanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rel = "page.html"
        self.store = OLD.RawStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, page):
        (self.root / self.rel).write_bytes(page.encode("utf-8"))
        self.store = OLD.RawStore(self.root)

    def page(self, raw_json=None):
        return '<html><script id="item-page-app-data" type="application/json">' + \
            (fixture_json() if raw_json is None else raw_json) + '</script></html>'

    def test_json_escape_spelling_equivalence_and_decoded_leaf_offsets(self):
        raw = fixture_json().replace(" = 10", r" \u003d 10")
        self.write(self.page(raw))
        expected = "Ａ．5色×2足 = 10足セット"
        span = ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "color", expected)
        self.assertIsNotNone(span)
        self.assertEqual(span["quote"], expected)
        self.assertEqual(span["locator"]["kind"], ADAPTER.SOURCE_KIND)
        self.assertEqual(span["locator"]["json_path"], "$.newApi.itemInfoSku.variantSelectors[1].values[0].value")
        with ADAPTER.install_adapter(OLD):
            self.assertTrue(OLD.verify_span(self.store, span))
            self.assertEqual(OLD.span_container(self.store, span), expected)
        self.assertEqual(span["sha256"], hashlib.sha256((self.root / self.rel).read_bytes()).hexdigest())

    def test_unicode_quote_backslash_and_coordinate_select_exact_member(self):
        vals = ['quote " slash \\ smile 😀', "same"]
        raw = fixture_json(vals).replace("😀", r"\ud83d\ude00")
        self.write(self.page(raw))
        picked = ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "color", vals[0])
        self.assertEqual(picked["locator"]["member_index"], 0)
        self.assertEqual(picked["quote"], vals[0])
        other_axis = ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 2, "other", "same")
        self.assertEqual(other_axis["locator"]["json_path"],
                         "$.newApi.itemInfoSku.variantSelectors[2].values[0].value")
        self.assertIsNone(ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "wrong", vals[0]))
        self.assertIsNone(ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", True, "color", vals[0]))
        # Repeated decoded member values within one axis are ambiguous.
        self.write(self.page(fixture_json(["repeat", "repeat"])))
        self.assertIsNone(ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "color", "repeat"))

    def test_html_parser_ignores_comment_and_handles_quoted_gt_but_rejects_duplicate_id_type(self):
        body = fixture_json()
        page = '<!-- <script id="item-page-app-data" type="application/json">{}</script> -->' \
            '<script data-note="a > b" type="application/json" id="item-page-app-data">' + body + '</script>'
        self.write(page)
        span = ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "color",
                                                   "Ａ．5色×2足 = 10足セット")
        self.assertIsNotNone(span)
        self.write(page + '<script id="item-page-app-data" type="text/plain">ignored</script>')
        self.assertIsNone(ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "color",
                                                              "Ａ．5色×2足 = 10足セット"))

    def test_duplicate_script_duplicate_keys_invalid_json_and_forged_sha_fail_closed(self):
        valid = self.page()
        self.write(valid + valid)
        self.assertIsNone(ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "color", "x"))
        self.write(self.page('{"newApi":{"newApi":{},"itemInfoSku":{}}}'))
        self.assertIsNone(ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "color", "x"))
        self.write(self.page('{broken'))
        self.assertIsNone(ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "color", "x"))

        self.write(self.page())
        span = ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "color",
                                                   "Ａ．5色×2足 = 10足セット")
        forged = dict(span, sha256="0" * 64)
        with ADAPTER.install_adapter(OLD):
            self.assertFalse(OLD.verify_span(self.store, forged))

    def test_tampered_json_path_cannot_change_the_bound_coordinate(self):
        self.write(self.page(fixture_json(["same"])))
        span = ADAPTER.rakuten_selector_value_span(self.store, self.rel, "utf-8", 1, "color", "same")
        self.assertIsNotNone(span)
        tampered = json.loads(json.dumps(span))
        tampered["locator"]["json_path"] = "$.newApi.itemInfoSku.variantSelectors[2].values[0].value"
        with ADAPTER.install_adapter(OLD):
            self.assertFalse(OLD.verify_span(self.store, tampered))
        tampered = json.loads(json.dumps(span))
        tampered["locator"]["axis_index"] = True
        with ADAPTER.install_adapter(OLD):
            self.assertFalse(OLD.verify_span(self.store, tampered))
            for offsets in ({"start": True, "end": 4, "quote": "ame"},
                            {"start": 0, "end": True, "quote": "s"}):
                self.assertFalse(OLD.verify_span(self.store, {**span, **offsets}))

    def test_legacy_delegation_and_context_restoration(self):
        self.write(self.page())
        funcs = (OLD.span_container, OLD.verify_span, OLD.rakuten_selector_value_span)
        legacy_span = {"raw_file": self.rel, "sha256": self.store.sha(self.rel),
                       "locator": {"kind": "html_text", "encoding": "utf-8"},
                       "start": 0, "end": 5, "quote": "<html"}
        with ADAPTER.install_adapter(OLD):
            self.assertEqual(OLD.span_container(self.store, legacy_span), self.page())
            self.assertTrue(OLD.verify_span(self.store, legacy_span))
        self.assertEqual((OLD.span_container, OLD.verify_span, OLD.rakuten_selector_value_span), funcs)

    def test_import_does_not_activate_adapter(self):
        before = (OLD.span_container, OLD.verify_span, OLD.rakuten_selector_value_span)
        spec = importlib.util.spec_from_file_location("isolated_candidate_reimport", ROOT /
            "experiments/sku-matching/sku_embedded_json_source_spans_v1.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual((OLD.span_container, OLD.verify_span, OLD.rakuten_selector_value_span), before)


if __name__ == "__main__":
    unittest.main()
