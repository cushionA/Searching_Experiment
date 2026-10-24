import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
TOOLS = ("camoufox", "camoufox-fourplay", "4play")
DENIAL_MARKERS = (
    "access denied",
    "you don't have permission",
    "forbidden",
    "errors.edgesuite.net",
)


def read_json(path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def records_from_ledger(ledger):
    if isinstance(ledger, list):
        return ledger
    if isinstance(ledger, dict):
        records = ledger.get("records")
        return records if isinstance(records, list) else []
    return []


def normalize_path(url):
    if not url:
        return None
    return urlparse(url).path.rstrip("/") or "/"


def body_review(run_dir, result):
    digest = result.get("dom_sha256")
    if not digest or not re.fullmatch(r"[a-fA-F0-9]{64}", str(digest)):
        return {"available": False, "normal_content_candidate": None}
    blob = run_dir / "blobs" / digest
    if not blob.is_file():
        return {"available": False, "normal_content_candidate": None}
    body = blob.read_text(encoding="utf-8", errors="replace")
    visible = result.get("visible_text_prefix") or ""
    scan = f"{result.get('title') or ''} {visible} {body}".lower()
    denial = any(marker in scan for marker in DENIAL_MARKERS)
    text = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>|<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    good_status = isinstance(result.get("http_status"), int) and 200 <= result["http_status"] < 300
    candidate = bool(
        good_status
        and result.get("outcome") == "content_observed"
        and result.get("title")
        and len(text) >= 80
        and not denial
    )
    return {
        "available": True,
        "title": result.get("title"),
        "outcome": result.get("outcome"),
        "denial_marker_present": denial,
        "dom_text_characters": len(text),
        "normal_content_candidate": candidate,
        "review_basis": "HTTP outcome/title and referenced DOM text; heuristic, not semantic validation",
    }


def matching_result(results, target_path):
    wanted = normalize_path(target_path)
    for result in results:
        if normalize_path(result.get("url")) == wanted or normalize_path(result.get("final_url")) == wanted:
            return result
    return None


def matching_results(results, target_path, role=None):
    wanted = normalize_path(target_path)
    return [
        result for result in results
        if (role is None or result.get("role") == role)
        and (normalize_path(result.get("url")) == wanted or normalize_path(result.get("final_url")) == wanted)
    ]


def document_records(ledger_records, roles=None, target_path=None):
    wanted = normalize_path(target_path) if target_path else None
    rows = []
    for row in ledger_records:
        if row.get("resource_type") not in ("document", "main_frame"):
            continue
        if roles is not None and row.get("role") not in roles:
            continue
        if wanted and normalize_path(row.get("url")) != wanted:
            continue
        rows.append({
            "index": row.get("index"),
            "url": row.get("url"),
            "status": row.get("status"),
            "response_status": row.get("response_status"),
            "request_method": row.get("request_method"),
            "role": row.get("role"),
            "error": row.get("error"),
        })
    return rows


def summarize_runtime(results):
    setup = next((row for row in results if row.get("role") == "adapter_setup"), {})
    runtime = setup.get("runtime") or {}
    fields = (
        "engine", "version", "camoufox_js", "playwright_core", "fourplay", "fourplay_upstream_commit",
        "browser_engine", "browser_control", "launch_backend", "executable", "executable_path",
        "config_sha256", "font_count", "font_list_sha256", "playwright_control", "headless", "display",
        "observation_limits",
    )
    return {key: runtime[key] for key in fields if key in runtime}


def summarize_tool(tool, site, sensor_url, matrix_child):
    run_dir = ROOT / "run" / tool
    results = read_json(run_dir / "results.json")
    ledger = read_json(run_dir / "ledger.json")
    pipeline = read_json(run_dir / "pipeline-results.json")
    verification = read_json(run_dir / "verification.json")
    invocation = read_json(run_dir / "invocation.json")
    results = results if isinstance(results, list) else []
    ledger_records = records_from_ledger(ledger)

    home_result = next((row for row in results if row.get("role") == "homepage"), None)
    if home_result is None:
        home_url = (site.get("links") or {}).get("home")
        home_result = matching_result(results, home_url)
    home_records = document_records(ledger_records, roles={"homepage"})

    sensor_events = []
    for index, row in enumerate(ledger_records, start=1):
        if row.get("url") == sensor_url:
            sensor_events.append({
                "ledger_index": index,
                "request_method": row.get("request_method"),
                "status": row.get("status"),
                "role": row.get("role"),
                "started_at": row.get("started_at"),
                "route": row.get("route"),
            })

    revisit_rows = [
        row for row in results
        if row.get("role") in ("same_session_revisit", "session_initialization_revisit")
    ]

    targets = {}
    for url in (site.get("links") or {}).get("targets", []):
        path = normalize_path(url)
        label = Path(path).name or path
        matching = matching_results(results, url, role="target")
        if not matching:
            matching = matching_results(results, url)
        row = matching[-1] if matching else None
        targets[label] = {
            "url": url,
            "http_status": row.get("http_status") if row else None,
            "final_url": row.get("final_url") if row else None,
            "outcome": row.get("outcome") if row else None,
            "dom": body_review(run_dir, row) if row else {"available": False, "normal_content_candidate": None},
            "ledger_document_events": document_records(ledger_records, roles={"target"}, target_path=url),
        }

    if isinstance(pipeline, list):
        pipeline_states = [row.get("state") for row in pipeline if isinstance(row, dict)]
    elif isinstance(pipeline, dict):
        pipeline_states = [pipeline.get("state")] if pipeline.get("state") else []
    else:
        pipeline_states = []

    result_errors = []
    for row in results:
        if row.get("error") or row.get("outcome") == "execution_error":
            result_errors.append({
                "role": row.get("role"),
                "outcome": row.get("outcome"),
                "error": row.get("error"),
            })
    pipeline_errors = []
    pipeline_rows = pipeline if isinstance(pipeline, list) else [pipeline] if isinstance(pipeline, dict) else []
    for row in pipeline_rows:
        state = row.get("state")
        if state and state not in ("navigation_completed", "complete", "completed"):
            pipeline_errors.append({"state": state, "error": row.get("error")})
    fixture_verification = read_json(ROOT / "fixture" / tool / "verification.json")

    return {
        "tool": tool,
        "run_directory": str(run_dir),
        "artifacts_present": {
            "results_json": (run_dir / "results.json").is_file(),
            "ledger_json": (run_dir / "ledger.json").is_file(),
            "pipeline_results_json": (run_dir / "pipeline-results.json").is_file(),
            "verification_json": (run_dir / "verification.json").is_file(),
        },
        "home_document": {
            "url": home_result.get("url") if home_result else None,
            "http_status": home_result.get("http_status") if home_result else None,
            "final_url": home_result.get("final_url") if home_result else None,
            "outcome": home_result.get("outcome") if home_result else None,
            "dom": body_review(run_dir, home_result) if home_result else {"available": False, "normal_content_candidate": None},
            "ledger_document_events": home_records,
        },
        "sensor_events": sensor_events,
        "sensor_has_get_200": any(e.get("request_method") == "GET" and e.get("status") == 200 for e in sensor_events),
        "sensor_has_post_201": any(e.get("request_method") == "POST" and e.get("status") == 201 for e in sensor_events),
        "same_session_revisits": [
            {"role": row.get("role"), "url": row.get("url"), "http_status": row.get("http_status"),
             "final_url": row.get("final_url"), "outcome": row.get("outcome"), "title": row.get("title"),
             "error": row.get("error"), "dom": body_review(run_dir, row),
             "ledger_document_events": document_records(ledger_records, roles={row.get("role")}, target_path=row.get("final_url") or row.get("url"))}
            for row in revisit_rows
        ],
        "target_pages": targets,
        "pipeline_states": pipeline_states,
        "execution_errors": {"result_errors": result_errors, "pipeline_errors": pipeline_errors},
        "setup_runtime": summarize_runtime(results),
        "matrix_child": matrix_child,
        "fixture_verification": None if fixture_verification is None else {
            "ok": fixture_verification.get("ok"),
            "errors": fixture_verification.get("errors"),
            "records": fixture_verification.get("records"),
            "results": fixture_verification.get("results"),
        },
        "verification": None if verification is None else {
            "ok": verification.get("ok"),
            "errors": verification.get("errors"),
            "records": verification.get("records"),
            "results": verification.get("results"),
        },
        "launch_template_blob_sha256": invocation.get("launch_template_sha256") if isinstance(invocation, dict) else None,
    }


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def summarize_overlap(progress):
    children = progress.get("children", []) if isinstance(progress, dict) else []
    windows = {}
    for item in children:
        start = parse_time(item.get("started_at"))
        end = parse_time(item.get("finished_at"))
        if start and end:
            windows[item.get("tool")] = (start, end)
    pairs = {}
    names = list(windows)
    for left_index, left in enumerate(names):
        for right in names[left_index + 1:]:
            start = max(windows[left][0], windows[right][0])
            end = min(windows[left][1], windows[right][1])
            pairs[f"{left}__{right}"] = {
                "overlap": start < end,
                "overlap_seconds": max(0.0, (end - start).total_seconds()),
            }
    all_three = None
    if all(tool in windows for tool in TOOLS):
        start = max(windows[tool][0] for tool in TOOLS)
        end = min(windows[tool][1] for tool in TOOLS)
        all_three = {"overlap": start < end, "overlap_seconds": max(0.0, (end - start).total_seconds())}
    return {
        "mode": progress.get("mode") if isinstance(progress, dict) else None,
        "started_at": progress.get("started_at") if isinstance(progress, dict) else None,
        "finished_at": progress.get("finished_at") if isinstance(progress, dict) else None,
        "children": children,
        "pairwise": pairs,
        "all_three": all_three,
    }


def render_report(comparison):
    lines = ["# Joshin 3方式比較", "", "結果は保存済みrunのDOM、台帳、pipeline、verificationから集計しました。", ""]
    lines += ["| 方式 | 初回home result | ledger document status | 最終URL | home DOM候補 | Sensor GET 200 | Sensor POST 201 | 再訪result status | 再訪ledger document | pipeline | verify |", "|---|---:|---|---|---:|---:|---:|---:|---|---|---|"]
    for tool in TOOLS:
        item = comparison["tools"][tool]
        home = item["home_document"]
        home_ledger = ", ".join(f"{row.get('status')}@{row.get('url')}" for row in home["ledger_document_events"]) or "未観測"
        home_dom = home["dom"]
        home_dom_label = home_dom.get("normal_content_candidate") if home_dom.get("available") else "未観測"
        revisit = ", ".join(str(row.get("http_status")) if row.get("http_status") is not None else f"error:{(row.get('error') or {}).get('message', row.get('outcome'))}" for row in item["same_session_revisits"]) or "未観測"
        revisit_ledger = "; ".join(
            f"{event.get('status')}@{event.get('url')}" for row in item["same_session_revisits"] for event in row["ledger_document_events"]
        ) or "未観測"
        pipeline = ", ".join(str(value) for value in item["pipeline_states"]) or "未観測"
        verify = item["verification"]
        verify_label = "未観測" if verify is None else str(verify.get("ok"))
        lines.append(f"| {tool} | {home['http_status'] if home['http_status'] is not None else '未観測'} | {home_ledger} | {home['final_url'] or '未観測'} | {home_dom_label} | {item['sensor_has_get_200']} | {item['sensor_has_post_201']} | {revisit} | {revisit_ledger} | {pipeline} | {verify_label} |")
    lines += ["", "## 対象ページ", "", "各セルのDOM確認はHTTP状態、結果のoutcome/title、およびresults.jsonが指すDOM blobを使った簡易判定です。", ""]
    lines += ["| 方式 | URL末尾 | result status | ledger document status | title/outcome | 通常本文候補 |", "|---|---|---:|---|---|---:|"]
    for tool in TOOLS:
        for label, target in comparison["tools"][tool]["target_pages"].items():
            dom = target["dom"]
            doc_status = ", ".join(str(event.get("status")) for event in target["ledger_document_events"]) or "未観測"
            lines.append(f"| {tool} | {label} | {target['http_status'] if target['http_status'] is not None else '未観測'} | {doc_status} | {(dom.get('title') or target.get('outcome') or '未観測') if dom.get('available') else target.get('outcome') or '未観測'} | {dom.get('normal_content_candidate') if dom.get('available') else '未観測'} |")
    lines += ["", "## Home DOMとsensor応答", "", "| 方式 | home title | deny marker | sensor ledger順（method/status/role） |", "|---|---|---:|---|"]
    for tool in TOOLS:
        item = comparison["tools"][tool]
        home_dom = item["home_document"]["dom"]
        sensor = ", ".join(f"{event.get('request_method')}/{event.get('status')}/{event.get('role')}" for event in item["sensor_events"]) or "未観測"
        lines.append(f"| {tool} | {home_dom.get('title') if home_dom.get('available') else '未観測'} | {home_dom.get('denial_marker_present') if home_dom.get('available') else '未観測'} | {sensor} |")
    lines += ["", "## 実行時制御とエラー", "", "| 方式 | setup runtime | child exit | fixture verify | 実行エラー |", "|---|---|---:|---:|---|"]
    for tool in TOOLS:
        item = comparison["tools"][tool]
        child = item.get("matrix_child") or {}
        errors = item["execution_errors"]
        error_text = json.dumps(errors, ensure_ascii=False, separators=(",", ":")) if errors["result_errors"] or errors["pipeline_errors"] else "なし"
        lines.append(f"| {tool} | {json.dumps(item['setup_runtime'], ensure_ascii=False, separators=(',', ':'))} | {child.get('exit_code', '未観測')} | {(item['fixture_verification'] or {}).get('ok', '未観測')} | {error_text} |")
    overlap = comparison["matrix_overlap"]
    all_three = overlap.get("all_three")
    lines += ["", "## 実行条件と制約", "", f"matrix-runの3方式同時区間: {all_three if all_three is not None else '未観測'}。", ""]
    hashes = comparison["launch_template_blob_sha256"]
    lines.append(f"launch template blob SHA-256: {json.dumps(hashes, ensure_ascii=False, sort_keys=True)}")
    lines += [
        "",
        "同じlaunch template SHAは同じファイルを記録したことを示します。4play単体にCamoufoxのfingerprint設定が適用されたことまでは示しません。",
        "Cookie単独の因果、性能差、住宅ISP経由、未記録のSet-Cookieやresponse headersは結論していません。exit IP測定と住宅ISP確認はconditions.jsonの明示値に従います。",
        "通常本文候補はDOM/title/outcomeに基づく機械的な印で、ページ内容の人手評価ではありません。sensorのstatus/methodはledgerのstatus/request_methodから集計しています。",
        "",
    ]
    for tool in TOOLS:
        for revisit in comparison["tools"][tool]["same_session_revisits"]:
            if revisit.get("error") or revisit.get("outcome") == "execution_error":
                statuses = [event.get("status") for event in revisit["ledger_document_events"]]
                lines.append(f"{tool}の再訪resultsはexecution_errorのため成功扱いしていません。ledgerには別記録として文書status {statuses or '未観測'} があります。")
    return "\n".join(lines)


def main():
    conditions = read_json(ROOT / "conditions.json") or {}
    manifest = read_json(ROOT / "manifest.json") or {}
    site = next((row for row in manifest.get("sites", []) if row.get("id") == "joshin"), {})
    sensor_url = conditions.get("sensor_url") or (site.get("session_initialization") or {}).get("sensor_url")
    progress = read_json(ROOT / "matrix-run.json")
    children = {
        item.get("tool"): item for item in (progress or {}).get("children", [])
        if isinstance(item, dict)
    }
    tools = {tool: summarize_tool(tool, site, sensor_url, children.get(tool)) for tool in TOOLS}
    hashes = {tool: tools[tool]["launch_template_blob_sha256"] for tool in TOOLS}
    unique_hashes = {value for value in hashes.values() if value}
    comparison = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "conditions": {
            "tools": conditions.get("tools"),
            "sites": conditions.get("sites"),
            "sensor_url": sensor_url,
            "proxy": conditions.get("proxy"),
            "residential_isp_verified": conditions.get("residential_isp_verified"),
            "actual_exit_ip_measured": conditions.get("actual_exit_ip_measured"),
            "concurrency": conditions.get("concurrency"),
            "camoufox_shared_fingerprint": conditions.get("camoufox_shared_fingerprint"),
        },
        "tools": tools,
        "matrix_overlap": summarize_overlap(progress),
        "launch_template_blob_sha256": hashes,
        "launch_template_shared_across_all_tools": len(unique_hashes) == 1 and len(unique_hashes) == len(set(hashes.values())) and None not in hashes.values(),
        "interpretation_limits": [
            "Do not infer cookie-only causality from same-session outcomes.",
            "Do not infer performance superiority from this comparison.",
            "Do not infer residential ISP routing without measured exit-IP evidence.",
            "Response Set-Cookie and response headers are not included unless separately captured in the input evidence.",
            "The common launch-template hash proves artifact identity, not that standalone 4play applied Camoufox fingerprint settings.",
        ],
    }
    (ROOT / "comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "report.md").write_text(render_report(comparison), encoding="utf-8")


if __name__ == "__main__":
    main()
