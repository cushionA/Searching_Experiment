"""Build a private, label-blind GPU trial from immutable real SKU dossiers."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


MODELS = [
    {"name": "Qwen/Qwen3.5-9B", "revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
     "load": "nf4", "model_type": "qwen3_5", "parameter_class": "9B",
     "quantization_origin": "official base checkpoint quantized at load time with bitsandbytes NF4"},
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def axis_text(case: dict) -> str:
    return " / ".join(f"{x.get('axis_name') or x.get('axis_key') or ''}={x['value']}"
                      for x in case["rakuten"]["option_values"])


def source_stratum(case: dict) -> tuple:
    """Balance actual lace choices and width scopes, without consulting labels."""
    axes = {x.get("axis_name") or x.get("axis_key") or "": str(x["value"])
            for x in case["rakuten"]["option_values"]}
    lace = next((v for k, v in axes.items() if "レース" in k), "not_a_lace_axis")
    size = axes.get("サイズ", "")
    width = re.match(r"(?:幅)?(\d+)[×xX]", size)
    return (lace, width.group(1) if width else "not_numeric_width")


def choose_cases(cases: list[dict], cap: int) -> list[dict]:
    if cap < 1:
        raise ValueError("per_dossier_cap must be positive")
    groups = defaultdict(list)
    for case in cases:
        groups[case["dossier_id"]].append(case)
    selected = set()
    for rows in groups.values():
        strata = defaultdict(list)
        for case in rows:
            strata[source_stratum(case)].append(case)
        ranked = [sorted(group, key=lambda c: hashlib.sha256(c["case_id"].encode()).hexdigest())
                  for _, group in sorted(strata.items())]
        picked = []
        while ranked and len(picked) < cap:
            next_round = []
            for group in ranked:
                if len(picked) == cap:
                    break
                picked.append(group[0])
                if len(group) > 1:
                    next_round.append(group[1:])
            ranked = next_round
        selected.update(x["case_id"] for x in picked)
    return [c for c in cases if c["case_id"] in selected]


def normalized_case(case: dict, dossier: dict, task: dict) -> dict:
    au, rak = dossier["au_product"], dossier["rakuten_product"]
    pool = [{"row_key": row["row_key"],
             "sku": " / ".join(f"{x.get('axis_name_raw') or ''}={x['value_raw']}"
                               for x in row["axes_raw"] if str(x.get("value_raw", "")).strip())}
            for row in dossier["au_rows"]]
    if [row["row_key"] for row in pool] != [row["row_key"] for row in task["au_candidates"]]:
        raise ValueError("Dossier and frozen full AU pool differ")
    descriptions = [block["text"] for block in au["description"]["blocks"]
                    if not any(tag in block.get("scope", "") for tag in
                               ("series", "sibling", "navigation", "related"))]
    purchase = au.get("purchase_options_raw")
    if purchase:
        descriptions.append("購入選択肢（価格を除外した仕様情報）: " +
                            (purchase if isinstance(purchase, str) else json.dumps(purchase, ensure_ascii=False)))
    return {"case_id": case["case_id"], "dossier_id": case["dossier_id"], "split": case["split"],
            "source_category": "curtain" if task["rakuten"]["attrs"].get("category") == "curtain" else "non_curtain",
            "rakuten": {"title": case["rakuten"]["title_raw"], "sku": axis_text(case),
                        "description": rak["description"]["individual_description_excerpt"]},
            "au": {"title": au["title_raw"], "description": "\n".join(descriptions), "sku_rows": pool}}


def prepare(inputs: Path, tasks_path: Path, output: Path, cap: int) -> dict:
    if output.exists():
        raise ValueError("Refusing existing output")
    cases = read_rows(inputs / "cases.jsonl")
    tasks = read_rows(tasks_path)
    if [c["case_id"] for c in cases] != [t["case_id"] for t in tasks]:
        raise ValueError("Frozen source/task case order differs")
    if len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    dossiers = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (inputs / "dossiers").glob("*.json")}
    paths = [inputs / "cases.jsonl", inputs / "manifest.json", tasks_path,
             *(inputs / "dossiers" / (key + ".json") for key in sorted(dossiers))]
    before = {str(p): sha(p) for p in paths}
    selected = choose_cases(cases, cap)
    mapping = {t["case_id"]: t for t in tasks}
    records = [normalized_case(c, dossiers[c["dossier_id"]], mapping[c["case_id"]]) for c in selected]
    output.mkdir(parents=True, exist_ok=False)
    with (output / "inputs.jsonl").open("x", encoding="utf-8") as f:
        for row in records:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    config = {"models": MODELS, "max_input_tokens": 16000, "max_new_tokens": 384,
              "batch_size": 1, "runtime_budget_seconds": 7200,
              "enable_thinking": False,
              "selection_policy": "Evaluate current Qwen3.5 9B in NF4 first; consider quantized 4B only after reviewing adequate 9B accuracy.",
              "deployment_assumption": "Google Cloud GPU self-hosting; Kaggle used only for accuracy trial. GCP speed/cost measurement deferred until results reviewed."}
    (output / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    after = {str(p): sha(p) for p in paths}
    if before != after:
        raise ValueError("Immutable source input changed")
    manifest = {"schema_version": "label-blind-gpu-sku-input-v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_case_count": len(cases), "input_count": len(records),
                "inputs_sha256": sha(output / "inputs.jsonl"), "inputs_bytes": (output / "inputs.jsonl").stat().st_size,
                "config_sha256": sha(output / "config.json"), "source_sha256_pre": before, "source_sha256_post": after,
                "preparer_code_sha256": sha(Path(__file__)), "per_dossier_cap": cap,
                "sampling": "Round-robin source lace/width strata per dossier, sha256(case_id) order, before reading any labels/predictions",
                "case_ids": [r["case_id"] for r in records], "dossier_count": len({r["dossier_id"] for r in records}),
                "strata_counts": dict(Counter(r["source_category"] for r in records)),
                "split_counts": dict(Counter(r["split"] for r in records)), "labels_read": False,
                "synthetic_input_count": 0, "full_actual_au_pool": True, "other_au_url_routing": False,
                "source_titles_and_descriptions": "v3 semantic fields; monetary/commercial-only audit fields excluded",
                "task": "Product pair fixed; one real Rakuten SKU against all actual SKU rows of its fixed AU page",
                "test_status": "Previously inspected, reused diagnostic cases; not new human-verified gold"}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs-dir", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-dossier-cap", type=int, default=8)
    args = parser.parse_args()
    result = prepare(args.inputs_dir, args.tasks, args.output, args.per_dossier_cap)
    print(json.dumps({k: result[k] for k in ("input_count", "inputs_bytes", "inputs_sha256", "dossier_count", "strata_counts")}, ensure_ascii=False))
