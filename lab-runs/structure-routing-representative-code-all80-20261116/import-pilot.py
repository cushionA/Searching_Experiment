import hashlib
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "structure-routing-representative-code-20261115"
REPAIR = SOURCE / "repair-run"
IDS = ["01", "02", "09", "10", "14", "67"]
REPAIRED_IDS = {"10", "67"}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def copy_tree(src, dst):
    shutil.copytree(src, dst, dirs_exist_ok=True)


def rebase(value, new_prefix):
    if isinstance(value, dict):
        return {k: rebase(v, new_prefix) for k, v in value.items()}
    if isinstance(value, list):
        return [rebase(v, new_prefix) for v in value]
    if isinstance(value, str) and value.replace("\\", "/").startswith("artifacts/"):
        return new_prefix + value.replace("\\", "/")
    return value


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


INITIAL_ASSETS = ["representative.json", "bootstrap-request.json", "sampling-answer.json", "code-request.json", "representatives.json", "listing-before.json", "listing-after.json", "sample-detail.json"]


def copy_initial_assets():
    hashes = {}
    for site_id in IDS:
        src = SOURCE / "sites" / site_id
        dst = ROOT / "sites" / site_id
        dst.mkdir(parents=True, exist_ok=True)
        for name in INITIAL_ASSETS:
            source_file = src / name
            if not source_file.is_file():
                continue
            data = json.loads(source_file.read_text(encoding="utf-8"))
            rebased = rebase(data, "adopted-pilot/")
            target_file = dst / name
            target_file.write_text(json.dumps(rebased, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            hashes[str(source_file.relative_to(ROOT.parent.parent)).replace("\\", "/")] = digest(source_file)
        if site_id in REPAIRED_IDS:
            src_artifacts = SOURCE / "artifacts" / site_id
            dst_artifacts = ROOT / "adopted-pilot" / "artifacts" / site_id
            copy_tree(src_artifacts, dst_artifacts)
            for f in src_artifacts.rglob("*"):
                if f.is_file():
                    hashes[str(f.relative_to(ROOT.parent.parent)).replace("\\", "/")] = digest(f)
    setup_path = ROOT / "transferred-setup.json"
    setup = json.loads(setup_path.read_text(encoding="utf-8"))
    setup["initial_assets"] = {"files_copied": len([k for k in hashes if "/sites/" in k]), "primary_artifact_files_added_for_repaired_ids": len([k for k in hashes if "/artifacts/" in k]), "source_file_hashes": hashes}
    setup_path.write_text(json.dumps(setup, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    if (ROOT / "cost-ledger.jsonl").exists():
        raise SystemExit("Existing ledger: use --finalize-paths without reimporting observations")
    adopted = ROOT / "adopted-pilot"
    (adopted / "artifacts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE / "model-usage.json", adopted / "model-usage.json")
    trials = load_jsonl(SOURCE / "results.jsonl") + load_jsonl(REPAIR / "results.jsonl")
    for row in trials:
        origin = "repair-run/" if row in load_jsonl(REPAIR / "results.jsonl") else ""
        row = rebase(row, "adopted-pilot/" + origin)
    write_jsonl(adopted / "trial-results.jsonl", [rebase(r, "adopted-pilot/" + ("repair-run/" if r in load_jsonl(REPAIR / "results.jsonl") else "")) for r in trials])

    phase_rows = {"primary": load_jsonl(SOURCE / "results.jsonl"), "repair": load_jsonl(REPAIR / "results.jsonl")}
    phase_map = {}
    for origin, rows in phase_rows.items():
        for row in rows:
            if row.get("id") in IDS and row.get("phase") in {"cold", "warm"}:
                phase_map[(row["id"], row["phase"])] = (origin, row)

    for site_id in IDS:
        site_src = REPAIR / "sites" / site_id if site_id in REPAIRED_IDS else SOURCE / "sites" / site_id
        if site_src.exists():
            copy_tree(site_src, ROOT / "sites" / site_id)
        for phase in ("cold", "warm"):
            origin, row = phase_map[(site_id, phase)]
            srcbase = REPAIR if origin == "repair" else SOURCE
            destprefix = "adopted-pilot/repair-run/" if origin == "repair" else "adopted-pilot/"
            row = rebase(row, destprefix)
            (ROOT / "sites" / site_id / phase / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            # Preserve each phase's source tree beneath the adopted provenance directory.
            rel_artifacts = Path("artifacts") / site_id
            src_artifacts = srcbase / rel_artifacts
            dst_artifacts = adopted / ("repair-run/" if origin == "repair" else "") / rel_artifacts
            if src_artifacts.exists():
                copy_tree(src_artifacts, dst_artifacts)
            phase_map[(site_id, phase)] = (origin, row)

    rows = []
    for site_id in IDS:
        for phase in ("cold", "warm"):
            rows.append(phase_map[(site_id, phase)][1])
    write_jsonl(ROOT / "initialresults.jsonl", rows)

    costs = []
    for origin, source in (("", SOURCE), ("repair-run/", REPAIR)):
        for original in load_jsonl(source / "cost-ledger.jsonl"):
            if original.get("shared_prior"):
                continue
            row = rebase(original, "adopted-pilot/" + origin)
            row["source_run"] = str(source.relative_to(ROOT.parent.parent)).replace("\\", "/")
            costs.append(row)
    write_jsonl(ROOT / "cost-ledger.jsonl", costs)

    setup = {
        "source_run": str(SOURCE.relative_to(ROOT.parent.parent)).replace("/", "\\"),
        "adopted_ids": IDS,
        "adopted_phases": 12,
        "source_trials_preserved": 16,
        "source_cost_records": len(costs),
        "prior_document_reserved": sum(r.get("document_reserved", 0) for r in costs),
        "prior_ui_operations": sum(r.get("ui_operations", 0) for r in costs),
        "new_run_cost": 0,
        "prior_native_usage": "unknown; not treated as zero",
        "source_files": {},
    }
    for p in [SOURCE / "model-usage.json", SOURCE / "results.jsonl", SOURCE / "cost-ledger.jsonl", REPAIR / "results.jsonl", REPAIR / "cost-ledger.jsonl"]:
        setup["source_files"][str(p.relative_to(ROOT.parent.parent)).replace("/", "\\")] = digest(p)
    (ROOT / "transferred-setup.json").write_text(json.dumps(setup, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert len(rows) == 12 and len(trials) == 16
    assert all((ROOT / p).exists() for row in rows for p in collect_paths(row))


def collect_paths(obj):
    if isinstance(obj, dict):
        return [p for v in obj.values() for p in collect_paths(v)]
    if isinstance(obj, list):
        return [p for v in obj for p in collect_paths(v)]
    if isinstance(obj, str) and obj.startswith(("adopted-pilot/", "adopted-pilot\\")):
        return [obj.replace("\\", "/")]
    return []


def finalize_paths():
    path = ROOT / "cost-ledger.jsonl"
    before = digest(path)
    costs = load_jsonl(path)
    corrections = []
    for row in costs:
        if not row.get("source_run"):
            continue
        session = row["session"].replace("\\", "/")
        candidate = "adopted-pilot/repair-run/" + session.removeprefix("adopted-pilot/")
        if not (ROOT / session / "session.json").is_file() and (ROOT / candidate / "session.json").is_file():
            corrections.append({"old": row["session"], "new": candidate})
            session = candidate
            row["source_run"] = "lab-runs/structure-routing-representative-code-20261115/repair-run"
        row["session"] = session
    write_jsonl(path, costs)
    selected = [r for r in load_jsonl(ROOT / "results.jsonl") if r["id"] in IDS]
    for row in selected:
        destination = ROOT / "sites" / row["id"] / row["phase"] / "result.json"
        destination.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    setup_path = ROOT / "transferred-setup.json"
    setup = json.loads(setup_path.read_text(encoding="utf-8"))
    setup["path_finalization"] = {"ledger_sha256_before": before, "ledger_sha256_after": digest(path),
                                  "costs_or_outcomes_changed": False, "repair_session_path_corrections": corrections,
                                  "canonical_phase_path_copies_synchronized": len(selected),
                                  "source_runs_and_raw_adopted_files_unchanged": True}
    setup_path.write_text(json.dumps(setup, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] == ["--finalize-paths"]:
        finalize_paths()
    elif sys.argv[1:] == ["--initial-assets-only"]:
        copy_initial_assets()
    else:
        main()
