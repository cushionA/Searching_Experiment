import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


MODULE_PATH = Path(__file__).resolve().parents[1] / "experiments/sku-matching/sku_luna_inputs.py"
spec = importlib.util.spec_from_file_location("sku_luna_inputs", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
ROOT = mod.INPUT_ROOT


def span(raw_file, data, text, *, start=0, raw_hash=None):
    return {
        "raw_file": raw_file,
        "sha256": raw_hash or hashlib.sha256(data).hexdigest(),
        "start": start,
        "end": start + len(text),
        "quote": text,
        "locator": {"kind": "html_text", "encoding": "utf-8"},
    }


def write_checkpoint(path, cases, products, raw_members):
    with ZipFile(path, "w") as zf:
        zf.writestr(ROOT + "cases.jsonl", "".join(json.dumps(x) + "\n" for x in cases))
        zf.writestr(ROOT + "products.jsonl", "".join(json.dumps(x) + "\n" for x in products))
        for name, data in raw_members.items():
            zf.writestr(name, data)


def sample_case(case_id="c1", *, dossier="d1", au_id="au1", url="https://rak.test/p1",
                cohort="novel", sku="sku1", raw="raw/r1.html", axes=None, attrs=None):
    return {
        "case_id": case_id, "dossier_id": dossier, "au_product_id": au_id, "cohort": cohort,
        "rakuten": {"url": url, "variant_id": "v1", "source_sku_key": sku,
                    "axes": axes if axes is not None else [],
                    "variant_attributes": attrs if attrs is not None else []},
    }


def sample_product(dossier="d1", au_id="au1", *, rows=None, sku_id="grid-42"):
    return {"dossier_id": dossier, "au": {"product_id": au_id, "title": "AU title",
            "rows": rows if rows is not None else [
                {"row_key": "r0", "sku_id": sku_id, "row_index": 3, "column_index": 4,
                 "axes": []}]}}


class LunaInputsTests(unittest.TestCase):
    def test_attributes_require_exact_verification_and_keep_rejection_metadata(self):
        raw = "Color: Blue; Size: Large".encode()
        good = span("raw/r1.html", raw, "Blue", start=7)
        wrong_offset = span("raw/r1.html", raw, "Large", start=7)
        wrong_quote = dict(span("raw/r1.html", raw, "Blue", start=7), quote="Red")
        case = sample_case(axes=[{"axis_label": "Color", "value": "Blue", "value_span": span("raw/r1.html", raw, "Blue", start=7)}], attrs=[
            {"title": "Color", "value": "Blue", "unit": None, "value_span": good},
            {"title": "Size", "value": "Large", "unit": "cm", "value_span": wrong_offset},
            {"title": "Finish", "value": "Matte", "unit": "type", "value_span": wrong_quote},
            {"title": "Pack", "value": "2", "unit": "pcs"},
        ])
        path = Path(tempfile.mktemp(suffix=".zip"))
        try:
            write_checkpoint(path, [case], [sample_product()], {"raw/r1.html": raw})
            loaded, _ = mod.load_cases(path, ["c1"])
            self.assertEqual([(x["axis"], x["value"]) for x in loaded[0]["selected_attributes"]], [("Color", "Blue")])
            rejected = loaded[0]["selected_attributes_rejected"]
            self.assertEqual([(x["axis"], x["value"], x["unit"]) for x in rejected],
                             [("Size", "Large", "cm"), ("Finish", "Matte", "type"), ("Pack", "2", "pcs")])
            self.assertTrue(all(x["reason"] == "missing_or_unverified_source_span" for x in rejected))
            self.assertEqual(rejected[0]["source"]["quote"], "Large")
        finally:
            path.unlink(missing_ok=True)

    def test_bad_rakuten_axis_hash_fails_closed(self):
        raw = b"Blue"
        bad = span("raw/r1.html", raw, "Blue", raw_hash="0" * 64)
        case = sample_case(axes=[{"axis_label": "Color", "value": "Blue", "value_span": bad}])
        path = Path(tempfile.mktemp(suffix=".zip"))
        try:
            write_checkpoint(path, [case], [sample_product()], {"raw/r1.html": raw})
            with self.assertRaisesRegex(ValueError, "unverified selected Rakuten axis span"):
                mod.load_cases(path, ["c1"])
        finally:
            path.unlink(missing_ok=True)

    def test_real_sku_grid_id_and_coordinates_are_preserved(self):
        raw = b"Red"
        axis_span = span("raw/au.html", raw, "Red")
        product = sample_product(rows=[{"row_key": "row-z", "sku_id": "real-sku-918",
            "row_index": 8, "column_index": 2,
            "axes": [{"axis_name": "Color", "value": "Red", "value_span": axis_span}]}])
        path = Path(tempfile.mktemp(suffix=".zip"))
        try:
            case = sample_case(axes=[{"axis_label": "Color", "value": "Red", "value_span": span("raw/au.html", raw, "Red")}])
            write_checkpoint(path, [case], [product], {"raw/au.html": raw})
            loaded, _ = mod.load_cases(path, ["c1"])
            row = loaded[0]["au_rows"][0]
            self.assertEqual((row["row_key"], row["sku_id"], row["row_index"], row["column_index"]),
                             ("row-z", "real-sku-918", 8, 2))
        finally:
            path.unlink(missing_ok=True)

    def test_holdout_is_deterministic_excludes_dev_family_and_duplicates(self):
        raw = b"Size source"
        development = sample_case("dev", au_id="dev-au", url="https://rak.test/dev", raw="raw/doc-dev.html")
        cases = [development,
                 sample_case("same-au", au_id="dev-au", url="https://rak.test/a"),
                 sample_case("same-url", au_id="other-au", url="https://rak.test/dev"),
                 sample_case("family", au_id="family-au", cohort="other", url="https://rak.test/f"),
                 sample_case("eligible-a", dossier="ea", au_id="ea", url="https://rak.test/a", sku="sku-a"),
                 sample_case("eligible-b", dossier="eb", au_id="eb", url="https://rak.test/b", sku="sku-b", raw="raw/doc-b.html"),
                 sample_case("dev-document", dossier="df", au_id="df", url="https://rak.test/f", sku="sku-f", raw="raw/doc-dev.html"),
                 sample_case("duplicate-url", dossier="ec", au_id="ec", url="https://rak.test/a", sku="sku-c"),
                 sample_case("duplicate-au", dossier="ed", au_id="ea", url="https://rak.test/d", sku="sku-d"),
                 sample_case("duplicate-sku", dossier="ee", au_id="ee", url="https://rak.test/e", sku="sku-a")]
        products = [sample_product("d1", "dev-au"), sample_product("d1", "dev-au")]
        # Use unique dossiers/products for every non-development case.
        products = [sample_product(c["dossier_id"], c["au_product_id"], rows=[
            {"row_key": "r0", "sku_id": "sku", "row_index": 0, "column_index": 0, "axes": []},
            {"row_key": "r1", "sku_id": "sku", "row_index": 1, "column_index": 0, "axes": []}]) for c in cases]
        for c in cases:
            raw_name = c["rakuten"].pop("raw", "raw/shared.html")
            c["rakuten"]["axes"] = [{"axis_label": "Size", "value": "source", "value_span": span(raw_name, raw, "Size source")}]
        path = Path(tempfile.mktemp(suffix=".zip"))
        try:
            write_checkpoint(path, cases, products, {"raw/shared.html": raw, "raw/doc-b.html": raw, "raw/doc-dev.html": raw})
            one, meta1 = mod.choose_holdout(path, ["dev"], count=8)
            two, meta2 = mod.choose_holdout(path, ["dev"], count=8)
            ids = [x["case_id"] for x in one]
            self.assertEqual(ids, [x["case_id"] for x in two])
            self.assertEqual(meta1["selected_case_ids"], meta2["selected_case_ids"])
            self.assertTrue(set(ids).issubset({"eligible-a", "eligible-b", "duplicate-url", "duplicate-au", "duplicate-sku"}))
            self.assertNotIn("dev-document", ids)
            self.assertNotIn("raw/doc-dev.html", meta1["selected_source_documents"])
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(meta1["selected_count"], len(ids))
            # Distinct documents are preferred when eligible.
            self.assertEqual(meta1["unique_rakuten_source_documents"], len(ids))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
