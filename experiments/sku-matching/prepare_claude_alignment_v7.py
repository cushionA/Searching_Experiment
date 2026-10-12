#!/usr/bin/env python3
"""Prepare pinned v7 observations for the existing experimental binary gate.

This reads archive data, never imports its code or opens labels/raw sources.
Only complete NFKC-identical values can bypass whole-condition verification.
Core/residual decomposition is checked as provenance, never treated as proof.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata
from zipfile import ZipFile

from integrate_claude_description_v1 import axis_text, document, encoded, index, sha, verify_outputs, write_rows
from import_claude_alignment_handoff_v1 import jsonl, parse_json, verify_source
from product_condition_sharing_v1 import SEMANTIC_FIELDS

ROOT = Path(__file__).resolve().parents[2]
VERSION = "claude-alignment-v7-description-prepare-v1"
UPSTREAM_HEAD = "a15ed1e3b57607e07b69ef56e6921f6976ca48e7"
ARCHIVE_SHA256 = "f5522952c8a67d5a7289cdb6f9cdcb5f2d093026a79360b704c08fd68dd5503c"
CODE_MEMBER = "code/v7-7fd8e17/align_sku_conditions.py"
CODE_SHA256 = "173c9c90157278147740f3c885ba5fae9144152b0d20493a19056eb3efba5a7e"
INNER_MEMBER = "20261012-sku-align-v6-v7-outputs.inner-manifest.json"
COHORTS = ("legacy", "family")
INPUT_SHA256 = {
    "cases.jsonl": "1b758132e051d96f7672476236d3cdfc49ab689984fe78de91e3c517fbdf39e3",
    "products.jsonl": "7ed4e6896fba2a47fb262aa855c6cb335b4e5a64ff7713c4cbf6f44a85837acf",
    "manifest.json": "f0850669816add232530b1e2140016dfc47132bc35b4c38b249707846e1a2e86",
}
BRACKETED = re.compile(r"[（(【［\[]([^）)】］\]]*)[）)】］\]]")
SLASH = re.compile(r"(?<!\d)/|/(?!\d)")


def whole(value):
    if not isinstance(value, str) or not value:
        raise ValueError("whole selected values must be nonempty strings")
    return unicodedata.normalize("NFKC", value)


def finite(value):
    """Keep producer NaN as missing metadata; original line remains preserved."""
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("infinite producer value")
    if isinstance(value, dict):
        return {key: finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [finite(item) for item in value]
    return value


def decomposition(rakuten, au):
    # Local syntax validation of the pinned producer's hints. This result never
    # establishes alignment, even for identical sorted/slash cores.
    def parts(value):
        text = "".join(whole(value).split())
        return sorted(p for p in SLASH.split(BRACKETED.sub("", text)) if p), [p for p in BRACKETED.findall(text) if p]
    (rc, ra), (ac, aa) = parts(rakuten), parts(au)
    if not rc or not ac:
        return None
    identical, left_r, left_a = True, [], []
    if rc == ac:
        pass
    elif len(rc) == 1 and rc[0] in ac:
        left_a = [p for p in ac if p != rc[0]]
    elif len(ac) == 1 and ac[0] in rc:
        left_r = [p for p in rc if p != ac[0]]
    else:
        identical = False
    residual = ([{"side": "rakuten", "text": p, "source_value": rakuten, "verify_with": "au"}
                 for p in left_r + [p for p in ra if p not in aa]]
                + [{"side": "au", "text": p, "source_value": au, "verify_with": "rakuten"}
                   for p in left_a + [p for p in aa if p not in ra]])
    return {"core": {"identical": identical, "rakuten": rc, "au": ac}, "residual": residual}


def read_archive(archive, expected_sha256, external_manifest=None):
    data = archive.read_bytes()
    digest = sha(data)
    if digest != expected_sha256:
        raise ValueError("archive SHA256 differs from the pinned/external expected digest")
    external_manifest = external_manifest or archive.with_suffix(".manifest.json")
    sidecar = Path(str(archive) + ".sha256")
    external, external_paths = None, []
    if external_manifest.exists():
        external = parse_json(external_manifest.read_bytes(), str(external_manifest))
        if external.get("sha256") != digest or external.get("size_bytes") != len(data) or external.get("archive") != archive.name:
            raise ValueError("external archive manifest binding differs")
        external_paths.append(external_manifest)
    elif external_manifest != archive.with_suffix(".manifest.json"):
        raise ValueError("external archive manifest is missing")
    if sidecar.exists():
        tokens = sidecar.read_text(encoding="utf-8").strip().split()
        if len(tokens) != 2 or tokens[0] != digest or tokens[1].lstrip("*") != archive.name:
            raise ValueError("archive sidecar binding differs")
        external_paths.append(sidecar)
    if expected_sha256 == ARCHIVE_SHA256 and external is None:
        raise ValueError("pinned production archive requires its external manifest")
    payload = {}
    with ZipFile(archive) as zipped:
        seen = set()
        for info in zipped.infolist():
            name, path = info.filename, PurePosixPath(info.filename)
            if (not path.parts or name in seen or path.is_absolute() or ".." in path.parts or "\\" in name
                    or ":" in path.parts[0] or str(path) != name or info.is_dir()):
                raise ValueError("unsafe or duplicate ZIP member")
            seen.add(name)
            if info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("encrypted or symlink ZIP member")
            payload[name] = zipped.read(info)
    if INNER_MEMBER not in payload:
        raise ValueError("embedded v7 manifest is missing")
    inner = parse_json(payload[INNER_MEMBER], INNER_MEMBER)
    if inner.get("archive") != archive.name or not isinstance(inner.get("runs"), list):
        raise ValueError("embedded archive manifest binding differs")
    expected_members, runs = {}, {}
    for run in inner["runs"]:
        code = run.get("code_in_zip")
        if not isinstance(code, str) or code not in payload or sha(payload[code]) != run.get("code_sha256"):
            raise ValueError("archived producer code SHA differs")
        if code in expected_members and expected_members[code] != run["code_sha256"]:
            raise ValueError("conflicting archived code SHA")
        expected_members[code] = run["code_sha256"]
        for spec in run.get("files", []):
            member = spec.get("path")
            if (member in expected_members or member not in payload or spec.get("bytes") != len(payload[member])
                    or spec.get("sha256") != sha(payload[member])):
                raise ValueError("archive member fails embedded size/SHA256 check")
            expected_members[member] = spec["sha256"]
        if run.get("version") == "v7" and run.get("run") in COHORTS:
            cohort = run["run"]
            if cohort in runs or run.get("commit") != "7fd8e17" or code != CODE_MEMBER or run["code_sha256"] != CODE_SHA256:
                raise ValueError("selected v7 run/code pin differs")
            if run.get("out_dir") != f".lab-output/sku-align-20261012-v7/{cohort}":
                raise ValueError("selected v7 member path differs")
            runs[cohort] = run
    if set(expected_members) != set(payload) - {INNER_MEMBER} or set(runs) != set(COHORTS):
        raise ValueError("archive members or selected cohorts differ from embedded manifest")
    if external:
        if external.get("file_count") != len(payload) or external.get("uncompressed_bytes") != sum(map(len, payload.values())):
            raise ValueError("external archive member/byte count differs")
        for cohort, run in runs.items():
            peers = [r for r in external.get("runs", []) if r.get("version") == "v7" and r.get("run") == cohort]
            if len(peers) != 1 or any(peers[0].get(key) != run.get(key) for key in
                    ("commit", "code_sha256", "inputs_sha256", "similarities_sha256", "decision_reasons", "handoff_rows", "handoff_conditions")):
                raise ValueError("external and embedded selected run metadata differ")
    return digest, inner, payload, runs, external_paths


def au_options(product):
    return {name: list(dict.fromkeys(axis["value"] for row in product["au"]["rows"] for axis in row["axes"] if axis["axis_name"] == name))
            for name in dict.fromkeys(axis["axis_name"] for row in product["au"]["rows"] for axis in row["axes"])}


def validate_span(span, value, registry, location):
    verify_source(span, span, registry, location)
    if (type(span.get("start")) is not int or type(span.get("end")) is not int or span["start"] < 0
            or span["end"] - span["start"] != len(value) or span.get("quote") != value or not isinstance(span.get("locator"), dict)):
        raise ValueError(f"malformed original source span: {location}")


def validate_handoff(handoff, case, product, row, registry, location):
    for key in ("case_id", "dossier_id", "au_product_id"):
        if handoff.get(key) != case.get(key):
            raise ValueError(f"fixed case/product binding differs: {location}")
    sku = handoff.get("rakuten_sku", {})
    for key in ("source_sku_key", "sku_record_key", "variant_id", "url"):
        if not sku.get(key) or sku.get(key) != case["rakuten"].get(key):
            raise ValueError(f"fixed Rakuten SKU/URL binding differs: {location}")
    if case["rakuten"]["url"] != product["rakuten"].get("url"):
        raise ValueError("fixed Rakuten product URL differs")
    verify_source(handoff.get("au_product_source"), product["au"].get("title_source"), registry, location)
    expected_rak_source = product["rakuten"].get("title_source") or next((a.get("value_span") for a in case["rakuten"]["axes"]), None)
    verify_source(sku, expected_rak_source, registry, location)
    rak = index(case["rakuten"]["axes"], "axis_key", location)
    expected_axes = {key: {field: axis[field] for field in ("axis_label", "value", "family_values", "value_span")} for key, axis in rak.items()}
    expected_au = [{key: axis[key] for key in ("axis_name", "value", "value_span")} for axis in row["axes"]]
    if handoff.get("rakuten_axes") != expected_axes or handoff.get("au_row_axes") != expected_au or handoff.get("au_axis_options") != au_options(product):
        raise ValueError("handoff whole axes/options/spans differ from fixed originals")
    conditions = handoff.get("conditions")
    if not isinstance(conditions, list):
        raise ValueError("handoff conditions must be a list")
    au = index(row["axes"], "axis_name", location)
    represented_rak, represented_au = set(), set()
    for condition in conditions:
        status = condition.get("status")
        if status not in {"one_sided", "aligned", "residual", "model_candidate"}:
            raise ValueError("unsupported pending producer condition status")
        if condition.get("kind") == "rakuten":
            key = condition.get("axis_key")
            if key not in rak or key in represented_rak or condition.get("axis_label") != rak[key]["axis_label"] or condition.get("value") != rak[key]["value"]:
                raise ValueError("condition differs from/repeats original Rakuten whole axis")
            represented_rak.add(key)
            name = condition.get("au_axis")
            if status == "one_sided":
                if name is not None or condition.get("au_value") is not None or condition.get("core") is not None or condition.get("residual") is not None:
                    raise ValueError("one-sided condition contains a mapped AU/core/residual")
            else:
                if name not in au or name in represented_au or condition.get("au_value") != au[name]["value"]:
                    raise ValueError("mapped AU condition is not uniquely on fixed row")
                represented_au.add(name)
                expected = decomposition(condition["value"], condition["au_value"])
                if condition.get("core") is None:
                    if status != "aligned" or condition.get("residual") is not None or whole(condition["value"]).replace(" ", "") != whole(condition["au_value"]).replace(" ", ""):
                        raise ValueError("condition lacks valid original core/residual metadata")
                elif expected is None or condition.get("core") != expected["core"] or condition.get("residual") != expected["residual"]:
                    raise ValueError("core/residual side/source_value/verify_with differs from original whole values")
        elif condition.get("kind") == "au_only":
            name = condition.get("au_axis")
            if (status != "one_sided" or name not in au or name in represented_au or condition.get("au_value") != au[name]["value"]
                    or condition.get("core") is not None or condition.get("residual") is not None):
                raise ValueError("AU-only condition differs from original fixed row")
            represented_au.add(name)
        else:
            raise ValueError("unsupported producer condition kind")
    if represented_rak != set(rak):
        raise ValueError("handoff omits original Rakuten axes")


def prepare(input_dir: Path, archive: Path, output_dir: Path, expected_sha256: str | None = None,
            external_manifest: Path | None = None) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    archive_sha, inner, payload, runs, external_paths = read_archive(archive, expected_sha256 or ARCHIVE_SHA256, external_manifest)
    input_bytes = {name: (input_dir / name).read_bytes() for name in INPUT_SHA256}
    generic = parse_json(input_bytes["manifest.json"], "generic manifest")
    verify_outputs(input_dir, generic, ("cases.jsonl", "products.jsonl"))
    # Synthetic fixtures must opt into an explicit archive digest, and can never
    # use the real archive. Real inputs are pinned including their manifest.
    synthetic = generic.get("synthetic") is True
    if synthetic:
        if expected_sha256 is None or archive_sha == ARCHIVE_SHA256:
            raise ValueError("synthetic fixtures require their own explicit archive SHA")
    else:
        if archive_sha != ARCHIVE_SHA256:
            raise ValueError("real inputs require the pinned production archive SHA")
        if any(sha(data) != INPUT_SHA256[name] for name, data in input_bytes.items()):
            raise ValueError("generic cases/products/manifest differ from pinned inputs")
    registry = generic.get("source_raw_sha256")
    if not isinstance(registry, dict):
        raise ValueError("generic raw source registry is missing")
    case_rows = list(jsonl(input_bytes["cases.jsonl"], "cases.jsonl"))
    product_rows = list(jsonl(input_bytes["products.jsonl"], "products.jsonl"))
    cases = index([value for _, value, _ in case_rows], "case_id", "cases")
    products = index([value for _, value, _ in product_rows], "dossier_id", "products")
    product_lines = {value["dossier_id"]: number for number, value, _ in product_rows}
    case_lines = {value["case_id"]: number for number, value, _ in case_rows}
    paths = [archive, *external_paths, *(input_dir / name for name in input_bytes)]
    upstream_head_file = archive.with_name("upstream-head.txt")
    if upstream_head_file.exists():
        if upstream_head_file.read_text(encoding="utf-8").strip() != UPSTREAM_HEAD:
            raise ValueError("upstream HEAD sidecar differs from v7 pin")
        paths.append(upstream_head_file)
    input_shas = {str(path.resolve()): sha(path.read_bytes()) for path in paths}
    documents, requests, tasks, row_plans, case_plans, sources = {}, {}, [], [], [], []
    counts = Counter()
    generic_shas = {name: sha(data) for name, data in input_bytes.items()}
    member_shas = {name: sha(data) for name, data in payload.items()}

    def source(member, number, line):
        return {"archive": str(archive.resolve()), "archive_sha256": archive_sha, "member": member,
                "member_sha256": member_shas[member], "line": number, "line_sha256": sha(line), "raw_source_line": line.decode("utf-8")}

    def get_document(product, side):
        key = product["dossier_id"], side
        if key not in documents:
            documents[key] = document(product, side, {"path": str((input_dir / "products.jsonl").resolve()),
                "sha256": generic_shas["products.jsonl"], "line": product_lines[product["dossier_id"]]})
        return documents[key]

    def add_task(case, product, row, direction, axis, options, origin, provenance, handoff=None, condition=None):
        if not isinstance(options, list) or any(not isinstance(value, str) for value in options) or axis["value"] not in options:
            raise ValueError("complete original alternatives must contain selected whole value")
        side = "au" if direction == "rakuten_to_au" else "rakuten"
        doc = get_document(product, side)
        target_axes = [{"axis_name": a["axis_name"], "value": a["value"]} for a in row["axes"]] if side == "au" else [
            {"axis_name": a["axis_label"], "value": a["value"]} for a in case["rakuten"]["axes"]]
        hints = copy.deepcopy(condition.get("residual") or []) if condition else []
        semantic = {"direction": direction, "axis_name": axis["axis_label"] if side == "au" else axis["axis_name"],
                    "selected_value": axis["value"], "option_values": copy.deepcopy(options), "target_document_id": doc["document_id"],
                    "target_document_sha256": doc["document_sha256"], "target_selected_axes": target_axes,
                    "target_selected_axes_text": axis_text(target_axes)}
        digest = sha(encoded(semantic))
        request_id = "generic-description:" + digest
        identity = {"case_id": case["case_id"], "au_row_key": row["row_key"], "direction": direction,
                    "axis_key": axis.get("axis_key", axis.get("axis_name"))}
        task_id = "condition:" + sha(encoded(identity))
        request = requests.setdefault(request_id, {"request_id": request_id, "request_sha256": digest, **semantic,
            "selected_value_is_binding_only": True, "applicability_required": True, "production_adoption_allowed": False,
            "origin": origin, "origins": [], "focused_verification_hints": [], "task_provenance_hints": [],
            "task_metadata_is_authoritative": True, "focused_verification_hints_are_binding_metadata_only": True,
            "upstream_core_is_proof": False})
        # Preserve the established eight-field request identity. Producer status
        # and decomposition belong to each task, and cannot alter this model
        # input or authorize product-global sharing. Keep every binding's hints
        # as auxiliary provenance so deduplication never silently chooses one.
        if origin not in request["origins"]:
            request["origins"].append(origin)
        request["origin"] = origin if len(request["origins"]) == 1 else "mixed_task_provenance"
        request["task_provenance_hints"].append({"task_id": task_id, "origin": origin,
            "producer_status": condition.get("status") if condition else None, "focused_verification_hints": copy.deepcopy(hints)})
        for hint in hints:
            if hint not in request["focused_verification_hints"]:
                request["focused_verification_hints"].append(copy.deepcopy(hint))
        lineage = copy.deepcopy(provenance)
        if handoff is not None:
            lineage.update({key: copy.deepcopy(handoff[key]) for key in ("conditions", "rakuten_axes", "au_row_axes", "au_axis_options")})
        tasks.append({"task_id": task_id, **identity, "request_id": request_id, "dossier_id": case["dossier_id"],
            "au_product_id": case["au_product_id"], "source_sku_key": case["rakuten"]["source_sku_key"],
            "raw_condition": copy.deepcopy(axis), "option_values": copy.deepcopy(options), "origin": origin,
            "source_ref": copy.deepcopy(provenance), "source_handoff": lineage if handoff is not None else None,
            "producer_condition": copy.deepcopy(condition), "focused_verification_hints": hints,
            "selected_au_row_ref": {"path": str((input_dir / "products.jsonl").resolve()), "sha256": generic_shas["products.jsonl"],
                "line": product_lines[product["dossier_id"]], "json_path": f"$.au.rows[{product['au']['rows'].index(row)}]"},
            "selected_rakuten_sku_ref": {"path": str((input_dir / "cases.jsonl").resolve()), "sha256": generic_shas["cases.jsonl"],
                "line": case_lines[case["case_id"]], "case_id": case["case_id"], "json_path": "$.rakuten"},
            "status": "not_evaluated", "verification_required": True, "production_adoption_allowed": False})
        counts["task:" + direction] += 1
        counts["origin:" + origin] += 1
        return {"kind": "model", "task_id": task_id, "request_id": request_id, "direction": direction, "axis_key": identity["axis_key"]}

    for cohort in COHORTS:
        run = runs[cohort]
        parent = run["out_dir"] + "/align/"
        names = {name: parent + name for name in ("handoff.jsonl", "decisions.jsonl", "summary.json")}
        summary = parse_json(payload[names["summary.json"]], names["summary.json"])
        if summary.get("code_sha256") != CODE_SHA256 or summary.get("similarities_sha256") != run.get("similarities_sha256"):
            raise ValueError("selected summary/code/similarity binding differs")
        for name in ("handoff.jsonl", "decisions.jsonl"):
            if summary.get("outputs_sha256", {}).get(name) != sha(payload[names[name]]):
                raise ValueError("selected summary output SHA differs")
        decision_rows = list(jsonl(payload[names["decisions.jsonl"]], names["decisions.jsonl"]))
        decisions = index([d for _, d, _ in decision_rows], "case_id", cohort)
        expected_cases = {key for key, case in cases.items() if case.get("cohort") == cohort}
        if set(decisions) != expected_cases:
            raise ValueError("upstream decisions do not cover exactly frozen cohort")
        for field, key in (("decisions", "decision"), ("decision_reasons", "reason")):
            actual = dict(Counter(d[key] for d in decisions.values()))
            if summary.get(field) != actual or field == "decision_reasons" and run.get(field) != actual:
                raise ValueError("upstream declared decision counts differ")
        contexts, condition_counts = {}, Counter()
        for number, original, line in jsonl(payload[names["handoff.jsonl"]], names["handoff.jsonl"]):
            h = finite(original)
            case = cases.get(h.get("case_id"))
            if case is None or case.get("cohort") != cohort:
                raise ValueError("handoff case/cohort differs")
            product = products.get(case["dossier_id"])
            if product is None or product["au"].get("product_id") != case["au_product_id"]:
                raise ValueError("fixed AU product differs")
            fixed_rows = index(product["au"]["rows"], "row_key", case["case_id"])
            row = fixed_rows.get(h.get("au_row_key"))
            key = case["case_id"], h.get("au_row_key")
            if row is None or key in contexts:
                raise ValueError("unknown or repeated fixed handoff AU row")
            validate_handoff(h, case, product, row, registry, names["handoff.jsonl"])
            contexts[key] = h, source(names["handoff.jsonl"], number, line)
            condition_counts.update(f"{c['kind']}:{c['status']}" for c in h["conditions"] if c["status"] != "aligned")
        if any(summary.get(key) != actual or run.get(key) != actual for key, actual in
               (("handoff_rows", len(contexts)), ("handoff_conditions", dict(condition_counts)))):
            raise ValueError("upstream handoff row/condition counts differ")
        sources.append({"member": names["handoff.jsonl"], "sha256": sha(payload[names["handoff.jsonl"]]), "producer_summary": summary})
        for number, decision, line in decision_rows:
            case, provenance = cases[decision["case_id"]], source(names["decisions.jsonl"], number, line)
            product = products[case["dossier_id"]]
            if decision.get("dossier_id") != case["dossier_id"] or product["au"]["product_id"] != case["au_product_id"]:
                raise ValueError("upstream decision fixed product differs")
            if (case["rakuten"].get("url") != product["rakuten"].get("url") or
                    any(not isinstance(case["rakuten"].get(key), str) or not case["rakuten"][key]
                        for key in ("url", "source_sku_key", "sku_record_key", "variant_id"))):
                raise ValueError("fixed Rakuten SKU/product URL binding differs")
            fixed_rows = index(product["au"]["rows"], "row_key", case["case_id"])
            rak_axes = index(case["rakuten"]["axes"], "axis_key", case["case_id"])
            if len({a.get("axis_index") for a in rak_axes.values()}) != len(rak_axes):
                raise ValueError("duplicate Rakuten input axis index")
            for axis in rak_axes.values():
                validate_span(axis["value_span"], axis["value"], registry, case["case_id"])
                if axis.get("axis_label_span") is not None:
                    validate_span(axis["axis_label_span"], axis["axis_label"], registry, case["case_id"])
            for row in fixed_rows.values():
                for axis in row["axes"]:
                    validate_span(axis["value_span"], axis["value"], registry, case["case_id"])
                    if axis.get("axis_name_span") is not None:
                        validate_span(axis["axis_name_span"], axis["axis_name"], registry, case["case_id"])
            states = decision.get("row_states")
            if (not isinstance(states, dict) or any(key not in {"contradiction", "aligned", "pending_description_check", "symmetric_unresolved"}
                    or type(value) is not int or value < 0 for key, value in states.items()) or sum(states.values()) != len(fixed_rows)):
                raise ValueError("upstream row states do not cover fixed AU row array")
            pending = [key[1] for key in contexts if key[0] == case["case_id"]]
            if len(pending) != states.get("pending_description_check", 0):
                raise ValueError("upstream pending count differs from received handoff")
            reason = decision.get("reason")
            if reason == "pending_description_check":
                if decision.get("decision") != "unmatched" or not pending or decision.get("top_row_key") is not None or states.get("aligned", 0) > 1:
                    raise ValueError("invalid upstream pending decision")
                selected = pending
            elif reason == "all_conditions_aligned_on_one_row":
                if (decision.get("decision") != "matched" or pending or states.get("aligned") != 1
                        or states.get("contradiction", 0) != len(fixed_rows) - 1 or decision.get("top_row_key") not in fixed_rows):
                    raise ValueError("upstream direct match is not a unique fixed aligned row")
                selected = [decision["top_row_key"]]
            elif reason in {"symmetric_condition_unresolved", "contradiction_on_every_row"}:
                if decision.get("decision") != "unmatched" or pending or decision.get("top_row_key") is not None or states.get("aligned", 0):
                    raise ValueError("invalid upstream omitted decision")
                if ((reason == "contradiction_on_every_row" and states.get("contradiction", 0) != len(fixed_rows)) or
                        (reason == "symmetric_condition_unresolved" and not states.get("symmetric_unresolved", 0))):
                    raise ValueError("upstream omitted reason contradicts aggregate row states")
                selected = []
            elif reason == "several_rows_aligned":
                if decision.get("decision") != "unmatched" or decision.get("top_row_key") is not None or states.get("aligned", 0) <= 1:
                    raise ValueError("invalid upstream multiple-aligned decision")
                # Identities of aligned rows were not supplied. Receive every
                # pending row, but never invent a candidate for an aligned row.
                selected = pending
            else:
                raise ValueError("unsupported upstream decision reason")
            case_plans.append({"case_id": case["case_id"], "dossier_id": case["dossier_id"], "cohort": cohort,
                "au_product_id": case["au_product_id"], "source_sku_key": case["rakuten"]["source_sku_key"],
                "upstream_decision": copy.deepcopy(decision), "source_ref": provenance, "candidate_row_keys": selected,
                "total_fixed_au_row_count": len(fixed_rows), "upstream_contradiction_row_count": states.get("contradiction", 0),
                "unresolved_omitted_row_count": states.get("symmetric_unresolved", 0) +
                    (states.get("aligned", 0) if reason != "all_conditions_aligned_on_one_row" else 0), "all_pending_rows_received": True,
                "production_adoption_allowed": False})
            options = au_options(product)
            for row_key in selected:
                row, bindings = fixed_rows[row_key], []
                au_axes = index(row["axes"], "axis_name", case["case_id"])
                represented_rak, represented_au = set(), set()
                h, row_source = contexts.get((case["case_id"], row_key), (None, provenance))

                def identical(rak, au, producer=None):
                    represented_rak.add(rak["axis_key"])
                    represented_au.add(au["axis_name"])
                    bindings.append({"kind": "upstream_identical", "rakuten_axis_key": rak["axis_key"], "au_axis_name": au["axis_name"],
                        "raw_condition": copy.deepcopy(rak), "mapped_au_condition": copy.deepcopy(au), "source_ref": row_source,
                        "producer_condition": copy.deepcopy(producer), "independent_semantic_gold": False})
                    counts["upstream_identical_links"] += 1

                if h:
                    for condition in h["conditions"]:
                        status = condition["status"]
                        if condition["kind"] == "au_only":
                            name = condition["au_axis"]
                            bindings.append(add_task(case, product, row, "au_to_rakuten", au_axes[name], options[name],
                                "v7_au_only", row_source, h, condition))
                            represented_au.add(name)
                            continue
                        key, name = condition["axis_key"], condition.get("au_axis")
                        rak = rak_axes[key]
                        if status == "aligned" and whole(rak["value"]) == whole(au_axes[name]["value"]):
                            identical(rak, au_axes[name], condition)
                            continue
                        origin = "one_sided" if status == "one_sided" else "v7_" + status + ("_nonidentical" if status == "aligned" else "")
                        bindings.append(add_task(case, product, row, "rakuten_to_au", rak, rak["family_values"], origin, row_source, h, condition))
                        represented_rak.add(key)
                        if name is not None:
                            bindings.append(add_task(case, product, row, "au_to_rakuten", au_axes[name], options[name], origin, row_source, h, condition))
                            represented_au.add(name)
                else:
                    # No axis/value-pair archive exists for direct decisions.
                    # Confirm only a unique whole-value correspondence in both
                    # fixed selected arrays; names/categories confer no proof.
                    for key, rak in rak_axes.items():
                        matches = [au for au in au_axes.values() if whole(rak["value"]) == whole(au["value"])]
                        if len(matches) == 1 and sum(whole(a["value"]) == whole(matches[0]["value"]) for a in rak_axes.values()) == 1:
                            identical(rak, matches[0])
                    for key in rak_axes.keys() - represented_rak:
                        rak = rak_axes[key]
                        bindings.append(add_task(case, product, row, "rakuten_to_au", rak, rak["family_values"], "v7_direct_unresolved", row_source))
                        represented_rak.add(key)
                for name in au_axes.keys() - represented_au:
                    bindings.append(add_task(case, product, row, "au_to_rakuten", au_axes[name], options[name], "v7_unrepresented_au_axis", row_source, h))
                    represented_au.add(name)
                row_plans.append({"case_id": case["case_id"], "au_row_key": row_key,
                    "upstream_state": "pending_description_check" if h else "aligned", "condition_bindings": bindings,
                    "rakuten_axis_count": len(rak_axes), "au_axis_count": len(au_axes), "source_ref": row_source,
                    "all_original_axes_covered": represented_rak == set(rak_axes) and represented_au == set(au_axes),
                    "production_adoption_allowed": False})
    if len({task["task_id"] for task in tasks}) != len(tasks):
        raise ValueError("duplicate whole-condition task")
    if any(sha(path.read_bytes()) != input_shas[str(path.resolve())] for path in paths):
        raise ValueError("frozen source changed during preparation")
    output_dir.mkdir(parents=True, exist_ok=False)
    outputs = {"documents.jsonl": list(documents.values()), "requests.jsonl": list(requests.values()), "tasks.jsonl": tasks,
               "row_plans.jsonl": row_plans, "case_plans.jsonl": case_plans}
    for name, values in outputs.items():
        write_rows(output_dir / name, values)
    manifest = {"schema_version": VERSION, "phase": "prepare", "upstream_head": UPSTREAM_HEAD,
        "expected_v7_pins": {"upstream_head": UPSTREAM_HEAD, "archive_sha256": ARCHIVE_SHA256, "code_member": CODE_MEMBER,
            "code_sha256": CODE_SHA256, "commit": "7fd8e17", "generic_input_sha256": INPUT_SHA256},
        "archive_sha256": archive_sha, "external_expected_sha256": expected_sha256 or ARCHIVE_SHA256,
        "embedded_manifest_sha256": sha(payload[INNER_MEMBER]), "archived_code_executed": False,
        "upstream_head_sidecar_verified": upstream_head_file.exists(),
        "archive_members_verified": {name: sha(data) for name, data in payload.items()}, "selected_handoff_sources": sources,
        "selected_runs": runs, "input_sha256": input_shas, "code_sha256": sha(Path(__file__).read_bytes()),
        "labels_read": False, "model_inference_run": False, "synthetic": synthetic, "production_adoption_allowed": False,
        "scope_applicability_proved": False, "whole_values_and_alternatives_preserved": True, "candidate_url_routing": False,
        "upstream_core_is_proof": False, "raw_source_bytes_reverified": False, "raw_source_bindings_verified_against_input_manifest": True,
        "upstream_row_state_contract": "aggregate counts and top_row_key only; per-row contradiction identities are not independently supplied",
        "case_count": len(case_plans), "cohort_case_counts": dict(Counter(plan["cohort"] for plan in case_plans)),
        "input_case_count": len(cases), "input_cases_outside_received_cohorts": len(cases) - len(case_plans),
        "unreceived_cohort_cases_are_not_processed_or_adopted": True,
        "baseline_matched_count": sum(plan["upstream_decision"]["decision"] == "matched" for plan in case_plans),
        "row_count": len(row_plans), "task_count": len(tasks), "request_count": len(requests), "document_count": len(documents),
        "counts": dict(counts), "model_input_contract": "classify unchanged complete option_values against the fixed target; residual metadata is focused verification hints only",
        "request_semantic_fields": list(SEMANTIC_FIELDS),
        "request_metadata_contract": "origin and residual hints are auxiliary binding provenance; each task is authoritative; model/cache identity uses only request_semantic_fields",
        "source_raw_sha256": registry, "output_sha256": {name: sha((output_dir / name).read_bytes()) for name in outputs}}
    (output_dir / "manifest.json").write_bytes(encoded(manifest) + b"\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / ".lab-output/sku-generic-model-inputs-20261010-v2")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--external-manifest", type=Path)
    result = prepare(**vars(parser.parse_args(argv)))
    print(json.dumps({key: result[key] for key in ("upstream_head", "archive_sha256", "case_count", "row_count", "task_count", "request_count", "production_adoption_allowed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
