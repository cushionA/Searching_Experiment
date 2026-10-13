"""First-score immutability, exact text replay, and whole-product closure."""
import copy
import json
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

CODE = Path(__file__).resolve().parents[1] / "experiments/sku-matching"
sys.path.insert(0, str(CODE))
import nextdata_exact_cache_resume_v1 as R
import kaggle_nextdata_exact_resume_v1 as WORKER


class CharTokenizer:
    def encode(self, text, add_special_tokens=False):
        return list(text)

    def num_special_tokens_to_add(self, pair=True):
        return 3


def row(ref):
    fields = [{"field_ref": "inventory", "role": "current_all_options_not_actual_selection", "text": "L M 全SKU"},
              {"field_ref": "body", "role": "current_selected_product", "text": "a\nＡ🧵" * 250},
              {"field_ref": "reference", "role": "reference_source_product", "text": "メーカー規格文書"}]
    return {"pair_ref": ref, "axis_name": "サイズ", "option_values": ["L", "M"],
            "target_selected_facts": [{"axis_name": "サイズ", "value": "L"}], "context_header": "",
            "variants": {v: {"body_fields": copy.deepcopy(fields)} for v in R.TASK.VARIANTS}}


class FixtureCatalog:
    def __init__(self):
        self.rows = [row(1), row(2)]
        self.by_ref = {r["pair_ref"]: r for r in self.rows}
        self.chunks = {}
        for r in self.rows:
            for variant in R.TASK.VARIANTS:
                self.chunks[r["pair_ref"], variant] = R.TASK.prepare_chunks(r, variant, {k: CharTokenizer() for k in R.KINDS})

    request = R.Catalog.request
    occurrences = R.Catalog.occurrences


def nli_score(identity, state="support", technical=False):
    probabilities = None if technical else {"support": .96 if state == "support" else .02,
                                           "conflict": .96 if state == "conflict" else .02,
                                           "unknown": .96 if state == "unknown" else .02}
    return {"state": "unknown" if technical else state, "probabilities": probabilities,
            "predicted_class": "unknown" if technical else state,
            "predicted_confidence": None if technical else .96, "token_count_untruncated": 200,
            "cache_key": identity["legacy_key"], "error_type": "RuntimeError" if technical else None}


class ExactResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = FixtureCatalog()
        self.index = self.root / "index.sqlite"
        self.stats = R.build_index(self.index, self.catalog)
        self.db = R.connect(self.index)
        self.graph_patch = patch.object(R, "PINNED_INDEX_GRAPH", R.index_graph(self.db))
        self.graph_patch.start()

    def tearDown(self):
        self.db.close()
        self.graph_patch.stop()
        self.temp.cleanup()

    def first_request(self, kind="nli"):
        entry = self.db.execute("SELECT * FROM requests WHERE kind=? ORDER BY ordinal LIMIT 1", (kind,)).fetchone()
        request, identity = R.checked_request(self.db, self.catalog, kind, entry["exact_key"])
        return entry, request, identity

    def test_replay_matches_original_all_characters_headings_and_request_keys(self):
        for r in self.catalog.rows:
            for variant in R.TASK.VARIANTS:
                original, issue = self.catalog.chunks[r["pair_ref"], variant]
                coverage = {"pair_ref": r["pair_ref"], "variant": variant, "option_count": 2,
                            "chunk_count": len(original), "input_issue": issue,
                            "chunk_source_ranges": [{k: c[k] for k in ("chunk_ref", "field_ref", "start", "end", "raw_field_sha256")} for c in original]}
                replay, replay_issue = R.restore_chunks(r, variant, coverage)
                self.assertEqual((replay, replay_issue), (original, issue))
                for option in r["option_values"]:
                    for old, new in zip(original, replay):
                        a = R.TASK.choice_request(r, option, old["premise"], "old-id")
                        b = R.TASK.choice_request(r, option, new["premise"], "new-id")
                        self.assertEqual(R.request_identity("jev", a), R.request_identity("jev", b))
                corrupted = copy.deepcopy(coverage)
                corrupted["chunk_source_ranges"][0]["end"] -= 1
                with self.assertRaises(ValueError):
                    R.restore_chunks(r, variant, corrupted)

    def test_transport_ids_do_not_change_exact_key_but_raw_text_and_pins_do(self):
        _, request, identity = self.first_request("jev")
        moved = {**request, "id": "another-id"}
        self.assertEqual(identity, R.request_identity("jev", moved))
        self.assertNotEqual(identity, R.request_identity("jev", {**request, "state": request["state"] + " "}))
        with patch.object(R, "identity_sha", return_value="different-pins"):
            self.assertNotEqual(identity["exact_key"], R.request_identity("jev", request)["exact_key"])

    def test_original_occurrence_wins_over_conflicting_global_cache(self):
        entry, _, identity = self.first_request()
        old = nli_score(identity, "support")
        later = nli_score(identity, "conflict")
        R.put_cache(self.db, "nli", entry["exact_key"], old, {"line_number": 1})
        self.assertTrue(R.put_cache(self.db, "nli", entry["exact_key"], later, {"line_number": 2}))
        occurrences = self.db.execute("SELECT * FROM occurrences WHERE kind='nli' AND exact_key=? ORDER BY ordinal", (entry["exact_key"],)).fetchall()
        self.assertGreater(len(occurrences), 1)
        own = occurrences[-1]
        self.db.execute("INSERT INTO saved VALUES (?,?,?,?,?,?,?,?)", ("nli", own["pair_ref"], own["variant"], own["option_index"],
                        own["chunk_ref"], own["exact_key"], R.canonical(later), R.canonical({"line_number": 2})))
        score, provenance = R.resolve_score(self.db, "nli", (json.loads(own["pair_ref"]), own["variant"], own["option_index"], own["chunk_ref"]))
        self.assertEqual(score, later)
        self.assertEqual(provenance["resolution"], "original_occurrence")
        first = occurrences[0]
        score, provenance = R.resolve_score(self.db, "nli", (json.loads(first["pair_ref"]), first["variant"], first["option_index"], first["chunk_ref"]))
        self.assertEqual(score, old)
        self.assertEqual(provenance["resolution"], "first_exact_input_record")

    def test_technical_unknown_is_first_record_and_never_reinferred(self):
        entry, request, identity = self.first_request()
        unknown = nli_score(identity, technical=True)
        self.assertTrue(R.validate_score("nli", unknown, request, identity))
        R.put_cache(self.db, "nli", entry["exact_key"], unknown, {"line_number": 1})
        class ForbiddenScorer:
            def run_pairs(self, *args, **kwargs):
                raise AssertionError("already-written technical unknown was retried")
        written = []
        result = WORKER.execute_pending(self.db, self.catalog, [["nli", entry["exact_key"]]], ForbiddenScorer(), written.append)
        self.assertTrue(result["complete"])
        self.assertEqual(result["already_written_skipped"], 1)
        self.assertEqual(written, [])
        self.assertEqual(json.loads(self.db.execute("SELECT score FROM cache").fetchone()[0]), unknown)

    def test_unwritten_first_record_saved_once_with_original_decision(self):
        entry, _, identity = self.first_request()
        class FakeScorer:
            calls = 0
            def run_pairs(self, requests, *, threads, batch_size):
                self.calls += 1
                assert (threads, batch_size) == (4, 16)
                return {"records": [{**nli_score(identity, "conflict"), "relation": "conflict"}]}
        scorer, output = FakeScorer(), []
        keys = [["nli", entry["exact_key"]]]
        WORKER.execute_pending(self.db, self.catalog, keys, scorer, output.append)
        WORKER.execute_pending(self.db, self.catalog, keys, scorer, output.append)
        self.assertEqual(scorer.calls, 1)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["score"]["state"], "conflict")

    def test_nonsemantic_schedule_preserves_whole_products_cases_and_budget(self):
        closure = {"totals": {"product_count": 3, "case_count": 4, "row_count": 8, "task_count": 12}, "products": [
            {"product_id": "p1", "case_ids": ["c1", "c2"], "row_keys": list(range(4)), "task_ids": list(range(6)), "pair_refs": [1], "closure_sha256": "p1"},
            {"product_id": "p2", "case_ids": ["c3"], "row_keys": list(range(3)), "task_ids": list(range(4)), "pair_refs": [2], "closure_sha256": "p2"},
            {"product_id": "literal", "case_ids": ["c4"], "row_keys": [1], "task_ids": [1, 2], "pair_refs": [], "closure_sha256": "literal"}]}
        plan = R.bounded_plan(self.db, closure, max_unique_requests=2)
        required = {(r["kind"], r["exact_key"]) for r in self.db.execute("SELECT kind,exact_key FROM requests")}
        scheduled = [tuple(key) for s in plan["shards"] for key in s["request_keys"]]
        self.assertEqual(set(scheduled), required)
        self.assertEqual(len(scheduled), len(required))
        self.assertTrue(all(s["unique_request_count"] <= 2 for s in plan["shards"]))
        by_product = {p["product_id"]: p for p in plan["local_product_receipts"]}
        self.assertEqual(by_product["p1"]["case_count"], 2)
        self.assertTrue(by_product["literal"]["ready_before_new_inference"])
        self.assertTrue(all(s["product_publication_deferred_until_all_required_keys"] for s in plan["shards"]))

    def test_successful_score_cannot_be_claimed_without_original_threshold_or_key(self):
        _, request, identity = self.first_request()
        score = nli_score(identity)
        R.validate_score("nli", score, request, identity)
        score["probabilities"] = {"support": .6, "conflict": .2, "unknown": .2}
        score["predicted_confidence"] = .6
        with self.assertRaises(ValueError):
            R.validate_score("nli", score, request, identity)
        score = nli_score(identity)
        score["cache_key"] = "different-key"
        with self.assertRaises(ValueError):
            R.validate_score("nli", score, request, identity)

    def test_cuda_and_model_pins_required_for_cache_import(self):
        pins = {**R.model_identity("nli"), "device": "cuda", "precision": "fp32"}
        R.validate_pins("nli", pins)
        with self.assertRaises(ValueError):
            R.validate_pins("nli", {**pins, "device": "cpu"})
        runtime = {**R.RUNTIME_FIELDS, "gpu_name": "actual-test-device"}
        R.validate_runtime(runtime)
        with self.assertRaises(ValueError):
            R.validate_runtime({**runtime, "attention_implementation": "sdpa"})

    def test_partial_original_tail_is_preserved_but_import_is_blocked(self):
        raw = self.root / "raw"
        raw.mkdir()
        names = ("EXECUTION_FREEZE.json", "jev-chunk-scores.jsonl", "nli-chunk-scores.jsonl", "chunks.jsonl")
        for name in names:
            (raw / name).write_bytes(b"{}" if name == "nli-chunk-scores.jsonl" else b"{}\n")
        receipt = self.root / "recovery.json"
        R.write_json(receipt, {"remote_ref": R.ORIGINAL_REF, "version": 2, "terminal_status": "error",
                     "files": {name: {"size_bytes": (raw / name).stat().st_size, "sha256": R.sha(raw / name)} for name in names}})
        snapshot = self.root / "snapshot"
        with self.assertRaises(ValueError):
            R.snapshot_original_v2(raw, receipt, snapshot)
        self.assertEqual((snapshot / "nli-chunk-scores.jsonl").read_bytes(), b"{}")
        self.assertEqual(json.loads((snapshot / "RAW_SNAPSHOT.json").read_text())["partial_tail_files"], ["nli-chunk-scores.jsonl"])
        bad_receipt = json.loads(receipt.read_text())
        bad_receipt["version"] = 3
        R.write_json(receipt, bad_receipt)
        with self.assertRaises(ValueError):
            R.snapshot_original_v2(raw, receipt, self.root / "wrong-version")

    def test_partial_product_closure_cannot_be_published_as_complete(self):
        product = {"product_id": "one", "pair_refs": [1, 2], "case_ids": ["c1", "c2"],
                   "row_keys": [1, 2], "task_ids": [1, 2, 3]}
        product["closure_sha256"] = R.value_sha(product)
        closure = {"products": [product], "source_file_sha256": R.PINNED_SOURCE_JOIN_SHA}
        with patch.object(R, "PINNED_PRIVATE_CLOSURE_SHA", R.value_sha(closure)):
            R.validate_private_closure(closure)
            changed = copy.deepcopy(closure)
            changed["products"][0]["pair_refs"] = [1]
            with self.assertRaises(ValueError):
                R.validate_private_closure(changed)
            changed["products"][0]["closure_sha256"] = R.value_sha({k: v for k, v in changed["products"][0].items() if k != "closure_sha256"})
            with self.assertRaises(ValueError):
                R.validate_private_closure(changed)

    def test_new_cache_must_bind_exact_execution_index_state_and_shard(self):
        identities = {k: R.model_identity(k) for k in R.KINDS}
        plan = {"original_input_sha256": R.PINNED_INPUT_SHA, "original_bundle_manifest_sha256": R.ORIGINAL_BUNDLE_SHA,
                "model_identities": identities, "labels_read": False, "index_sha256": "original-index",
                "max_unique_requests_per_shard": 2,
                "shards": [{"shard_id": 0, "unique_request_count": 1, "request_keys": [["nli", "allowed-key"]]}]}
        freeze = {"original_input_sha256": R.PINNED_INPUT_SHA, "original_bundle_manifest_sha256": R.ORIGINAL_BUNDLE_SHA,
                  "model_identities": identities, "labels_read": False, "index_sha256": "original-index",
                  "state_bundle_manifest_sha256": "state-manifest", "shard_id": 0, "max_seconds": 6000}
        self.assertEqual(R.validate_worker_cache_origin(plan, freeze, "state-manifest"), {("nli", "allowed-key")})
        for field, wrong in (("index_sha256", "other-index"), ("state_bundle_manifest_sha256", "other-state"),
                             ("shard_id", 1), ("original_input_sha256", "other-input")):
            with self.assertRaises(ValueError):
                R.validate_worker_cache_origin(plan, {**freeze, field: wrong}, "state-manifest")

    def test_jev_sorted_json_maps_are_validated_without_changing_original_values(self):
        _, request, identity = self.first_request("jev")
        labels = [c["label"] for c in request["choices"]]
        logits = [0.1, 1.2, -0.3]
        probabilities = R.JEV._softmax(logits)
        original = {"state": "conflict", "status": "ok", "error_type": None, "cache_key": identity["legacy_key"],
                    "logits": dict(zip(labels, logits)), "probabilities": dict(zip(labels, probabilities)),
                    "token_counts": dict(zip(labels, [120, 122, 119]))}
        raw_bytes = R.canonical(original).encode("utf-8")
        raw = json.loads(raw_bytes)
        self.assertNotEqual(list(raw["probabilities"]), labels)
        self.assertFalse(R.validate_score("jev", raw, request, identity))
        self.assertEqual(R.canonical(raw).encode("utf-8"), raw_bytes)
        for field in ("state", "logits", "probabilities", "token_counts"):
            self.assertEqual(raw[field], original[field])

    def test_merge_must_preserve_existing_technical_unknown_and_occurrence_records(self):
        with self.assertRaises(ValueError):
            R.verify_resume_ready(self.db)
        self.db.execute("INSERT INTO metadata VALUES ('v2_origin_status','frozen_terminal_v2_imported')")
        self.db.execute("INSERT INTO metadata VALUES ('v2_raw_snapshot_sha256',?)", ("a" * 64,))
        entry, _, identity = self.first_request()
        unknown = nli_score(identity, technical=True)
        R.put_cache(self.db, "nli", entry["exact_key"], unknown, {"line_number": 1})
        occurrence = self.db.execute("SELECT * FROM occurrences WHERE kind='nli' AND exact_key=? LIMIT 1", (entry["exact_key"],)).fetchone()
        self.db.execute("INSERT INTO saved VALUES (?,?,?,?,?,?,?,?)", ("nli", occurrence["pair_ref"], occurrence["variant"],
                        occurrence["option_index"], occurrence["chunk_ref"], entry["exact_key"], R.canonical(unknown), R.canonical({"line_number": 1})))
        self.db.commit()
        target_path = self.root / "target.sqlite"
        shutil.copyfile(self.index, target_path)
        target = R.connect(target_path)
        try:
            R.validate_merge_base(self.db, target)
            target.execute("DELETE FROM cache")
            with self.assertRaises(ValueError):
                R.validate_merge_base(self.db, target)
            target.rollback()
            target.execute("UPDATE saved SET score=?", (R.canonical(nli_score(identity, "support")),))
            with self.assertRaises(ValueError):
                R.validate_merge_base(self.db, target)
        finally:
            target.close()

    def test_index_metadata_cannot_hide_missing_or_reassociated_requests(self):
        R.verify_index_identity(self.db)
        self.db.execute("DELETE FROM occurrences WHERE rowid=(SELECT min(rowid) FROM occurrences)")
        with self.assertRaises(ValueError):
            R.verify_index_identity(self.db)


if __name__ == "__main__":
    unittest.main()
