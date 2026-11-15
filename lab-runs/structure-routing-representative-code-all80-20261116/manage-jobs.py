import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--stage", choices=("sampling", "code"), required=True)
parser.add_argument("--batch-size", type=int, default=4)
args = parser.parse_args()
jobs = ROOT / "model-jobs"
jobs.mkdir(exist_ok=True)
assigned = set()
existing = sorted(jobs.glob(f"{args.stage}-*.request.json"))
for file in existing:
    assigned.update(json.loads(file.read_text())["site_ids"])
ready = []
private = Path.home() / ".codex/evaluations/structure-routing-representative-code-all80-20261116/targets.json"
frozen = {row["id"] for row in json.loads(private.read_text()).get("sites", [])} if private.exists() else set()
for site in sorted((ROOT / "sites").iterdir()):
    if not site.is_dir() or site.name in assigned:
        continue
    input_file = site / ("bootstrap-request.json" if args.stage == "sampling" else "code-request.json")
    output_file = site / ("sampling-answer.json" if args.stage == "sampling" else "crawl.js")
    if not input_file.exists() or output_file.exists() or (args.stage == "code" and site.name not in frozen):
        continue
    try:
        value = json.loads(input_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        continue
    ready.append({"id": site.name, "input_file": str(input_file.resolve()), "input_sha256": hashlib.sha256(input_file.read_bytes()).hexdigest(),
                  "input_artifact_characters": len(input_file.read_text(encoding="utf-8")), "representative_only": True})
out = []
for offset in range(0, len(ready), args.batch_size):
    items = ready[offset:offset + args.batch_size]
    name = f"{args.stage}-{len(existing) + len(out) + 1:03d}"
    request = {"job_id": name, "stage": args.stage, "requested_model": "gpt-6-luna", "site_ids": [item["id"] for item in items], "inputs": items,
               "created_at": datetime.now(timezone.utc).isoformat(), "input_artifact_characters": sum(item["input_artifact_characters"] for item in items),
               "actual_native_tokens": None, "actual_native_context_characters": None, "model_identity_independently_verified": False,
               "heldout_HTML_supplied": False, "state": "pending"}
    file = jobs / (name + ".request.json")
    file.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.append({"job": name, "site_ids": request["site_ids"], "request_file": str(file.resolve())})
print(json.dumps({"created": out}, ensure_ascii=False))
