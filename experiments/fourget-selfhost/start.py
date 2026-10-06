"""Start the local 4get container using this cloud session's proxy and CA."""

from pathlib import Path
import json
import hashlib
import os
import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
IMAGE = "luuul/4get@sha256:fb762cd27c29c0e2bba89848fa4b858d719d40396dea25baa66b95a75cb8b3fc"
DOCKER_ENV = os.environ.copy()
DOCKER_ENV["BUILDX_CONFIG"] = str(ROOT / ".runtime" / "buildx")
for selector in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
    DOCKER_ENV.pop(selector, None)
DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]
EXTENSION_ID = "{d58b1f6b-0386-4b99-9208-a1635ad8ae41}"
EXTENSION_SHA256 = "70937b52eada128b3966d0628d4aad7369abb9ed4357c03ab4576235de410fcb"


def docker(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        [*DOCKER, *args], cwd=ROOT, env=DOCKER_ENV, check=True,
        text=True, stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout or ""


def main() -> None:
    docker("info", "--format", "{{.ServerVersion}}")
    proxy = urllib.parse.urlparse(os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "")
    if proxy.scheme not in ("http", "https") or not proxy.hostname:
        raise SystemExit("This cloud setup needs the inherited HTTP_PROXY or HTTPS_PROXY.")
    proxy_ip = socket.gethostbyname(proxy.hostname)
    runtime = ROOT / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    (runtime / "proxies").mkdir(exist_ok=True)
    username = urllib.parse.unquote(proxy.username or "")
    password = urllib.parse.unquote(proxy.password or "")
    (runtime / "proxies" / "cloud.txt").write_text(
        f"{proxy.scheme}:{proxy_ip}:{proxy.port or (443 if proxy.scheme == 'https' else 80)}:{username}:{password}\n"
    )
    # The container's Apache user differs from the host's task user. The host
    # runtime directory stays private, while these two bind mounts are readable.
    (runtime / "proxies" / "cloud.txt").chmod(0o644)
    shutil.copyfile("/etc/ssl/certs/ca-certificates.crt", runtime / "ca-bundle.pem")
    (runtime / "ca-bundle.pem").chmod(0o644)
    proxy_ca = os.environ.get("CODEX_PROXY_CERT")
    if not proxy_ca or not Path(proxy_ca).is_file():
        raise SystemExit("The session CODEX_PROXY_CERT file is required for Firefox trust.")
    shutil.copyfile(proxy_ca, runtime / "proxy-ca.pem")
    (runtime / "proxy-ca.pem").chmod(0o644)
    password_file = runtime / "fourplay-password.txt"
    if not password_file.exists():
        password_file.write_text(secrets.token_hex(24) + "\n")
    password_file.chmod(0o600)
    renderer_password = password_file.read_text().strip()
    no_proxy = ",".join(filter(None, [os.environ.get("NO_PROXY") or os.environ.get("no_proxy"), "fourplay,localhost,127.0.0.1"]))
    (runtime / "renderer.env").write_text(
        "FOURGET_FPLAY_EXTERNAL_ENDPOINT=http://fourplay:3000/\n"
        f"FOURGET_FPLAY_PASSWORD={renderer_password}\n"
        f"NO_PROXY={no_proxy}\nno_proxy={no_proxy}\n"
    )
    (runtime / "fourplay.env").write_text(
        f"FOURPLAY_CLOUD_PROXY_HOST={proxy.hostname}\nFOURPLAY_CLOUD_PROXY_IP={proxy_ip}\n"
    )
    extension = runtime / "fourplay-1.10.xpi"
    if not extension.exists():
        with urllib.request.urlopen("https://addons.mozilla.org/firefox/downloads/file/4975759/4play-1.10.xpi", timeout=30) as response:
            extension.write_bytes(response.read())
    if hashlib.sha256(extension.read_bytes()).hexdigest() != EXTENSION_SHA256:
        raise SystemExit("4play extension checksum mismatch.")
    extension.chmod(0o644)
    profile = runtime / "profile-seed"
    storage = profile / "browser-extension-data" / EXTENSION_ID
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "storage.js").write_text(json.dumps({
        "ws_url": f"ws://127.0.0.1:3030/{renderer_password}", "ws_timeout": 30000,
    }))
    preferences = {
        "extensions.webextensions.ExtensionStorageIDB.enabled": False,
        "xpinstall.signatures.required": False,
        "browser.shell.checkDefaultBrowser": False,
        "browser.startup.homepage_override.mstone": "ignore",
        "browser.startup.page": 0,
        "browser.startup.homepage": "about:blank",
        "privacy.userContext.enabled": True,
        "privacy.userContext.ui.enabled": True,
        "places.history.enabled": False,
        "network.proxy.type": 1,
        "network.proxy.http": proxy_ip,
        "network.proxy.http_port": proxy.port or (443 if proxy.scheme == "https" else 80),
        "network.proxy.ssl": proxy_ip,
        "network.proxy.ssl_port": proxy.port or (443 if proxy.scheme == "https" else 80),
        "network.proxy.no_proxies_on": "localhost,127.0.0.1,fourplay,fourget",
        "network.proxy.failover_direct": False,
    }
    (profile / "user.js").write_text("".join(
        f"user_pref({json.dumps(key)}, {json.dumps(value)});\n" for key, value in preferences.items()
    ))
    # Read the proxy constant names from the exact image being started.
    constants = json.loads(docker(
        "run", "--rm", "--entrypoint", "php", IMAGE, "-r",
        'require "/var/www/html/4get/data/config.php"; '
        'echo json_encode(array_keys((new ReflectionClass("config"))->getConstants()));',
        capture=True,
    ))
    (runtime / "scrapers.env").write_text("".join(
        f"FOURGET_{name}=cloud\n" for name in constants if name.startswith("PROXY_")
    ))
    docker("compose", "build", "fourplay")
    docker("compose", "up", "-d", "--force-recreate")
    # Loopback traffic stays local; scraper traffic uses the configured sidecar.
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with local.open("http://127.0.0.1:8084/", timeout=2) as response:
                if response.status == 200:
                    print("4get ready: http://127.0.0.1:8084/")
                    break
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    else:
        docker("compose", "logs", "--tail", "50")
        raise SystemExit("4get did not become ready within 30 seconds.")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with local.open("http://127.0.0.1:3004/health", timeout=2) as response:
                health = json.load(response)
            if health.get("browser_connected"):
                print("4play Firefox ready: http://127.0.0.1:3004/health")
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    docker("compose", "logs", "--tail", "30", "fourplay")
    raise SystemExit("4play server started but Firefox did not connect within 60 seconds.")


if __name__ == "__main__":
    main()
