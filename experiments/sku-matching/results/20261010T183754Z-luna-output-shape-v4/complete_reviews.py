#!/usr/bin/env python3
"""Supply missing review deliveries while preserving every original raw answer."""
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    recovery = read(HERE / "review-recovery/manifest.json")
    original_path = HERE / recovery["original_answer"]
    request_path = HERE / recovery["request"]
    answer_path = HERE / recovery["answer"]
    assert digest(original_path) == recovery["original_answer_sha256"]
    assert digest(request_path) == recovery["request_sha256"]
    original, additional = read(original_path), read(answer_path)
    assert isinstance(additional, list)
    assert [c["case_id"] for c in additional] == recovery["missing_case_ids"]
    assert [c["case_id"] for c in original] == recovery["returned_case_ids"]
    by_id = {c["case_id"]: c for c in original + additional}
    assert len(by_id) == len(original) + len(additional) == 10
    manifest = read(HERE / "review-dispatch-manifest.json")
    entry = next(e for e in manifest["entries"] if HERE / e["answer"] == original_path)
    completed = [by_id[cid] for cid in entry["case_ids"]]
    source_hashes = {str(p): digest(p) for p in (original_path, request_path, answer_path)}
    # Reuse the original frozen validator; substitute one in-memory derived
    # array for its incomplete delivery. Never edit the original answer file.
    spec = importlib.util.spec_from_file_location("original_frozen_review_validator", HERE / "reviews.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_reader = module.read

    def completed_reader(path):
        return completed if path == original_path else original_reader(path)

    module.read = completed_reader
    module.collect()
    assert source_hashes == {str(p): digest(p) for p in (original_path, request_path, answer_path)}
    (HERE / "review-completion-derived.json").write_text(json.dumps({
        "original_raw_unchanged": True, "source_sha256": source_hashes,
        "missing_case_count": 9, "duplicate_case_count": 0,
        "complete_baseline_batch01": completed,
        "validator": "original frozen reviews.py collect", "human_verified": False,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
