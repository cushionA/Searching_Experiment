import argparse
import builtins
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
import re
import sys
import time
from pathlib import Path

_original_open = builtins.open


def configure():
    """Use an ignored writable directory, without persisting authentication."""
    directory = os.environ.get("KAGGLE_CONFIG_DIR")
    if not directory:
        directory = str(Path(__file__).resolve().parents[4] / ".deps/kaggle-config")
        os.environ["KAGGLE_CONFIG_DIR"] = directory
    Path(directory).expanduser().mkdir(parents=True, exist_ok=True)
    return directory


def utf8_open(file, mode="r", *args, **kwargs):
    if "b" not in mode and len(args) < 2 and "encoding" not in kwargs:
        kwargs["encoding"] = "utf-8"
    return _original_open(file, mode, *args, **kwargs)


def redact(text):
    for key in ("KAGGLE_API_TOKEN", "KAGGLE_KEY"):
        value = os.environ.get(key)
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(redact(json.dumps(value, ensure_ascii=False, indent=2)), encoding="utf-8")
    temporary.replace(path)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def append_monitor_event(output, job, event, monitor_started, status=None, error_type=None):
    """Best-effort local audit trail; never changes wait's API or return behavior."""
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ref": job.get("ref"),
        "version": job.get("version"),
        "source_signature": job.get("source_signature"),
        "monitor_elapsed_seconds": round(max(0.0, time.monotonic() - monitor_started), 3),
        "event": event,
    }
    if status is not None:
        record["status"] = status
    if error_type is not None:
        record["error_type"] = error_type
    try:
        path = Path(output) / "monitor-events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(redact(json.dumps(record, ensure_ascii=False, separators=(",", ":"))) + "\n")
    except Exception:
        pass


def kernel_ref(value):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+", value):
        raise ValueError("ref must be owner/slug")
    return value


def validate_job(job):
    kernel_ref(job["ref"])
    if type(job.get("version")) is not int or job["version"] < 1:
        raise ValueError("version must be a positive integer")
    return job


def api():
    if not os.environ.get("KAGGLE_API_TOKEN"):
        raise ValueError("KAGGLE_API_TOKEN is not configured")
    configure()
    if sys.platform == "win32":
        builtins.open = utf8_open
    import requests
    original_send = requests.sessions.Session.send
    def bounded_send(session, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (10, 30)
        return original_send(session, request, **kwargs)
    requests.sessions.Session.send = bounded_send
    os.environ["KAGGLE_ENABLE_OAUTH"] = "false"
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
        import kaggle
    return kaggle.api


def read_retry(call):
    import requests
    for attempt in range(3):
        try:
            return call()
        except requests.RequestException as error:
            status = getattr(error.response, "status_code", None)
            if attempt == 2 or (status is not None and status != 429 and status < 500):
                raise
            retry_after = error.response.headers.get("Retry-After", "") if error.response is not None else ""
            if retry_after.isdecimal() and int(retry_after) > 60:
                raise
            time.sleep(max(2 ** attempt, int(retry_after) if retry_after.isdecimal() else 0))


def quota(client):
    def get():
        with client.build_kaggle_client() as sdk:
            response = sdk.kernels.kernels_api_client.get_accelerator_quota_statistics()
        result = {"refresh_at": response.quota_refresh_time.isoformat() if response.quota_refresh_time else None}
        for label in ("gpu", "tpu"):
            q = getattr(response, label + "_quota")
            if q is None:
                result[label] = None
                continue
            fields = {name: getattr(q, name).total_seconds() if getattr(q, name) is not None else None for name in ("time_used", "time_reserved", "total_time_allowed")}
            fields["remaining_seconds"] = max(0, fields["total_time_allowed"] - fields["time_used"] - fields["time_reserved"]) if all(v is not None for v in fields.values()) else None
            result[label] = fields
        return result
    return read_retry(get)


def status(client, ref, version=None):
    kernel_ref(ref)
    if version is not None:
        validate_job({"ref": ref, "version": version})
    def get():
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusRequest
        request = ApiGetKernelSessionStatusRequest()
        request.user_name, request.kernel_slug = ref.split("/")
        if version is not None:
            request.version_label = "v" + str(version)
        with client.build_kaggle_client() as sdk:
            response = sdk.kernels.kernels_api_client.get_kernel_session_status(request)
        return {"ref": ref, "version": version, "status": response.status.name.lower(), "failure_message": redact(response.failure_message or "")}
    return read_retry(get)


def get_kernel(client, ref):
    from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest
    request = ApiGetKernelRequest()
    request.user_name, request.kernel_slug = kernel_ref(ref).split("/")
    def get():
        with client.build_kaggle_client() as sdk:
            return sdk.kernels.kernels_api_client.get_kernel(request)
    return read_retry(get)


def owned_kernel_absent(client, ref):
    """A 403 alone cannot distinguish a new notebook from a permission failure."""
    owner = getattr(client, "config_values", {}).get("username")
    if ref.split("/")[0] != owner:
        return False
    for page in range(1, 101):
        kernels = read_retry(lambda: client.kernels_list(mine=True, page_size=100, page=page))
        if any(kernel.ref == ref for kernel in kernels):
            return False
        if not kernels:
            return True
    raise ValueError("own notebook listing exceeded the preflight limit")


def latest_version(client, ref):
    import requests
    try:
        return get_kernel(client, ref).metadata.current_version_number
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            return 0
        if error.response is not None and error.response.status_code == 403 and owned_kernel_absent(client, ref):
            return 0
        raise


def source_signature(source, kernel_type):
    if kernel_type == "notebook":
        notebook = json.loads(source)
        cells = [(cell["cell_type"], "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"])
                 for cell in notebook["cells"]]
        source = json.dumps(cells, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def verify_saved_version(client, job, version=None):
    """Reconcile only a newer saved version containing exactly our code."""
    if not job.get("source_signature"):
        raise ValueError("job lacks a source signature; inspect the remote source before reconciling")
    remote = get_kernel(client, job["ref"])
    current = remote.metadata.current_version_number
    if current <= job.get("previous_version", 0) or (version is not None and current != version):
        raise ValueError("saved notebook version does not match the new submission")
    if remote.blob.kernel_type != job["kernel_type"]:
        raise ValueError("saved notebook type does not match")
    if source_signature(remote.blob.source, job["kernel_type"]) != job["source_signature"]:
        raise ValueError("saved notebook source does not match the submitted code")
    if remote.metadata.is_private is not True or remote.metadata.enable_gpu != job["enable_gpu"]:
        raise ValueError("saved notebook privacy or GPU setting does not match")
    if remote.metadata.enable_internet != job["enable_internet"]:
        raise ValueError("saved notebook Internet setting does not match")
    return current


def clean_upload(folder):
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError("upload folder does not exist")
    forbidden = {".git", ".env", ".kaggle", "kaggle.json", "access_token"}
    secrets = [value.encode() for key in ("KAGGLE_API_TOKEN", "KAGGLE_KEY") if (value := os.environ.get(key))]
    overlap = max((len(value) for value in secrets), default=1) - 1
    for path in folder.rglob("*"):
        if path.is_symlink() or path.name.lower() in forbidden or path.name.lower().startswith(".env."):
            raise ValueError("upload folder contains credentials, git data or symlinks")
        if secrets and path.is_file():
            with path.open("rb") as handle:
                tail = b""
                while chunk := handle.read(1048576):
                    content = tail + chunk
                    if any(secret in content for secret in secrets):
                        raise ValueError("upload file contains a configured credential")
                    tail = content[-overlap:] if overlap else b""
    return folder


def submit(client, folder, output):
    folder = clean_upload(folder)
    metadata = load(folder / "kernel-metadata.json")
    ref = kernel_ref(metadata["id"])
    if metadata.get("is_private") is not True or metadata.get("enable_tpu") is not False:
        raise ValueError("private notebook with TPU disabled is required")
    code = (folder / metadata["code_file"]).resolve()
    if not code.is_relative_to(folder) or not code.is_file():
        raise ValueError("code_file must be inside the upload folder")
    params = load(folder / "training-params.json") if (folder / "training-params.json").exists() else {}
    seconds = params.get("timeout_seconds", 21600)
    if type(seconds) is not int or not 60 <= seconds <= 21600:
        raise ValueError("timeout_seconds must be 60..21600")
    budget = quota(client) if metadata.get("enable_gpu") else None
    if metadata.get("enable_gpu"):
        remaining = (budget.get("gpu") or {}).get("remaining_seconds")
        if remaining is None or remaining < seconds:
            raise ValueError("insufficient or unknown GPU quota")
    output = Path(output)
    job_path = output / "job.json"
    if job_path.exists():
        raise ValueError("job.json already exists; resume instead of submitting again")
    previous_version = latest_version(client, ref)
    if previous_version and status(client, ref, previous_version)["status"] not in ("complete", "error", "cancel_acknowledged"):
        raise ValueError("an existing kernel version is active; resume it instead of submitting")
    job = {"ref": ref, "state": "submission_unknown", "previous_version": previous_version, "timeout_seconds": seconds, "quota_before": budget, "code_sha256": hashlib.sha256(code.read_bytes()).hexdigest(), "created_at": time.time(),
           "kernel_type": metadata["kernel_type"], "source_signature": source_signature(code.read_text(encoding="utf-8"), metadata["kernel_type"]),
           "enable_gpu": bool(metadata.get("enable_gpu")), "enable_internet": metadata.get("enable_internet", True)}
    output.mkdir(parents=True, exist_ok=True)
    with job_path.open("x", encoding="utf-8") as handle:
        json.dump(job, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    response = client.kernels_push(str(folder), timeout=str(seconds))
    job["submission_response"] = {"ref": response.ref, "version": response.version_number,
                                  "error": redact(getattr(response, "error", ""))}
    save(job_path, job)
    returned_ref = kernel_ref(response.ref.removeprefix("/code/")) if response.ref else ref
    if returned_ref != ref:
        raise ValueError("submission response has an unexpected ref; inspect without resubmitting")
    version = response.version_number
    if getattr(response, "error", "") or not version:
        version = verify_saved_version(client, job)
        job["source_verified"] = True
    if version <= previous_version:
        raise ValueError("submission response lacks a newer version; inspect without resubmitting")
    job.update(version=version, state="submitted")
    save(job_path, job)
    return job


def reconcile(client, job_path, version):
    job = load(job_path)
    if job.get("state") != "submission_unknown" or version <= job.get("previous_version", 0):
        raise ValueError("reconcile requires an unknown submission and a newer verified version")
    verify_saved_version(client, job, version)
    current = status(client, job["ref"], version)
    job.update(version=version, state="submitted", source_verified=True, reconciliation_status=current["status"])
    save(job_path, job)
    return job


def log_slice(job, output, seconds=20):
    import requests
    validate_job(job)
    ref = kernel_ref(job["ref"])
    endpoint = f"https://api.kaggle.com/v1/kernels/logs/stream/{ref}"
    started = time.monotonic()
    text = bytearray()
    try:
        with requests.get(endpoint, headers={"Authorization": "Bearer " + os.environ["KAGGLE_API_TOKEN"], "Accept": "text/event-stream, */*"}, params={"versionLabel": "v" + str(job["version"])}, stream=True, timeout=(10, seconds)) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1024):
                text.extend(chunk[:1048576 - len(text)])
                if len(text) >= 1048576 or time.monotonic() - started >= seconds or b"END_OF_LOG" in text:
                    break
            result = {"content_type": response.headers.get("content-type"), "bytes": len(text), "truncated": len(text) >= 1048576}
    finally:
        if text:
            Path(output).mkdir(parents=True, exist_ok=True)
            (Path(output) / "session.log").write_text(redact(text.decode("utf-8", errors="replace")), encoding="utf-8")
    return result


def outputs(client, job, output, download=True):
    import requests
    from urllib.parse import urlsplit
    from kagglesdk.kernels.types.kernels_api_service import ApiListKernelSessionOutputRequest
    validate_job(job)
    request = ApiListKernelSessionOutputRequest()
    request.user_name, request.kernel_slug = job["ref"].split("/")
    request.version_label = "v" + str(job["version"])
    request.page_size = 100
    root = (Path(output) / "artifacts").resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = []
    tokens = set()
    total = 0
    for _ in range(100):
        def listing():
            with client.build_kaggle_client() as sdk:
                return sdk.kernels.kernels_api_client.list_kernel_session_output(request)
        response = read_retry(listing)
        if response.log:
            (Path(output) / "persisted.log").write_text(redact(response.log), encoding="utf-8")
        if not download:
            return []
        for item in response.files:
            target = (root / item.file_name.replace("\\", "/")).resolve()
            if not target.is_relative_to(root) or target == root:
                raise ValueError("output filename escapes artifact directory")
            url = urlsplit(item.url)
            if url.scheme != "https" or url.username or url.password:
                raise ValueError("output URL must be HTTPS without credentials")
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            checksum = hashlib.sha256()
            size = 0
            with requests.get(item.url, stream=True, timeout=(10, 60)) as content:
                content.raise_for_status()
                with partial.open("wb") as handle:
                    for chunk in content.iter_content(chunk_size=1048576):
                        total += len(chunk)
                        if total > 1000000000:
                            raise ValueError("output exceeds the 1GB recovery budget")
                        handle.write(chunk)
                        checksum.update(chunk)
                        size += len(chunk)
            partial.replace(target)
            manifest.append({"path": str(target.relative_to(root)), "bytes": size, "sha256": checksum.hexdigest()})
        if not response.next_page_token:
            return manifest
        if response.next_page_token in tokens:
            raise ValueError("output pagination did not advance")
        tokens.add(response.next_page_token)
        request.page_token = response.next_page_token
    raise ValueError("output pagination limit reached")


def wait(client, job_path, output, wait_seconds=21600, interval=30):
    if not 1 <= wait_seconds <= 21600:
        raise ValueError("wait_seconds must be 1..21600")
    job = validate_job(load(job_path))
    if job.get("state") not in ("submitted", "waiting", "complete") or not job.get("version"):
        raise ValueError("job submission needs reconciliation before waiting")
    result = {"ref": job["ref"], "version": job["version"], "ready_for_verification": False, "next_phase": "waiting_kaggle"}
    monitor_started = time.monotonic()
    deadline = monitor_started + wait_seconds
    append_monitor_event(output, job, "wait_started", monitor_started)
    while True:
        try:
            current = status(client, job["ref"], job["version"])
        except Exception as error:
            append_monitor_event(output, job, "status_retrieval_error", monitor_started, error_type=type(error).__name__)
            raise
        result.update(current)
        append_monitor_event(output, job, "status_observed", monitor_started, status=current.get("status"))
        try:
            result["log"] = read_retry(lambda: log_slice(job, output, seconds=min(20, max(1, int(deadline - time.monotonic())))))
        except Exception as error:
            result["log_error"] = type(error).__name__
            append_monitor_event(output, job, "log_retrieval_error", monitor_started, status=current.get("status"), error_type=type(error).__name__)
        if current["status"] == "complete":
            append_monitor_event(output, job, "terminal", monitor_started, status=current["status"])
            result["next_phase"] = "recovering_outputs"
            save(Path(output) / "continuation.json", result)
            try:
                result["files"] = outputs(client, job, Path(output))
            except Exception as error:
                result["output_error"] = type(error).__name__
                append_monitor_event(output, job, "output_recovery_error", monitor_started, status=current["status"], error_type=type(error).__name__)
                save(Path(output) / "continuation.json", result)
                raise
            result["ready_for_verification"] = True
            result["next_phase"] = "verification"
            save(Path(output) / "continuation.json", result)
            append_monitor_event(output, job, "verification_ready", monitor_started, status=current["status"])
            return result
        if current["status"] in ("error", "cancel_acknowledged", "cancel_requested"):
            append_monitor_event(output, job, "terminal", monitor_started, status=current["status"])
            result["next_phase"] = "failure_review"
            try:
                outputs(client, job, Path(output), download=False)
            except Exception as error:
                result["output_error"] = type(error).__name__
                append_monitor_event(output, job, "output_recovery_error", monitor_started, status=current["status"], error_type=type(error).__name__)
            save(Path(output) / "continuation.json", result)
            return result
        if time.monotonic() >= deadline:
            result["status"] = "waiting_timeout"
            append_monitor_event(output, job, "wait_deadline", monitor_started, status=current["status"])
            save(Path(output) / "continuation.json", result)
            return result
        save(Path(output) / "continuation.json", result)
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "quota", "kernel-list", "kernel-status", "kernel-output", "submit", "reconcile", "wait", "dataset-create", "dataset-update", "dataset-download"):
        sub = commands.add_parser(name)
        if name in ("kernel-status", "kernel-output", "dataset-download"):
            sub.add_argument("--ref", required=True, type=kernel_ref)
        if name in ("kernel-output", "submit", "wait", "dataset-download"):
            sub.add_argument("--output", required=True, type=Path)
        if name in ("submit", "dataset-create", "dataset-update"):
            sub.add_argument("--folder", required=True, type=Path)
        if name == "dataset-update":
            sub.add_argument("--message", required=True)
        if name in ("wait", "reconcile"):
            sub.add_argument("--job", required=True, type=Path)
        if name in ("kernel-status", "reconcile"):
            sub.add_argument("--version", required=name == "reconcile", type=int)
        if name == "wait":
            sub.add_argument("--wait-seconds", type=int, default=21600)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=False, interpolate=False)
    try:
        configure()
        if args.command == "doctor":
            result = {"kaggle_version": importlib.metadata.version("kaggle"), "token_present": bool(os.environ.get("KAGGLE_API_TOKEN")), "username_present": bool(os.environ.get("KAGGLE_USERNAME"))}
        else:
            client = api()
            if args.command == "quota":
                result = quota(client)
            elif args.command == "kernel-list":
                result = {"kernels": [{"ref": k.ref, "title": k.title} for k in read_retry(lambda: client.kernels_list(mine=True, page_size=20))], "note": "一覧は稼働中に限定されない"}
            elif args.command == "kernel-status":
                result = status(client, args.ref, args.version)
            elif args.command == "kernel-output":
                job = {"ref": args.ref, "version": latest_version(client, args.ref)}
                result = dict(job, files=outputs(client, job, args.output))
            elif args.command == "submit":
                result = submit(client, args.folder, args.output)
            elif args.command == "wait":
                result = wait(client, args.job, args.output, args.wait_seconds)
            elif args.command == "reconcile":
                result = reconcile(client, args.job, args.version)
            elif args.command == "dataset-create":
                client.dataset_create_new(str(clean_upload(args.folder)), public=False, quiet=True, convert_to_csv=False, dir_mode="zip")
                result = {"created": True, "public": False}
            elif args.command == "dataset-update":
                client.dataset_create_version(str(clean_upload(args.folder)), args.message, quiet=True, convert_to_csv=False, delete_old_versions=False, dir_mode="zip")
                result = {"version_created": True}
            else:
                read_retry(lambda: client.dataset_download_files(args.ref, path=str(args.output), quiet=True, unzip=False))
                result = {"downloaded": True, "extracted": False}
        print(redact(json.dumps(result, ensure_ascii=False)))
        return 0 if result.get("ready_for_verification", True) else 3
    except (Exception, SystemExit) as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(redact(json.dumps({"error": message, "http_status": getattr(getattr(error, "response", None), "status_code", None), "errno": getattr(error, "errno", None)}, ensure_ascii=False)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
