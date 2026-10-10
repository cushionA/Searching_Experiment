import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "sku-matching"))
import import_claude_alignment_handoff_v1 as importer


class ClaudeAlignmentHandoffTests(unittest.TestCase):
    """Artificial contract fixtures stay in tests, separate from real samples."""

    def fixture(self, directory):
        directory.mkdir(exist_ok=True)
        def span(file, digest, quote, locator):
            return {"raw_file": file, "sha256": digest, "quote": quote,
                    "start": 0, "end": len(quote), "locator": locator}
        rkfile, au100, au200 = "rakuten.html", "au100.json", "au200.json"
        registry = {rkfile: "a" * 64, au100: "b" * 64, au200: "c" * 64}
        axes = []
        for index, (label, value, options) in enumerate((
            ("構成", "本棚付き / 天然木", ["本棚付き / 天然木", "本棚なし / 合板"]),
            ("色", "青", ["青", "赤"]),
            ("レース", "なし", ["あり", "なし"]),
        )):
            axes.append({"axis_index": index, "axis_key": f"key{index}", "axis_label": label,
                         "value": value, "family_values": options,
                         "axis_label_span": span(rkfile, registry[rkfile], label, f"label{index}"),
                         "value_span": span(rkfile, registry[rkfile], value, f"value{index}")})
        row = {"row_key": "au:100:0", "axes": [
            {"axis_name": "材質", "value": "天然木", "value_span": span(au100, registry[au100], "天然木", "$.material")},
            {"axis_name": "カラー", "value": "青", "value_span": span(au100, registry[au100], "青", "$.color")},
        ]}
        sku = {"source_sku_key": "https://example.invalid/item/#sku1:0", "sku_record_key": "record1",
               "variant_id": "sku1", "url": "https://example.invalid/item/"}
        case = {"case_id": "case1", "dossier_id": "dossier1", "cohort": "legacy", "au_product_id": "100",
                "rakuten": {**sku, "axes": axes}}
        products = [{"dossier_id": "dossier1", "au": {"product_id": "100", "title": "本棚付き",
                     "title_source": span(au100, registry[au100], "本棚付き", "$.title"), "rows": [row]},
                     "rakuten": {"title_source": span(rkfile, registry[rkfile], "机", "title")}},
                    {"dossier_id": "other", "au": {"product_id": "200", "rows": [{"row_key": "au:200:0", "axes": []}]},
                     "rakuten": {}}]
        def condition(axis, status, au_axis=None):
            value = {"axis_key": axis["axis_key"], "axis_label": axis["axis_label"],
                     "selected_value": axis["value"], "family_values": axis["family_values"],
                     "value_span": axis["value_span"], "status": status}
            if au_axis is not None:
                value.update({"axis": {"au_axis": au_axis["axis_name"]},
                              "mapping": {"option": {"axis_name": au_axis["axis_name"], "value": au_axis["value"]}},
                              "au_value": au_axis["value"], "au_value_span": au_axis["value_span"]})
            return value
        handoff = {"case_id": "case1", "dossier_id": "dossier1", "au_product_id": "100", "au_row_key": row["row_key"],
                   "au_product_source": {"raw_file": au100, "sha256": registry[au100]},
                   "rakuten_sku": {**sku, "raw_file": rkfile, "sha256": registry[rkfile]},
                   "extra_in_value_conditions": [condition(axes[0], "extra_in_value", row["axes"][0])],
                   "aligned_conditions": [condition(axes[1], "aligned", row["axes"][1])],
                   "one_sided_conditions": [condition(axes[2], "one_sided")], "au_only_varying_conditions": []}
        self.write_inputs(directory, case, products, registry)
        archive = directory / "handoff.zip"
        self.write_archive(archive, handoff)
        return archive, case, products, registry, handoff

    @staticmethod
    def write_inputs(directory, case, products, registry):
        for name, rows in (("cases.jsonl", [case]), ("products.jsonl", products)):
            (directory / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        (directory / "manifest.json").write_text(json.dumps({"source_raw_sha256": registry}), encoding="utf-8")

    @staticmethod
    def write_archive(path, handoff, tamper=None):
        payload = {importer.HANDOFF_MEMBERS[0]: (json.dumps(handoff, ensure_ascii=False) + "\n").encode(),
                   importer.HANDOFF_MEMBERS[1]: b""}
        entries = {name: {"size_bytes": len(data), "sha256": importer.sha256(data)} for name, data in payload.items()}
        checkpoint = {"entries": entries, "payload_file_count": len(entries),
                      "uncompressed_payload_bytes": sum(map(len, payload.values())),
                      "label_derived_content": "Artificial fixture; no labels."}
        if tamper is not None:
            payload[importer.HANDOFF_MEMBERS[0]] = tamper
        with ZipFile(path, "w") as zipped:
            for name, data in payload.items():
                zipped.writestr(name, data)
            zipped.writestr(importer.CHECKPOINT_MEMBER, json.dumps(checkpoint))

    @staticmethod
    def rows(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_complete_composite_values_and_alternatives_are_not_consumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive, case, _, _, handoff = self.fixture(directory)
            output = directory / "out"
            digest = importer.sha256(archive.read_bytes())
            manifest = importer.prepare(archive, directory, output, digest)
            cards = self.rows(output / "cards.jsonl")
            composite = next(card for card in cards if card["condition_id"] == "claude-whole-axis:0")
            self.assertEqual(composite["selected_value"], "本棚付き / 天然木")
            self.assertEqual(composite["option_values"], case["rakuten"]["axes"][0]["family_values"])
            self.assertEqual(composite["raw_condition"], case["rakuten"]["axes"][0])
            self.assertEqual(composite["producer_condition"], handoff["extra_in_value_conditions"][0])
            self.assertEqual(composite["source_sku_key"], case["rakuten"]["source_sku_key"])
            self.assertEqual(composite["direction"], "rakuten_to_au")
            self.assertEqual(composite["source_handoff"]["archive_sha256"], digest)
            self.assertEqual(self.rows(output / "handoff_rows.jsonl"), [handoff])
            context = self.rows(output / "upstream_context.jsonl")[0]
            self.assertFalse(context["upstream_alignment_is_proof"])
            self.assertEqual(context["aligned_conditions"][0]["raw_condition"], case["rakuten"]["axes"][1])
            self.assertEqual(manifest["condition_counts"]["covered_rakuten_axes"], 3)
            self.assertEqual(manifest["card_count"], 2)
            self.assertFalse(manifest["legacy_rule_mask_used"])
            self.assertEqual(manifest["final_sku_adoption"], "not_decided")
            reverse = self.rows(output / "reverse_conditions.jsonl")
            self.assertEqual(reverse[0]["selected_value"], "天然木")
            self.assertEqual(reverse[0]["option_values"], ["天然木"])
            self.assertEqual(reverse[0]["raw_condition"]["value"], "天然木")
            self.assertEqual(reverse[0]["parent_condition_ref"]["condition_id"], "claude-whole-axis:0")
            self.assertTrue(manifest["reverse_verification_required"])
            self.assertEqual(composite["reverse_condition_ref"]["line"], 1)
            for name, expected in manifest["output_sha256"].items():
                self.assertEqual(importer.sha256((output / name).read_bytes()), expected)
            with self.assertRaises(FileExistsError):
                importer.prepare(archive, directory, output)

    def test_wrong_sku_and_cross_product_row_bindings_are_rejected(self):
        for field, bad_value in (("source_sku_key", "different-SKU"), ("sku_record_key", "wrong-record"),
                                 ("variant_id", "other-variant"), ("au_row_key", "au:200:0"),
                                 ("au_product_id", "200"), ("dossier_id", "other")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                archive, _, _, _, handoff = self.fixture(directory)
                if field in handoff["rakuten_sku"]:
                    handoff["rakuten_sku"][field] = bad_value
                else:
                    handoff[field] = bad_value
                self.write_archive(archive, handoff)
                with self.assertRaises(ValueError):
                    importer.prepare(archive, directory, directory / "out")
                self.assertFalse((directory / "out").exists())

    def test_missing_duplicate_or_unknown_axes_are_rejected(self):
        for change in ("missing", "duplicate", "unknown"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                archive, _, _, _, handoff = self.fixture(directory)
                if change == "missing":
                    handoff["extra_in_value_conditions"] = []
                elif change == "duplicate":
                    handoff["one_sided_conditions"].append(copy.deepcopy(handoff["aligned_conditions"][0]))
                else:
                    handoff["one_sided_conditions"][0]["axis_key"] = "not-a-case-axis"
                self.write_archive(archive, handoff)
                with self.assertRaises(ValueError):
                    importer.prepare(archive, directory, directory / "out")

    def test_raw_value_options_label_or_span_changes_are_rejected(self):
        for field in ("selected_value", "family_values", "axis_label", "value_span"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                archive, _, _, _, handoff = self.fixture(directory)
                condition = handoff["extra_in_value_conditions"][0]
                condition[field] = {"wrong": "span"} if field == "value_span" else ["new"] if field == "family_values" else "different"
                self.write_archive(archive, handoff)
                with self.assertRaises(ValueError):
                    importer.prepare(archive, directory, directory / "out")

    def test_zip_manifest_tamper_external_hash_and_crc_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive, _, _, _, handoff = self.fixture(directory)
            with self.assertRaises(ValueError):
                importer.read_archive(archive, "0" * 64)
            self.write_archive(archive, handoff, tamper=b"tampered\n")
            with self.assertRaises(ValueError):
                importer.read_archive(archive)
            self.write_archive(archive, handoff)
            blob = bytearray(archive.read_bytes())
            filename_length, extra_length = struct.unpack_from("<HH", blob, 26)
            blob[30 + filename_length + extra_length] ^= 1
            archive.write_bytes(blob)
            with self.assertRaises(BadZipFile):
                importer.read_archive(archive)

    def test_au_only_reverse_conditions_are_separate_from_rakuten_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive, case, products, registry, handoff = self.fixture(directory)
            au_axis = {"axis_name": "追加加工", "value": "あり",
                       "axis_name_span": {"raw_file": "au100.json", "sha256": registry["au100.json"], "quote": "追加加工", "start": 0, "end": 4, "locator": "$.process.name"},
                       "value_span": {"raw_file": "au100.json", "sha256": registry["au100.json"], "quote": "あり", "start": 0, "end": 2, "locator": "$.process[0]"}}
            products[0]["au"]["rows"][0]["axes"].append(au_axis)
            products[0]["au"]["rows"].append({"row_key": "au:100:1", "axes": [{"axis_name": "追加加工", "value": "なし"}]})
            self.write_inputs(directory, case, products, registry)
            handoff["au_only_varying_conditions"] = [{"axis_name": au_axis["axis_name"], "value": au_axis["value"], "value_span": au_axis["value_span"]}]
            self.write_archive(archive, handoff)
            manifest = importer.prepare(archive, directory, directory / "out")
            cards = self.rows(directory / "out/cards.jsonl")
            reverse = self.rows(directory / "out/reverse_conditions.jsonl")
            self.assertEqual(len(cards), 2)
            explicit = next(r for r in reverse if r["origin"] == "au_only_varying_conditions")
            self.assertEqual(explicit["producer_condition"], handoff["au_only_varying_conditions"][0])
            self.assertEqual(explicit["direction"], "au_to_rakuten")
            self.assertEqual(explicit["selected_value"], "あり")
            self.assertEqual(explicit["option_values"], ["あり", "なし"])
            self.assertEqual(explicit["raw_condition"], au_axis)
            self.assertEqual(explicit["source_refs"], [au_axis["axis_name_span"], au_axis["value_span"]])
            self.assertEqual(manifest["reverse_condition_count"], 2)
            self.assertTrue(all(card["direction"] == "rakuten_to_au" for card in cards))

    def test_au_only_missing_axis_wrong_value_or_span_are_rejected(self):
        for change in ("missing_axis", "wrong_value", "missing_span", "wrong_span", "wrong_registry", "duplicate"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                archive, case, products, registry, handoff = self.fixture(directory)
                au_axis = products[0]["au"]["rows"][0]["axes"][0]
                condition = {"axis_name": au_axis["axis_name"], "value": au_axis["value"], "value_span": copy.deepcopy(au_axis["value_span"])}
                if change == "missing_axis":
                    condition["axis_name"] = "追加加工"
                elif change == "wrong_value":
                    condition["value"] = "存在しない選択値"
                elif change == "missing_span":
                    condition.pop("value_span")
                elif change == "wrong_span":
                    condition["value_span"]["start"] = 1
                elif change == "wrong_registry":
                    # Equal row and producer refs must still match the frozen registry.
                    condition["value_span"]["sha256"] = "d" * 64
                    au_axis["value_span"]["sha256"] = "d" * 64
                    handoff["extra_in_value_conditions"][0]["au_value_span"] = copy.deepcopy(au_axis["value_span"])
                    self.write_inputs(directory, case, products, registry)
                handoff["au_only_varying_conditions"] = [condition]
                if change == "duplicate":
                    handoff["au_only_varying_conditions"].append(copy.deepcopy(condition))
                self.write_archive(archive, handoff)
                with self.assertRaises(ValueError):
                    importer.prepare(archive, directory, directory / "out")
                self.assertFalse((directory / "out").exists())

    def test_future_unresolved_group_and_missing_title_span_keep_raw_source_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive, case, products, registry, handoff = self.fixture(directory)
            products[0]["rakuten"]["title_source"] = None
            self.write_inputs(directory, case, products, registry)
            handoff["unresolved_conditions"] = handoff.pop("one_sided_conditions")
            self.write_archive(archive, handoff)
            manifest = importer.prepare(archive, directory, directory / "out", validate_only=True)
            self.assertEqual(manifest["condition_counts"]["unresolved_conditions"], 1)
            self.assertEqual(manifest["card_count"], 2)
            self.assertFalse((directory / "out").exists())

    def test_mapped_value_must_belong_to_the_fixed_au_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive, _, _, _, handoff = self.fixture(directory)
            mapped = handoff["aligned_conditions"][0]
            mapped["au_value"] = "赤"
            mapped["mapping"]["option"]["value"] = "赤"
            self.write_archive(archive, handoff)
            with self.assertRaises(ValueError):
                importer.prepare(archive, directory, directory / "out")

    def test_au_extra_value_is_preserved_with_all_au_axis_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive, case, products, registry, handoff = self.fixture(directory)
            axis = case["rakuten"]["axes"][0]
            axis["value"] = "ホワイトグレージュ"
            axis["family_values"] = ["ホワイトグレージュ", "グレー"]
            axis["value_span"]["quote"] = axis["value"]
            axis["value_span"]["end"] = len(axis["value"])
            au_axis = products[0]["au"]["rows"][0]["axes"][0]
            au_axis.update({"axis_name": "生地カラー", "value": "ホワイトグレージュ（パイル）"})
            au_axis["value_span"]["quote"] = au_axis["value"]
            au_axis["value_span"]["end"] = len(au_axis["value"])
            products[0]["au"]["rows"].append({"row_key": "au:100:1", "axes": [{"axis_name": "生地カラー", "value": "グレー（メッシュ）"}]})
            condition = handoff["extra_in_value_conditions"][0]
            condition.update({"selected_value": axis["value"], "family_values": axis["family_values"], "value_span": axis["value_span"],
                              "axis": {"au_axis": "生地カラー"}, "mapping": {"option": {"axis_name": "生地カラー", "value": au_axis["value"]}},
                              "au_value": au_axis["value"], "au_value_span": au_axis["value_span"]})
            self.write_inputs(directory, case, products, registry)
            self.write_archive(archive, handoff)
            importer.prepare(archive, directory, directory / "out")
            reverse = self.rows(directory / "out/reverse_conditions.jsonl")[0]
            self.assertEqual(reverse["selected_value"], "ホワイトグレージュ（パイル）")
            self.assertEqual(reverse["raw_condition"], au_axis)
            self.assertEqual(reverse["option_values"], ["ホワイトグレージュ（パイル）", "グレー（メッシュ）"])
            self.assertTrue(reverse["verification_required"])


if __name__ == "__main__":
    unittest.main()
