"""Install optional benchmark dependencies and hash-check the tested Lightpanda build."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEPS = Path(os.environ.get("BOT_DIAGNOSTICS_DEPS", REPO / ".deps/bot-diagnostics"))
VERSIONS = json.loads((HERE / "versions.json").read_text())


def download(url, cap):
    with urllib.request.urlopen(url, timeout=60) as response:
        body = response.read(cap + 1)
    if len(body) > cap:
        raise RuntimeError("download size cap exceeded")
    return body


def main():
    DEPS.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(HERE / name, DEPS / name)
    subprocess.run(
        ["npm", "ci", "--prefix", str(DEPS), "--cache", str(REPO / ".deps/npm-cache"), "--no-audit", "--no-fund"],
        check=True,
    )
    commit = VERSIONS["detector_commit"]
    archive = download(
        "https://codeload.github.com/rebrowser/rebrowser-bot-detector/zip/" + commit,
        3_000_000,
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        destination = (DEPS / "detector").resolve()
        for member in zipped.infolist():
            relative = Path(*Path(member.filename).parts[1:])
            output = (destination / relative).resolve()
            if member.is_dir():
                continue
            if not output.is_relative_to(destination):
                raise RuntimeError("unsafe archive path")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(zipped.read(member))
    (DEPS / "detector-source.json").write_text(json.dumps({
        "repo": "https://github.com/rebrowser/rebrowser-bot-detector", "commit": commit,
    }, indent=2) + "\n")
    binary = DEPS / "lightpanda"
    if not binary.exists():
        body = download(VERSIONS["lightpanda_url"], 256_000_000)
        if hashlib.sha256(body).hexdigest() != VERSIONS["lightpanda_sha256"]:
            raise RuntimeError("Nightly changed. Supply the tested build; do not silently compare a different version.")
        binary.write_bytes(body)
    if hashlib.sha256(binary.read_bytes()).hexdigest() != VERSIONS["lightpanda_sha256"]:
        raise RuntimeError("Lightpanda hash mismatch")
    binary.chmod(0o755)
    subprocess.run([str(binary), "version"], check=True)
    print("Dependencies ready. Chromium's trust of the environment proxy CA is a separate prerequisite.")


if __name__ == "__main__":
    main()
