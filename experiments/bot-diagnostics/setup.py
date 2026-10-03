"""Install optional benchmark dependencies and hash-check the tested Lightpanda build."""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-browser", action="store_true", help="Install the pinned Playwright Chrome for Testing in the workspace")
    parser.add_argument("--lightpanda-file", type=Path, help="Use an existing public Lightpanda binary after checking the pinned SHA256")
    parser.add_argument("--lightpanda-release", choices=["legacy", "1.0.0"], default="legacy",
                        help="Opt into the separately pinned Linux x86_64 enhancement experiment")
    args = parser.parse_args()
    DEPS.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(HERE / name, DEPS / name)
    subprocess.run(
        ["npm", "ci", "--prefix", str(DEPS), "--cache", str(REPO / ".deps/npm-cache"), "--no-audit", "--no-fund"],
        check=True,
    )
    if args.with_browser:
        browser_root = DEPS / "browsers"
        subprocess.run(
            ["node", str(DEPS / "node_modules/playwright/cli.js"), "install", "chromium", "--no-shell"],
            env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browser_root)}, check=True,
        )
        data = json.loads((DEPS / "node_modules/playwright-core/browsers.json").read_text())
        browser = next(b for b in data["browsers"] if b["name"] == "chromium")
        executable = browser_root / ("chromium-" + browser["revision"]) / "chrome-linux64/chrome"
        if not executable.exists():
            raise RuntimeError("The bundled-browser option currently requires Linux x86_64")
        (DEPS / "browser-runtime.json").write_text(json.dumps({
            "executable": str(executable), "version": browser["browserVersion"],
            "revision": browser["revision"], "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }, indent=2) + "\n")
        link = DEPS / "chromium"
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            raise RuntimeError("Refusing to replace a non-symlink Chromium path")
        link.symlink_to(executable.relative_to(DEPS))
        print("BOT_DIAGNOSTICS_CHROMIUM=" + str(executable))
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
    stable = args.lightpanda_release == "1.0.0"
    binary = REPO / ".deps/lightpanda" if stable else DEPS / "lightpanda"
    expected = "aa5a4b8ed53d1e38b3c73f5b2647d0a84a82e6744557f45f9a9c85858aa031c3" if stable else VERSIONS["lightpanda_sha256"]
    url = "https://github.com/lightpanda-io/browser/releases/download/1.0.0/lightpanda-x86_64-linux" if stable else VERSIONS["lightpanda_url"]
    binary.parent.mkdir(parents=True, exist_ok=True)
    if args.lightpanda_file:
        body = args.lightpanda_file.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected:
            raise RuntimeError("Supplied Lightpanda hash mismatch")
        binary.write_bytes(body)
    if not binary.exists():
        body = download(url, 256_000_000)
        if hashlib.sha256(body).hexdigest() != expected:
            raise RuntimeError("Nightly changed. Supply the tested build; do not silently compare a different version.")
        binary.write_bytes(body)
    if hashlib.sha256(binary.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Lightpanda hash mismatch")
    binary.chmod(0o755)
    subprocess.run([str(binary), "version"], check=True)
    print("Dependencies ready. Chromium's trust of the environment proxy CA is a separate prerequisite.")


if __name__ == "__main__":
    main()
