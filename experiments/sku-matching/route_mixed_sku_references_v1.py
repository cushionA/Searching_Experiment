#!/usr/bin/env python3
"""Route only selected mixed-SKU references after prediction and gate freezes."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
STRATA = ("prior_errors", "independent_dev", "old_structural_control", "product_pair_diagnostic")
EXPECTED = {"prior_errors": 44, "independent_dev": 31, "old_structural_control": 7,
            "product_pair_diagnostic": 1}
VARIANTS = ("baseline", "product-top1-jev", "product-top3-jev", "product-top3-verified")
PREDICTIONS = tuple(name + "-predictions.jsonl" for name in VARIANTS)
CONTROL_ARCHIVE = ROOT / "experiments/sku-matching/results/20261010-sku-nonllm-v5-checkpoint.zip"
CONTROL_MEMBER = ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_file(path: Path):
    return json.loads(Path(path).read_bytes())


def jsonl_rows(path: Path):
    for raw in Path(path).read_bytes().splitlines():
        if raw.strip():
            yield json.loads(raw)


def write_rows(path: Path, values):
    with Path(path).open("xb") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n")


def verify_hash_file(path: Path, expected: str, kind: str):
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(kind + " has an invalid SHA256")
    if not path.is_file() or sha_file(path) != expected:
        raise ValueError(kind + " SHA256 differs: " + str(path))


def validate_gate_freeze(prepared_dir: Path, freeze_dir: Path):
    """Verify all source/prediction and gate hashes without opening labels."""
    prepared_dir, freeze_dir = Path(prepared_dir), Path(freeze_dir)
    prep_manifest = json_file(prepared_dir / "manifest.json")
    if (prep_manifest.get("schema_version") != "mixed-real-gpu-sku-inputs-v1"
            or prep_manifest.get("input_ready") is not True
            or prep_manifest.get("labels_read") is not False
            or prep_manifest.get("labels_in_model_inputs") is not False):
        raise ValueError("unsupported or non-ready mixed preparation")
    for name, expected in prep_manifest.get("output_sha256", {}).items():
        verify_hash_file(prepared_dir / name, expected, "prepared output")

    all_gates = json_file(freeze_dir / "all-gates-frozen.json")
    freeze = json_file(freeze_dir / "freeze.json")
    if (all_gates.get("labels_read") is not False
            or all_gates.get("all_predictions_and_gates_frozen") is not True
            or set(all_gates.get("gate_manifest_sha256", {})) != set(VARIANTS)
            or freeze.get("schema_version") != "mixed-gpu-sku-evaluation-v1"
            or freeze.get("labels_read") is not False
            or freeze.get("labels_in_model_inputs") is not False
            or freeze.get("labels_are_loaded_only_after_all_gates_frozen") is not True
            or freeze.get("execution_device") not in {"cpu", "cuda"}):
        raise ValueError("prediction/gate freeze does not prove a label-free completed run")
    freeze_gate_hashes = freeze.get("gate_manifest_sha256", {})
    if freeze_gate_hashes != all_gates["gate_manifest_sha256"]:
        raise ValueError("all-gates freeze and evaluation freeze disagree")
    eligibility_sha = freeze.get("case_eligibility_sha256")
    verify_hash_file(freeze_dir / "case-eligibility.jsonl", eligibility_sha, "case eligibility")

    # The frozen inventory is restricted to model inputs and predictions. This
    # prevents a malformed freeze from making label files part of validation.
    input_hashes = freeze.get("input_sha256")
    if not isinstance(input_hashes, dict) or not input_hashes:
        raise ValueError("evaluation freeze has no input/prediction hashes")
    found_predictions = set()
    for raw_path, digest in input_hashes.items():
        path = Path(raw_path)
        if any(token in part.lower() for part in path.parts for token in ("label", "reference", "gold", "truth")):
            raise ValueError("label path must not appear in the label-free freeze inventory")
        verify_hash_file(path, digest, "frozen input")
        if path.name in PREDICTIONS:
            found_predictions.add(path.name)
    if found_predictions != set(PREDICTIONS):
        raise ValueError("frozen prediction inventory is incomplete")

    for variant in VARIANTS:
        gate_dir = freeze_dir / variant
        manifest_path = gate_dir / "manifest.json"
        verify_hash_file(manifest_path, freeze_gate_hashes[variant], "gate manifest")
        gate_manifest = json_file(manifest_path)
        output_hashes = gate_manifest.get("output_sha256")
        if not isinstance(output_hashes, dict) or "decisions.jsonl" not in output_hashes:
            raise ValueError("frozen gate manifest lacks decision output hashes: " + variant)
        for relative, digest in output_hashes.items():
            verify_hash_file(gate_dir / relative, digest, "gate output")

    membership_path = prepared_dir / "membership.jsonl"
    if str(membership_path.resolve()) not in input_hashes:
        raise ValueError("freeze does not bind the supplied mixed membership file")
    for name in ("manifest.json", "requests.jsonl", "documents.jsonl", "tasks.jsonl",
                 "row_plans.jsonl", "case_plans.jsonl", "membership.jsonl", "gpu-cases.jsonl", "failures.jsonl"):
        if str((prepared_dir / name).resolve()) not in input_hashes:
            raise ValueError("freeze does not bind a required prepared input: " + name)
    if freeze.get("case_count") != prep_manifest.get("case_count"):
        raise ValueError("frozen gate case count differs from preparation")
    return prep_manifest, freeze


def indexed_membership(prepared_dir: Path):
    memberships = {}
    for member in jsonl_rows(Path(prepared_dir) / "membership.jsonl"):
        identity = member.get("case_id")
        if not isinstance(identity, str) or identity in memberships:
            raise ValueError("mixed membership has missing or duplicate case ids")
        strata = member.get("membership_strata")
        if not isinstance(strata, list) or len(strata) != 1 or strata[0] not in STRATA:
            raise ValueError("mixed membership has an unsupported stratum")
        memberships[identity] = member
    return memberships


def _source_route(kind, *, path=None, archive=None, archive_sha=None, member=None,
                  member_sha=None, line_number, offset, record_bytes, original=None):
    route = {"reference_kind": kind, "jsonl_line": line_number, "byte_offset": offset,
             "record_sha256": sha_bytes(record_bytes)}
    if path is not None:
        route.update({"source_path": str(Path(path).resolve()), "source_sha256": sha_file(Path(path))})
    if archive is not None:
        route.update({"source_archive": str(Path(archive).resolve()), "source_archive_sha256": archive_sha,
                      "member": member, "member_sha256": member_sha})
    if original is not None:
        route["original_route"] = original
    return route


def selected_records_from_bytes(payload: bytes, allowed_ids: set[str], kind: str, route_kwargs,
                               normalize_record=None):
    """Parse matching JSONL lines only; nonselected records stay opaque bytes."""
    if not allowed_ids:
        return {}
    matcher = re.compile(br'"case_id"\s*:\s*"(' + b"|".join(
        re.escape(value.encode("utf-8")) for value in sorted(allowed_ids, key=len, reverse=True)) + br')"')
    result, offset = {}, 0
    for line_number, line in enumerate(payload.splitlines(keepends=True), 1):
        body = line.rstrip(b"\r\n")
        found = matcher.search(body)
        if found:
            record = json.loads(body)
            identity = record.get("case_id")
            if identity not in allowed_ids:
                raise ValueError("selected route case id differs from exact byte filter")
            if identity in result:
                raise ValueError("duplicate selected reference: " + identity)
            if normalize_record is not None:
                record = normalize_record(record)
            original_route = record.get("scoring_reference_source")
            result[identity] = {**record, "scoring_reference_source": _source_route(
                kind, line_number=line_number, offset=offset, record_bytes=body,
                original=original_route, **route_kwargs)}
        offset += len(line)
    return result


def normalize_independent_label(record):
    """Map frozen human-gold expected states while preserving the source row."""
    expected = record.get("expected")
    if expected == "accept":
        label = "matched"
    elif expected == "drop":
        label = "unmatched"
    elif expected is None:
        label = "review"
    elif isinstance(expected, str) and expected in {
            "review", "uncertain", "unknown", "needs_review", "needs_confirmation"}:
        label = expected
    else:
        raise ValueError("selected independent label has unsupported expected state")
    return {**record, "source_label_record": dict(record), "label": label, "decision": label,
            "reference_uncertain": label in {"review", "uncertain", "unknown", "needs_review", "needs_confirmation"}}


def read_path_references(path: Path, allowed_ids: set[str], kind: str):
    path = Path(path)
    payload = path.read_bytes()
    return selected_records_from_bytes(payload, allowed_ids, kind, {"path": path})


def dev_reference_records(prepared_dir: Path, memberships: dict, allowed_ids: set[str], prep_manifest):
    by_version = {"independent_real_eval_v2": "v2", "independent_real_eval_v3": "v3"}
    gpu_cases = {row["case_id"]: row for row in jsonl_rows(Path(prepared_dir) / "gpu-cases.jsonl")}
    selected = {}
    for identity in allowed_ids:
        member = memberships[identity]["memberships"][0]
        cohort = member.get("source_cohort")
        version = by_version.get(cohort)
        if version is None or member.get("split") != "dev":
            raise ValueError("selected independent reference is not a v2/v3 dev case: " + identity)
        original = member.get("original_case") or {}
        ref = member.get("source_case_ref")
        archive = Path(member["source_archive"])
        archive_sha = member.get("source_archive_sha256")
        verify_hash_file(archive, archive_sha, "independent source archive")
        # The archive route is pinned by membership.jsonl, whose digest was
        # already checked against the prepared manifest and pre-label freeze.
        # The producer also pins raw members individually; it need not repeat
        # every capture ZIP in the top-level input file inventory.
        direct_pin = prep_manifest.get("input_sha256", {}).get(str(archive.resolve()))
        if direct_pin is not None and direct_pin != archive_sha:
            raise ValueError("independent source archive differs from its direct manifest pin")
        expected_member = f".lab-output/sku-independent-real-20261011/eval-{version}/run-token/inputs/cases.jsonl"
        if not isinstance(ref, dict) or ref.get("member") != expected_member:
            raise ValueError("selected independent case lacks its pinned case source route")
        host_case = gpu_cases.get(identity, {})
        group = (host_case.get("dev_group_provenance") or {}).get("original_group") or {}
        if (original.get("case_id") != identity or original.get("split") == "test"
                or host_case.get("split") != "dev" or group.get("split") != "dev"
                or group.get("excluded") or group.get("group_id") != host_case.get("group_id")
                or group.get("dossier_id") != original.get("dossier_id")):
            raise ValueError("selected independent case identity/split differs")
        with ZipFile(archive) as zipped:
            cases_payload = zipped.read(expected_member)
        if sha_bytes(cases_payload) != ref.get("member_sha256"):
            raise ValueError("selected original case input member SHA differs")
        offset = ref.get("byte_offset")
        if type(offset) is not int or offset < 0:
            raise ValueError("selected original case byte offset is invalid")
        source_case = json.loads(cases_payload[offset:].splitlines()[0])
        if source_case.get("case_id") != identity or source_case != original:
            raise ValueError("selected original case bytes differ from prepared membership")
        selected.setdefault((str(archive), archive_sha, version), set()).add(identity)

    records = {}
    for (archive_name, archive_sha, version), ids in selected.items():
        archive = Path(archive_name)
        with ZipFile(archive) as zipped:
            member_name = f".lab-output/sku-independent-real-20261011/eval-{version}/labels-token/private/resolved.jsonl"
            for member_name in (member_name,):
                payload = zipped.read(member_name)
                member_sha = sha_bytes(payload)
                routed = selected_records_from_bytes(payload, ids, "independent_dev", {
                    "archive": archive, "archive_sha": archive_sha, "member": member_name,
                    "member_sha": member_sha}, normalize_independent_label)
                for identity, record in routed.items():
                    source_member = memberships[identity]["memberships"][0]
                    source_case_ref = source_member["source_case_ref"]
                    record["scoring_reference_source"].update({
                        "source_case_member": source_case_ref["member"],
                        "source_case_member_sha256": source_case_ref["member_sha256"],
                        "source_case_line": source_case_ref.get("line"),
                        "source_case_byte_offset": source_case_ref.get("byte_offset"),
                        "source_case_split": source_member["original_case"].get("split"),
                        "eligible_group_split": "dev"})
                overlap = records.keys() & routed.keys()
                if overlap:
                    raise ValueError("duplicate selected independent labels: " + ", ".join(sorted(overlap)))
                records.update(routed)
    if set(records) != allowed_ids:
        raise ValueError("selected independent-dev reference coverage is incomplete")
    return records


def control_reference_records(archive_path: Path, allowed_ids: set[str], member_name=CONTROL_MEMBER):
    archive_path = Path(archive_path)
    archive_sha = sha_file(archive_path)
    with ZipFile(archive_path) as zipped:
        if member_name not in zipped.namelist():
            raise ValueError("control reference member is missing from frozen source archive")
        payload = zipped.read(member_name)
    return selected_records_from_bytes(payload, allowed_ids, "old_structural_control", {
        "archive": archive_path, "archive_sha": archive_sha, "member": member_name,
        "member_sha": sha_bytes(payload)})


def route(prepared_dir: Path, freeze_dir: Path, prior_error_references: Path,
          controls_archive: Path, output_dir: Path, *, control_member=CONTROL_MEMBER,
          allow_unresolved_controls=False,
          expected_counts=EXPECTED):
    prepared_dir, freeze_dir, output_dir = Path(prepared_dir), Path(freeze_dir), Path(output_dir)
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite routed references")

    # This must remain the first operation that can precede reading source labels.
    prep_manifest, freeze = validate_gate_freeze(prepared_dir, freeze_dir)
    membership = indexed_membership(prepared_dir)
    strata = {identity: member["membership_strata"][0] for identity, member in membership.items()}
    for variant in VARIANTS:
        decision_ids = {row.get("case_id") for row in jsonl_rows(freeze_dir / variant / "decisions.jsonl")}
        if decision_ids != set(membership):
            raise ValueError("frozen gate decisions do not cover mixed membership: " + variant)
    eligibility_ids = {row.get("case_id") for row in jsonl_rows(freeze_dir / "case-eligibility.jsonl")}
    if eligibility_ids != set(membership):
        raise ValueError("frozen case eligibility does not cover mixed membership")
    counts = Counter(strata.values())
    if expected_counts is not None and dict(counts) != expected_counts:
        raise ValueError("mixed cohort cardinalities differ from requested reference routes")
    primary = {identity for identity, member in membership.items()
               if member["memberships"][0].get("primary_score_eligible") is True}
    dev_ids = {identity for identity, stratum in strata.items() if stratum == "independent_dev"}
    diag_ids = {identity for identity, stratum in strata.items() if stratum == "product_pair_diagnostic"}
    if not dev_ids <= primary or dev_ids & diag_ids:
        raise ValueError("independent label route contains diagnostic/nonprimary membership")

    prior_ids = {identity for identity, stratum in strata.items() if stratum == "prior_errors"}
    control_ids = {identity for identity, stratum in strata.items() if stratum == "old_structural_control"}
    prior = read_path_references(Path(prior_error_references), prior_ids, "prior_errors")
    dev = dev_reference_records(prepared_dir, membership, dev_ids, prep_manifest)
    controls = control_reference_records(Path(controls_archive), control_ids, control_member)
    for kind, values, wanted in (("prior errors", prior, prior_ids), ("independent dev", dev, dev_ids)):
        if set(values) != wanted:
            raise ValueError(kind + " routed reference coverage differs from selected membership")
    missing_controls = control_ids - set(controls)
    if missing_controls and not allow_unresolved_controls:
        raise ValueError("some selected controls have no source reference; use explicit unresolved-control mode")
    if (set(prior) & set(dev)) or (set(prior) & set(controls)) or (set(dev) & set(controls)):
        raise ValueError("reference strata overlap")
    if set(prior) | set(dev) | set(controls) | missing_controls != (set(membership) - diag_ids):
        raise ValueError("routed and explicitly missing references do not cover exactly the selected scoring cohort")

    output_dir.mkdir(parents=True)
    outputs = {"prior-errors.jsonl": list(prior.values()), "independent-dev.jsonl": list(dev.values()),
               "old-structural-controls.jsonl": list(controls.values()),
               "missing-control-references.jsonl": [{"case_id": identity,
                   "stratum": "old_structural_control", "reference_state": "unresolved_no_source_label",
                   "labels_read": False} for identity in sorted(missing_controls)]}
    for name, values in outputs.items():
        write_rows(output_dir / name, values)
    manifest = {"schema_version": "mixed-sku-reference-routes-v1", "labels_read_after_freeze": True,
        "prediction_and_gate_freeze_sha256": sha_file(freeze_dir / "freeze.json"),
        "all_gates_frozen_sha256": sha_file(freeze_dir / "all-gates-frozen.json"),
        "prepared_manifest_sha256": sha_file(prepared_dir / "manifest.json"),
        "membership_sha256": sha_file(prepared_dir / "membership.jsonl"),
        "routed_case_counts": {"prior_errors": len(prior), "independent_dev": len(dev),
                                "old_structural_control": len(controls),
                                "old_structural_control_unresolved": len(missing_controls),
                                "product_pair_diagnostic": 0},
        "diagnostic_labels_read": False, "independent_test_labels_read": False,
        "allow_unresolved_controls": bool(allow_unresolved_controls),
        "unresolved_control_case_ids": sorted(missing_controls),
        "source_reference_files": {"prior_errors": str(Path(prior_error_references).resolve()),
                                   "prior_errors_sha256": sha_file(Path(prior_error_references)),
                                   "controls_archive": str(Path(controls_archive).resolve()),
                                   "controls_archive_sha256": sha_file(Path(controls_archive)),
                                   "controls_member": control_member},
        "output_sha256": {name: sha_file(output_dir / name) for name in outputs}}
    with (output_dir / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--freeze-dir", type=Path, required=True,
                        help="evaluation output containing all-gates-frozen.json and freeze.json")
    parser.add_argument("--prior-error-references", type=Path, required=True)
    parser.add_argument("--controls-archive", type=Path, default=CONTROL_ARCHIVE)
    parser.add_argument("--controls-member", dest="control_member", default=CONTROL_MEMBER)
    parser.add_argument("--allow-unresolved-controls", action="store_true",
                        help="record missing legacy/family control references without inventing labels")
    parser.add_argument("--out", dest="output_dir", type=Path, required=True)
    args = vars(parser.parse_args(argv))
    result = route(**args)
    print(json.dumps(result["routed_case_counts"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
