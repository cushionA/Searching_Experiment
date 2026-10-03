#!/usr/bin/env python3
"""Run a command with one explicit HTTP(S) proxy shared by all clients."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


POLICY_PATH = Path("/etc/codex/network-policy.json")
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
POLICY_MESSAGE = (
    "Proxy changes are blocked by the managed network policy; proxy routing changes "
    "must use the environment management path. This proxy can be used on a regular GCP VM."
)


class ProxyError(ValueError):
    pass


def _host_is_valid(host: str) -> bool:
    if not host or any(ch.isspace() or ord(ch) < 33 for ch in host):
        return False
    if ":" in host:  # urlsplit has already checked IPv6 bracket syntax.
        try:
            ipaddress.IPv6Address(host)
            return True
        except ipaddress.AddressValueError:
            return False
    try:
        host.encode("idna")
    except UnicodeError:
        return False
    return all(
        label and len(label) <= 63 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in host.rstrip(".").split(".")
    )


def validate_proxy(value: str) -> dict[str, object]:
    """Validate a credential-free HTTP(S) proxy URL without echoing it on errors."""
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ProxyError("invalid proxy URL")
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
    except (ValueError, UnicodeError):
        raise ProxyError("invalid proxy URL") from None
    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or "?" in value
        or "#" in value
        or parsed.path not in {"", "/"}
        or host is None
        or not _host_is_valid(host)
        or port is None
        or not 1 <= port <= 65535
    ):
        raise ProxyError("invalid proxy URL")
    return {"scheme": scheme, "host": host, "port": port}


def _identity(url: str) -> tuple[str, str, int]:
    info = validate_proxy(url)
    return str(info["scheme"]), str(info["host"]).lower(), int(info["port"])


def _managed_policy(policy_path: str | Path) -> tuple[bool, bool]:
    """Return (managed, snapshot_valid); absence is an unmanaged environment."""
    path = Path(policy_path)
    try:
        with path.open("r", encoding="utf-8") as stream:
            snapshot = json.load(stream)
    except FileNotFoundError:
        return False, True
    except (OSError, UnicodeError, json.JSONDecodeError):
        return True, False
    return True, isinstance(snapshot, dict) and type(snapshot.get("version")) is int and snapshot["version"] == 1


def _inherited_proxy(env: dict[str, str]) -> str | None:
    values = [env[name] for name in PROXY_VARIABLES if env.get(name)]
    if not values:
        return None
    try:
        identities = {_identity(value) for value in values}
    except ProxyError:
        return None
    return values[0] if len(identities) == 1 else None


def prepare(proxy: str | None, source: str, env: dict[str, str], policy_path: str | Path) -> dict[str, object]:
    """Prepare child env and safe check metadata. Does not mutate the parent env."""
    managed, snapshot_valid = _managed_policy(policy_path)
    info: dict[str, object] | None = None
    same_managed_proxy = False
    if proxy is not None:
        info = validate_proxy(proxy)
        inherited = _inherited_proxy(env)
        same = False
        if inherited is not None:
            try:
                same = _identity(proxy) == _identity(inherited)
            except ProxyError:
                same = False
        if managed and (not snapshot_valid or not same):
            raise ProxyError(POLICY_MESSAGE)
        same_managed_proxy = managed and same

    child_env = dict(env)
    if proxy is not None and not same_managed_proxy:
        for name in PROXY_VARIABLES:
            child_env[name] = proxy
        child_env.pop("ALL_PROXY", None)
        child_env.pop("all_proxy", None)

    selected = proxy
    if selected is None:
        selected = _inherited_proxy(env)
        if selected is not None:
            info = validate_proxy(selected)
            source = "inherited"
        else:
            info = None
            source = "inherited"
    check = {
        "scheme": info["scheme"] if info else None,
        "host": info["host"] if info else None,
        "port": info["port"] if info else None,
        "source": source,
        "managed": managed,
        "settings_restricted": managed,
        "no_proxy": {name: env[name] for name in ("NO_PROXY", "no_proxy") if name in env},
    }
    return {"env": child_env, "check": check, "proxy": proxy, "managed": managed}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", dest="proxy")
    parser.add_argument("--check", action="store_true", help="print safe proxy configuration JSON")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    return args


def _terminate(proc: subprocess.Popen[bytes]) -> int:
    if os.name == "posix":
        # Browsers can own several children. Stop the whole command session.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            return int(proc.wait(timeout=3))
        except subprocess.TimeoutExpired:
            pass
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return int(proc.wait())
    if proc.poll() is not None:
        return int(proc.returncode or 0)
    try:
        proc.terminate()
        return int(proc.wait(timeout=3))
    except subprocess.TimeoutExpired:
        proc.kill()
        return int(proc.wait())


def main(argv: list[str] | None = None, *, policy_path: str | Path = POLICY_PATH) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    env_proxy = os.environ.get("JSE_PROXY_URL")
    proxy = args.proxy if args.proxy is not None else env_proxy
    source = "cli" if args.proxy is not None else ("env" if env_proxy is not None else "inherited")
    try:
        result = prepare(proxy, source, dict(os.environ), policy_path)
    except ProxyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.check:
        print(json.dumps(result["check"], ensure_ascii=True, separators=(",", ":")))
        return 0
    if not args.command:
        print("a command is required (use --check to inspect proxy settings)", file=sys.stderr)
        return 2
    try:
        proc = subprocess.Popen(args.command, env=result["env"], shell=False, start_new_session=os.name == "posix")
        status = int(proc.wait())
        return 128 - status if status < 0 else status
    except KeyboardInterrupt:
        if "proc" in locals():
            _terminate(proc)
        return 130
    except OSError:
        print("could not start command", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
