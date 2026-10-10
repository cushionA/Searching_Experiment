"""Artificial importer contract fixtures, kept separate from real SKU samples."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/sku-matching"))
import import_claude_alignment_handoff_v2 as importer
import prepare_generic_residual_evidence_v1 as evidence


class ClaudeFlatHandoffV2Tests(unittest.TestCase):
    def fixture(self, root):
        registry = {"rak.html": "a" * 64, "au100.json": "b" * 64}
        def span(file, text, locator):
            return {"raw_file": file, "sha256": registry[file], "quote": text,
                    "start": 0, "end": len(text), "locator": locator}
        axes = [{"axis_key": key, "axis_index": index, "axis_label": label, "value": value,
                 "family_values": options, "axis_label_span": span("rak.html", label, f"label{index}"),
                 "value_span": span("rak.html", value, f"value{index}")}
                for index, (key, label, value, options) in enumerate((
                    ("kind", "構成", "本棚付き / 天然木", ["本棚付き / 天然木", "本棚なし / 合板"]),
                    ("color", "カラー", "青", ["青", "赤"]),
                    ("lace", "レース", "なし", ["あり", "なし"])))]
        row = {"row_key": "au:100:0", "axes": [
            {"axis_name": name, "value": value, "axis_name_span": span("au100.json", name, f"name{index}"),
             "value_span": span("au100.json", value, f"auValue{index}")}
            for index, (name, value) in enumerate((("材質", "天然木（無塗装）"), ("色", "青")))]}
        sku = {"source_sku_key": "https://example.invalid/item/#sku1:0", "sku_record_key": "record1",
               "variant_id": "sku1", "url": "https://example.invalid/item/"}
        case = {"case_id": "case1", "cohort": "legacy", "dossier_id": "dossier1", "au_product_id": "100",
                "rakuten": {**sku, "axes": axes}}
        product = {"dossier_id": "dossier1", "au": {"product_id": "100", "title": "本棚付き", "descriptions": [],
                   "title_source": span("au100.json", "本棚付き", "$.title"), "rows": [row]},
                   "rakuten": {"title_source": span("rak.html", "机", "title")}}
        conditions = []
        for axis, status, mapped in zip(axes, ("extra_in_value", "aligned", "one_sided"), row["axes"] + [None]):
            conditions.append({"kind": "rakuten", "axis_key": axis["axis_key"], "axis_label": axis["axis_label"],
                               "value": axis["value"], "au_axis": mapped["axis_name"] if mapped else None,
                               "au_value": mapped["value"] if mapped else float("nan"), "status": status})
        handoff = {"case_id": case["case_id"], "dossier_id": case["dossier_id"], "au_product_id": "100",
                   "au_row_key": row["row_key"], "au_product_source": {"raw_file": "au100.json", "sha256": registry["au100.json"]},
                   "rakuten_sku": {**sku, "raw_file": "rak.html", "sha256": registry["rak.html"]}, "conditions": conditions,
                   "rakuten_axes": {axis["axis_key"]: {field: axis[field] for field in ("family_values", "value_span")} for axis in axes}}
        self.write_inputs(root, case, product, registry)
        archive = root / "handoff.zip"
        self.write_archive(archive, handoff)
        return archive, case, product, registry, handoff

    @staticmethod
    def write_inputs(root, case, product, registry):
        data = {name: (json.dumps(row, ensure_ascii=False) + "\n").encode()
                for name, row in (("cases.jsonl", case), ("products.jsonl", product))}
        for name, body in data.items():
            (root / name).write_bytes(body)
        (root / "manifest.json").write_text(json.dumps({"source_raw_sha256": registry,
            "output_sha256": {name: importer.sha256(body) for name, body in data.items()}}))

    @staticmethod
    def write_archive(path, handoff, extra_members=None, tamper=None, omitted_cases=(), run=importer.RUN):
        payload = {}
        members = tuple(f"{run}/{cohort}/align/handoff.jsonl" for cohort in importer.COHORTS)
        for cohort, member in zip(importer.COHORTS, members):
            parent = str(Path(member).parent)
            payload[member] = (json.dumps(handoff, ensure_ascii=False) + "\n").encode() if cohort == "legacy" else b""
            decision = {"case_id": "case1", "dossier_id": "dossier1", "reason": "pending_description_check"}
            decisions = [decision] + [{"case_id": identity, "dossier_id": "dossier1",
                                       "reason": "symmetric_condition_unresolved"} for identity in omitted_cases]
            payload[parent + "/decisions.jsonl"] = b"".join((json.dumps(row) + "\n").encode() for row in decisions) if cohort == "legacy" else b""
            sim = f"{run}/{cohort}/similarities.jsonl"
            payload[sim] = b""
            metadata = {"inputs_sha256": {name: "e" * 64 for name in ("cases.jsonl", "products.jsonl")},
                        "similarities_sha256": importer.sha256(payload[sim])}
            payload[f"{run}/{cohort}/similarities.json"] = json.dumps(metadata).encode()
            payload[parent + "/summary.json"] = json.dumps({"outputs_sha256": {
                "handoff.jsonl": importer.sha256(payload[member]),
                "decisions.jsonl": importer.sha256(payload[parent + "/decisions.jsonl"])},
                "similarities_sha256": metadata["similarities_sha256"],
                "handoff_rows": 1 if cohort == "legacy" else 0}).encode()
        if extra_members:
            payload.update(extra_members)
        checkpoint = {"entries": {name: {"size_bytes": len(body), "sha256": importer.sha256(body)} for name, body in payload.items()},
                      "payload_file_count": len(payload), "uncompressed_payload_bytes": sum(map(len, payload.values()))}
        if tamper:
            payload[members[0]] = tamper
        with ZipFile(path, "w") as zipped:
            for name, body in payload.items():
                zipped.writestr(name, body)
            zipped.writestr(importer.CHECKPOINT_MEMBER, json.dumps(checkpoint))

    @staticmethod
    def rows(path):
        return [json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                for line in path.read_text().splitlines()]

    def run_import(self, archive, root, output="out", **kwargs):
        return importer.prepare(archive, root, root / output, expected_sha256=importer.sha256(archive.read_bytes()), **kwargs)

    def test_full_composites_bidirectional_and_nan_are_preserved_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, case, product, _, handoff = self.fixture(root)
            manifest = self.run_import(archive, root)
            cards = self.rows(root / "out/cards.jsonl")
            reverse = self.rows(root / "out/reverse_conditions.jsonl")
            context = self.rows(root / "out/upstream_context.jsonl")
            self.assertEqual(cards[0]["selected_value"], "本棚付き / 天然木")
            self.assertEqual(cards[0]["option_values"], case["rakuten"]["axes"][0]["family_values"])
            self.assertEqual(cards[0]["raw_condition"], case["rakuten"]["axes"][0])
            self.assertEqual(reverse[0]["selected_value"], "天然木（無塗装）")
            self.assertEqual(reverse[0]["direction"], "au_to_rakuten")
            self.assertFalse(reverse[0]["automatic_adoption_allowed"])
            self.assertFalse(context[0]["upstream_alignment_is_proof"])
            self.assertIsNone(cards[1]["producer_condition"]["au_value"])
            self.assertTrue(cards[1]["producer_condition"]["producer_missing_au_value_was_nan"])
            original = (root / "out/original_handoff.jsonl").read_bytes()
            with ZipFile(archive) as zipped:
                self.assertEqual(original, b"".join(zipped.read(member) for member in importer.HANDOFF_MEMBERS))
            self.assertIn(b"NaN", original)
            self.assertEqual(evidence.validate([case], [product], cards)[0]["case1"], case)
            for name, digest in manifest["output_sha256"].items():
                self.assertEqual(importer.sha256((root / "out" / name).read_bytes()), digest)
            self.assertFalse(manifest["producer_input_bytes_reverified"])

    def test_au_only_goes_to_reverse_not_au_premise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, case, product, registry, handoff = self.fixture(root)
            axis = copy.deepcopy(product["au"]["rows"][0]["axes"][0])
            axis.update({"axis_name": "追加加工", "value": "あり"})
            product["au"]["rows"][0]["axes"].append(axis)
            self.write_inputs(root, case, product, registry)
            handoff["conditions"].append({"kind": "au_only", "axis_key": None, "axis_label": "追加加工", "value": None,
                                          "au_axis": "追加加工", "au_value": "あり", "status": "one_sided"})
            self.write_archive(archive, handoff)
            result = self.run_import(archive, root)
            self.assertEqual(result["card_count"], 2)
            self.assertEqual(result["reverse_condition_count"], 2)
            reverse = self.rows(root / "out/reverse_conditions.jsonl")[-1]
            self.assertEqual(reverse["origin"], "au_only")
            self.assertEqual(reverse["selected_value"], "あり")
            self.assertEqual(reverse["raw_condition"], axis)

    def test_v3_method_provenance_survives_without_becoming_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, _, _, _, handoff = self.fixture(root)
            for condition, method in zip(handoff["conditions"], ("model", "identical", None)):
                condition["matched_by"] = method
            run = importer.PINNED_RUNS[importer.UPSTREAM_V3_HEAD][0]
            self.write_archive(archive, handoff, run=run)
            manifest = self.run_import(archive, root, expected_head=importer.UPSTREAM_V3_HEAD)
            cards = self.rows(root / "out/cards.jsonl")
            context = self.rows(root / "out/upstream_context.jsonl")[0]
            reverse = self.rows(root / "out/reverse_conditions.jsonl")[0]
            self.assertEqual(manifest["upstream_run"], run)
            self.assertEqual(cards[0]["producer_condition"]["matched_by"], "model")
            self.assertEqual(reverse["producer_condition"]["matched_by"], "model")
            self.assertEqual(context["aligned_conditions"][0]["producer_condition"]["matched_by"], "identical")
            self.assertFalse(context["upstream_alignment_is_proof"])
            self.assertFalse(reverse["automatic_adoption_allowed"])
            with self.assertRaises(ValueError):
                self.run_import(archive, root, output="wrong-version")
            handoff["conditions"][1]["matched_by"] = "human_confirmed"
            self.write_archive(archive, handoff, run=run)
            with self.assertRaises(ValueError):
                self.run_import(archive, root, output="invalid-method", expected_head=importer.UPSTREAM_V3_HEAD)

    def test_invalid_fixed_bindings_options_spans_and_condition_states_are_rejected(self):
        for change in ("row", "fixed_product", "sku_url", "raw_source", "span", "option_values", "whole_value",
                       "duplicate_axis", "missing_axis", "contradiction", "symmetric_unresolved", "unknown_kind", "mapped_value"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive, _, _, _, handoff = self.fixture(root)
                if change == "row": handoff["au_row_key"] = "au:200:0"
                elif change == "fixed_product": handoff["au_product_id"] = "200"
                elif change == "sku_url": handoff["rakuten_sku"]["url"] = "https://example.invalid/other/"
                elif change == "raw_source": handoff["au_product_source"]["sha256"] = "d" * 64
                elif change == "span": handoff["rakuten_axes"]["kind"]["value_span"]["start"] = 1
                elif change == "option_values": handoff["rakuten_axes"]["kind"]["family_values"].pop()
                elif change == "whole_value": handoff["conditions"][0]["value"] = "天然木"
                elif change == "duplicate_axis": handoff["conditions"].append(copy.deepcopy(handoff["conditions"][0]))
                elif change == "missing_axis": handoff["conditions"].pop(0)
                elif change in ("contradiction", "symmetric_unresolved"): handoff["conditions"][0]["status"] = change
                elif change == "unknown_kind": handoff["conditions"][0]["kind"] = "feature"
                elif change == "mapped_value": handoff["conditions"][0]["au_value"] = "合板"
                self.write_archive(archive, handoff)
                with self.assertRaises(ValueError): self.run_import(archive, root)
                self.assertFalse((root / "out").exists())

    def test_nonfinite_only_allowed_for_unmapped_one_sided_absence(self):
        for target, value in ((0, float("nan")), (2, float("inf")), (2, float("-inf"))):
            with self.subTest(target=target, value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive, _, _, _, handoff = self.fixture(root)
                handoff["conditions"][target]["au_value"] = value
                self.write_archive(archive, handoff)
                with self.assertRaises(ValueError): self.run_import(archive, root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, _, _, _, handoff = self.fixture(root)
            handoff["unrelated_number"] = float("nan")
            self.write_archive(archive, handoff)
            with self.assertRaises(ValueError): self.run_import(archive, root)

    def test_omitted_symmetric_case_is_recorded_without_recovery_or_adoption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, case, _, _, handoff = self.fixture(root)
            omitted = {**case, "case_id": "omitted-case"}
            body = b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode() for row in (case, omitted))
            (root / "cases.jsonl").write_bytes(body)
            manifest = json.loads((root / "manifest.json").read_text())
            manifest["output_sha256"]["cases.jsonl"] = importer.sha256(body)
            (root / "manifest.json").write_text(json.dumps(manifest))
            self.write_archive(archive, handoff, omitted_cases=("omitted-case",))
            result = self.run_import(archive, root)
            self.assertEqual(result["unprocessed_upstream_case_count"], 1)
            group = result["unprocessed_upstream_groups"]["symmetric_condition_unresolved"]["legacy"]
            self.assertEqual(group["case_ids"], ["omitted-case"])
            self.assertFalse(group["processed_by_this_importer"])
            self.assertEqual(result["handoff_case_count"], 1)
            self.assertEqual(result["final_sku_adoption"], "not_decided")

    def test_external_sha_head_and_frozen_input_hash_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, _, _, _, _ = self.fixture(root)
            with self.assertRaises(ValueError): importer.prepare(archive, root, root / "out", expected_sha256="0" * 64)
            with self.assertRaises(ValueError): self.run_import(archive, root, expected_head="0" * 40)
            (root / "cases.jsonl").write_bytes((root / "cases.jsonl").read_bytes() + b"\n")
            with self.assertRaises(ValueError): self.run_import(archive, root)

    def test_archive_checks_all_payload_even_unused_member_and_crc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, _, _, _, handoff = self.fixture(root)
            self.write_archive(archive, handoff, extra_members={"unused.txt": b"unused-source-facts"}, tamper=b"wrong\n")
            with self.assertRaises(ValueError): self.run_import(archive, root)
            self.write_archive(archive, handoff, extra_members={"unused.txt": b"unused-source-facts"})
            body = bytearray(archive.read_bytes())
            with ZipFile(archive) as zipped:
                info = zipped.getinfo("unused.txt")
                name_length, extra_length = struct.unpack_from("<HH", body, info.header_offset + 26)
                body[info.header_offset + 30 + name_length + extra_length] ^= 1
            archive.write_bytes(body)
            with self.assertRaises(BadZipFile): self.run_import(archive, root)

    def test_validation_only_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, _, _, _, _ = self.fixture(root)
            result = self.run_import(archive, root, validate_only=True)
            self.assertEqual(result["card_count"], 2)
            self.assertFalse((root / "out").exists())
            self.run_import(archive, root)
            with self.assertRaises(FileExistsError): self.run_import(archive, root)


if __name__ == "__main__":
    unittest.main()
