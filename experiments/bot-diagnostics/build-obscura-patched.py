#!/usr/bin/env python3
"""Build the patched, no-render Obscura v0.2.3 binaries from pinned source.

Prerequisites: git, Rust/Cargo (install separately with rustup), and normal
verified network/proxy configuration for GitHub and Cargo. This helper never
installs toolchains, edits the official release under .deps/obscura-0.2.3, or
changes HOME, proxy variables, or TLS settings.

Run from any directory:
    python3 experiments/bot-diagnostics/build-obscura-patched.py
    python3 experiments/bot-diagnostics/build-obscura-patched.py --replace

The second form is required to replace a different existing custom-build
manifest. Cargo home and target directory may be supplied through CARGO_HOME
and CARGO_TARGET_DIR; otherwise isolated defaults are created under .deps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


REPOSITORY = "https://github.com/h4ckf0r0day/obscura.git"
SOURCE_URL = "https://github.com/h4ckf0r0day/obscura"
TAG = "v0.2.3"
COMMIT = "1a3169da276d7720732c7b20535474942917fb83"
REPORTED_VERSION = "obscura 0.1.0"
SOURCE_PACKAGE_VERSION = "0.1.0"
BUILD_COMMAND = [
    "cargo",
    "build",
    "--release",
    "--locked",
    "--no-default-features",
    "-p",
    "obscura-cli",
    "--bins",
]
BINARIES = ("obscura", "obscura-worker")
MAX_BINARY_BYTES = 256 * 1024 * 1024


class BuildError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_checked(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=None,
        )
    except FileNotFoundError as error:
        raise BuildError(f"required command is unavailable: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "").strip()
        # Keep build diagnostics useful without echoing credentials inherited
        # in proxy or registry environment variables.
        if env:
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                secret = env.get(name)
                if secret:
                    details = details.replace(secret, "[redacted proxy]")
        details = details[-8_000:]
        suffix = f":\n{details}" if details else ""
        raise BuildError(f"command failed ({error.returncode}): {command[0]}{suffix}") from None


def command_version(command: str, env: dict[str, str]) -> str:
    result = run_checked([command, "--version"], env=env)
    return result.stdout.strip().splitlines()[0]


def load_manifest(path: Path) -> dict[str, object] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def build(root: Path, *, replace: bool, source_dir: Path | None = None) -> Path:
    script_dir = Path(__file__).resolve().parent
    patch_path = script_dir / "patches" / "obscura-0.2.3-interception.patch"
    if patch_path.is_symlink() or not patch_path.is_file():
        raise BuildError(f"required source patch is missing: {patch_path}")
    patch_sha256 = sha256_file(patch_path)

    deps = root / ".deps"
    deps.mkdir(mode=0o700, exist_ok=True)
    if deps.is_symlink() or not deps.is_dir():
        raise BuildError(".deps must be a real directory")

    target = deps / "obscura-patched"
    if target.is_symlink():
        raise BuildError("refusing to replace a symlink at .deps/obscura-patched")
    existing = load_manifest(target / "build.json") if target.exists() else None
    if target.exists() and not replace:
        same_build = (
            existing is not None
            and existing.get("project") == SOURCE_URL
            and existing.get("upstream_tag") == TAG
            and existing.get("upstream_commit") == COMMIT
            and existing.get("patch_sha256") == patch_sha256
            and existing.get("features") == "no-default-features"
        )
        if not same_build:
            raise BuildError(
                "an existing custom binary set has a different or unrecognized manifest; "
                "use --replace only after reviewing it"
            )

    cargo = shutil.which("cargo")
    rustc = shutil.which("rustc")
    git = shutil.which("git")
    if not cargo or not rustc or not git:
        missing = [name for name, path in (("cargo", cargo), ("rustc", rustc), ("git", git)) if not path]
        raise BuildError(
            "missing prerequisite command(s): "
            + ", ".join(missing)
            + ". Install Rust/Cargo separately with rustup, then rerun this helper."
        )

    build_env = os.environ.copy()
    cargo_home = build_env.get("CARGO_HOME")
    if not cargo_home:
        cargo_home = str(deps / "obscura-build" / "cargo")
        build_env["CARGO_HOME"] = cargo_home
    elif not Path(cargo_home).is_absolute():
        cargo_home = str((Path.cwd() / cargo_home).resolve())
        build_env["CARGO_HOME"] = cargo_home
    target_dir = build_env.get("CARGO_TARGET_DIR")
    if not target_dir:
        target_dir = str(deps / "obscura-build" / "target")
        build_env["CARGO_TARGET_DIR"] = target_dir
    elif not Path(target_dir).is_absolute():
        target_dir = str((Path.cwd() / target_dir).resolve())
        build_env["CARGO_TARGET_DIR"] = target_dir
    Path(cargo_home).mkdir(mode=0o700, parents=True, exist_ok=True)
    Path(target_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    build_env.setdefault("CARGO_BUILD_JOBS", "2")
    build_env.setdefault("CARGO_INCREMENTAL", "0")

    build_root = deps / "obscura-patched-build"
    build_root.mkdir(mode=0o700, exist_ok=True)
    if build_root.is_symlink() or not build_root.is_dir():
        raise BuildError(".deps/obscura-patched-build must be a real directory")
    stage = Path(tempfile.mkdtemp(prefix="stage-", dir=build_root))
    package_stage: Path | None = None
    backup: Path | None = None
    try:
        if source_dir is None:
            source = stage / "source"
            run_checked(
                [git, "clone", "--no-checkout", "--filter=blob:none", REPOSITORY, str(source)],
                env=build_env,
            )
            run_checked([git, "-C", str(source), "checkout", "--detach", TAG], env=build_env)
        else:
            source = source_dir.resolve(strict=True)
            if source.is_symlink() or not source.is_dir():
                raise BuildError("--source-dir must be a real Obscura source directory")
        actual_commit = run_checked(
            [git, "-C", str(source), "rev-parse", "HEAD"], env=build_env
        ).stdout.strip()
        if actual_commit != COMMIT:
            raise BuildError(f"tag {TAG} resolved to an unexpected commit: {actual_commit}")
        remote = run_checked(
            [git, "-C", str(source), "remote", "get-url", "origin"], env=build_env
        ).stdout.strip().removesuffix("/")
        if remote.removesuffix(".git") != SOURCE_URL:
            raise BuildError("source checkout origin is not the official Obscura repository")
        if source_dir is None:
            run_checked(
                [git, "-C", str(source), "apply", "--check", "--whitespace=error", str(patch_path)],
                env=build_env,
            )
            run_checked(
                [git, "-C", str(source), "apply", "--whitespace=error", str(patch_path)],
                env=build_env,
            )
        else:
            source_diff = run_checked(
                [git, "-C", str(source), "diff", "--binary", COMMIT], env=build_env
            ).stdout
            if hashlib.sha256(source_diff.encode("utf-8")).hexdigest() != patch_sha256:
                raise BuildError("existing source diff does not exactly match the pinned patch file")
            status = run_checked(
                [git, "-C", str(source), "status", "--porcelain", "--untracked-files=all"],
                env=build_env,
            ).stdout.strip()
            if any(line.startswith("??") for line in status.splitlines()):
                raise BuildError("existing source checkout contains untracked files")

        rustc_version = command_version("rustc", build_env)
        cargo_version = command_version("cargo", build_env)
        print(f"Building {TAG} at {COMMIT} with {rustc_version}", flush=True)
        print("Command: " + " ".join(BUILD_COMMAND), flush=True)
        run_checked(BUILD_COMMAND, cwd=source, env=build_env)

        binary_dir = Path(target_dir) / "release"
        outputs: dict[str, dict[str, object]] = {}
        package_stage = Path(tempfile.mkdtemp(prefix=".obscura-patched-stage-", dir=deps))
        for name in BINARIES:
            built = binary_dir / name
            if built.is_symlink() or not built.is_file():
                raise BuildError(f"cargo did not produce the expected binary: {name}")
            size = built.stat().st_size
            if size <= 0 or size > MAX_BINARY_BYTES:
                raise BuildError(f"unexpected size for {name}: {size}")
            destination = package_stage / name
            shutil.copy2(built, destination)
            destination.chmod(0o755)
            outputs[name] = {"bytes": size, "sha256": sha256_file(destination)}

        cli_result = run_checked([str(package_stage / "obscura"), "--version"], env=build_env)
        cli_reported_version = cli_result.stdout.strip()
        if cli_reported_version != REPORTED_VERSION and not (
            cli_reported_version.startswith(REPORTED_VERSION + "-")
            or cli_reported_version.startswith(REPORTED_VERSION + "+")
        ):
            raise BuildError(
                f"unexpected CLI version string: {cli_reported_version!r}; "
                f"expected {REPORTED_VERSION!r} (optionally with build metadata) "
                "for the pinned source tag"
            )

        license_source = source / "LICENSE"
        if license_source.is_symlink() or not license_source.is_file():
            raise BuildError("upstream source tree does not contain its LICENSE")
        shutil.copy2(license_source, package_stage / "LICENSE")
        (package_stage / "patches").mkdir()
        shutil.copy2(patch_path, package_stage / "patches" / patch_path.name)
        notice = (
            "Obscura is based on the h4ckf0r0day/obscura v0.2.3 source tag, "
            f"commit {COMMIT}, and is licensed under Apache-2.0.\n"
            f"Source: {SOURCE_URL}/tree/{TAG}\n"
            "See LICENSE for the upstream license text. No upstream NOTICE file "
            "was present in this pinned source tree.\n"
            "This binary includes the project patch identified by patch_sha256 "
            "in build.json; a copy is included in the patches directory.\n"
        )
        (package_stage / "UPSTREAM-NOTICES.txt").write_text(notice, encoding="utf-8")

        manifest: dict[str, object] = {
            "upstream_commit": COMMIT,
            "patch_sha256": patch_sha256,
            "features": "no-default-features",
            "reported_version": cli_reported_version.removeprefix("obscura "),
            "binaries": {name: outputs[name]["sha256"] for name in BINARIES},
            "project": SOURCE_URL,
            "upstream_tag": TAG,
            "source_url": f"{SOURCE_URL}/tree/{TAG}",
            "license": "Apache-2.0",
            "license_sha256": sha256_file(package_stage / "LICENSE"),
            "patch_file": f"patches/{patch_path.name}",
            "build_command": BUILD_COMMAND,
            "cargo_version": cargo_version,
            "rustc_version": rustc_version,
            "source_package_version": SOURCE_PACKAGE_VERSION,
            "tagged_release_cli_version": "obscura 0.2.3",
            "cli_reported_version": cli_reported_version,
            "cdp_reported_product": "Chrome/145.0.0.0",
            "binary_metadata": outputs,
        }
        (package_stage / "build.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (package_stage / "SHA256SUMS").write_text(
            "".join(f"{outputs[name]['sha256']}  {name}\n" for name in BINARIES)
            + f"{sha256_file(package_stage / 'LICENSE')}  LICENSE\n"
            + f"{patch_sha256}  patches/{patch_path.name}\n",
            encoding="ascii",
        )

        if target.exists():
            backup = deps / f".obscura-patched-backup-{os.getpid()}"
            if backup.exists() or backup.is_symlink():
                raise BuildError("refusing to overwrite an existing backup path")
            target.rename(backup)
        try:
            package_stage.rename(target)
            package_stage = None
        except OSError:
            if backup is not None and backup.exists() and not target.exists():
                backup.rename(target)
                backup = None
            raise
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
        print(f"Installed patched Obscura binaries at {target}")
        print(f"CLI reports {cli_reported_version}; source tag is {TAG}")
        return target
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if package_stage is not None:
            shutil.rmtree(package_stage, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build patched Obscura v0.2.3 from the pinned official source tag."
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="replace an existing custom build even if its manifest differs",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="reuse an exact-tag checkout whose git diff matches the patch file",
    )
    parser.epilog = (
        "Rust/Cargo and git must already be installed. The helper does not change "
        "HOME, proxies, TLS settings, or official binaries. It honors CARGO_HOME "
        "and CARGO_TARGET_DIR; defaults are isolated under .deps."
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        build(root, replace=args.replace, source_dir=args.source_dir)
    except BuildError as error:
        print(f"Obscura patched build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
