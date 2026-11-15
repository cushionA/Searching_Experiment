import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {".git", "__pycache__", "node_modules", ".venv", "venv"}
UPSTREAM = {"pinchtab-webgraph", "smart-crawler", "smart-crawler-upstream", "autopager", "co-scraper", "webNavigator"}


def included(run, file):
    relative = file.relative_to(run)
    if any(part in EXCLUDED for part in relative.parts):
        return False
    if relative.parts[0].startswith(("fixture-", "bulk-fixture-")) and relative.parts[0] not in {"fixture-e2e-ry4fik", "bulk-fixture-G3IXpj"}:
        return False
    if relative.parts[0] == "candidate-audit" and len(relative.parts) > 1 and relative.parts[1] in UPSTREAM:
        return False
    return file.suffix.lower() not in {".exe", ".bin", ".safetensors", ".pyc", ".zip"} and file != run / "inventory.json" and file.name not in {"run.lock", "CLOUD-CHECKPOINT.json", "B2-CHECKPOINT.json", "ALL-CANDIDATES-CHECKPOINT.json", "SITEMAP-AGENT-CHECKPOINT.json"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-targets", type=int, default=80)
    args = parser.parse_args()
    run = args.run.resolve()
    targets = [json.loads(line) for line in (run / "targets.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert args.expected_targets > 0
    assert len(targets) == args.expected_targets and len({row["domain"] for row in targets}) == args.expected_targets
    files = [file for file in sorted(run.rglob("*")) if file.is_file() and included(run, file)]
    code = [file for file in sorted(Path(__file__).parent.iterdir()) if file.suffix in {".py", ".mjs"}]
    inventory = {str(file.relative_to(ROOT)).replace("\\", "/"): {
        "bytes": file.stat().st_size, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()
    } for file in files + code}
    (run / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    verification = {"target_count": len(targets), "registered_domains": len({row["domain"] for row in targets}),
                    "files": len(inventory), "bytes": sum(row["bytes"] for row in inventory.values())}
    if args.output:
        output = args.output.resolve()
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for file in files + code + [run / "inventory.json"]:
                archive.write(file, str(file.relative_to(ROOT)).replace("\\", "/"))
        with zipfile.ZipFile(output) as archive:
            assert archive.testzip() is None
            for name, entry in inventory.items():
                assert hashlib.sha256(archive.read(name)).hexdigest() == entry["sha256"], name
        verification.update({"archive": str(output), "archive_bytes": output.stat().st_size,
                             "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "archive_verified": True})
    print(json.dumps(verification, ensure_ascii=False))


if __name__ == "__main__":
    main()
