import hashlib
import json
import math
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import MODEL, VERSION


class LabError(Exception):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def canonical(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise LabError("URLは空白を含まない2048文字以内の文字列が必要です")
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError()
        if parts.port not in (None, 443) or "\\" in url:
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if ":" in host:
            raise ValueError()
        return urlunsplit(("https", host, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        raise LabError("URLは認証情報のないHTTPS・標準ポートに限定します") from None


def origin(url):
    parts = urlsplit(canonical(url))
    return f"https://{parts.netloc}"


LIMITS = {
    "pages_per_arm": (1, 200),
    "attempts_per_arm": (1, 300),
    "requests_per_arm": (2, 1000),
    "bytes_per_arm": (1024, 50000000),
    "bytes_per_response": (512, 2000000),
    "max_depth": (0, 10),
    "agent_requests": (3, 100),
    "frontier_size": (10, 10000),
    "candidates_per_request": (1, 100),
}


def validate_config(config):
    expected = {"objective", "seeds", "allowed_origins", "transport", "user_agent", "delay_seconds", "timeout_seconds", "limits"}
    if not isinstance(config, dict) or set(config) != expected:
        raise LabError("設定のキーが実験設定の形式と一致しません")
    if not isinstance(config["objective"], str) or not 1 <= len(config["objective"]) <= 4000:
        raise LabError("objectiveは1〜4000文字にしてください")
    if config["transport"] not in ("fixture", "live"):
        raise LabError("transportはfixtureまたはliveです")
    for key in ("seeds", "allowed_origins"):
        if not isinstance(config[key], list) or not 1 <= len(config[key]) <= 10:
            raise LabError(f"{key}は1〜10件必要です")
    config = json.loads(json.dumps(config))
    config["seeds"] = list(dict.fromkeys(canonical(url) for url in config["seeds"]))
    for value in config["allowed_origins"]:
        if canonical(value) != origin(value) + "/":
            raise LabError("allowed_originsにはパスやクエリを含められません")
    config["allowed_origins"] = list(dict.fromkeys(origin(url) for url in config["allowed_origins"]))
    if any(origin(url) not in config["allowed_origins"] for url in config["seeds"]):
        raise LabError("seedが承認対象のorigin外です")
    if config["transport"] == "fixture" and config["allowed_origins"] != ["https://lab.example"]:
        raise LabError("fixtureはlab.exampleだけを扱います")
    ua = config["user_agent"]
    if not isinstance(ua, str) or not ua.startswith("DiscoveryLab/") or not 15 <= len(ua) <= 200 or not ua.isascii() or any(ord(c) < 32 for c in ua):
        raise LabError("user_agentはDiscoveryLab/で始まるASCIIの識別文字列が必要です")
    for key, low, high in (("delay_seconds", 0.5, 60), ("timeout_seconds", 1, 30)):
        val = config[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not low <= val <= high:
            raise LabError(f"{key}は{low}〜{high}です")
    if not isinstance(config["limits"], dict) or set(config["limits"]) != set(LIMITS):
        raise LabError("limitsのキーが一致しません")
    for key, (low, high) in LIMITS.items():
        val = config["limits"][key]
        if type(val) is not int or not low <= val <= high:
            raise LabError(f"{key}は整数{low}〜{high}です")
    if config["limits"]["bytes_per_response"] > config["limits"]["bytes_per_arm"]:
        raise LabError("応答サイズ上限がarm全体の転送上限を超えています")
    return config


@contextmanager
def locked(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "run.lock").open("a+b")
    if os.fstat(handle.fileno()).st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise LabError("同じ実験を別のプロセスが操作中です") from None
    try:
        yield
    finally:
        handle.close()


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "state.json"
        self.state = json.loads(self.path.read_text(encoding="utf-8"))
        if self.state["version"] != VERSION:
            raise LabError("未対応のstateバージョンです")
        config = validate_config(self.state["config"])
        if digest(config) != self.state["config_hash"]:
            raise LabError("初期化後に設定が変更されています。別の実験として初期化してください")

    @classmethod
    def create(cls, directory, config):
        directory = Path(directory)
        if (directory / "state.json").exists():
            raise LabError("既存の実験を上書きできません")
        config = validate_config(config)
        arms = {}
        for name in ("bfs", "luna"):
            arms[name] = {"frontier": [{"url": url, "parent": None, "anchor": "seed", "depth": 0} for url in config["seeds"]], "seen": list(config["seeds"]), "pages": [], "attempts": [], "http": [], "bytes_charged": 0, "robots": {}, "pending_urls": [], "done": False, "stop_reason": None, "dropped_links": 0}
        value = {"version": VERSION, "model_requested": MODEL, "model_runtime_verified": False, "created_at": time.time(), "config": config, "config_hash": digest(config), "status": "ready", "phase": "plan", "agent_requests": [], "active_request": None, "gate": None, "decisions": [], "arms": arms, "claims": [], "inflight": None, "last_request_at": {}, "events": []}
        atomic(directory / "state.json", encoded(value))
        return cls(directory)

    def save(self):
        atomic(self.path, encoded(self.state))

    def artifact(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"(?:state\.json|decision\.json|DECISION\.md|report\.json|requests/\d{3}\.json|answers/\d{3}\.json|blobs/[0-9a-f]{64})", name):
            raise LabError("不正な成果物パスです")
        path = self.directory / name
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise LabError("成果物がrunディレクトリの外を指しています")
        return path

    def event(self, kind, detail):
        self.state["events"].append({"time": time.time(), "kind": kind, "detail": detail})
        self.save()

    def write(self, name, value):
        atomic(self.artifact(name), encoded(value))

    def status(self):
        s = self.state
        return {"status": s["status"], "phase": s["phase"], "model_requested": MODEL, "model_runtime_verified": False, "transport": s["config"]["transport"], "gate": s["gate"], "active_request": s["active_request"], "agent_requests": len(s["agent_requests"]), "arms": {name: {"pages": len(arm["pages"]), "attempts": len(arm["attempts"]), "http_requests": len(arm["http"]), "bytes_charged": arm["bytes_charged"], "stop_reason": arm["stop_reason"]} for name, arm in s["arms"].items()}}
