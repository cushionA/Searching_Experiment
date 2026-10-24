import json
from pathlib import Path
import time

proof = {"purpose": "issue24 public CPU cancellation test", "synthetic": True,
         "probe": "issue24-public-cancel-20261024", "gpu_requested": False}
Path("/kaggle/working/public-cancel-proof.json").write_text(json.dumps(proof))
print("PUBLIC_CANCEL_PROOF " + json.dumps(proof), flush=True)
for tick in range(30):
    print("CPU waiting for authorized API cancel", tick, flush=True)
    time.sleep(5)
print("No API cancellation observed before normal exit", flush=True)
