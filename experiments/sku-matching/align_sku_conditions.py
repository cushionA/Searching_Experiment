"""Align the selected Rakuten SKU's conditions with every row of its fixed AU product.

similarity  per fixed pair, score every Rakuten option against every AU option with a Japanese
            cross-encoder reranker, in both directions. Scores are only ranked, never thresholded.
align       four table steps, no vocabulary and no value parsing:
            1. identical strings after NFKC rank first in both directions;
            2. a Rakuten value and an AU value pair when each is the other's best match; a pair whose
               strings contain one another is marked extra (the longer side states more);
            3. a Rakuten axis and an AU axis are symmetric when their pairs join only each other; other
               Rakuten axes, and AU axes with several values that join none, are one-sided;
            4. per AU row: aligned or contradiction on symmetric axes; extra and one-sided conditions
               go to the description check; an unpaired value on a symmetric axis is excluded.
score       after the outputs are saved: Luna machine labels and the manual axis reference.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import platform
import time
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MODEL = ("hotchpotch/japanese-reranker-small-v2", "e8091d132c23b372e059505edbb9255f346100d3")
LABELS = ".lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl"
# A row takes the first state any of its conditions has, in this order.
ROW_STATES = ["contradiction", "symmetric_unresolved", "pending_description_check", "aligned"]
CONDITION_TO_ROW = {"contradiction": "contradiction", "symmetric_unresolved": "symmetric_unresolved",
                    "one_sided": "pending_description_check", "extra_in_value": "pending_description_check",
                    "aligned": "aligned"}


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).open(encoding="utf-8") if line.strip()]


def write_new(path: Path, rows=None, text=None):
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = text if text is not None else "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(body, encoding="utf-8", newline="\n")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def load(run: Path):
    """Fixed pairs with their Rakuten options (axis_key, label, value) and AU options (axis, value)."""
    contexts = {c["dossier_id"]: c for c in read_jsonl(run / "inputs" / "products.jsonl")}
    cases = read_jsonl(run / "inputs" / "cases.jsonl")
    rakuten = {}
    for case in cases:
        for a in case["rakuten_selected"]["axes"]:
            for v in a["family_values"]:
                rakuten.setdefault(case["dossier_id"], {}).setdefault((a["axis_key"], v), a["axis_label"])
    rakuten = {d: [[k, label, v] for (k, v), label in opts.items()] for d, opts in rakuten.items()}
    au = {d: [list(o) for o in dict.fromkeys((a["axis_name"], a["value"]) for row in c["au_rows"] for a in row["axes"])]
          for d, c in contexts.items()}
    return contexts, cases, rakuten, au


def similarity(args):
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    torch.set_num_threads(args.threads)
    run, out = ROOT / args.run, ROOT / args.out
    _, _, rakuten, au = load(run)
    local = Path(snapshot_download(MODEL[0], revision=MODEL[1]))
    tok = AutoTokenizer.from_pretrained(local)
    model = AutoModelForSequenceClassification.from_pretrained(local).eval()

    def scores(query, docs):
        enc = tok([query] * len(docs), docs, padding=True, truncation=True, max_length=512, return_tensors="pt")
        with torch.inference_mode():
            return model(**enc).logits[:, 0].float().tolist()

    start, rows = time.perf_counter(), []
    for d in sorted(rakuten):
        r_text = [f"{label}: {v}" for _, label, v in rakuten[d]]
        a_text = [f"{n}: {v}" for n, v in au[d]]
        rows.append({"dossier_id": d, "rakuten": rakuten[d], "au": au[d],
                     "forward": [scores(q, a_text) for q in r_text], "reverse": [scores(q, r_text) for q in a_text]})
    seconds = time.perf_counter() - start
    write_new(out / "similarities.jsonl", rows)
    write_new(out / "similarities.json", text=json.dumps({
        "model": MODEL[0], "revision": MODEL[1], "threads": args.threads, "seconds": seconds,
        "pairs_scored": sum(2 * len(r["rakuten"]) * len(r["au"]) for r in rows),
        "weights_sha256": {p.name: sha256(p) for p in sorted(local.glob("*.safetensors"))},
        "inputs_sha256": {n: sha256(run / "inputs" / n) for n in ("products.jsonl", "cases.jsonl")},
        "similarities_sha256": sha256(out / "similarities.jsonl"), "code_sha256": sha256(Path(__file__)),
        "torch": torch.__version__, "python": platform.python_version(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(rows)} pairs, {seconds:.0f}s")


def value_pairs(entry) -> pd.DataFrame:
    """Steps 1-2: each Rakuten option with its best AU option, paired when the match is mutual."""
    rak = pd.DataFrame(entry["rakuten"], columns=["axis_key", "axis_label", "value"])
    au = pd.DataFrame(entry["au"], columns=["au_axis", "au_value"])
    same = np.array([norm(v) for v in rak["value"]])[:, None] == np.array([norm(v) for v in au["au_value"]])[None, :]
    forward = np.where(same, np.inf, np.array(entry["forward"], dtype=float).reshape(same.shape))
    reverse = np.where(same.T, np.inf, np.array(entry["reverse"], dtype=float).reshape(same.T.shape))
    best_au, best_rak = forward.argmax(axis=1), reverse.argmax(axis=1)
    table = pd.concat([rak, au.iloc[best_au].reset_index(drop=True)], axis=1)
    table["paired"] = best_rak[best_au] == np.arange(len(rak))
    table["extra"] = [a != b and (a in b or b in a) for a, b in zip(table["value"].map(norm), table["au_value"].map(norm))]
    table["matched_by"] = np.where(same[np.arange(len(rak)), best_au], "identical", "model")
    return table


def axis_pairs(table: pd.DataFrame, au_options) -> tuple[dict, list]:
    """Step 3: {Rakuten axis_key: AU axis} for symmetric axes, and the varying AU-only axes."""
    links = pd.crosstab(table.loc[table["paired"], "axis_key"], table.loc[table["paired"], "au_axis"]) > 0
    alone = links & (links.sum(axis=1) == 1).to_numpy()[:, None] & (links.sum(axis=0) == 1).to_numpy()[None, :]
    symmetric = alone.idxmax(axis=1)[alone.any(axis=1)].to_dict()
    values = pd.DataFrame(au_options, columns=["au_axis", "au_value"]).groupby("au_axis")["au_value"].nunique()
    au_only = sorted(values[(values > 1) & ~values.index.isin(list(symmetric.values()))].index)
    return symmetric, au_only


def conditions(case, context, table, symmetric, au_only) -> pd.DataFrame:
    """Step 4: one status per (AU row, condition); AU-only axes count as one-sided conditions."""
    rows = pd.DataFrame([{a["axis_name"]: a["value"] for a in r["axes"]} for r in context["au_rows"]],
                        index=[r["row_key"] for r in context["au_rows"]])
    paired = table[table["paired"] & (table["au_axis"] == table["axis_key"].map(symmetric))]
    mapped = {(k, v): (a, x, m) for k, v, a, x, m in paired[["axis_key", "value", "au_value", "extra", "matched_by"]].itertuples(index=False)}
    out = []
    for axis in case["rakuten_selected"]["axes"]:
        au_axis = symmetric.get(axis["axis_key"])
        au_value, extra, matched_by = mapped.get((axis["axis_key"], axis["value"]), (None, False, None))
        row_values = rows.get(au_axis, pd.Series(None, index=rows.index, dtype=object)).to_numpy()
        status = np.select([np.full(len(rows), au_axis is None), np.full(len(rows), au_value is None),
                            row_values != au_value, np.full(len(rows), extra)],
                           ["one_sided", "symmetric_unresolved", "contradiction", "extra_in_value"], "aligned")
        out.append(pd.DataFrame({"row_key": rows.index, "kind": "rakuten", "axis_key": axis["axis_key"],
                                 "axis_label": axis["axis_label"], "value": axis["value"], "au_axis": au_axis,
                                 "au_value": row_values, "status": status,
                                 "matched_by": np.where(np.isin(status, ["aligned", "extra_in_value"]), matched_by, None)}))
    out += [pd.DataFrame({"row_key": rows.index, "kind": "au_only", "axis_key": None, "axis_label": n, "value": None,
                          "au_axis": n, "au_value": rows[n].to_numpy(), "status": "one_sided", "matched_by": None})
            for n in au_only]
    return pd.concat(out, ignore_index=True)


def row_states(conds: pd.DataFrame) -> pd.Series:
    rank = conds["status"].map(CONDITION_TO_ROW).map(ROW_STATES.index)
    return rank.groupby(conds["row_key"], sort=False).min().map(dict(enumerate(ROW_STATES)))


def decide(states: pd.Series) -> tuple[str, str | None, str]:
    counts = states.value_counts()
    single = counts.get("aligned", 0) == 1 and counts.get("contradiction", 0) == len(states) - 1
    reason = ("all_conditions_aligned_on_one_row" if single else
              "several_rows_aligned" if counts.get("aligned", 0) > 1 else
              "contradiction_on_every_row" if counts.get("contradiction", 0) == len(states) else
              "pending_description_check" if counts.get("pending_description_check", 0) else
              "symmetric_condition_unresolved")
    return ("matched" if single else "unmatched"), (states.index[states == "aligned"][0] if single else None), reason


def tables(out: Path):
    sims = {s["dossier_id"]: s for s in read_jsonl(out / "similarities.jsonl")}
    pairs = {d: value_pairs(s) for d, s in sims.items()}
    axes = {d: axis_pairs(pairs[d], sims[d]["au"]) for d in sims}
    return pairs, axes


def align(args):
    run, out = ROOT / args.run, ROOT / args.out
    meta = json.loads((out / "similarities.json").read_text(encoding="utf-8"))
    if meta["similarities_sha256"] != sha256(out / "similarities.jsonl"):
        raise RuntimeError("Similarities changed after they were saved")
    contexts, cases, _, _ = load(run)
    pairs, axes = tables(out)
    rule = {r["case_id"]: r["binary"] for r in read_jsonl(run / "predictions" / "A-full_spec_notice.jsonl")}
    decisions, handoff, diff = [], [], []
    for case in cases:
        d, sel = case["dossier_id"], case["rakuten_selected"]
        conds = conditions(case, contexts[d], pairs[d], *axes[d])
        states = row_states(conds)
        decision, top, reason = decide(states)
        decisions.append({"case_id": case["case_id"], "dossier_id": d, "decision": decision, "top_row_key": top,
                          "reason": reason, "row_states": states.value_counts().to_dict()})
        for row_key in states.index[states == "pending_description_check"]:
            mine = conds[conds["row_key"] == row_key]
            handoff.append({"case_id": case["case_id"], "dossier_id": d, "au_product_id": case["au_product_id"],
                            "au_row_key": row_key,
                            "au_product_source": {k: contexts[d]["au_product"][k] for k in ("raw_file", "sha256")},
                            "rakuten_sku": {k: sel[k] for k in ("source_sku_key", "sku_record_key", "variant_id", "url",
                                                                "raw_file", "sha256")},
                            "conditions": mine.drop(columns="row_key").to_dict("records"),
                            "rakuten_axes": {a["axis_key"]: {"family_values": a["family_values"], "value_span": a["value_span"]}
                                             for a in sel["axes"]}})
        rb = rule[case["case_id"]]
        if (rb["decision"], rb.get("top_row_key")) != (decision, top):
            diff.append({"case_id": case["case_id"], "rule": [rb["decision"], rb.get("top_row_key")],
                         "rule_reasons": rb["reason_codes"], "alignment": [decision, top], "alignment_reason": reason})
    dest = out / "align"
    write_new(dest / "value_pairs.jsonl", [{"dossier_id": d, **r} for d, t in sorted(pairs.items()) for r in t.to_dict("records")])
    write_new(dest / "axes.jsonl", [{"dossier_id": d, "symmetric": s, "au_only": a} for d, (s, a) in sorted(axes.items())])
    for name, rows in (("decisions.jsonl", decisions), ("handoff.jsonl", handoff), ("diff-vs-rule-v10.jsonl", diff)):
        write_new(dest / name, rows)
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "labels_read": False,
               "similarities_sha256": meta["similarities_sha256"], "code_sha256": sha256(Path(__file__)),
               "decisions": dict(Counter(x["decision"] for x in decisions)),
               "decision_reasons": dict(Counter(x["reason"] for x in decisions)),
               "handoff_rows": len(handoff), "diff_vs_rule": dict(Counter(f"{x['rule'][0]}->{x['alignment'][0]}" for x in diff)),
               "outputs_sha256": {p.name: sha256(p) for p in sorted(dest.glob("*.jsonl"))}}
    write_new(dest / "summary.json", text=json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("decisions", "decision_reasons", "handoff_rows", "diff_vs_rule")},
                     ensure_ascii=False, indent=1))


def score(args):
    run, out = ROOT / args.run, ROOT / args.out
    dest = out / "align"
    summary = json.loads((dest / "summary.json").read_text(encoding="utf-8"))
    changed = [n for n, digest in summary["outputs_sha256"].items() if sha256(dest / n) != digest]
    if changed:
        raise RuntimeError(f"Prediction files changed before scoring: {changed}")
    contexts, cases, _, _ = load(run)
    pairs, axes = tables(out)
    reference = json.loads((ROOT / args.axis_reference).read_text(encoding="utf-8"))["pairs"]
    axis_result = Counter()
    for d, t in pairs.items():
        for key, label in t[["axis_key", "axis_label"]].drop_duplicates().itertuples(index=False):
            want, got = reference[d][label]["au_axis"], axes[d][0].get(key)
            axis_result[("symmetric" if want else "one_sided") + ("_correct" if want == got else "_wrong")] += 1
    gold = {g["case_id"]: g for g in read_jsonl(ROOT / LABELS)}
    cond, competitors = Counter(), Counter()
    for case in cases:
        g = gold.get(case["case_id"])
        if g is None or g["decision"] != "matched":
            continue
        d = case["dossier_id"]
        conds = conditions(case, contexts[d], pairs[d], *axes[d])
        states = row_states(conds)
        true = conds[conds["row_key"].isin(g["matching_au_row_keys"]) & (conds["kind"] == "rakuten")]
        side = true["axis_label"].map(lambda label: "symmetric" if reference[d][label]["au_axis"] else "one_sided")
        cond.update(side + "_" + true["status"] + true["matched_by"].map(lambda m: f"_{m}" if m else ""))
        competitors["cases"] += 1
        competitors["all_competitors_contradicted"] += bool((states[~states.index.isin(g["matching_au_row_keys"])]
                                                             == "contradiction").all())
    result = {"axis_reference_sha256": sha256(ROOT / args.axis_reference), "labels_sha256": sha256(ROOT / LABELS),
              "axes": dict(axis_result), "labelled_row_conditions": dict(cond), "competitors": dict(competitors),
              "note": "axis reference is Claude's own reading (not blind); labels are Luna machine labels"}
    write_new(dest / "score.json", text=json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["similarity", "align", "score"])
    parser.add_argument("--run", default=".lab-output/sku-gate-tasks-20261010-v10")
    parser.add_argument("--out", default=".lab-output/sku-align-20261011-v2/legacy")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--axis-reference", default=".lab-output/sku-align-20261010-v1/reference/axis-correspondence-legacy.json")
    args = parser.parse_args()
    {"similarity": similarity, "align": align, "score": score}[args.step](args)


if __name__ == "__main__":
    main()
