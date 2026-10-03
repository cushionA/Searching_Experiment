import argparse
import hashlib
import json
import zipfile
from pathlib import Path


STATIC_FILES = (
    "package.json", "docs/browser-benchmark.md", "docs/proxy.md",
    "experiments/browser-benchmark-20261003/results.json",
    "experiments/records/browser-benchmark-20261003.json",
    "AGENTS.md", "README.md", "PLAN.md", "Dockerfile", "compose.yaml",
    "compose.bot-diagnostics-cloud.yaml", ".dockerignore", ".gitignore", ".gitattributes",
    ".github/workflows/lab.yml", ".codex/config.toml", ".codex/agents/luna.toml",
    "jse/__init__.py", "experiments/pilot.example.json", "experiments/search-candidates-20261002.json",
    "docs/cloud-lab.md", "docs/cloud-validation.md", "docs/topic-discovery.md", "docs/free-search-engines.md",
    "docs/bot-diagnostics-2026-10-01.md", "docs/bot-diagnostics-evidence.md",
    "docs/bot-diagnostics-framework-validation-2026-10-01.md",
    "docs/oxibrowser-experiment.md", "docs/oxibrowser-selectors-20261001.json",
    ".agents/skills/bot-blocking-scenarios/SKILL.md",
    ".agents/skills/kaggle-ops/SKILL.md", ".agents/skills/kaggle-ops/requirements.txt",
    ".agents/skills/kaggle-ops/agents/openai.yaml", ".agents/skills/kaggle-ops/references/notebooks.md",
    ".agents/skills/kaggle-ops/scripts/kaggle_ops.py",
)

# Enumerate only these public code directories, each at one level.
PATTERNS = {
    "jse/lab": ("*.py",),
    "tests": ("test*.py", "__init__.py"),
    "scripts": ("check_*.py", "cloud_setup.sh", "setup_kaggle.sh", "package_cloud.py",
                "browser_benchmark.py", "run_browser_benchmark.sh", "setup_browser_benchmark.sh", "with_proxy.py"),
    "requirements": ("crawl-tools.txt", "browser-tools.txt", "adaptive-tools.txt", "browser-benchmark.txt"),
    "experiments/bot-diagnostics": ("*.mjs", "*.json", "*.py", "*.sh", "Dockerfile", "README.md"),
    "experiments/bot-diagnostics/extensions/observation-probe": ("*.json", "*.js"),
}


def distribution_files(root):
    """Return sorted public code, excluding runtime, credentials and saved runs."""
    root = root.resolve()
    files = {root / name for name in STATIC_FILES}
    for directory, patterns in PATTERNS.items():
        for pattern in patterns:
            files.update((root / directory).glob(pattern))
    if (root / "LICENSE").is_file():
        files.add(root / "LICENSE")
    for file in files:
        if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(root):
            raise RuntimeError(f"Unsafe or missing distribution file: {file.relative_to(root)}")
    return sorted(files)


def package(root, output):
    root = root.resolve()
    files = distribution_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {}
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            name = file.relative_to(root).as_posix()
            content = file.read_bytes()
            archive.writestr(name, content)
            manifest[name] = hashlib.sha256(content).hexdigest()
        archive.writestr("MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Distribution ZIP CRC verification failed")
        for name, digest in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError("Distribution SHA256 mismatch: " + name)
    return {"output": str(output), "files": len(files), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description="秘密情報・既存コーパスを含まないクラウド配布用ZIPを作る")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(Path(__file__).resolve().parents[1], args.output)))


if __name__ == "__main__":
    main()
