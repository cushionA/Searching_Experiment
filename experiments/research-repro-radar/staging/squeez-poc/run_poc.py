import hashlib
import json
import resource
import time
from pathlib import Path

import torch
from transformers import AutoModel


torch.set_num_threads(2)
started = time.perf_counter()
load_started = time.perf_counter()
model = AutoModel.from_pretrained(
    "/opt/model", trust_remote_code=True, local_files_only=True
).to("cpu").eval()
load_seconds = time.perf_counter() - load_started
samples = json.loads(Path("/app/input.json").read_text())
results = []
tp = fp = fn = 0
with torch.inference_mode():
    for sample in samples:
        raw = sample["context"]
        input_hash = hashlib.sha256(
            (sample["question"] + "\0" + raw).encode("utf-8")
        ).hexdigest()
        t0 = time.perf_counter()
        extracted = model.process(
            question=sample["question"],
            context=raw,
            threshold=0.2,
            max_length=8192,
            doc_stride=256,
            min_span_chars=30,
            merge_gap_chars=20,
        )
        elapsed = time.perf_counter() - t0
        gold = set(sample["gold_lines"])
        selected = set()
        offsets = []
        start = 0
        for line in raw.splitlines():
            end = start + len(line)
            if line:
                offsets.append((start, end, line))
            start = end + 1
        for span in extracted["spans"]:
            for a, b, line in offsets:
                if span["start"] < b and span["end"] > a:
                    selected.add(line)
        hit = selected & gold
        tp += len(hit)
        fp += len(selected - gold)
        fn += len(gold - selected)
        results.append({
            "id": sample["id"], "pair": sample["pair"], "kind": sample["kind"],
            "input_sha256": input_hash, "latency_seconds": elapsed,
            "selected_lines": sorted(selected), "gold_lines": sorted(gold),
            "gold_line_hits": len(hit), "gold_line_count": len(gold),
            "spans": extracted["spans"],
        })

precision = tp / (tp + fp) if tp + fp else 0.0
recall = tp / (tp + fn) if tp + fn else 0.0
report = {
    "label": "10 synthetic tool-log cases; illustrative line-overlap check, not Squeez author F1 reproduction",
    "model_id": "KRLabsOrg/verbatim-rag-modern-bert-v2",
    "model_revision": "6a967332efedfe5aca9b85b8310cc68d9ac6f881",
    "method": "author README AutoModel.process; threshold=0.2; min_span_chars=30; CPU; 2 torch threads",
    "runtime": {"torch": torch.__version__, "transformers": __import__("transformers").__version__},
    "cases": len(results), "clean_noisy_pairs": len(results) // 2,
    "model_load_seconds": load_seconds,
    "elapsed_seconds": time.perf_counter() - started,
    "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    "line_overlap": {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
                     "f1": (2 * precision * recall / (precision + recall)) if precision + recall else 0.0},
    "paid_services_used": False,
    "results": results,
}
print(json.dumps(report, ensure_ascii=False, indent=2))
