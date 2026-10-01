"""Export verified runs and reusable scenario code without runtime binaries or credentials."""
import argparse
import hashlib
import json
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
    for run in runs:
        if not run.is_relative_to(REPO) or not run.is_dir():
            raise RuntimeError("Run must be an existing directory in this repository")
        for ledger in sorted(run.rglob("ledger.json")):
            directory = ledger.parent
            result = subprocess.run(["node", str(HERE / "runner.mjs"), "verify", str(directory)],
                                    capture_output=True, text=True, check=True, timeout=60)
            verified = json.loads(result.stdout)
            if not verified["ok"]:
                raise RuntimeError(f"Verification failed: {directory}: {verified['errors']}")
            verification[str(directory.relative_to(REPO))] = verified
    roots = [HERE, REPO / ".agents/skills/bot-blocking-scenarios", *runs]
    files = [REPO / p for p in ["README.md", "AGENTS.md", "tests/test_lab_bot_diagnostics.py",
             "docs/bot-diagnostics-2026-10-01.md", "docs/bot-diagnostics-evidence.md",
             "docs/oxibrowser-experiment.md", "docs/oxibrowser-selectors-20261001.json"]]
    for root in roots:
        files += [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
                  and p.suffix not in {".zip", ".pyc"} and "__pycache__" not in p.parts]
    files = sorted(set(files))
    manifest = {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for file in files:
            zipped.write(file, str(file.relative_to(REPO)))
        zipped.writestr("SHA256.json", json.dumps(manifest, indent=2) + "\n")
        zipped.writestr("CHECKPOINT.json", json.dumps({"commit": commit, "verification": verification,
            "source_files_are_authoritative": True, "runtime_binaries_included": False,
            "resume": "Use saved scenario and remaining ledger budget; browser/client cookies restart."}, indent=2) + "\n")
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
