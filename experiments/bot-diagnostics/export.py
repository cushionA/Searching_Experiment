"""Export verified runs and reusable scenario code without runtime binaries or credentials."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", default=[])
    parser.add_argument("--include-measured", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists():
        raise RuntimeError("Existing checkpoint cannot be overwritten")
    runs = [Path(p).resolve() for p in args.run]
    if args.include_measured:
        runs += sorted((REPO / "lab-runs").glob("bot-diagnostics-00*-*"))
        runs += [REPO / "lab-runs/bot-diagnostics-evidence"]
    runs = list(dict.fromkeys(runs))
    if not runs:
        raise RuntimeError("Supply --run or --include-measured")
    verification = {}
    run_roots = [REPO]
    if os.environ.get("BOT_DIAGNOSTICS_RUN_ROOT"):
        run_roots.append(Path(os.environ["BOT_DIAGNOSTICS_RUN_ROOT"]).resolve())
    prefixes = {}
    for run in runs:
        if not any(run.is_relative_to(root) for root in run_roots) or not run.is_dir():
            raise RuntimeError("Run must be in the repository or BOT_DIAGNOSTICS_RUN_ROOT")
        prefixes[run] = str(run.relative_to(REPO)) if run.is_relative_to(REPO) else "external-runs/" + run.name + "-" + hashlib.sha256(str(run).encode()).hexdigest()[:8]
        for ledger in sorted(run.rglob("ledger.json")):
            directory = ledger.parent
            result = subprocess.run(["node", str(HERE / "runner.mjs"), "verify", str(directory)],
                                    capture_output=True, text=True, check=True, timeout=60)
            verified = json.loads(result.stdout)
            if not verified["ok"]:
                raise RuntimeError(f"Verification failed: {directory}: {verified['errors']}")
            verification[str(Path(prefixes[run]) / directory.relative_to(run))] = verified
    roots = [HERE, REPO / ".agents/skills/bot-blocking-scenarios", *runs]
    files = [REPO / p for p in ["README.md", "AGENTS.md", "Dockerfile", "compose.yaml", "compose.bot-diagnostics-cloud.yaml", ".dockerignore", "tests/test_lab_bot_diagnostics.py",
             "docs/bot-diagnostics-2026-10-01.md", "docs/bot-diagnostics-evidence.md",
             "docs/oxibrowser-experiment.md", "docs/oxibrowser-selectors-20261001.json"]]
    for root in roots:
        files += [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
                  and p.suffix not in {".zip", ".pyc"} and "__pycache__" not in p.parts]
    files = sorted(set(files))
    def archive_path(file):
        if file.is_relative_to(REPO):
            return str(file.relative_to(REPO))
        run = next(run for run in runs if file.is_relative_to(run))
        return str(Path(prefixes[run]) / file.relative_to(run))
    manifest = {archive_path(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    if len(manifest) != len(files):
        raise RuntimeError("Archive path collision")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip() if shutil.which("git") else None
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for file in files:
            zipped.write(file, archive_path(file))
        zipped.writestr("SHA256.json", json.dumps(manifest, indent=2) + "\n")
        zipped.writestr("CHECKPOINT.json", json.dumps({"commit": commit, "verification": verification,
            "run_paths": {str(run): prefix for run, prefix in prefixes.items()},
            "source_files_are_authoritative": True, "runtime_binaries_included": False,
            "resume": "Use saved scenario, recorded execution policy and existing ledger; grounding alone enforces remaining budget. Browser/client cookies restart."}, indent=2) + "\n")
    with zipfile.ZipFile(temporary) as zipped:
        if zipped.testzip() is not None:
            raise RuntimeError("ZIP CRC verification failed")
        for name, digest in manifest.items():
            if hashlib.sha256(zipped.read(name)).hexdigest() != digest:
                raise RuntimeError("Checkpoint SHA256 mismatch: " + name)
    temporary.rename(output)
    print(json.dumps({"output": str(output), "files": len(files), "bytes": output.stat().st_size,
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "verified_runs": len(verification)}))


if __name__ == "__main__":
    main()
