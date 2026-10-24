import json
import os
from pathlib import Path
import time

# Only these non-secret runtime metadata fields are inspected. Never dump env.
names = ["KAGGLE_KERNEL_ID", "KAGGLE_KERNEL_SESSION_ID", "KAGGLE_KERNEL_VERSION_ID",
         "KAGGLE_KERNEL_VERSION", "KAGGLE_KERNEL_RUN_ID", "KAGGLE_KERNEL_RUN_TYPE",
         "KAGGLE_CONTAINER_NAME", "KAGGLE_CONTAINER_GROUP"]
identity = {"probe": "issue24-cpu-session-probe-20261024", "metadata": {}}
for name in names:
    value = os.environ.get(name)
    item = {"present": value is not None}
    if value and value.isdecimal():
        item["integer"] = int(value)
    elif name == "KAGGLE_KERNEL_RUN_TYPE" and value in ("Interactive", "Batch"):
        item["run_type"] = value
    elif value:
        item["non_numeric"] = True
    identity["metadata"][name] = item
print("ISSUE24_RUNTIME_IDENTITY " + json.dumps(identity, sort_keys=True), flush=True)
Path("/kaggle/working/runtime-identity.json").write_text(json.dumps(identity, indent=2))
for tick in range(24):
    print("CPU heartbeat", tick, flush=True)
    time.sleep(5)
print("CPU probe completed", flush=True)
