"""Explicitly artificial fixtures for frozen top-K relation input contracts."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "experiments/sku-matching" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREP = load("retrieved_relation_preparer", "prepare_retrieved_relation_probe_v1.py")
RANK = load("retrieval_fixture_backend", "trial_generic_evidence_reranker_v1.py")


def artificial_requests(task="artificial:task", source="window:0", quote="人工試験資料：現在の仕様。", options=None):
    options = options or ["寸法45×45cm（付属品込み）", "寸法70×100cm（本体のみ）"]
    rows = []
    for index, option in enumerate(options):
        provenance = {"task_id": task, "source_request_id": task + ":" + source,
                      "case_id": "artificial:" + task, "condition_id": "artificial:condition",
                      "axis_name": "寸法と構成", "option_values": options, "selected_value": options[0],
                      "au_row_key": "artificial:au:0", "selected_au_row": {
                          "row_key": "artificial:au:0", "axes": [{"axis_name": "複合選択", "value": "幅100×丈80cm(4枚) / 青"}]},
                      "fixed_au_product_ref": {"product_id": "artificial:1"},
                      "window": {"quote": quote, "source_ref": {"raw_file": "artificial-fixture.json", "sha256": "artificial"},
                                 "structural_context": [{"role": "heading", "text": "人工試験資料"}]},
                      "candidate_option_index": index, "candidate_option_value": option,
                      "original_relation_request_id": task + ":old:" + source + ":" + str(index),
                      "scope_proven": False}
        rows.append({"id": task + ":" + source + ":" + str(index), "question": "人工fixtureの関係判定",
                     "state": json.dumps({"前提": quote, "仮説": option}, ensure_ascii=False),
                     "premise": quote, "hypothesis": option,
                     "choices": [{"label": "entailment", "description": "支持"}, {"label": "neutral", "description": "不明"}],
                     "provenance": provenance})
    return rows


class ArtificialModel:
    max_length = 512

    def __init__(self, *args, **kwargs):
        pass

    def token_lengths_pairs(self, pairs):
        return [513 if passage == "人工試験資料：超長文" else 10 for _, passage in pairs]

    def score_pairs(self, pairs, batch_size=8):
        return [float(index) for index in range(len(pairs))]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")


class RetrievedRelationProbeTests(unittest.TestCase):
    def fixture(self, root, requests=None, empty=None):
        input_dir, ranking_dir, output_dir = root / "input", root / "ranking", root / "selected"
        input_dir.mkdir()
        requests = requests if requests is not None else sum((artificial_requests(source="window:" + str(index)) for index in range(7)), [])
        empty = empty or []
        RANK.write(input_dir / "requests.jsonl", requests)
        RANK.write(input_dir / "empty-quote-requests.jsonl", empty)
        manifest = {"request_count": len(requests), "empty_literal_quote_request_count": len(empty),
                    "output_sha256": {name: PREP.sha(input_dir / name) for name in ("requests.jsonl", "empty-quote-requests.jsonl")}}
        write_json(input_dir / "manifest.json", manifest)
        with redirect_stdout(io.StringIO()):
            RANK.run(input_dir, ranking_dir, root / "no-model", model_factory=ArtificialModel, query_style="axis_only")
        return input_dir, ranking_dir, output_dir

    def reseal(self, ranking_dir, name):
        summary = json.loads((ranking_dir / "summary.json").read_text())
        summary["output_sha256"][name] = PREP.sha(ranking_dir / name)
        write_json(ranking_dir / "summary.json", summary)

    def test_selected_requests_keep_all_whole_alternatives_and_every_field(self):
        with tempfile.TemporaryDirectory() as directory:
            input_dir, ranking_dir, output_dir = self.fixture(Path(directory))
            manifest = PREP.prepare(input_dir, ranking_dir, output_dir)
            original = {row["id"]: row for row, _ in PREP.lines((input_dir / "requests.jsonl").read_bytes())}
            selected = [row for row, _ in PREP.lines((output_dir / "requests.jsonl").read_bytes())]
            self.assertEqual(len(selected), 10)
            self.assertTrue(all(row == original[row["id"]] for row in selected))
            self.assertEqual({row["provenance"]["candidate_option_index"] for row in selected}, {0, 1})
            self.assertTrue(all(row["provenance"]["option_values"] == ["寸法45×45cm（付属品込み）", "寸法70×100cm（本体のみ）"] for row in selected))
            self.assertEqual(manifest["unselected_request_count"], 4)
            self.assertFalse(manifest["selected_requests_modified"])
            self.assertFalse(manifest["scope_proven"])
            self.assertFalse(manifest["production_eligible"])
            self.assertFalse(manifest["labels_read"])

    def test_selected_and_unselected_lines_partition_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            input_dir, ranking_dir, output_dir = self.fixture(Path(directory))
            PREP.prepare(input_dir, ranking_dir, output_dir)
            original_lines = (input_dir / "requests.jsonl").read_bytes().splitlines(keepends=True)
            selected = (output_dir / "requests.jsonl").read_bytes().splitlines(keepends=True)
            unselected = (output_dir / "unselected-requests.jsonl").read_bytes().splitlines(keepends=True)
            self.assertCountEqual(original_lines, selected + unselected)
            self.assertEqual((output_dir / "source-requests.jsonl").read_bytes(), (input_dir / "requests.jsonl").read_bytes())
            metadata = json.loads((output_dir / "selections.jsonl").read_text())
            self.assertEqual(metadata["maximum_top_k_selected"], 5)
            self.assertEqual(len(metadata["selected_source_request_ids"]), 5)

    def test_empty_and_overlimit_only_tasks_are_retained_as_empty_selections(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = artificial_requests(task="artificial:empty", quote="\n \r\n")
            long = artificial_requests(task="artificial:long", quote="人工試験資料：超長文")
            input_dir, ranking_dir, output_dir = self.fixture(Path(directory), requests=long, empty=empty)
            manifest = PREP.prepare(input_dir, ranking_dir, output_dir)
            self.assertEqual(manifest["request_count"], 0)
            self.assertEqual(manifest["empty_selection_task_ids"], ["artificial:empty", "artificial:long"])
            self.assertEqual(manifest["unselected_request_count"], 2)
            self.assertEqual((output_dir / "empty-quote-requests.jsonl").read_bytes(), (input_dir / "empty-quote-requests.jsonl").read_bytes())
            metadata = [row for row, _ in PREP.lines((output_dir / "selections.jsonl").read_bytes())]
            self.assertTrue(all(row["empty_selection"] for row in metadata))

    def test_sha_tampering_rejected_before_output_creation(self):
        for artifact in ("input", "rankings.jsonl", "code/backend_reranker.py"):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as directory:
                input_dir, ranking_dir, output_dir = self.fixture(Path(directory))
                path = input_dir / "requests.jsonl" if artifact == "input" else ranking_dir / artifact
                path.write_bytes(path.read_bytes() + b"\n")
                with self.assertRaises(ValueError):
                    PREP.prepare(input_dir, ranking_dir, output_dir)
                self.assertFalse(output_dir.exists())

    def test_missing_completed_summary_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            input_dir, ranking_dir, output_dir = self.fixture(Path(directory))
            (ranking_dir / "summary.json").unlink()
            with self.assertRaises(FileNotFoundError):
                PREP.prepare(input_dir, ranking_dir, output_dir)
            self.assertFalse(output_dir.exists())

    def test_resealed_output_cannot_change_source_quote_axis_or_au_row(self):
        for field in ("quote", "axis_name", "au_row_key", "source_ref"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                input_dir, ranking_dir, output_dir = self.fixture(Path(directory))
                rows = [row for row, _ in PREP.lines((ranking_dir / "rankings.jsonl").read_bytes())]
                if field == "quote":
                    rows[0]["provenance"]["window"]["quote"] = "別の引用"
                elif field == "source_ref":
                    rows[0]["provenance"]["window"]["source_ref"]["raw_file"] = "another-artificial.json"
                else:
                    rows[0]["provenance"][field] = "別の条件"
                (ranking_dir / "rankings.jsonl").unlink()
                RANK.write(ranking_dir / "rankings.jsonl", rows)
                self.reseal(ranking_dir, "rankings.jsonl")
                with self.assertRaises(ValueError):
                    PREP.prepare(input_dir, ranking_dir, output_dir)

    def test_resealed_rank_or_selection_tampering_rejected(self):
        for artifact in ("rankings.jsonl", "top-k-windows.jsonl"):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as directory:
                input_dir, ranking_dir, output_dir = self.fixture(Path(directory))
                rows = [row for row, _ in PREP.lines((ranking_dir / artifact).read_bytes())]
                if artifact == "rankings.jsonl":
                    rows[0]["rank"] = 1
                else:
                    rows[0]["top_k_source_request_ids"]["5"] = rows[0]["top_k_source_request_ids"]["5"][:1]
                (ranking_dir / artifact).unlink()
                RANK.write(ranking_dir / artifact, rows)
                self.reseal(ranking_dir, artifact)
                with self.assertRaises(ValueError):
                    PREP.prepare(input_dir, ranking_dir, output_dir)

    def test_foreign_input_manifest_cannot_replace_bound_source(self):
        with tempfile.TemporaryDirectory() as directory:
            input_dir, ranking_dir, output_dir = self.fixture(Path(directory))
            manifest = json.loads((input_dir / "manifest.json").read_text())
            manifest["extra"] = "another-version"
            write_json(input_dir / "manifest.json", manifest)
            with self.assertRaises(ValueError):
                PREP.prepare(input_dir, ranking_dir, output_dir)
            self.assertFalse(output_dir.exists())

    def test_all_output_files_have_verified_sha_and_frozen_code_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            input_dir, ranking_dir, output_dir = self.fixture(Path(directory))
            manifest = PREP.prepare(input_dir, ranking_dir, output_dir)
            for name, expected in manifest["output_sha256"].items():
                self.assertEqual(PREP.sha(output_dir / name), expected)
            self.assertEqual(manifest["output_sha256"]["requests.jsonl"], PREP.sha(output_dir / "requests.jsonl"))
            self.assertTrue((output_dir / "ranking-snapshot/code/backend_reranker.py").exists())

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaises(FileExistsError):
                PREP.prepare(path / "absent", path / "absent-ranking", path)

    def test_nonfinite_json_and_unsafe_artifact_paths_are_rejected(self):
        for value in ('{"score":NaN}', '{"score":1e999}'):
            with self.assertRaises(ValueError):
                PREP.parse(value)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                PREP._read_verified(Path(directory), {"../foreign.json": "artificial"})

    def test_incomplete_alternatives_cannot_reach_selection(self):
        with self.assertRaises(ValueError):
            PREP._expected_windows(artificial_requests()[:1], [], "axis_only")
        changed = deepcopy(artificial_requests())
        changed[1]["provenance"]["selected_au_row"]["axes"][0]["value"] = "別の複合値"
        with self.assertRaises(ValueError):
            PREP._expected_windows(changed, [], "axis_only")


if __name__ == "__main__":
    unittest.main()
