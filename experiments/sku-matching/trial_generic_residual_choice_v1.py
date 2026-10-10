#!/usr/bin/env python3
"""Small task-format probe with the original selector alternatives.

This is not full-description verification or a production adoption decision.
The desired Rakuten value never enters the question/state; all raw alternatives
are scored together, plus an explicit unknown outcome.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def run(input_dir: Path, output_dir: Path, limit: int = 16,
        scope: str = "title", threads: int = 2):
    if limit < 1 or threads < 1:
        raise ValueError("limit and threads must be positive")
    if output_dir.exists():
        raise FileExistsError(output_dir)
    read = lambda path: [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tasks = read(input_dir / "tasks.jsonl")
    documents = {doc["dossier_id"]: doc for doc in read(input_dir / "documents.jsonl")}
    selected, seen = [], set()
    for task in sorted(tasks, key=lambda task: task["task_id"]):
        key = (task["au_product_id"], task["axis_name"], tuple(task["option_values"]),
               tuple((axis["axis_name"], axis["value"]) for axis in task["selected_au_row"]["axes"]))
        if key in seen:
            continue
        seen.add(key)
        selected.append(task)
        if len(selected) == limit:
            break
    requests = []
    for task in selected:
        doc = documents[task["dossier_id"]]
        choices = [{"label": f"option:{index}", "description": value}
                   for index, value in enumerate(task["option_values"])]
        choices.append({"label": "unknown", "description": "商品名だけでは該当する選択肢を一つに決められない" if scope == "title"
                        else "引用だけではこのAU行に当てはまる選択肢を一つに確定できない"})
        windows = [None] if scope == "title" else [window for field in ("title_document", "description_document")
                                                  for window in doc[field]["windows"]]
        for window in windows:
            state = {"AU商品名": doc["au"]["title"],
                     "AU選択行": [{"axis_name": axis["axis_name"], "value": axis["value"]}
                                  for axis in task["selected_au_row"]["axes"]]}
            question = f"このAU商品の「{task['axis_name']}」はどの選択肢ですか？商品名だけから判断してください。"
            if window is not None:
                state["同じAU商品ページの引用"] = window["text"]
                question = (f"このAU商品の選択中の行の「{task['axis_name']}」はどの選択肢ですか？"
                            "この行に適用される引用だけで判断してください。別ページや他の選択肢の説明だけでは確定できません。")
            requests.append({"id": task["task_id"] if window is None else f"{task['task_id']}:{window['window_id']}",
                             "question": question, "state": json.dumps(state, ensure_ascii=False),
                             "choices": choices, "residual_card": task, "window": window,
                             "title_source": doc["au"].get("title_source")})
    output_dir.mkdir(parents=True, exist_ok=False)
    code_dir = output_dir / "code"; code_dir.mkdir()
    backend = HERE / "trial_generic_model_jev_v1.py"
    for path in (Path(__file__), backend):
        shutil.copyfile(path, code_dir / path.name)
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    with (output_dir / "requests.jsonl").open("x", encoding="utf-8") as stream:
        for row in requests:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    freeze = {"labels_used_to_construct_requests": False, "probe_scope": "title_only" if scope == "title" else "all_flat_windows",
              "production_eligible": False, "desired_value_in_state": False,
              "limit": limit, "task_ids": [row["id"] for row in requests],
              "request_sha256": sha(output_dir / "requests.jsonl"),
              "input_sha256": {name: sha(input_dir / name) for name in ("tasks.jsonl", "documents.jsonl")},
              "code_sha256": {path.name: sha(code_dir / path.name) for path in (Path(__file__), backend)}}
    (output_dir / "freeze.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("jev", backend)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    started = time.perf_counter()
    result = module.run_choices(requests, threads=threads, batch_size=8)
    with (output_dir / "predictions.jsonl").open("x", encoding="utf-8") as stream:
        for row in result["records"]:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {**freeze, "pins": result["pins"], "runtime": result["runtime"],
               "elapsed_seconds": time.perf_counter() - started,
               "prediction_sha256": sha(output_dir / "predictions.jsonl")}
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"request_count": len(requests), "elapsed_seconds": summary["elapsed_seconds"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / ".lab-output/sku-generic-residual-evidence-20261010-v2")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=16)
    parser.add_argument("--scope", choices=("title", "all_windows"), default="title")
    parser.add_argument("--threads", type=int, default=2)
    run(**vars(parser.parse_args()))
