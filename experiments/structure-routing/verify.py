#!/usr/bin/env python3
"""Offline integrity and budget checks for a structure-routing run."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


SEALED_CLOUD_LAYER = "cloud-linux-chromium151-proxy-sealed-20261115"
INITIAL_CANDIDATE_ROWS = 60
EXPECTED_TARGETS = 80
EXPECTED_FINAL_ROWS = 480
EXPECTED_CHALLENGES = 20
MAIN_BUDGET = 3500
STAGE_C_BUDGET = 400


def jsonl(path):
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception as exc:
                raise ValueError(f"{path.name}:{number}: invalid JSON ({exc})") from exc
    return rows


def safe_relative(value):
    """Normalize Windows paths while rejecting absolute paths and traversal."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty/non-string path")
    raw = value.replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("absolute path")
    path = PurePosixPath(raw)
    if any(part in {"..", "."} for part in path.parts):
        raise ValueError("path traversal")
    return Path(*path.parts)


def doc_cost(row):
    return max(int(row.get("document_requests") or 0), int(row.get("document_responses") or 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve()
    errors, warnings = [], []
    report = {"run": str(run), "errors": errors, "warnings": warnings}

    def check(condition, message, *, incomplete=False):
        if condition:
            return
        (warnings if args.allow_incomplete and incomplete else errors).append(message)

    try:
        targets = jsonl(run / "targets.jsonl")
        results = jsonl(run / "results.jsonl")
        gold_meta = jsonl(run / "evaluator-results-v2.jsonl")
        preflight = jsonl(run / "evaluator-results.preflight-v1.jsonl")
        challenges = jsonl(run / "challenge-results.jsonl")
        b0 = jsonl(run / "b0-results.jsonl")
        recovery = jsonl(run / "recovery-results.jsonl")
    except ValueError as exc:
        errors.append(str(exc))
        targets = results = gold_meta = preflight = challenges = b0 = recovery = []

    ids = [str(row.get("id", "")) for row in targets]
    target_ids = set(ids)
    check(len(targets) == EXPECTED_TARGETS, f"targets: expected 80, found {len(targets)}")
    check(len(target_ids) == EXPECTED_TARGETS, f"targets: expected 80 unique IDs, found {len(target_ids)}")
    report["target_count"] = len(targets)
    check(len(b0) == 80 and len({r.get("id") for r in b0}) == 80, "B0 matrix must contain 80 unique sites", incomplete=True)
    report["B0_sites"] = len(b0)
    check(len({(r.get("id"),r.get("method"),r.get("mode")) for r in recovery}) == len(recovery), "duplicate recovery keys")
    for row in recovery:
        key = f"{row['id']}:{row['method']}:{row['mode']}"
        f = run / "site-state" / f"{row['id']}.network_recovery.json"
        check(f.exists() and key in json.loads(f.read_text()).get("completed", []), f"recovery state missing {key}")
    report["network_recovery_rows"] = len(recovery)

    # Verify the frozen initial 60 rows against the checkpoint archive, without
    # inspecting any independent-evaluation content.
    archive = None
    try:
        checkpoint = json.loads((run / "CHECKPOINT.json").read_text(encoding="utf-8"))
        archive = (run.parent / Path(checkpoint["archive"]).name)
        if not archive.is_file():
            archive = run.parent / checkpoint["archive"]
        if not archive.is_file():
            archive = run / Path(checkpoint["archive"]).name
        if not archive.is_file():
            raise FileNotFoundError(checkpoint["archive"])
        archived_run = PurePosixPath(checkpoint["archive"]).name.removesuffix("-checkpoint.zip")
        expected_member = f"{PurePosixPath(checkpoint['archive']).parent}/{archived_run}/results.jsonl"
        with zipfile.ZipFile(archive) as zf:
            member = expected_member if expected_member in zf.namelist() else None
            if not member:
                raise FileNotFoundError("results.jsonl in checkpoint archive")
            original = [json.loads(line) for line in zf.read(member).decode("utf-8").splitlines() if line]
        original = original[:INITIAL_CANDIDATE_ROWS]
        actual = results[:INITIAL_CANDIDATE_ROWS]
        check(len(original) == INITIAL_CANDIDATE_ROWS, f"checkpoint has {len(original)} initial candidate rows, expected 60")
        check(len(actual) >= INITIAL_CANDIDATE_ROWS, f"results has only {len(actual)} rows; initial 60 missing")
        check(original == actual, "the initial 60 results rows differ semantically from checkpoint archive")
        report["initial_rows"] = {"verified": original == actual and len(original) == INITIAL_CANDIDATE_ROWS,
                                  "count": len(actual), "archive": str(archive)}
    except Exception as exc:
        errors.append(f"cannot verify frozen initial results against checkpoint archive: {exc}")

    keys = [(str(r.get("id")), str(r.get("method")), str(r.get("mode"))) for r in results]
    from collections import Counter
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
    check(not duplicates, f"duplicate result keys id:method:mode: {duplicates[:10]}")
    counts = {"total": len(results), "by_status": {}, "by_method_mode": {}}
    for row in results:
        status = str(row.get("status", "<missing>"))
        counts["by_status"][status] = counts["by_status"].get(status, 0) + 1
        label = f"{row.get('method')}:{row.get('mode')}"
        counts["by_method_mode"][label] = counts["by_method_mode"].get(label, 0) + 1
    report["candidate_results"] = counts
    check(len(results) == EXPECTED_FINAL_ROWS, f"candidate results: expected 480 rows, found {len(results)}", incomplete=True)
    expected_keys = {(site, method, mode) for site in target_ids for method in ("B1", "C1") for mode in ("cold", "G", "R")}
    missing_keys = sorted(expected_keys - set(keys))
    extra_keys = sorted(set(keys) - expected_keys)
    check(not missing_keys, f"candidate result keys missing: {len(missing_keys)}", incomplete=True)
    check(not extra_keys, f"candidate result keys outside expected target/method/mode matrix: {extra_keys[:10]}")
    state_keys = set()
    for site in target_ids:
        state_path = run / "site-state" / f"{site}.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state_keys.update(str(key) for key in state.get("completed", []))
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid site state {state_path.name}: {exc}")
    result_state_keys = {f"{site}:{method}:{mode}" for site, method, mode in keys}
    check(state_keys == result_state_keys, f"site-state completed keys do not match results rows (state={len(state_keys)}, results={len(result_state_keys)})", incomplete=True)
    report["completed_state_keys"] = len(state_keys)

    # Each referenced saved page must be a nonempty file below this row's artifact folder.
    checked_paths = 0
    for row in results + recovery + b0:
        site, method = str(row.get("id", "")), str(row.get("method", ""))
        for page_index, page in enumerate(row.get("pages") or []):
            for field in ("response_html", "rendered_dom"):
                value = page.get(field)
                if value is None:
                    if field == "response_html":
                        warnings.append(f"{site}/{method} page {page_index}: response_html unsaved (status={page.get('status')!r})")
                        continue
                    errors.append(f"{site}/{method} page {page_index}: null {field} without permitted failed navigation")
                    continue
                try:
                    rel = safe_relative(value)
                    candidate = (run / "artifacts" / site / method / rel).resolve()
                    base = (run / "artifacts" / site / method).resolve()
                    if not candidate.is_relative_to(base):
                        raise ValueError("path escapes artifact directory")
                    if not candidate.is_file() or candidate.stat().st_size == 0:
                        errors.append(f"{site}/{method} page {page_index}: missing or empty {field} file {value!r}")
                    checked_paths += 1
                except (ValueError, OSError) as exc:
                    errors.append(f"{site}/{method} page {page_index}: invalid {field} path {value!r}: {exc}")
    report["referenced_html"] = {"checked": checked_paths}

    # Layer-specific strict caps. Historical Windows rows may exceed a cap only
    # when named explicitly in deviations.json; each overage remains reported.
    deviations = {}
    try:
        d = json.loads((run / "deviations.json").read_text(encoding="utf-8"))
        deviations = d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    cloud_deviations = {}
    try:
        cloud_deviations = json.loads((run / "cloud-deviations.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    known_cloud_overages = {
        (str(r.get("id")), str(r.get("method")), str(r.get("mode")))
        for r in cloud_deviations.get("rows", []) if isinstance(r, dict)
    }
    interrupted = {}
    try:
        interrupted = json.loads((run / "interrupted-executions.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        interrupted = {}
    interrupted_by_method = {}
    for item in interrupted.get("main", []):
        site = str(item.get("id", ""))
        for method, amount in (item.get("method_unlogged_document_reservation") or {}).items():
            key = (site, str(method))
            interrupted_by_method[key] = interrupted_by_method.get(key, 0) + int(amount or 0)
    overages = []
    targets_by_id = {str(row.get("id")): row for row in targets}
    shared_spend = {}
    for row in results + recovery + b0:
        site = str(row.get("id", ""))
        allowance = 3 if row.get("method") == "B0" else (40 if targets_by_id.get(site, {}).get("practice") else 15)
        spent = doc_cost(row)
        shared_key = (site, str(row.get("method")))
        shared_spend[shared_key] = shared_spend.get(shared_key, 0) + spent
        layer = row.get("environment_layer")
        record = {"id": row.get("id"), "method": row.get("method"), "mode": row.get("mode"),
                  "spent": spent, "reserved": row.get("reserved_document_budget", allowance), "allowance": allowance,
                  "environment_layer": layer}
        if "sealed" in str(layer):
            guard = row.get("document_guard")
            if row.get("method") == "B0" and not isinstance(guard, dict):
                trace = run / "artifacts" / site / "B0" / "cold" / "observations.json"
                if trace.exists(): guard = json.loads(trace.read_text()).get("document_guard")
            if not isinstance(guard, dict):
                if spent or row.get("http_requests", 0):
                    errors.append(f"sealed cloud communication row lacks document_guard: {record}")
                continue
            reserved = int(guard.get("reserved", -1))
            requests = int(row.get("document_requests", 0))
            responses = int(row.get("document_responses", 0))
            if reserved != requests or responses > reserved:
                errors.append(f"cloud response/request cost disagrees with guard reservation: {record}")
            if reserved < 0 or reserved > allowance:
                errors.append(f"cloud reservation exceeds method allowance: {record}")
    for key in interrupted_by_method:
        shared_spend.setdefault(key, 0)
    for (site, method), spent in shared_spend.items():
        spent += interrupted_by_method.get((site, method), 0)
        allowance = 3 if method == "B0" else (40 if targets_by_id.get(site, {}).get("practice") else 15)
        if spent > allowance:
            record = {"id": site, "method": method, "spent": spent, "allowance": allowance}
            overages.append(record)
            permitted = {
                tuple(map(str, x)) for x in deviations.get("permitted_rows", []) if isinstance(x, list) and len(x) == 3
            }
            layer_rows = [r for r in results if str(r.get("id")) == site and str(r.get("method")) == method]
            permitted_keys = {(site, method, str(r.get("mode"))) for r in layer_rows}
            recorded_deviations = deviations.get("protocol_deviations", [])
            explicitly_recorded = any(d.get("site_id") == site and d.get("method") == method
                                      for d in recorded_deviations if isinstance(d, dict))
            initial_key_set = set(keys[:INITIAL_CANDIDATE_ROWS])
            applies_to_original = any((site, method, mode) in initial_key_set for _, m, mode in permitted_keys if m == method)
            allowed_historical = (all(r.get("environment_layer") != SEALED_CLOUD_LAYER for r in layer_rows)
                                  and (bool(permitted_keys & permitted) or (explicitly_recorded and applies_to_original)))
            if allowed_historical:
                warnings.append(f"historical cap overage retained: {record}")
            else:
                if all(r.get("environment_layer") != SEALED_CLOUD_LAYER for r in layer_rows) and any(
                    key in known_cloud_overages for key in permitted_keys
                ):
                    warnings.append(f"recorded unsealed-cloud cap overage retained: {record}")
                else:
                    errors.append(f"shared site/method cap exceeded: {record}")
    for row in cloud_deviations.get("rows", []):
        if isinstance(row, dict):
            warnings.append(f"recorded unsealed-cloud reservation overage: {row}")
    report["cap_overages"] = overages

    # All document spend is conservatively counted as max(requests, responses).
    gold_cost = sum(int(r.get("document_attempts") or 0) for r in gold_meta)
    v1_cost = sum(int(r.get("requests") or 0) for r in preflight)
    candidate_cost = sum(doc_cost(r) for r in results)
    recovery_cost = sum(doc_cost(r) for r in recovery)
    b0_cost = sum(doc_cost(r) for r in b0)
    challenge_cost = sum(doc_cost(r) for r in challenges)
    interrupted_main = int(interrupted.get("main_unlogged_reserved_documents") or 0)
    interrupted_stage_c = int((interrupted.get("stage_c") or {}).get("unlogged_document_reservation") or 0)
    main_cost = v1_cost + gold_cost + candidate_cost + recovery_cost + b0_cost + challenge_cost + interrupted_main
    check(main_cost <= MAIN_BUDGET, f"main document budget exceeded: {main_cost}/{MAIN_BUDGET}")
    report["main_budget"] = {"v1_requests": v1_cost, "v2_attempts": gold_cost,
                             "candidate": candidate_cost, "recovery": recovery_cost, "b0": b0_cost,
                             "challenges_including_accidental": challenge_cost,
                             "interrupted_main_unlogged_reserved": interrupted_main,
                             "total": main_cost, "limit": MAIN_BUDGET}
    accidental = [r for r in challenges if r.get("execution_origin") == "accidental_preflight_flag_parse"]
    real_challenges = [r for r in challenges if r not in accidental]
    report["challenges"] = {"rows": len(challenges), "true_rows": len(real_challenges),
                            "accidental_rows": len(accidental), "accidental_document_cost": sum(doc_cost(r) for r in accidental)}
    check(len(real_challenges) == EXPECTED_CHALLENGES, f"challenge results: expected 20 true rows, found {len(real_challenges)}", incomplete=True)

    # Stage C selection and execution evidence are only read as metadata.
    stage_manifest = run / "stage-c-independent-manifest.json"
    stage_ledger = run / "stage-c-selection-ledger.json"
    stage_cost = interrupted_stage_c
    if stage_manifest.exists():
        try:
            manifest = json.loads(stage_manifest.read_text(encoding="utf-8"))
            check(bool(manifest.get("frozen_at")), "stage C independent manifest lacks fixed selection timestamp")
            check(not manifest.get("candidate_url_additions"), "stage C manifest records candidate URL additions")
            if stage_ledger.exists():
                ledger = json.loads(stage_ledger.read_text(encoding="utf-8"))
                expected_hash = ledger.get("manifest_sha256")
                actual_hash = hashlib.sha256(stage_manifest.read_bytes()).hexdigest()
                check(bool(expected_hash) and expected_hash == actual_hash,
                      "stage C frozen manifest hash does not match selection ledger")
                check(bool(ledger.get("created_at")), "stage C selection ledger lacks fixed timestamp")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid stage C metadata: {exc}")
    # Count actual revision ledgers once and independently charged aborted work.
    manifests = []
    for file in sorted(run.glob("stage-c-selection-ledger*.json")):
        try:
            obj = json.loads(file.read_text(encoding="utf-8"))
            stage_cost += int(obj.get("total_documents") or 0)
            suffix = file.stem.removeprefix("stage-c-selection-ledger")
            fixed = run / f"stage-c-independent-manifest{suffix}.json"
            if fixed.exists():
                m = json.loads(fixed.read_text(encoding="utf-8"))
                check(bool(m.get("frozen_at")), f"{fixed.name} lacks a fixed timestamp")
                check(hashlib.sha256(fixed.read_bytes()).hexdigest() == obj.get("manifest_sha256"), f"{fixed.name} hash mismatch")
                manifests.append(fixed.name)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid stage C metadata {file.name}: {exc}")
    bulk_documents = 0
    for site in ("01", "08"):
        file = run / "artifacts" / site / "C1" / "bulk" / "result.json"
        if file.exists():
            obj = json.loads(file.read_text(encoding="utf-8"))
            bulk_documents += int(obj.get("document_attempts") or 0)
    diagnostic = run / "artifacts" / "stage-c-transport-diagnostic" / "result.json"
    diagnostic_documents = int(json.loads(diagnostic.read_text()).get("document_attempts", 0)) if diagnostic.exists() else 0
    stage_cost += bulk_documents + diagnostic_documents
    check(stage_cost <= STAGE_C_BUDGET, f"stage C selection+bulk document cost exceeds {STAGE_C_BUDGET}: {stage_cost}")
    report["stage_c"] = {"selection_and_bulk_document_cost": stage_cost, "bulk_documents": bulk_documents, "diagnostic_documents": diagnostic_documents,
                         "limit": STAGE_C_BUDGET, "manifests": manifests,
                         "interrupted_v2_reserved": interrupted_stage_c}

    report["ok"] = not errors
    output = run / "verify-cloud.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "errors": len(errors), "warnings": len(warnings),
                      "report": str(output)}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
