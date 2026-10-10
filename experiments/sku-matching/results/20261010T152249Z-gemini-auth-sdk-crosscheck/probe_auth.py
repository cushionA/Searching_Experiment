"""Credential-safe reproduction of the two read-only authentication probes.

No model inference, retries, token values, response headers, or bodies are output.
Inherit proxy settings and CA trust. Run with the repository's isolated venv.
"""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

import google.auth.transport.requests
import requests


def main() -> None:
    script = Path(__file__).resolve().parents[2] / "trial_gemini_sku_smoke_v2.py"
    spec = importlib.util.spec_from_file_location("trial", script)
    assert spec and spec.loader
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    token = os.environ.get("GCP_ACCESS_TOKEN", "").strip()
    if not token or any(c.isspace() for c in token):
        print(json.dumps({"authenticated": False, "reason": "missing_or_malformed_token"}))
        return
    probes = []
    started = monotonic()
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "probe": "requests_tokeninfo_same_env", "proxy_preserved": True,
              "TLS_verification_enabled": True}
    try:
        response = requests.get("https://oauth2.googleapis.com/tokeninfo",
                                params={"access_token": token}, timeout=30)
        result["http_status"] = response.status_code
        data = response.json()
        result["authenticated"] = response.status_code == 200 and int(data.get("expires_in", 0)) > 0
        if data.get("error") in {"invalid_token", "invalid_request"}:
            result["reason"] = data["error"]
    except Exception as exc:
        result.update(authenticated=False, error_type=type(exc).__name__)
    result["elapsed_seconds"] = round(monotonic() - started, 3)
    probes.append(result)
    started = monotonic()
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "probe": "trial_vertex_credentials_AuthorizedSession_read_only",
              "project": "groundingsearch", "quota_project": "groundingsearch",
              "proxy_preserved": True, "TLS_verification_enabled": True,
              "model_inference_calls": 0}
    try:
        credentials, source = trial.vertex_credentials("GCP_SERVICE_ACCOUNT_JSON", "GCP_ACCESS_TOKEN")
        if source != "oauth_access_token_secret_environment":
            raise RuntimeError("unexpected_credential_source")
        credentials = credentials.with_quota_project("groundingsearch")
        session = google.auth.transport.requests.AuthorizedSession(credentials, max_refresh_attempts=0)
        response = session.get("https://aiplatform.googleapis.com/v1/projects/groundingsearch/locations",
                               params={"pageSize": 1}, timeout=30)
        result.update(http_status=response.status_code, authenticated=response.status_code == 200)
        data = response.json()
        reason = data.get("error", {}).get("status")
        if reason in {"UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND", "INVALID_ARGUMENT"}:
            result["reason"] = reason
        allowed = {"ACCESS_TOKEN_EXPIRED", "ACCESS_TOKEN_INVALID", "CREDENTIALS_MISSING",
                   "SERVICE_DISABLED", "IAM_PERMISSION_DENIED", "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                   "CONSUMER_INVALID", "USER_PROJECT_DENIED"}
        result["allowed_error_reasons"] = [x["reason"] for x in data.get("error", {}).get("details", [])
                                           if isinstance(x, dict) and x.get("reason") in allowed]
    except Exception as exc:
        result.update(authenticated=False, error_type=type(exc).__name__)
    result["elapsed_seconds"] = round(monotonic() - started, 3)
    probes.append(result)
    print(json.dumps({"probes": probes, "response_headers_recorded": False,
                      "response_body_recorded": False, "credential_values_recorded": False},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
