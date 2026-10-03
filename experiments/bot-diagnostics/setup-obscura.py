#!/usr/bin/env python3
"""Install the pinned official Obscura v0.2.3 Linux x86_64 release assets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath


VERSION = "0.2.3"
TAG = f"v{VERSION}"
REPOSITORY = "https://github.com/h4ckf0r0day/obscura"
RELEASE_URL = f"{REPOSITORY}/releases/tag/{TAG}"
LICENSE_URL = f"https://raw.githubusercontent.com/h4ckf0r0day/obscura/{TAG}/LICENSE"
ASSETS = (
    {
        "variant": "default",
        "archive": "obscura-x86_64-linux.tar.gz",
        "sha256": "1534d1e6ddaf3d080ec4091eb41d0a4d8cc042a48b607d3c410fc13b482a9eec",
        "bytes": 70_880_637,
    },
    {
        "variant": "stealth",
        "archive": "obscura-x86_64-linux-stealth.tar.gz",
        "sha256": "1283fff4b781eca438294ae1ba4bf986b63d7628097150a3910ed8f3e3e2142e",
        "bytes": 74_262_812,
    },
    {
        "variant": "no-render",
        "archive": "obscura-x86_64-linux-no-render.tar.gz",
        "sha256": "b5e55e8f2c97814127a521cd59af1a84b79dc40cf658fda04df04af81a2d89f3",
        "bytes": 42_966_565,
    },
)
LICENSE_SHA256 = "50e6751797c50dedd75ef1b8a0d9e42f5f8472e9fbce91f34718e9f97b0c780a"
ARCHIVE_MEMBERS = {"obscura", "obscura-worker"}
MAX_MEMBER_BYTES = 200 * 1024 * 1024
ALLOWED_REDIRECT_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
}


class SetupError(RuntimeError):
    pass


class CheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urllib.parse.urlsplit(new_url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_REDIRECT_HOSTS:
            raise SetupError("refusing an unexpected download redirect")
        return super().redirect_request(request, response, code, message, headers, new_url)


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def opener() -> urllib.request.OpenerDirector:
    # ProxyHandler reads the inherited HTTP(S)_PROXY values. The explicit
    # context uses the normal verified TLS store, including SSL_CERT_FILE/DIR.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        CheckedRedirectHandler(),
    )


def download(
    opener_: urllib.request.OpenerDirector,
    url: str,
    path: Path,
    *,
    limit: int,
    expected_size: int | None = None,
) -> int:
    count = 0
    request = urllib.request.Request(url, headers={"User-Agent": "Searching-Experiment-Obscura-Setup/1"})
    try:
        with opener_.open(request, timeout=60) as response, path.open("xb") as output:
            if response.status != 200:
                raise SetupError("download returned an unexpected status")
            length = response.headers.get("Content-Length")
            if length and int(length) > limit:
                raise SetupError("download exceeded the pinned size limit")
            if length and expected_size is not None and int(length) != expected_size:
                raise SetupError("download size differs from the pinned release metadata")
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > limit:
                    raise SetupError("download exceeded the pinned release size")
                output.write(chunk)
    except SetupError:
        raise
    except (OSError, urllib.error.URLError, ValueError) as error:
        # Avoid echoing URLs or environment-derived proxy credentials.
        raise SetupError(f"download failed ({type(error).__name__})") from None
    if expected_size is not None and count != expected_size:
        raise SetupError("download size differs from the pinned release metadata")
    return count


def extract_archive(archive: Path, destination: Path) -> dict[str, str]:
    destination.mkdir(mode=0o700)
    binary_hashes: dict[str, str] = {}
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            members = tar.getmembers()
            if len(members) != len(ARCHIVE_MEMBERS) or {m.name for m in members} != ARCHIVE_MEMBERS:
                raise SetupError(f"unexpected members in {archive.name}")
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or len(path.parts) != 1
                    or not member.isfile()
                    or member.size < 0
                    or member.size > MAX_MEMBER_BYTES
                ):
                    raise SetupError(f"unsafe archive member in {archive.name}")
                source = tar.extractfile(member)
                if source is None:
                    raise SetupError(f"unreadable archive member in {archive.name}")
                target = destination / member.name
                written = 0
                with source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > member.size:
                            raise SetupError(f"archive member exceeded its declared size")
                        output.write(chunk)
                if written != member.size:
                    raise SetupError("archive member was truncated")
                target.chmod(0o755)
                binary_hashes[member.name] = digest_file(target)
    except (tarfile.TarError, OSError) as error:
        if isinstance(error, SetupError):
            raise
        raise SetupError(f"archive extraction failed ({type(error).__name__})") from None
    return binary_hashes


def expected_asset_url(asset: dict[str, object]) -> str:
    return f"{REPOSITORY}/releases/download/{TAG}/{asset['archive']}"


def verify_existing(target: Path) -> bool:
    if target.is_symlink() or not target.is_dir():
        return False
    try:
        manifest = json.loads((target / "SOURCE.json").read_text(encoding="utf-8"))
        if (
            manifest.get("project") != REPOSITORY
            or manifest.get("version") != VERSION
            or manifest.get("tag") != TAG
            or manifest.get("license") != "Apache-2.0"
            or manifest.get("license_source_url") != LICENSE_URL
            or manifest.get("license_sha256") != LICENSE_SHA256
            or len(manifest.get("assets", [])) != len(ASSETS)
            or digest_file(target / "LICENSE") != LICENSE_SHA256
            or (target / "SHA256SUMS").read_text(encoding="ascii")
            != "".join(f"{asset['sha256']}  {asset['archive']}\n" for asset in ASSETS)
        ):
            return False
        for expected, actual in zip(ASSETS, manifest["assets"], strict=True):
            if (
                actual.get("variant") != expected["variant"]
                or actual.get("archive") != expected["archive"]
                or actual.get("source_url") != expected_asset_url(expected)
                or actual.get("sha256") != expected["sha256"]
                or actual.get("archive_bytes") != expected["bytes"]
                or digest_file(target / expected["archive"]) != expected["sha256"]
            ):
                return False
            for name in ARCHIVE_MEMBERS:
                binary = target / expected["variant"] / name
                if not binary.is_file() or not os.access(binary, os.X_OK):
                    return False
                if actual.get("binary_sha256", {}).get(name) != digest_file(binary):
                    return False
                if name == "obscura":
                    completed = subprocess.run(
                        [str(binary), "--version"],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if completed.returncode or completed.stdout.strip() != f"obscura {VERSION}":
                        return False
        return True
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def install(root: Path) -> Path:
    deps = root / ".deps"
    deps.mkdir(mode=0o700, exist_ok=True)
    if deps.is_symlink() or not deps.is_dir():
        raise SetupError(".deps must be a real directory")
    target = deps / f"obscura-{VERSION}"
    if target.exists() or target.is_symlink():
        if verify_existing(target):
            print(f"Obscura {VERSION} already matches the pinned release at {target}")
            return target
        raise SetupError(f"target exists and does not match the pinned release; refusing to overwrite: {target}")

    stage = Path(tempfile.mkdtemp(prefix=f".obscura-{VERSION}-stage-", dir=deps))
    os.chmod(stage, 0o700)
    try:
        download_opener = opener()
        manifest: dict[str, object] = {
            "project": REPOSITORY,
            "version": VERSION,
            "tag": TAG,
            "release_url": RELEASE_URL,
            "license": "Apache-2.0",
            "license_source_url": LICENSE_URL,
            "license_sha256": LICENSE_SHA256,
            "assets": [],
        }
        for asset in ASSETS:
            archive = stage / str(asset["archive"])
            url = expected_asset_url(asset)
            download(
                download_opener,
                url,
                archive,
                limit=int(asset["bytes"]),
                expected_size=int(asset["bytes"]),
            )
            if digest_file(archive) != asset["sha256"]:
                raise SetupError(f"SHA-256 mismatch for {asset['archive']}")
            binary_hashes = extract_archive(archive, stage / str(asset["variant"]))
            binary = stage / str(asset["variant"]) / "obscura"
            completed = subprocess.run(
                [str(binary), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if completed.returncode or completed.stdout.strip() != f"obscura {VERSION}":
                raise SetupError(f"unexpected executable version in {asset['archive']}")
            manifest["assets"].append(
                {
                    "variant": asset["variant"],
                    "archive": asset["archive"],
                    "source_url": url,
                    "sha256": asset["sha256"],
                    "archive_bytes": asset["bytes"],
                    "binary_paths": [f".deps/obscura-{VERSION}/{asset['variant']}/{name}" for name in sorted(ARCHIVE_MEMBERS)],
                    "binary_sha256": binary_hashes,
                }
            )
        license_path = stage / "LICENSE"
        download(download_opener, LICENSE_URL, license_path, limit=100_000)
        if digest_file(license_path) != LICENSE_SHA256:
            raise SetupError("LICENSE SHA-256 mismatch for the pinned tag")
        (stage / "SOURCE.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (stage / "SHA256SUMS").write_text(
            "".join(f"{asset['sha256']}  {asset['archive']}\n" for asset in ASSETS),
            encoding="ascii",
        )

        # Never replace an existing target. The final rename is atomic on the
        # repository's filesystem; if another installer won the race, leave it.
        if target.exists() or target.is_symlink():
            if verify_existing(target):
                print(f"Obscura {VERSION} already matches the pinned release at {target}")
                return target
            raise SetupError(f"target appeared during install; refusing to overwrite: {target}")
        stage.rename(target)
        print(f"Installed Obscura {VERSION} (default, stealth, no-render) under {target}")
        return target
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        target = install(root)
    except SetupError as error:
        print(f"Obscura setup failed: {error}", file=sys.stderr)
        return 1
    print(f"Default binary:   {target / 'default' / 'obscura'}")
    print(f"Stealth binary:   {target / 'stealth' / 'obscura'}")
    print(f"No-render binary: {target / 'no-render' / 'obscura'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
