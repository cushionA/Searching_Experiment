import argparse
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import subprocess
import threading
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
    parser.add_argument("--methods", default="B1,C1")
    parser.add_argument("--jobs", type=int, default=4, choices=range(1, 5))
    parser.add_argument("--phase", default="both", choices=["cold", "warm", "both"])
    args = parser.parse_args()
    run = args.run.resolve()
    selected = set(args.ids.split(",")) if args.ids else None
    targets = [row for row in rows(run / "targets.jsonl") if selected is None or row["id"] in selected]
    allowed_methods = {"B0", "B1", "C1"}
    if not set(args.methods.split(",")) <= allowed_methods:
        parser.error("Unknown method")
    gold_ids = {row["id"] for row in rows(run / "evaluator-results-v2.jsonl")}
    missing = [target["id"] for target in targets if target["id"] not in gold_ids]
    if missing:
        parser.error("Independent selection must finish before candidate runs: " + ",".join(missing))
    logs = run / "execution-logs"
    logs.mkdir(exist_ok=True)
    environment = os.environ.copy()
    if "CHROME_PATH" not in environment and os.name == "nt":
        environment["CHROME_PATH"] = "C:/Program Files/Google/Chrome/Application/chrome.exe"
    phases = ["cold", "warm"] if args.phase == "both" else [args.phase]
    started = time.monotonic()
    reservations = {}
    budget_lock = threading.Lock()

    def main_spent():
        preflight = sum(row.get("requests", 0) for row in rows(run / "evaluator-results.preflight-v1.jsonl"))
        gold = sum(row.get("document_attempts", 0) for row in rows(run / "evaluator-results-v2.jsonl"))
        candidate = sum(row.get("document_requests", 0) for row in rows(run / "results.jsonl"))
        challenges = sum(row.get("document_requests", 0) for row in rows(run / "challenge-results.jsonl"))
        return preflight + gold + candidate + challenges

    def execute(target):
        site_started = time.monotonic()
        outcomes = []
        with budget_lock:
            prior = rows(run / "results.jsonl")
            allowance = 40 if target["practice"] else 15
            reserve = sum(max(0, (3 if method == "B0" else allowance) - sum(
                row.get("document_requests", 0) for row in prior
                if row["id"] == target["id"] and row["method"] == method
            )) for method in args.methods.split(","))
            if main_spent() + sum(reservations.values()) + reserve > 3500:
                return {"id": target["id"], "status": "global_budget_reservation_reached", "processes": []}
            reservations[target["id"]] = reserve
        for phase in phases:
            command = ["node", str(ROOT / "experiments/structure-routing/runner.mjs"),
                       "--run", str(run), "--ids", target["id"], "--methods", args.methods,
                       "--phase", phase, "--observe-limit", "4", "--resume"]
            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            log = logs / f"{target['id']}-{args.methods.replace(',', '_')}-{phase}-{timestamp}.log"
            with log.open("w", encoding="utf-8") as stream:
                stream.write(json.dumps({"command": command, "started_at": timestamp}) + "\n")
                stream.flush()
                completed = subprocess.run(command, cwd=ROOT, env=environment, stdout=stream,
                                           stderr=subprocess.STDOUT, timeout=1500, check=False)
            outcomes.append({"phase": phase, "returncode": completed.returncode,
                             "log": str(log.relative_to(run))})
            if completed.returncode:
                break
        with budget_lock:
            reservations.pop(target["id"], None)
        return {"id": target["id"], "elapsed_seconds": round(time.monotonic() - site_started, 3),
                "processes": outcomes}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = [pool.submit(execute, target) for target in targets]
        for future in concurrent.futures.as_completed(pending):
            record = future.result()
            with (run / "orchestration.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(json.dumps(record, ensure_ascii=False), flush=True)
    print(json.dumps({"completed_sites": len(targets), "elapsed_seconds": round(time.monotonic() - started, 3)}))


if __name__ == "__main__":
    main()
