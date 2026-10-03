"""Install an isolated, optional Camoufox arm; preserve the main diagnostics dependencies."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEPS = Path(os.environ.get("BOT_DIAGNOSTICS_CAMOUFOX_DEPS", REPO / ".deps/camoufox")).resolve()
VERSION = "152.0.4"
RELEASE = "beta.30"
ARCHIVE = f"camoufox-{VERSION}-{RELEASE}-lin.x86_64.zip"
URL = f"https://github.com/daijro/camoufox/releases/download/v{VERSION}-{RELEASE}/{ARCHIVE}"
# Digest published on the upstream GitHub release asset, checked 2026-10-03.
SHA256 = "5720d45b894ce1770543de024c6f10d514b38be560fa2dc3226b3d8586caf672"
DOWNLOAD_CAP = 750_000_000


def digest(filename):
    with filename.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def install_archive(archive, destination):
    if archive.stat().st_size > DOWNLOAD_CAP or digest(archive) != SHA256:
        raise RuntimeError("Camoufox archive size or SHA256 mismatch")
    # A new directory avoids overwriting a partial or different installation.
    with tempfile.TemporaryDirectory(prefix="camoufox-install-", dir=destination.parent) as directory:
        staging = Path(directory)
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.infolist():
                output = (staging / member.filename).resolve()
                mode = member.external_attr >> 16
                if not output.is_relative_to(staging) or stat.S_ISLNK(mode):
                    raise RuntimeError("Unsafe Camoufox archive entry")
                if member.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(member) as source, output.open("wb") as target:
                    shutil.copyfileobj(source, target)
                output.chmod(0o755 if mode & 0o111 else 0o644)
        executable = staging / "camoufox-bin"
        if not executable.is_file() or not (staging / "properties.json").is_file():
            raise RuntimeError("Unexpected Camoufox archive layout")
        executable.chmod(0o755)
        (staging / "version.json").write_text(json.dumps({"version": VERSION, "release": RELEASE}) + "\n")
        staging.rename(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-archive", type=Path, help="Reuse the exact pinned upstream ZIP after hash validation")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("This pinned experiment currently supports Linux x86_64 only")
    DEPS.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(HERE / "camoufox" / name, DEPS / name)
    subprocess.run([
        "npm", "ci", "--prefix", str(DEPS), "--cache", str(REPO / ".deps/npm-cache"),
        "--no-audit", "--no-fund",
    ], check=True, env={**os.environ, "npm_config_devdir": str(DEPS / "node-gyp")})
    browser = DEPS / "browser"
    metadata = DEPS / "browser-runtime.json"
    if browser.exists():
        recorded = json.loads(metadata.read_text()) if metadata.exists() else {}
        if recorded.get("archive_sha256") != SHA256 or recorded.get("executable_sha256") != digest(browser / "camoufox-bin"):
            raise RuntimeError("Existing Camoufox installation differs; use a new dependency directory")
    else:
        archive = args.browser_archive or DEPS / ARCHIVE
        if not archive.exists():
            if args.browser_archive:
                raise FileNotFoundError(archive)
            temporary = archive.with_suffix(".download")
            try:
                request = urllib.request.Request(URL, headers={"User-Agent": "SearchingExperiment-setup"})
                with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as target:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > DOWNLOAD_CAP:
                            raise RuntimeError("Camoufox download size cap exceeded")
                        target.write(chunk)
                if digest(temporary) != SHA256:
                    raise RuntimeError("Camoufox download SHA256 mismatch")
                temporary.rename(archive)
            finally:
                temporary.unlink(missing_ok=True)
        install_archive(archive, browser)
        metadata.write_text(json.dumps({
            "package": "camoufox-js@0.12.0", "playwright_core": "1.60.0",
            "browser_version": f"{VERSION}-{RELEASE}", "source": URL,
            "archive_sha256": SHA256, "executable": str(browser / "camoufox-bin"),
            "executable_sha256": digest(browser / "camoufox-bin"),
        }, indent=2) + "\n")
    print(json.dumps({"camoufox": "installed", "runtime": str(metadata),
                      "scope": "optional browser observation; no external site test"}))


if __name__ == "__main__":
    main()
