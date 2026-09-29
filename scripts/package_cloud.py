import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="秘密情報・既存コーパスを含まないクラウド配布用ZIPを作る")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = ["AGENTS.md", "README.md", "PLAN.md", "Dockerfile", ".dockerignore", ".gitignore", ".gitattributes", ".github/workflows/lab.yml", "compose.yaml", "jse/__init__.py", "docs/cloud-lab.md", "scripts/cloud_setup.sh", "scripts/package_cloud.py", "experiments/pilot.example.json", "tests/test_lab.py"]
    files.extend(str(path.relative_to(root)).replace("\\", "/") for path in sorted((root / "jse/lab").glob("*.py")))
    if (root / "LICENSE").is_file():
        files.append("LICENSE")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {}
    with zipfile.ZipFile(args.output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            content = (root / name).read_bytes()
            archive.writestr(name, content)
            manifest[name] = hashlib.sha256(content).hexdigest()
        archive.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
    print(json.dumps({"output": str(args.output), "files": len(files), "bytes": args.output.stat().st_size}))


if __name__ == "__main__":
    main()
