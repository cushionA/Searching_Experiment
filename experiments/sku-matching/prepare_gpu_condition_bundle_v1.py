"""Apply the frozen condition task to another complete, unlabeled real bundle."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source: Path, output: Path, expected_cases: int) -> dict:
    if output.exists():
        raise FileExistsError(output)
    code = Path(__file__).with_name("prepare_gpu_condition_tasks_v1.py")
    spec = importlib.util.spec_from_file_location("condition_bundle_preparer", code)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FROZEN = source.resolve()
    inputs = module.read_jsonl(source / "inputs.jsonl")
    cases = module.read_jsonl(source / "source-cases.jsonl")
    if len(inputs) != expected_cases or len(cases) != expected_cases:
        raise ValueError("Unexpected complete case count")
    if [r["case_id"] for r in inputs] != [r["case_id"] for r in cases]:
        raise ValueError("Input/source inventory differs")
    if len({c["case_id"] for c in cases}) != expected_cases:
        raise ValueError("Duplicate case IDs")
    # The new capture retains native row/column fields and stock. Add the
    # literal axis/value display consumed by the existing task without removing
    # any original row or field. This is a layout transform, not invented data.
    for case in cases:
        for index, row in enumerate(case["au_rows"]):
            if "sku" not in row:
                parts = []
                for prefix in ("row", "column"):
                    name, value = row[prefix + "_option_name"], row[prefix + "_option_value"]
                    if value:
                        parts.append(name + "=" + value)
                row["sku"] = " / ".join(parts)
                row["alias"] = f"a{index:03d}"
    output.mkdir(parents=True)
    view = output / "source-view"
    view.mkdir()
    for name, values in (("inputs.jsonl", inputs), ("source-cases.jsonl", cases)):
        (view / name).write_text("".join(module.canonical(value) + "\n" for value in values), encoding="utf-8")
    module.FROZEN = view.resolve()
    packets, pools = [], []
    input_sha = sha(view / "inputs.jsonl")
    for case in cases:
        generated, host = module.packets_for_case(case, input_sha)
        packets.extend(generated); pools.append(host)
    for folder in ("model", "host"):
        (output / folder).mkdir()
    for name, values in (("model/packets.jsonl", packets), ("host/fullpools.jsonl", pools)):
        (output / name).write_text("".join(module.canonical(value) + "\n" for value in values), encoding="utf-8")
    dependencies = {name: sha(module.CLAUDE_CODE / name)
                    for name in ("sku_gate_atoms.py", "sku_gate_sources.py", "sku_gates.py", "run_sku_gates.py")}
    manifest = {"schema_version": "gpu-condition-task-manifest-v1", "task_version": "gpu-condition-task-v1",
                "case_count": len(cases), "case_ids_in_order": [c["case_id"] for c in cases],
                "au_row_count_total": sum(len(h["au_rows"]) for h in pools),
                "all_au_rows_host_only": True, "packet_count": len(packets), "condition_count": len(packets),
                "host_review_case_count": sum(h["focus_row_key"] is None for h in pools),
                "blocked_case_count": sum(bool(h.get("blocking_issues")) for h in pools),
                "model_packets_sha256": sha(output / "model/packets.jsonl"),
                "host_fullpools_sha256": sha(output / "host/fullpools.jsonl"),
                "frozen_input_sha256": {name: sha(view / name) for name in ("inputs.jsonl", "source-cases.jsonl")},
                "original_input_sha256": {name: sha(source / name) for name in ("inputs.jsonl", "source-cases.jsonl")},
                "layout_transform": "AU literal sku/alias added from native row/column; all source fields and rows retained",
                "preparer_sha256": sha(code), "bundle_adapter_sha256": sha(Path(__file__)),
                "claude_gate_source_path": str(module.CLAUDE_CODE), "claude_gate_source_sha256": dependencies,
                "labels_read": False, "selection_basis": "selected SKU atoms and source text only",
                "evidence_char_limit": module.MAX_EVIDENCE_CHARS,
                "evidence_truncation": "whole blocks; omissions are explicit review blockers",
                "interpretation": "unlabeled real-family candidate experiment; no confirmed gold or independent holdout"}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--expected-cases", required=True, type=int)
    args = ap.parse_args()
    print(json.dumps(prepare(args.source, args.output, args.expected_cases), indent=2))
