import hashlib
import json
from pathlib import Path
import time

print("issue24: CPU-only smoke, no model training, three synthetic review rows", flush=True)
for step in range(6):
    print(f"heartbeat {step + 1}/6", flush=True)
    time.sleep(5)
rows = [{"case_id": f"synthetic-{n}", "decision": "review"} for n in range(3)]
result = {"schema_version": 1, "test": "issue24_cpu_notification_smoke", "synthetic": True,
          "compute": "cpu", "case_count": 3, "review_count": 3,
          "score": None, "sum_squares_1000": sum(n*n for n in range(1000)), "rows": rows}
path = Path("/kaggle/working/issue24_cpu_result.json")
path.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
print("saved", path.name, "sha256", hashlib.sha256(path.read_bytes()).hexdigest(), flush=True)
