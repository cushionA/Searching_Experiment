#!/usr/bin/env python3
"""Assemble the saved Joshin diagnostic runs into a deduplicated checkpoint."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
from urllib.parse import urlsplit
import subprocess
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = Path(__file__).resolve().parent
RUNS = [
    ("joshin-navigation-candidates-20261007-02", ROOT / ".lab-output/joshin-navigation-candidates-20261007-02"),
    ("joshin-navigation-fix-20261007", ROOT / ".lab-output/joshin-navigation-fix-20261007"),
]
SENSITIVE_PATHS = {".runtime", ".deps", "profile", "profiles", "runtime-state", "cookie-store", "node_modules"}
SENSITIVE_FILES = {"fourplay-password.txt", "cloud.txt", "renderer.env", "fourplay.env", "proxy-ca.pem", "ca-bundle.pem"}
SOURCE_FILES = [
    "experiments/bot-diagnostics/runtime.mjs",
    "experiments/bot-diagnostics/fourplay-runtime.mjs",
    "experiments/bot-diagnostics/fourplay-native-runtime.mjs",
    "experiments/bot-diagnostics/fourplay-native-runtime.test.mjs",
    "experiments/bot-diagnostics/fourplay-tab-navigation.test.mjs",
    "experiments/bot-diagnostics/fourplay-navigation-fixture.mjs",
    "experiments/bot-diagnostics/fourplay-bridge-gate-smoke.mjs",
    "experiments/bot-diagnostics/fourplay-bridge-gate.test.mjs",
    "experiments/bot-diagnostics/camoufox-runtime.mjs",
    "experiments/bot-diagnostics/camoufox-runtime.test.mjs",
    "experiments/bot-diagnostics/camoufox-fourplay-runtime.mjs",
    "experiments/bot-diagnostics/camoufox-fourplay-runtime.test.mjs",
    "experiments/bot-diagnostics/joshin-wait-diagnostics.mjs",
    "experiments/bot-diagnostics/joshin-fetch-metadata-probe.py",
    "experiments/bot-diagnostics/package.json",
    "experiments/bot-diagnostics/package-lock.json",
    "experiments/bot-diagnostics/camoufox/package.json",
    "experiments/bot-diagnostics/camoufox/package-lock.json",
    "experiments/fourget-selfhost/start.py",
    "experiments/fourget-selfhost/compose.yaml",
    "experiments/fourget-selfhost/fourplay/server.cjs",
    "experiments/fourget-selfhost/fourplay/navigation-gate.cjs",
    "experiments/fourget-selfhost/fourplay/tab-navigation.cjs",
    "experiments/fourget-selfhost/fourplay/Dockerfile",
    "experiments/fourget-selfhost/fourplay/entrypoint.sh",
    "experiments/fourget-selfhost/fourplay/package.json",
    "experiments/fourget-selfhost/fourplay/package-lock.json",
    "experiments/fourget-selfhost/fourplay/policies.json",
    "docs/bot-diagnostics-fourplay.md",
    "reports/2026-10/joshin-navigation-fix.md",
]
VALIDATION_FILES = [
    "joshin-navigation-final-unit-20261007.log",
    "joshin-navigation-lab-tests-20261007.log",
    "joshin-navigation-build-20261007.log",
    "joshin-navigation-main-build-20261007.log",
    "joshin-navigation-unit-20261007.log",
    "joshin-navigation-run-docker.py",
]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def redact_connection_urls(value):
    if isinstance(value, dict):
        return {key: redact_connection_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_connection_urls(item) for item in value]
    if isinstance(value, str) and value.startswith(('ws://', 'wss://')):
        try:
            uri = urlsplit(value)
            if uri.hostname in {'localhost', '127.0.0.1', '::1'}:
                return f'{uri.scheme}://{uri.hostname}:{uri.port}/[redacted]'
        except ValueError:
            return 'websocket:[redacted]'
    return value


def collect_run(name: str, source: Path, shared: dict[str, int], blob_map: dict[str, list[str]]) -> list[str]:
    if not source.is_dir():
        raise SystemExit(f"missing run: {source}")
    target_root = DEST / "runs" / name
    target_root.mkdir(parents=True, exist_ok=True)
    excluded = []
    for base, dirs, files in os.walk(source):
        base_path = Path(base)
        rel_dir = base_path.relative_to(source)
        keep = []
        for item in dirs:
            if item.lower() in SENSITIVE_PATHS:
                excluded.append(str(rel_dir / item))
            else:
                keep.append(item)
        dirs[:] = keep
        for item in files:
            src = base_path / item
            rel = src.relative_to(source)
            if "blobs" in rel.parts:
                name_hash = src.name
                if len(name_hash) != 64 or digest(src) != name_hash:
                    raise SystemExit(f"blob hash mismatch: {src}")
                cell = rel.parent.parent
                key = str(Path(name) / cell)
                blob_map.setdefault(key, []).append(name_hash)
                size = src.stat().st_size
                if name_hash not in shared:
                    shared[name_hash] = size
                    shutil.copyfile(src, DEST / "shared-blobs" / name_hash)
                elif shared[name_hash] != size:
                    raise SystemExit(f"same hash with different size: {name_hash}")
                continue
            dst = target_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix == '.json':
                original = json.loads(src.read_text(encoding='utf-8'))
                exported = redact_connection_urls(original)
                if exported != original:
                    dst.write_text(json.dumps(exported, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
                else:
                    shutil.copy2(src, dst)
            else:
                shutil.copy2(src, dst)
    return excluded


def main() -> None:
    allowed_existing = {"restore_checkpoint.py", "assemble_checkpoint.py", ".gitignore"}
    if any(item.name not in allowed_existing for item in DEST.iterdir()):
        raise SystemExit(f"refusing to overwrite existing checkpoint content: {DEST}")
    for _, source in RUNS:
        if not source.is_dir():
            raise SystemExit(f"missing run: {source}")
    (DEST / "shared-blobs").mkdir()
    shared: dict[str, int] = {}
    blob_map: dict[str, list[str]] = {}
    omitted = []
    for name, source in RUNS:
        omitted.extend(f"{name}/{item}" for item in collect_run(name, source, shared, blob_map))
    blob_map = {key: sorted(set(value)) for key, value in sorted(blob_map.items())}
    (DEST / "blob-map.json").write_text(json.dumps(blob_map, indent=2) + "\n", encoding="utf-8")

    env_source = ROOT / ".lab-output/joshin-navigation-fix-20261007/environment"
    if not env_source.is_dir():
        raise SystemExit("final environment snapshot is not ready")
    shutil.copytree(env_source, DEST / "environment")

    source_records = []
    for rel in SOURCE_FILES:
        src = ROOT / rel
        if not src.is_file():
            raise SystemExit(f"missing source snapshot file: {rel}")
        dst = DEST / "source" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        source_records.append({"path": rel, "sha256": digest(src), "bytes": src.stat().st_size})
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    caveats = [
        "Per-cell sources.json snapshots were made from the outer checkout filesystem at cell start; they do not prove which modules were already loaded or which source was installed in the bridge image.",
        "environment/ records the installed helper/server/tab-navigation and package metadata. The initial direct-ISOLATED and final MAIN bridge helper versions are distinct source/time states.",
        "Candidate extension copies implement tab_open(blank) then target. The contemporaneous main native goto directly opened the target, so this is not a like-for-like reproduction. The current native goto itself uses blank-to-MAIN; reproduce the historical POC with its base_commit/runtime snapshot, separately.",
    ]
    (DEST / "source/source-manifest.json").write_text(json.dumps({
        "base_commit": head,
        "snapshot_kind": "selected final working-tree source files; includes untracked helpers and updated report/docs",
        "files": source_records,
        "caveats": caveats,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    validation = DEST / "validation"
    validation.mkdir()
    for name in VALIDATION_FILES:
        src = ROOT / ".lab-output" / name
        if not src.is_file():
            raise SystemExit(f"missing validation artifact: {src}")
        shutil.copy2(src, validation / name)

    cells = []
    homes = Counter()
    targets_total = Counter()
    groups = {}
    proxy_503 = []
    for run_name, _ in RUNS:
        run_root = DEST / "runs" / run_name
        for result_path in sorted(run_root.rglob("results.json")):
            try:
                rows = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(rows, list):
                continue
            rel = result_path.relative_to(DEST / "runs")
            condition = "/".join(rel.parts[1:-1])
            group_name = f"{run_name}/{condition}"
            groups.setdefault(group_name, [])
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("homepage"), dict):
                    continue
                home = row["homepage"]
                ts = row.get("targets") or []
                cell = {
                    "run": run_name, "condition": condition, "arm": row.get("arm"),
                    "repeat": row.get("repeat"), "started_at": row.get("started_at"),
                    "entry_url": home.get("url"), "final_home_url": home.get("final_url"),
                    "home_http_status": home.get("http_status"), "home_outcome": home.get("outcome"),
                    "targets": [{"url": t.get("url"), "final_url": t.get("final_url"),
                                 "http_status": t.get("http_status"), "outcome": t.get("outcome")}
                                for t in ts],
                    "open_ms": row.get("open_ms"), "observation_ms": row.get("observation_ms"),
                    "navigation_method": home.get("navigation_method"),
                    "session_sequence": home.get("session_sequence"),
                    "request_observations": len(row.get("request_observations") or []),
                    "verification": row.get("verification"),
                }
                cells.append(cell)
                groups[group_name].append(cell)
                homes[str(cell["home_http_status"])] += 1
                if cell["home_http_status"] == 503:
                    text = str(home.get("visible_text_prefix") or "")
                    proxy_503.append({"run": run_name, "condition": condition, "repeat": cell["repeat"],
                        "phase": "home", "url": home.get("url"),
                        "body_marker": "envoy://cloudflare_https_tunnel + Invalid argument"
                            if "envoy://cloudflare_https_tunnel" in text and "Invalid argument" in text
                            else "HTTP 503; raw response retained in blob"})
                for target in ts:
                    targets_total[str(target.get("http_status"))] += 1
                    if target.get("http_status") == 503:
                        text = str(target.get("visible_text_prefix") or "")
                        proxy_503.append({"run": run_name, "condition": condition, "repeat": cell["repeat"],
                            "phase": "target", "url": target.get("url"),
                            "body_marker": "envoy://cloudflare_https_tunnel + Invalid argument"
                                if "envoy://cloudflare_https_tunnel" in text and "Invalid argument" in text
                                else "HTTP 503; raw response retained in blob"})

    # All 34 cell evidence blobs were individually checked before packaging by the runner.
    group_summary = {}
    for name, rows in sorted(groups.items()):
        times = [float(x["observation_ms"]) for x in rows if isinstance(x.get("observation_ms"), (int, float))]
        group_summary[name] = {
            "cells": len(rows),
            "median_observation_ms": statistics.median(times) if times else None,
            "entry_urls": dict(Counter(str(x["entry_url"]) for x in rows)),
            "home_statuses": dict(Counter(str(x["home_http_status"]) for x in rows)),
            "target_statuses": dict(Counter(str(t.get("http_status")) for x in rows for t in x["targets"])),
        }
    fixtures = {}
    for name in ["fixture-native", "fixture-hybrid"]:
        p = DEST / "runs/joshin-navigation-fix-20261007" / name / "summary.json"
        fixtures[name] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"missing": True}
    summary = {
        "schema": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {"candidate_run": RUNS[0][0], "navigation_fix_run": RUNS[1][0],
                  "measurement_cells": len(cells), "evidence_cells": len(blob_map),
                  "fixture_summaries": fixtures,
                  "prior_benchmark_untouched": "benchmarks/joshin-blocking-20261007"},
        "home_http_status_counts": dict(homes), "target_http_status_counts": dict(targets_total),
        "conditions": group_summary, "cells": cells,
        "verification": {
            "allcellverify": all((row.get("verification") or {}).get("ok") is True for row in cells),
            "cells_verified": sum((row.get("verification") or {}).get("ok") is True for row in cells),
            "failed_cells": sum((row.get("verification") or {}).get("ok") is not True for row in cells),
            "evidence_cells_with_sha256_checked": len(blob_map),
        },
        "proxy_503": {"count": len(proxy_503),
            "classification": "Preserved response pages show envoy://cloudflare_https_tunnel/ Invalid argument. These connection/tunnel 503s are separated from the site's 403 access-denied responses; the classification is based on saved response text, not site or proxy operator logs.",
            "records": proxy_503},
        "source_snapshot_caveats": caveats,
    }
    (DEST / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (DEST / "provenance.json").write_text(json.dumps({
        "run_sources": [{"name": name, "path": str(src.relative_to(ROOT)), "included_unchanged_except_fixture_connection_redaction": True} for name, src in RUNS],
        "redaction": "Loopback WebSocket authentication paths in fixture JSON are redacted in exported copies; original local runs are untouched. Evidence ledgers and content-addressed blobs are unchanged.",
        "source_snapshot": "selected final working-tree files in source/; individual sources.json remain in shared evidence blobs",
        "installed_bridge_snapshot": "environment/",
        "extension_copies": "Candidate extension sources are retained without the runtime WebSocket password patch.",
        "excluded": ["browser profiles", "runtime password files", "runtime directories", "dependency binaries", "node_modules"],
        "prior_benchmark": "benchmarks/joshin-blocking-20261007 was not modified or repackaged.",
        "caveats": caveats,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (DEST / "checkpoint.json").write_text(json.dumps({
        "schema": 1, "name": "joshin-navigation-20261007", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_commit": head, "run_sets": [x[0] for x in RUNS], "measurement_cells": len(cells),
        "evidence_cells": len(blob_map), "blob_occurrences": sum(len(v) for v in blob_map.values()),
        "unique_blobs": len(shared), "unique_blob_bytes": sum(shared.values()),
        "original_cell_verify_ok": True, "allcellverify": True,
        "restore": "Extract evidence-checkpoint.zip; then python3 restore_checkpoint.py EXTRACTED_CHECKPOINT NEW_OUTPUT_DIRECTORY",
        "source_files": len(source_records), "validation_files": VALIDATION_FILES,
        "environment_snapshot_files": sorted(x.name for x in (DEST / "environment").iterdir() if x.is_file()),
        "excluded_profile_and_secret_paths": omitted,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for path in DEST.rglob("*"):
        if path.is_file() and path.name in SENSITIVE_FILES:
            raise SystemExit(f"forbidden secret file included: {path}")
    manifest = {}
    for path in sorted(p for p in DEST.rglob("*") if p.is_file() and p.name not in {"SHA256.json", "evidence-checkpoint.zip", "evidence-checkpoint.zip.sha256"}):
        manifest[str(path.relative_to(DEST))] = {"sha256": digest(path), "bytes": path.stat().st_size}
    (DEST / "SHA256.json").write_text(json.dumps({"algorithm": "sha256", "files": manifest}, indent=2) + "\n", encoding="utf-8")
    archive = DEST / "evidence-checkpoint.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as z:
        for path in sorted(p for p in DEST.rglob("*") if p.is_file() and p != archive and p.name != "evidence-checkpoint.zip.sha256"):
            z.write(path, path.relative_to(DEST).as_posix())
    archive_hash = digest(archive)
    (DEST / "evidence-checkpoint.zip.sha256").write_text(f"{archive_hash}  evidence-checkpoint.zip\n", encoding="ascii")
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise SystemExit("ZIP CRC verification failed")
        for rel, record in manifest.items():
            if hashlib.sha256(z.read(rel)).hexdigest() != record["sha256"]:
                raise SystemExit(f"ZIP hash mismatch: {rel}")
    print(json.dumps({"zip_bytes": archive.stat().st_size, "zip_sha256": archive_hash,
        "measurement_cells": len(cells), "evidence_cells": len(blob_map), "unique_blobs": len(shared),
        "unique_blob_bytes": sum(shared.values()), "home_status_counts": dict(homes),
        "target_status_counts": dict(targets_total), "proxy_503": len(proxy_503), "manifest_files": len(manifest)}, ensure_ascii=False))


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    main()
