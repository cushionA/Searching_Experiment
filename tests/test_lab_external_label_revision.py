import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "sku-matching"))
import revise_labels_from_external_audit as revision


class ExternalLabelRevisionTests(unittest.TestCase):
    def test_quote_is_found_in_embedded_rakuten_json_with_whitespace_variation(self):
        raw = (b'<script id="item-page-app-data">'
               b'{"api":{"data":{"itemInfoSku":{"description":"width 50cm\\ncolor black"}}}}'
               b'</script>')
        self.assertTrue(revision.raw_contains_quote(raw, "width 50cm color black"))
        self.assertFalse(revision.raw_contains_quote(raw, "width 55cm color black"))

    def test_quote_is_found_in_euc_jp_html(self):
        raw = "<td>持ち手：ポリエステル</td>".encode("euc_jp")
        self.assertTrue(revision.raw_contains_quote(raw, "持ち手：ポリエステル"))

    def test_evidence_must_bind_to_case_dossier_raw_hash_and_verbatim_quote(self):
        raw = json.dumps({"itemInfo": {"extraItemComment": "AU width is 66 cm"}}).encode()
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "au.json"
            raw_path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            case = {"case_id": "case-a", "dossier_id": "d-a"}
            dossier = {"au_product": {"source": {"raw_file": str(raw_path), "sha256": digest}}}
            evidence = {"side": "au", "dossier_id": "d-a", "raw_file": str(raw_path),
                        "raw_sha256": digest, "quote": "AU width is 66 cm", "source_kind": "description"}
            self.assertEqual(revision.verify_audit_evidence(evidence, case, dossier)["quote"],
                             "AU width is 66 cm")
            mismatched = dict(evidence, dossier_id="d-other")
            with self.assertRaisesRegex(ValueError, "not bound"):
                revision.verify_audit_evidence(mismatched, case, dossier)
            absent_quote = dict(evidence, quote="AU width is 50 cm")
            with self.assertRaisesRegex(ValueError, "not found"):
                revision.verify_audit_evidence(absent_quote, case, dossier)
            bad_hash = dict(evidence, raw_sha256="0" * 64)
            with self.assertRaisesRegex(ValueError, "differs from case dossier"):
                revision.verify_audit_evidence(bad_hash, case, dossier)

    def test_au_title_reference_fix_resolves_real_raw_path_and_quote(self):
        raw = json.dumps({"itemInfo": {"itemTitle": "Verified title for case"}}).encode()
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "au.json"
            raw_path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            case = {"case_id": "case-title", "dossier_id": "d-title"}
            dossier = {"au_product": {"source": {"raw_file": str(raw_path), "sha256": digest}}}
            label = {"evidence": [{"source": "au_title", "source_ref": "$.itemInfo.itemName",
                                   "quote": "Verified title"}]}
            fix = {"evidence_index": 0, "source": "au_title", "old_source_ref": "$.itemInfo.itemName",
                   "proposed_source_ref": "$.itemInfo.itemTitle", "quote": "Verified title"}
            revision.verify_title_reference_fix(fix, case, dossier, label)
            fix["quote"] = "not in title"
            with self.assertRaisesRegex(ValueError, "does not match label evidence"):
                revision.verify_title_reference_fix(fix, case, dossier, label)

    def test_masked_title_citation_rebind_requires_quote_in_raw_title(self):
        raw_title = "【10%OFFクーポン配布中】商品タイトル".encode("euc_jp")
        raw_html = b"<title>" + raw_title + b"</title>"
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "rakuten.html"
            raw_path.write_bytes(raw_html)
            digest = hashlib.sha256(raw_html).hexdigest()
            case = {"case_id": "case-raku", "dossier_id": "d-raku"}
            dossier = {"dossier_id": "d-raku", "rakuten_product": {
                "title_raw": "【10%OFF[販売案内]中】商品タイトル",
                "title_evidence_raw": {"text": "【10%OFFクーポン配布中】商品タイトル",
                                       "use": "audit_only_not_semantic_input"},
                "source": {"raw_file": str(raw_path), "sha256": digest}}}
            label = {"case_id": "case-raku", "evidence": [{"source": "rakuten_title",
                "source_ref": "products.jsonl[manage_number=item]", "quote": "クーポン配布中"}]}
            rows = {"case-raku": label}
            ledger = []
            count = revision.rebind_masked_title_citations(
                rows, {"case-raku": case}, {"d-raku": dossier}, ledger, "a" * 64)
            self.assertEqual(count, 1)
            self.assertEqual(label["evidence"][0]["source_ref"],
                "dossiers/d-raku.json#$.rakuten_product.title_evidence_raw.text")
            self.assertEqual(ledger[0]["raw_sha256"], digest)

            label["evidence"][0]["quote"] = "not in captured title"
            count = revision.rebind_masked_title_citations(
                rows, {"case-raku": case}, {"d-raku": dossier}, [], "a" * 64)
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
