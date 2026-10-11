"""One-attempt Vertex transport; credentials never enter persisted artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = "groundingsearch"
LOCATION = "global"
MODEL = "gemini-3.5-flash-lite"
ENDPOINT = f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{LOCATION}/publishers/google/models/{MODEL}:generateContent"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def token() -> str:
    value = os.environ.get("GCP_ACCESS_TOKEN", "")
    if not value or value != value.strip() or any(c.isspace() for c in value):
        raise ValueError("GCP_ACCESS_TOKEN is missing or malformed; credential suppressed")
    return value


def save_new(path: Path, obj: Any, secret: str = "") -> None:
    raw = json.dumps(obj, ensure_ascii=False, indent=2)
    if secret:
        raw = raw.replace(secret, "[REDACTED]")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(raw + "\n")


def auth_check(secret: str, opener=urllib.request.urlopen) -> dict:
    """Probe the exact environment credential; suppress bodies, headers and URLs."""
    result = {"checkedUTC": utc(), "project": PROJECT, "location": LOCATION,
              "model": MODEL, "credential_source": "GCP_ACCESS_TOKEN", "probes": []}
    probes = [
        ("tokeninfo", "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode({"access_token": secret}), {}),
        ("vertex_project_access", f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations?pageSize=1",
         {"Authorization": "Bearer " + secret, "x-goog-user-project": PROJECT}),
    ]
    for name, url, headers in probes:
        item = {"name": name, "authenticated": False}
        try:
            with opener(urllib.request.Request(url, headers=headers), timeout=25) as response:
                item.update(http_status=response.status, authenticated=response.status == 200)
        except urllib.error.HTTPError as exc:
            item["http_status"] = exc.code
            # Only constant, non-sensitive error codes are retained.
            try:
                body = json.loads(exc.read())
                if name == "tokeninfo" and body.get("error") in {"invalid_token", "invalid_request"}:
                    item["error"] = body["error"]
                status = body.get("error", {}).get("status")
                if name == "vertex_project_access" and status in {"UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND", "RESOURCE_EXHAUSTED"}:
                    item["error_status"] = status
            except Exception:
                pass
        except Exception as exc:
            item["exception_type"] = type(exc).__name__
        result["probes"].append(item)
    result["authenticated"] = all(p["authenticated"] for p in result["probes"])
    return result


def provider_fields(body: Any) -> dict:
    """Save model output/usage, never transport headers or arbitrary error bodies."""
    if not isinstance(body, dict):
        return {"provider_body_type": type(body).__name__}
    output = {key: body[key] for key in ("modelVersion", "usageMetadata") if key in body}
    candidates = []
    for candidate in body.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        entry = {key: candidate[key] for key in ("index", "finishReason", "safetyRatings") if key in candidate}
        parts = candidate.get("content", {}).get("parts", [])
        entry["content"] = {"parts": [{"text": p["text"]} for p in parts
                                        if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought")]}
        candidates.append(entry)
    output["candidates"] = candidates
    error = body.get("error", {})
    if isinstance(error, dict):
        status = error.get("status")
        if status in {"UNAUTHENTICATED", "PERMISSION_DENIED", "INVALID_ARGUMENT", "RESOURCE_EXHAUSTED", "NOT_FOUND", "INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED"}:
            output["error_status"] = status
        if isinstance(error.get("code"), int):
            output["error_code"] = error["code"]
    return output


def model_json(response: dict) -> tuple[Any, str | None]:
    candidates = response.get("candidates", [])
    if len(candidates) != 1:
        return None, "candidate_coverage_error"
    if candidates[0].get("finishReason") != "STOP":
        return None, "non_STOP_finish"
    text = "".join(p["text"] for p in candidates[0].get("content", {}).get("parts", []))
    try:
        return json.loads(text), None
    except (ValueError, TypeError):
        return None, "invalid_model_json"


def call_once(run: Path, stage: str, index: int, body: dict, answer_path: Path,
              secret: str, opener=urllib.request.urlopen) -> dict:
    """Exclusive attempt ledger prevents retries, including after interrupted runs."""
    prefix = run / "provider" / stage / f"{index:03}"
    request_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    attempt = {"startedUTC": utc(), "stage": stage, "index": index,
               "request_body_sha256": hashlib.sha256(request_bytes).hexdigest(),
               "model_requested": MODEL, "project": PROJECT, "location": LOCATION,
               "answer": str(answer_path.relative_to(run)), "retry_policy": "never"}
    if answer_path.exists():
        raise FileExistsError("refusing overwrite existing answer")
    save_new(prefix.with_suffix(".attempt.json"), attempt)
    request = urllib.request.Request(ENDPOINT, data=request_bytes, method="POST",
                                     headers={"Authorization": "Bearer " + secret,
                                              "x-goog-user-project": PROJECT,
                                              "Content-Type": "application/json"})
    start = time.monotonic()
    result = {**attempt, "http_status": None}
    raw = {}
    try:
        with opener(request, timeout=120) as response:
            result["http_status"] = response.status
            raw = provider_fields(json.loads(response.read()))
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        try:
            raw = provider_fields(json.loads(exc.read()))
        except Exception as parse_exc:
            result["provider_parse_exception_type"] = type(parse_exc).__name__
    except Exception as exc:
        # Exception messages may include token-bearing URLs. Never serialize them.
        result["exception_type"] = type(exc).__name__
    result.update(raw)
    result["elapsed_seconds"] = round(time.monotonic() - start, 6)
    result["completedUTC"] = utc()
    save_new(prefix.with_suffix(".response.json"), result, secret)
    result["provider_response_sha256"] = hashlib.sha256(prefix.with_suffix(".response.json").read_bytes()).hexdigest()
    obj, error = model_json(raw) if result["http_status"] == 200 else (None, "transport_failure")
    result["parse_error"] = error
    if error is None:
        save_new(answer_path, obj, secret)
        result["answer_sha256"] = hashlib.sha256(answer_path.read_bytes()).hexdigest()
    save_new(prefix.with_suffix(".receipt.json"), result, secret)
    return result


def verify_attempt(run: Path, stage: str, index: int, body: dict, answer_path: Path) -> dict:
    """Bind reconstructed task, persisted provider response and parsed answer."""
    prefix = run / "provider" / stage / f"{index:03}"
    receipt = json.loads(prefix.with_suffix(".receipt.json").read_text())
    attempt = json.loads(prefix.with_suffix(".attempt.json").read_text())
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if receipt.get("request_body_sha256") != digest or attempt.get("request_body_sha256") != digest:
        raise ValueError("attempt request body changed")
    response_path = prefix.with_suffix(".response.json")
    if receipt.get("provider_response_sha256") != hashlib.sha256(response_path.read_bytes()).hexdigest():
        raise ValueError("provider response changed")
    if receipt.get("http_status") != 200:
        raise ValueError("previous transport failure; no retry allowed")
    response = json.loads(response_path.read_text())
    obj, error = model_json(response)
    if receipt.get("parse_error") != error:
        raise ValueError("receipt parse status changed")
    if error is None:
        if not answer_path.exists() or receipt.get("answer_sha256") != hashlib.sha256(answer_path.read_bytes()).hexdigest():
            raise ValueError("parsed answer missing or changed")
        canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if canonical(obj) != canonical(json.loads(answer_path.read_text())):
            raise ValueError("answer differs from provider model output")
    elif answer_path.exists():
        raise ValueError("unexpected parsed answer for failed model JSON")
    return receipt
