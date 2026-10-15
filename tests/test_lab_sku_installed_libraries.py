"""Source-preserving tests for direct installed-library SKU views and ranking."""
from __future__ import annotations

import ast
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE, *HERE.parents)
            if (parent / "experiments/sku-matching").is_dir())
EXPERIMENTS = ROOT / "experiments/sku-matching"
sys.path.insert(0, str(EXPERIMENTS))

try:
    import installed_library_views_v1 as library_views
    import jaconv
    import spacy
    from sudachipy import dictionary, tokenizer
except ModuleNotFoundError as error:
    if error.name in {"jaconv", "spacy", "sudachipy"}:
        library_views = None
    else:
        raise

try:
    from rapidfuzz import fuzz
except ModuleNotFoundError as error:
    if error.name == "rapidfuzz":
        fuzz = None
    else:
        raise

try:
    import run_installed_library_ranking_v1 as ranker
except ModuleNotFoundError as error:
    if error.name in {"run_installed_library_ranking_v1", "rapidfuzz",
                      "jaconv", "spacy", "sudachipy"}:
        ranker = None
    else:
        raise

try:
    import run_installed_library_selection_v1 as selection
except ModuleNotFoundError as error:
    if error.name in {"run_installed_library_selection_v1", "rapidfuzz"}:
        selection = None
    else:
        raise

try:
    import prepare_library_price_free_views_v1 as price_free
    from quantulum3 import parser as quantulum_parser
except ModuleNotFoundError as error:
    if error.name in {"prepare_library_price_free_views_v1", "quantulum3"}:
        price_free = None
        quantulum_parser = None
    else:
        raise

try:
    import probe_library_residual_text_v1 as residual_probe
except ModuleNotFoundError as error:
    if error.name in {"probe_library_residual_text_v1", "spacy"}:
        residual_probe = None
    else:
        raise


@unittest.skipIf(library_views is None, "install jaconv, SudachiPy and spaCy in the dedicated venv")
class InstalledLibraryViewTests(unittest.TestCase):
    def test_adapter_uses_installed_libraries_without_importing_custom_sku_engines(self):
        paths = [EXPERIMENTS / "installed_library_views_v1.py",
                 EXPERIMENTS / "run_installed_library_ranking_v1.py"]
        imports = []
        for path in paths:
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
        self.assertTrue(any(name == "jaconv" for name in imports))
        self.assertTrue(any(name == "spacy" for name in imports))
        self.assertTrue(any(name.startswith("sudachipy") for name in imports))
        self.assertTrue(any(name.startswith("rapidfuzz") for name in imports))
        self.assertFalse(any(name.startswith(("sku_classical_quantity", "sku_classical_matcher"))
                             for name in imports))

    def test_all_original_values_survive_each_view_and_width_conversion_is_only_a_view(self):
        values = ["１００cm", "100cm", "幅１００cm", "青✨", "250g×2袋", "１００cm"]
        result = library_views.build_views(values)
        self.assertEqual(result["input_count"], len(values))
        self.assertEqual(result["unique_value_count"], len(set(values)))
        self.assertEqual(result["trained_components"], [])
        for mode in ("raw", "width_jaconv", "sudachi_norm", "spacy_norm"):
            self.assertEqual(set(result["views"][mode]), set(values))
            self.assertEqual(set(result["observations"][mode]), set(values))
        self.assertEqual(result["views"]["raw"]["１００cm"], "１００cm")
        self.assertEqual(result["views"]["width_jaconv"]["１００cm"], "100cm")
        self.assertEqual(result["views"]["width_jaconv"]["100cm"], "100cm")
        # A normalized representation may collide; the raw keys and full candidate
        # vector still distinguish the original values.
        self.assertNotEqual("１００cm", "100cm")
        for mode in result["views"]:
            self.assertEqual(len(result["views"][mode]), len(set(values)))

    def test_tokenizer_spans_quote_original_unicode_and_cover_nonspace_source(self):
        values = ["🧵幅１００×８０ｃｍ（特別）", "250g×2袋 + 3箱", "XL×L"]
        result = library_views.build_views(values)
        for mode in ("sudachi_norm", "spacy_norm"):
            for raw in values:
                tokens = result["observations"][mode][raw]["tokens"]
                covered = set()
                previous_start = -1
                for token in tokens:
                    start, end = token["source_span"]
                    self.assertEqual((start, end), (token["start"], token["end"]))
                    self.assertGreaterEqual(start, previous_start)
                    self.assertGreaterEqual(start, 0)
                    self.assertGreater(end, start)
                    self.assertLessEqual(end, len(raw))
                    self.assertEqual(raw[start:end], token["surface"])
                    covered.update(range(start, end))
                    previous_start = start
                self.assertEqual({i for i, char in enumerate(raw) if not char.isspace()} - covered,
                                 set(), (mode, raw))

    def test_jaconv_transform_record_keeps_the_whole_source_span(self):
        raw = "🧵幅１００×８０ｃｍ"
        result = library_views.build_views([raw])
        token = result["observations"]["width_jaconv"][raw]["tokens"][0]
        self.assertEqual(token["source_span"], [0, len(raw)])
        self.assertEqual(token["surface"], raw)
        self.assertEqual(token["output"], jaconv.z2h(raw, kana=False, ascii=True, digit=True))


@unittest.skipIf(fuzz is None, "install RapidFuzz in the dedicated venv")
class FuzzyAdvisoryTests(unittest.TestCase):
    def test_token_set_subset_score_is_perfect_but_cannot_be_an_acceptance_result(self):
        # RapidFuzz intentionally treats a strict token subset as a perfect score.
        # This demonstrates why ranking evidence cannot establish equivalence.
        score = fuzz.token_set_ratio("赤 青", "赤 青 大")
        self.assertEqual(score, 100.0)


@unittest.skipIf(ranker is None, "install RapidFuzz, jaconv, SudachiPy and spaCy in the dedicated venv")
class RankOnlyTests(unittest.TestCase):
    def test_axis_value_serialization_preserves_order_duplicates_and_raw_labels(self):
        axes = [
            {"axis_name": "色", "value": "青"},
            {"axis_name": "サイズ", "value": "１００cm"},
            {"axis_name": "色", "value": "青"},
        ]
        self.assertEqual(ranker.axis_value_text(axes), "青 | １００cm | 青")

    def test_ranker_returns_every_source_row_and_keeps_numeric_tokens_and_ties(self):
        case = {"case_id": "synthetic", "rakuten_selected": {"axes": [
            {"axis_name": "サイズ", "value": "250g×2袋"},
            {"axis_name": "長さ", "value": "100cm"},
        ]}}
        candidates = [
            {"row_key": "row-a", "axes": [
                {"axis_name": "重量", "value": "250g×2袋"},
                {"axis_name": "長さ", "value": "100cm"},
            ]},
            {"row_key": "row-b", "axes": [
                {"axis_name": "重量", "value": "250g×2袋"},
                {"axis_name": "長さ", "value": "100cm"},
            ]},
            {"row_key": "row-c", "axes": [
                {"axis_name": "重量", "value": "250g×2袋"},
                {"axis_name": "長さ", "value": "80cm"},
            ]},
        ]
        # The lookup is an explicit, already-built library-view map; no custom
        # quantity parser or whole-family matcher participates in ranking.
        lookup = {
            mode: {text: text for text in {"250g×2袋", "100cm", "80cm"}}
            for mode in ("raw", "width_jaconv", "sudachi_norm", "spacy_norm")
        }
        records = ranker.rank_case(case, candidates, lookup)
        self.assertTrue(records)
        for record in records:
            self.assertEqual(record["query_raw_axis_values"], [
                {"axis_name": "サイズ", "value": "250g×2袋"},
                {"axis_name": "長さ", "value": "100cm"},
            ])
            self.assertEqual(record["query_text"], "250g×2袋 | 100cm")
            ranked = record["candidates"]
            self.assertEqual({row["row_key"] for row in ranked}, {"row-a", "row-b", "row-c"})
            by_key = {row["row_key"]: row for row in ranked}
            self.assertEqual(by_key["row-a"]["raw_axis_values"], [
                {"axis_name": "重量", "value": "250g×2袋"},
                {"axis_name": "長さ", "value": "100cm"},
            ])
            self.assertEqual(by_key["row-b"]["rank"], by_key["row-a"]["rank"])
            self.assertEqual(by_key["row-a"]["tie_size"], 2)
            self.assertEqual(by_key["row-b"]["tie_size"], 2)
            self.assertEqual([row["source_order"] for row in ranked], [0, 1, 2])
            self.assertFalse(any(key in record for key in ("accept", "state", "decision")))
            self.assertFalse(any(key in candidate for candidate in ranked
                                 for key in ("accept", "state", "decision")))

    def test_perfect_fuzzy_subset_candidate_remains_rank_evidence_not_acceptance(self):
        case = {"case_id": "subset", "rakuten_selected": {"axes": [
            {"axis_name": "name", "value": "赤 青"},
        ]}}
        candidates = [
            {"row_key": "subset", "axes": [{"axis_name": "name", "value": "赤 青 大"}]},
            {"row_key": "exact", "axes": [{"axis_name": "name", "value": "赤 青"}]},
        ]
        lookup = {mode: {text: text for text in ("赤 青", "赤 青 大")}
                  for mode in ("raw", "width_jaconv", "sudachi_norm", "spacy_norm")}
        records = ranker.rank_case(case, candidates, lookup)
        token_records = [record for record in records
                         if record["view"] == "raw" and record["scorer"] == "token_set_ratio"]
        self.assertTrue(token_records)
        record = token_records[0]
        candidates_by_key = {candidate["row_key"]: candidate for candidate in record["candidates"]}
        self.assertEqual(candidates_by_key["subset"]["score"], 100.0)
        self.assertEqual(candidates_by_key["exact"]["score"], 100.0)
        self.assertEqual(candidates_by_key["subset"]["rank"], candidates_by_key["exact"]["rank"])
        self.assertFalse(any(key in record for key in ("accept", "state", "decision")))


@unittest.skipIf(selection is None, "selection gate dependencies are unavailable")
class SelectedCatalogTests(unittest.TestCase):
    @staticmethod
    def setup_catalog(source_values, target_values, selected_value, actual_value,
                      *, axis="Size", target_axes=None, source_status="complete"):
        docs = [
            {"document_id": "source-doc", "dossier_id": "d1", "side": "rakuten"},
            {"document_id": "target-doc", "dossier_id": "d1", "side": "au"},
        ]
        request = {
            "request_id": "eval",
            "target_document_id": "target-doc",
            "direction": "rakuten_to_au",
            "axis_name": axis,
            "selected_value": selected_value,
            "option_values": list(source_values),
            "target_selected_axes": (target_axes if target_axes is not None else [
                {"axis_name": axis, "value": actual_value},
            ]),
            "source_family_status": source_status,
        }
        # Reverse-direction requests bind the target document's native catalog
        # to the same dossier, exactly as `catalogs` groups production inputs.
        target_catalog = {
            "request_id": "target-catalog",
            "target_document_id": "source-doc",
            "direction": "au_to_rakuten",
            "axis_name": axis,
            "selected_value": target_values[0] if target_values else "",
            "option_values": list(target_values),
            "target_selected_axes": [],
        }
        docs, families = selection.catalogs([request, target_catalog], docs)
        return request, docs, families

    def test_pointwise_shared_value_supports_without_whole_family_bijection(self):
        request, docs, families = self.setup_catalog(
            ["100cm", "200cm"], ["100cm"], "100cm", "100cm")
        state, reason = selection.selected_state(
            request, docs, families, {"100cm": "100cm", "200cm": "200cm"})
        self.assertEqual((state, reason), ("support", "selected_values_equal_in_library_view"))

    def test_selected_source_value_missing_from_target_catalog_is_unknown(self):
        request, docs, families = self.setup_catalog(
            ["100cm", "200cm"], ["100cm"], "200cm", "100cm")
        state, reason = selection.selected_state(
            request, docs, families, {"100cm": "100cm", "200cm": "200cm"})
        self.assertEqual(state, "unknown")
        self.assertEqual(reason, "selected_value_has_no_catalog_counterpart")

    def test_different_actual_target_member_conflicts_only_when_catalog_knows_counterpart(self):
        request, docs, families = self.setup_catalog(
            ["100cm"], ["100cm", "200cm"], "100cm", "200cm")
        state, reason = selection.selected_state(
            request, docs, families, {"100cm": "100cm", "200cm": "200cm"})
        self.assertEqual((state, reason), (
            "conflict", "different_native_options_under_shared_identifier"))

    def test_duplicate_target_axis_binding_is_unknown_even_when_values_match(self):
        axes = [{"axis_name": "Size", "value": "100cm"},
                {"axis_name": "Size", "value": "100cm"}]
        request, docs, families = self.setup_catalog(
            ["100cm"], ["100cm"], "100cm", "100cm", target_axes=axes)
        state, reason = selection.selected_state(
            request, docs, families, {"100cm": "100cm"})
        self.assertEqual(state, "unknown")
        self.assertEqual(reason, "target_axis_missing_or_ambiguous")
        rows, decisions = selection.assemble(
            [{"case_id": "case-1", "au_row_key": "row-1",
              "bindings": [{"request_id": "eval"}]}],
            [request], {"eval": state})
        self.assertEqual(rows[0]["state"], "unknown")
        self.assertNotEqual(decisions[0]["decision"], "accept")

    def test_library_normalization_collision_abstains_before_selected_match(self):
        request, docs, families = self.setup_catalog(
            ["１００cm", "100cm"], ["100cm"], "１００cm", "100cm")
        state, reason = selection.selected_state(request, docs, families, {
            "１００cm": "100cm", "100cm": "100cm",
        })
        self.assertEqual(state, "unknown")
        self.assertEqual(reason, "library_view_catalog_collision")

    def test_different_raw_axis_labels_do_not_cross_bind_as_aliases(self):
        request, docs, families = self.setup_catalog(
            ["100cm"], ["100cm"], "100cm", "100cm", axis="幅",
            target_axes=[{"axis_name": "横幅", "value": "100cm"}])
        state, reason = selection.selected_state(
            request, docs, families, {"100cm": "100cm"})
        self.assertEqual(state, "unknown")
        self.assertEqual(reason, "target_axis_missing_or_ambiguous")

    def test_incomplete_source_family_stays_unknown_under_exact_library_view(self):
        request, docs, families = self.setup_catalog(
            ["100cm"], ["100cm"], "100cm", "100cm", source_status="incomplete")
        state, reason = selection.selected_state(
            request, docs, families, {"100cm": "100cm"})
        self.assertEqual(state, "unknown")
        self.assertEqual(reason, "incomplete_native_catalog")


@unittest.skipIf(price_free is None or library_views is None,
                 "install Quantulum3 and tokenizer libraries in the dedicated venv")
class PriceFreeViewTests(unittest.TestCase):
    def test_normalized_token_offsets_map_to_exact_original_unicode_quotes(self):
        raw = "💴8yen 2P 12枚"
        tokens = [
            {"norm": "8", "start": 1, "end": 2, "surface": "8"},
            {"norm": "yen", "start": 2, "end": 5, "surface": "yen"},
            {"norm": "2", "start": 6, "end": 7, "surface": "2"},
            {"norm": "p", "start": 7, "end": 8, "surface": "P"},
            {"norm": "12", "start": 9, "end": 11, "surface": "12"},
            {"norm": "枚", "start": 11, "end": 12, "surface": "枚"},
        ]
        mapping = price_free.norm_to_source("8 yen 2 p 12 枚", tokens)
        self.assertEqual([entry["norm"] for entry in mapping],
                         ["8", "yen", "2", "p", "12", "枚"])
        for entry in mapping:
            start, end = entry["source_start"], entry["source_end"]
            self.assertEqual(raw[start:end], entry["surface"])
        self.assertEqual(mapping[0]["source_start"], 1)
        self.assertEqual(mapping[-1]["source_end"], len(raw))

    def test_native_quantulum_masks_yen_only_and_preserves_pack_geometry_and_count(self):
        values = ["8 yen 2P 12枚セット", "楕円形", "100cm"]
        views = library_views.build_views(values)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "frozen"
            base.mkdir()
            (base / "raw-values.json").write_text(
                json.dumps(values, ensure_ascii=False), encoding="utf-8")
            (base / "views.json").write_text(
                json.dumps(views, ensure_ascii=False), encoding="utf-8")
            output = root / "out"
            with patch.object(price_free, "BASE", base), \
                    patch("sys.argv", ["prepare", "--output-dir", str(output)]), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(price_free.main(), 0)

            result = json.loads((output / "views-price-free.json").read_text(encoding="utf-8"))
            audit = json.loads((output / "price-exclusion-audit.json").read_text(encoding="utf-8"))
            masked = result["views"]["raw"]
            original = "8 yen 2P 12枚セット"
            self.assertEqual(masked[original], " 2P 12枚セット")
            self.assertIn("2P", masked[original])
            self.assertIn("12枚", masked[original])
            self.assertEqual(masked["楕円形"], "楕円形")
            self.assertEqual(masked["100cm"], "100cm")
            self.assertTrue(result["all_nonmasked_original_characters_preserved"])
            self.assertFalse(result["geometry_guard"]["masked"])
            entities = audit["per_original_raw_key"][original]["currency_entities"]
            yen = [entity for entity in entities if entity["unit"] == "japanese yen"]
            penny = [entity for entity in entities if entity["unit"] == "penny"]
            self.assertEqual(len(yen), 1)
            self.assertEqual(yen[0]["source_quote"], "8 yen")
            self.assertTrue(yen[0]["masked_as_price"])
            # Quantulum may expose the ambiguous product suffix as penny; its
            # entity class alone must never mask a SKU pack token.
            self.assertEqual(len(penny), 1)
            self.assertEqual(penny[0]["source_quote"], "2P")
            self.assertFalse(penny[0]["masked_as_price"])


@unittest.skipIf(ranker is None, "install RapidFuzz in the dedicated venv")
class PriceFreeRankingTests(unittest.TestCase):
    @staticmethod
    def write_jsonl(path: Path, rows):
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                        encoding="utf-8")

    def test_feature_subset_ranks_all_original_rows_and_keeps_raw_only_as_provenance(self):
        raw_query = "12枚 +1000円"
        raw_a = "12枚 +500円"
        raw_b = "2P +100円"
        raw_values = [raw_query, raw_a, raw_b]
        price_free_maps = {
            "raw": {value: value for value in raw_values},
            "width_jaconv": {raw_query: "12枚 +", raw_a: "12枚 +", raw_b: "2P +"},
            "sudachi_norm": {raw_query: "12 枚 +", raw_a: "12 枚 +", raw_b: "2 P +"},
            "spacy_norm": {raw_query: "12 枚 +", raw_a: "12 枚 +", raw_b: "2 p +"},
        }
        feature_modes = ["width_jaconv", "sudachi_norm", "spacy_norm"]
        view_payload = {
            "schema": "installed-library-price-free-views-v1",
            "raw_is_source_provenance_only": True,
            "price_free_feature_modes": feature_modes,
            "views": price_free_maps,
            "versions": {"synthetic": "test"},
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            self.write_jsonl(source / "cases.jsonl", [{
                "case_id": "c1", "dossier_id": "d1", "rakuten_selected": {
                    "axes": [{"axis_name": "set", "value": raw_query}],
                },
            }])
            self.write_jsonl(source / "products.jsonl", [{
                "dossier_id": "d1", "au_rows": [
                    {"row_key": "au-a", "axes": [
                        {"axis_name": "set", "value": raw_a},
                    ]},
                    {"row_key": "au-b", "axes": [
                        {"axis_name": "set", "value": raw_b},
                    ]},
                ],
            }])
            views_path = root / "views-price-free-ranking.json"
            views_path.write_text(json.dumps(view_payload, ensure_ascii=False), encoding="utf-8")
            output = root / "rankings"
            frozen = ranker.run(source, output, views_path, enforce_population=False,
                                view_modes=feature_modes)

            input_freeze = json.loads((output / "INPUT_FREEZE.json").read_text(encoding="utf-8"))
            self.assertEqual(input_freeze["view_modes"], feature_modes)
            self.assertEqual(frozen["case_row_candidate_count"], 2)
            self.assertEqual(frozen["candidate_score_count"], 2 * len(feature_modes) * 3)
            self.assertEqual(frozen["ranking_record_count"], len(feature_modes) * 3)
            records = [json.loads(line) for line in
                       (output / "rankings.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 9)
            self.assertEqual({record["view"] for record in records}, set(feature_modes))
            self.assertNotIn("raw", {record["view"] for record in records})
            for record in records:
                self.assertEqual(record["query_raw_axis_values"], [
                    {"axis_name": "set", "value": raw_query},
                ])
                self.assertIn("12", record["query_text"])
                self.assertIn("枚", record["query_text"])
                self.assertNotIn("1000円", record["query_text"])
                self.assertEqual({candidate["row_key"] for candidate in record["candidates"]},
                                 {"au-a", "au-b"})
                self.assertEqual({candidate["source_order"] for candidate in record["candidates"]},
                                 {0, 1})
                by_key = {candidate["row_key"]: candidate for candidate in record["candidates"]}
                self.assertEqual(by_key["au-a"]["raw_axis_values"], [
                    {"axis_name": "set", "value": raw_a},
                ])
                self.assertEqual(by_key["au-b"]["raw_axis_values"], [
                    {"axis_name": "set", "value": raw_b},
                ])
                self.assertIn("12", by_key["au-a"]["text"])
                self.assertIn("枚", by_key["au-a"]["text"])
                self.assertNotIn("500円", by_key["au-a"]["text"])
                self.assertIn("2", by_key["au-b"]["text"])
                self.assertNotIn("100円", by_key["au-b"]["text"])
                self.assertFalse(any(key in record for key in ("accept", "decision", "state")))


@unittest.skipIf(residual_probe is None, "install spaCy in the dedicated venv")
class ResidualTextPolicyTests(unittest.TestCase):
    @staticmethod
    def write_jsonl(path: Path, rows):
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                        encoding="utf-8")

    def test_spec_table_search_isolated_from_host_body_and_miss_keeps_table(self):
        table_text = "商品仕様\nカラー：青色\nサイズ：M\n表末尾保持"
        table_ref = {"source_id": "selected-table", "start": 0,
                     "end": len(table_text), "quote": table_text}
        fallback_text = "FALLBACK_ONLY_SENTINEL と本文"
        fallback_ref = {"source_id": "raw-description"}
        docs, selections, contexts = [], [], []
        for index in range(40):
            document_id = f"doc-{index:02d}"
            digest = f"{index + 1:064x}"
            mode = "spec_table" if index == 0 else "full_body_fallback"
            selected_text = table_text if index == 0 else (
                fallback_text if index == 1 else f"fallback document {index}")
            original_text = (
                "full host body contains BODY_ONLY_SENTINEL which is forbidden in table mode"
                if index == 0 else selected_text)
            docs.append({
                "document_id": document_id, "document_sha256": digest,
                "side": "au",
                "description": {"text": original_text,
                                "segments": [{"input_ref": {"source_id": "host-body"}}]},
                "title": {"text": "title has no requested phrase", "source_refs": []},
            })
            selections.append({
                "document_id": document_id, "document_sha256": digest,
                "side": "au", "mode": mode, "text": selected_text,
                "tables": ([{"text": table_text, "source_ref": table_ref}]
                           if index == 0 else []),
                "raw_sources": ([fallback_ref] if index == 1 else []),
            })
            contexts.append({"document_sha256": digest, "body_text": selected_text})

        requests = [
            {"request_id": "table-miss", "target_document_id": "doc-00",
             "axis_name": "Color", "selected_value": "BODY_ONLY_SENTINEL",
             "option_values": ["BODY_ONLY_SENTINEL"], "target_selected_axes": []},
            {"request_id": "table-hit", "target_document_id": "doc-00",
             "axis_name": "Color", "selected_value": "青色",
             "option_values": ["青色"], "target_selected_axes": []},
            {"request_id": "fallback-hit", "target_document_id": "doc-01",
             "axis_name": "Color", "selected_value": "FALLBACK_ONLY_SENTINEL",
             "option_values": ["FALLBACK_ONLY_SENTINEL"], "target_selected_axes": []},
        ]
        view_payload = {"views": {"spacy_norm": {}}, "versions": {"spaCy": "synthetic"}}

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_dir = root / "host"
            spec_dir.mkdir()
            public_bundle = root / "public-bundle"
            public_bundle.mkdir()
            catalog_dir = root / "catalog"
            catalog_dir.mkdir()
            self.write_jsonl(spec_dir / "requests.jsonl", requests)
            self.write_jsonl(spec_dir / "documents.jsonl", docs)
            self.write_jsonl(spec_dir / "spec-table-selections.jsonl", selections)
            (spec_dir / "SPEC_TABLE_POLICY.json").write_text(
                json.dumps({"semantic_or_sku_scope_proof": False}), encoding="utf-8")
            self.write_jsonl(public_bundle / "contexts.jsonl", contexts)
            self.write_jsonl(catalog_dir / "requests.jsonl", [])
            self.write_jsonl(catalog_dir / "documents.jsonl", [])
            views_path = root / "views.json"
            views_path.write_text(json.dumps(view_payload), encoding="utf-8")
            output = root / "residual-output"
            result = residual_probe.run(spec_dir, catalog_dir, views_path, output)

            observations = [json.loads(line) for line in
                           (output / "request-observations.jsonl").read_text(
                               encoding="utf-8").splitlines()]
        by_request = {row["request"]["request_id"]: row for row in observations}
        self.assertEqual(set(by_request), {"table-miss", "table-hit", "fallback-hit"})

        missed = by_request["table-miss"]
        self.assertEqual(missed["spec_selection_mode"], "spec_table")
        self.assertEqual(missed["search_route"], "spec_table")
        self.assertEqual(missed["matched_description_hits"], [])
        self.assertEqual(missed["reading_focus_segments"], [{
            "scope": "selected_body_text", "text": table_text, "source_refs": [table_ref],
        }])
        self.assertNotIn("BODY_ONLY_SENTINEL", json.dumps(
            [missed["reading_focus_segments"], missed["matched_description_hits"]],
            ensure_ascii=False))
        self.assertNotIn("full_original_description", missed)
        self.assertEqual(missed["reading_focus_chars"], len(table_text))

        hit = by_request["table-hit"]
        self.assertEqual(hit["reading_focus_segments"][0]["text"], table_text)
        self.assertEqual(len(hit["matched_description_hits"]), 1)
        match = hit["matched_description_hits"][0]
        start, end = match["source_span"]
        self.assertEqual(match["coordinate_unit"], "unicode_characters")
        self.assertEqual((match["start"], match["end"]), (start, end))
        self.assertEqual(match["quote"], table_text[start:end])
        self.assertEqual(match["quote"], "青色")
        self.assertEqual(match["source_refs"], [table_ref])
        self.assertEqual(match["scope"], "selected_body_text")

        fallback = by_request["fallback-hit"]
        self.assertEqual(fallback["spec_selection_mode"], "full_body_fallback")
        self.assertEqual(fallback["search_route"], "full_body_fallback")
        self.assertEqual(fallback["reading_focus_segments"][0]["text"], fallback_text)
        self.assertEqual(fallback["matched_description_hits"][0]["quote"],
                         "FALLBACK_ONLY_SENTINEL")
        self.assertEqual(fallback["matched_description_hits"][0]["source_refs"],
                         [fallback_ref])
        self.assertEqual(result["no_hit_kept_full_selected_text"], 1)


if __name__ == "__main__":
    unittest.main()
