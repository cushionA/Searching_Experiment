import argparse
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]


def rows(file):
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--ids")
    parser.add_argument("--jobs", type=int, default=3, choices=range(1, 5))
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--methods", default="B1,C1,B2")
    parser.add_argument("--capture-metadata", action="store_true")
    parser.add_argument("--executable")
    args = parser.parse_args()
    selected_methods = args.methods.split(",")
    if not selected_methods or any(method not in {"B1", "C1", "B2"} for method in selected_methods):
        parser.error("methods must be B1,C1,B2")
    if args.capture_metadata and selected_methods != ["B2"]:
        parser.error("metadata capture runner supports B2 only")
    run = args.run.resolve()
    selected = set(args.ids.split(",")) if args.ids else None
    targets = [row for row in rows(run / "targets.jsonl") if selected is None or row["id"] in selected]
    if not (run / "protocol.json").is_file():
        parser.error("Independent protocol must be frozen first")
    logs = run / "execution-logs"
    logs.mkdir(exist_ok=True)
    environment = os.environ.copy()
    if os.name == "nt" and "CHROME_PATH" not in environment:
        environment["CHROME_PATH"] = "C:/Program Files/Google/Chrome/Application/chrome.exe"

    def execute(index, target):
        offset = index % len(selected_methods)
        methods = selected_methods[offset:] + selected_methods[:offset]
        result = {"id": target["id"], "order": methods, "processes": []}
        for method in methods:
            for phase in ["cold", "warm"]:
                prior = rows(run / "results.jsonl")
                if any(row["id"] == target["id"] and row["method"] == method and row["phase"] == phase for row in prior):
                    continue
                stop = next((row for row in prior if row["id"] == target["id"] and row.get("access_stop")), None)
                if stop:
                    skipped = {"id": target["id"], "domain": target["domain"], "method": method, "phase": phase,
                               "status": "not_run_access_condition", "access_stop": True,
                               "stop_reason": stop.get("stop_reason", "prior_access_stop"), "observations": [],
                               "cost": {"document_requests": 0, "document_responses": 0, "http_requests": 0,
                                        "xhr_fetch": 0, "response_bytes": 0, "browser_ms": 0},
                               "external_llm_calls": 0, "local_inference": {"calls": 0}, "gold_input": False}
                    directory = run / "artifacts" / target["id"] / method / phase
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory / "result.json").write_text(json.dumps(skipped, indent=2), encoding="utf-8")
                    with (run / "results.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(skipped) + "\n")
                    continue
                time.sleep(1 if target["practice"] else 5)
                runner = "sitemap-supplement.mjs" if args.capture_metadata else "sitemap-benchmark.mjs"
                command = ["node", str(ROOT / "experiments/structure-routing" / runner),
                           "--run", str(run), "--ids", target["id"], "--method", method,
                           "--phase", phase, "--live", "true"]
                if args.executable or environment.get("CHROME_PATH"):
                    command.extend(["--executable", args.executable or environment["CHROME_PATH"]])
                log = logs / f"{target['id']}-{method}-{phase}.log"
                with log.open("a", encoding="utf-8") as stream:
                    sources = [ROOT / "experiments/structure-routing" / name for name in
                               {runner, "sitemap-benchmark.mjs", "sitemap-discovery.mjs", "runner.mjs"}]
                    stream.write(json.dumps({"command": command, "started_at": datetime.now(timezone.utc).isoformat(),
                                             "source_sha256": {str(file.relative_to(ROOT)): hashlib.sha256(file.read_bytes()).hexdigest()
                                                               for file in sources}}) + "\n")
                    stream.flush()
                    completed = subprocess.run(command, cwd=ROOT, env=environment, stdout=stream,
                                               stderr=subprocess.STDOUT, timeout=660, check=False)
                result["processes"].append({"method": method, "phase": phase, "returncode": completed.returncode})
                if completed.returncode:
                    result["status"] = "execution_error"
                    return result
        result["status"] = "finished"
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = [pool.submit(execute, index, target) for index, target in enumerate(targets)]
        for future in concurrent.futures.as_completed(pending):
            result = future.result()
            with (run / "orchestration.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result) + "\n")
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
