#!/usr/bin/env python3
"""Remove only exact repeated source citation rows; preserve all response semantics."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonicalize_response(response: Any) -> tuple[Any, dict[str, Any]]:
    """Return a copied response and an auditable report; malformed/ambiguous rows fail closed."""
    base = {"status": "unchanged_fail_closed", "removed_entries": 0}
    try:
        if not isinstance(response, dict):
            return copy.deepcopy(response), {**base, "reason": "response_not_object"}
        table = response.get("citation_table")
        if not isinstance(table, list):
            return copy.deepcopy(response), {**base, "reason": "missing_or_invalid_citation_table"}
        signatures: dict[str, bytes] = {}
        for citation in table:
            if not isinstance(citation, dict):
                return copy.deepcopy(response), {**base, "reason": "citation_entry_not_object"}
            citation_id = citation.get("citation_id")
            if not isinstance(citation_id, str) or not citation_id:
                return copy.deepcopy(response), {**base, "reason": "missing_or_invalid_citation_id"}
            signature = canonical(citation)
            previous = signatures.get(citation_id)
            if previous is not None and previous != signature:
                return copy.deepcopy(response), {**base, "status": "ambiguous_fail_closed",
                                                  "reason": "conflicting_entries_for_citation_id",
                                                  "citation_id": citation_id}
            signatures[citation_id] = signature

        result = copy.deepcopy(response)
        kept: list[dict[str, Any]] = []
        first_by_id: dict[str, bytes] = {}
        removed = 0
        for citation in table:
            citation_id = citation["citation_id"]
            signature = canonical(citation)
            if citation_id in first_by_id:
                if first_by_id[citation_id] != signature:
                    raise AssertionError("conflict escaped preflight")
                removed += 1
            else:
                first_by_id[citation_id] = signature
                kept.append(copy.deepcopy(citation))
        result["citation_table"] = kept

        before_non_table = {k: v for k, v in response.items() if k != "citation_table"}
        after_non_table = {k: v for k, v in result.items() if k != "citation_table"}
        if canonical(before_non_table) != canonical(after_non_table):
            raise AssertionError("canonicalizer changed response fields outside citation_table")
        if "judgments" in response and canonical(response["judgments"]) != canonical(result["judgments"]):
            raise AssertionError("canonicalizer changed project judgments")
        before_map = {cid: sig for cid, sig in signatures.items()}
        after_map = {row["citation_id"]: canonical(row) for row in kept}
        if before_map != after_map:
            raise AssertionError("citation_id to full-entry mapping changed")
        return result, {"status": "deduplicated" if removed else "unchanged",
                        "removed_entries": removed}
    except (TypeError, ValueError, OverflowError):
        return copy.deepcopy(response), {**base, "reason": "noncanonical_json_value"}


def read_rows(path: Path) -> list[Any]:
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_rows(path: Path, rows: list[Any]) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical(row).decode("utf-8") + "\n")


def canonicalize_file(responses_path: Path, output_dir: Path) -> dict[str, Any]:
    responses_path, output_dir = Path(responses_path).resolve(), Path(output_dir).resolve()
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite output directory: " + str(output_dir))
    responses = read_rows(responses_path)
    canonicalized, reports = [], []
    for index, response in enumerate(responses):
        result, report = canonicalize_response(response)
        canonicalized.append(result)
        reports.append({"response_index": index, **report})
    output_dir.mkdir(parents=True)
    output_path, report_path = output_dir / "responses.jsonl", output_dir / "change-report.jsonl"
    write_rows(output_path, canonicalized)
    write_rows(report_path, reports)
    receipt = {"schema_version": "source-citation-table-canonicalizer-v1",
               "input_responses_sha256": sha_file(responses_path),
               "input_response_count": len(responses), "output_response_count": len(canonicalized),
               "output_responses_sha256": sha_file(output_path),
               "change_report_sha256": sha_file(report_path),
               "removed_duplicate_entries": sum(r["removed_entries"] for r in reports),
               "ambiguous_fail_closed_responses": sum(r["status"] == "ambiguous_fail_closed" for r in reports),
               "unchanged_fail_closed_responses": sum(r["status"] == "unchanged_fail_closed" for r in reports),
               "judgments_changed": False, "all_fields_outside_citation_table_changed": False,
               "citation_content_or_ids_rewritten": False, "inference_or_label_data_read": False}
    with (output_dir / "receipt.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    return {**receipt, "output_dir": str(output_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--responses", type=Path, required=True, help="JSONL response objects")
    parser.add_argument("--output-dir", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    print(json.dumps(canonicalize_file(args.responses, args.output_dir), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
