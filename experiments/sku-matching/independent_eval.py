#!/usr/bin/env python3
"""Independent real-SKU evaluation data for PR26 v5 (2026-10-11): from the category captures to scored cases.

pairs      build_observed_pair_records over the old and the new captures (new output directory).
token-pairs  candidate_review pairs with unique same-shop primary-image model-number evidence, as their own status.
inputs     the PR26 case/product inputs for one pair status, through build_raw_pair_inputs with the new pairs.
groups     parent groups (pairs sharing a product, Rakuten series prefix), old/new grain, dev/test split.
batches    blind annotation batches: titles, AU rows, Rakuten SKUs and description lines with reference ids.
check      validates answers and resolves every rationale quote to its source span (file, sha256, locator).
freeze     label manifest with input and label hashes; run align only after this.
score      align decisions against the frozen machine gold, by split and grain.

Machine gold is Haiku 5.5 annotation, not independent human gold. Price and stock are never shown.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import glob
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUN = ROOT / ".lab-output/sku-independent-real-20261011"
OLD_PAIRS = ROOT / ".lab-output/sku-observed-product-pairs-20261010-final-v4"
OLD_AU = [".lab-output/sku-real-au-20261010", ".lab-output/sku-real-au-20261010-expanded"]
OLD_RAKUTEN = [".lab-output/sku-real-rakuten-20261010-normalized-v2", ".lab-output/sku-real-rakuten-20261010-expanded"]
STATUS = {"confirmed": "confirmed_source_mapping", "family": "evidence_backed_primary_image_family_mapping",
          "candidate": "candidate_review", "token": "primary_image_token_mapping_review"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def write_new(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def jsonl(rows) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def pairs(args) -> None:
    au_dirs = OLD_AU + sorted(p.parent.relative_to(ROOT).as_posix() for p in RUN.glob("*/au/products.jsonl"))
    rk_dirs = OLD_RAKUTEN + sorted(p.parent.relative_to(ROOT).as_posix() for p in RUN.glob("*/rakuten/products.jsonl")
                                   if (p.parent / "skus.jsonl").exists())
    cmd = [sys.executable, "-B", str(HERE / "build_observed_pair_records.py"), "--output-dir", args.out]
    cmd += [x for d in au_dirs for x in ("--au-dir", d)] + [x for d in rk_dirs for x in ("--rakuten-dir", d)]
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)
    write_new(ROOT / args.out / "build-command.json", json.dumps({"cmd": cmd[1:], "au_dirs": au_dirs, "rakuten_dirs": rk_dirs,
                                                                   "at": now()}, ensure_ascii=False, indent=1) + "\n")


def token_pairs(args) -> None:
    """candidate_review pairs whose AU primary image names exactly one Rakuten SKU product, this pair's, in the same shop.

    build_observed_pair_records also asks for a shared product-type term from a fixed list that only covers the first
    categories, so same-model pairs in newer categories stay candidate_review. This keeps the image evidence and drops
    the term list; product identity is then checked by the annotator. Expanded SKU rows are reused unchanged."""
    import build_observed_pair_records as b
    from urllib.parse import urlparse
    src, out = ROOT / args.pairs, ROOT / args.out
    if out.exists():
        raise FileExistsError(out)
    command = json.loads((src / "build-command.json").read_text(encoding="utf-8"))
    raku, _ = b.rake_rows_from_roots([ROOT / d for d in command["rakuten_dirs"]])
    sku_products = {str(r["manage_number"]): r for r in raku if r.get("manage_number") and int(r.get("sku_count") or 0) > 0}
    rows = read_jsonl(src / "candidate_sku_review.jsonl")
    by_pair = defaultdict(list)
    for r in rows:
        if r["pair_status"] == STATUS["candidate"]:
            by_pair[r["pair_id"]].append(r)
    kept, evidence, skipped = [], [], Counter()
    for pair_id, recs in sorted(by_pair.items()):
        manage = pair_id.split("|raku:", 1)[1]
        au_raw = ROOT / recs[0]["provenance"]["au_raw_item"]["file"]
        info = json.loads(au_raw.read_text(encoding="utf-8")).get("itemInfo", {})
        images = info.get("productImageUrls") or []
        if not images:
            skipped["no_primary_image"] += 1
            continue
        base = urlparse(str(images[0])).path.rsplit("/", 1)[-1].casefold()
        hits = [m for m in sku_products if re.search(r"(?<![a-z0-9])" + re.escape(m.casefold()) + r"(?:[-_.]|$)", base)]
        if hits != [manage]:
            skipped["token_absent" if not hits else "token_other_or_ambiguous"] += 1
            continue
        slug = [x for x in urlparse(str(sku_products[manage].get("source_url") or "")).path.split("/") if x][0].casefold()
        shop = re.sub(r"[^a-z0-9]", "", str(info.get("shopName") or recs[0].get("au_product_ref", {}).get("shop_name") or "").casefold())
        if shop and shop != re.sub(r"[^a-z0-9]", "", slug):
            skipped["shop_mismatch"] += 1
            continue
        ev = {"primary_image_url": images[0], "primary_image_json_path": "$.itemInfo.productImageUrls[0]",
              "au_raw_item": recs[0]["provenance"]["au_raw_item"], "primary_image_basename": base, "manage_number_token": manage,
              "rakuten_store_slug": slug, "au_shop_name": info.get("shopName"),
              "mapping_basis": "unique Rakuten SKU-product manage token in AU primary image basename + same shop; "
                               "no product-type term; review mapping, identity checked by the annotator"}
        evidence.append({"pair_id": pair_id, "sku_records": len(recs), **ev})
        kept += [{**r, "pair_status": STATUS["token"], "source_pair_status": r["pair_status"], "token_evidence": ev} for r in recs]
    out.mkdir(parents=True)
    arrays = (src / "au_product_sku_arrays.jsonl").read_bytes()
    (out / "au_product_sku_arrays.jsonl").write_bytes(arrays)
    write_new(out / "candidate_sku_review.jsonl", jsonl(kept))
    write_new(out / "token_pairs.jsonl", jsonl(evidence))
    manifest = {"created_at_utc": now(), "source_pairs_dir": args.pairs, "pair_status": STATUS["token"],
                "candidate_review_pairs_examined": len(by_pair), "pairs_kept": len(evidence), "sku_records_kept": len(kept),
                "skipped": dict(skipped), "rakuten_sku_products": len(sku_products),
                "sha256": {"source_candidate_sku_review.jsonl": sha256(src / "candidate_sku_review.jsonl"),
                           "au_product_sku_arrays.jsonl": hashlib.sha256(arrays).hexdigest(),
                           "candidate_sku_review.jsonl": sha256(out / "candidate_sku_review.jsonl")},
                "code_sha256": sha256(Path(__file__))}
    write_new(out / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: manifest[k] for k in ("candidate_review_pairs_examined", "pairs_kept", "sku_records_kept", "skipped")},
                     ensure_ascii=False))


def inputs(args) -> None:
    import build_raw_pair_inputs as b
    pairs_rel = Path(args.pairs).as_posix()
    b.PAIRS = pairs_rel
    b.SOURCES = {"confirmed": f"{pairs_rel}/rakuten_sku_expanded.jsonl", "family": f"{pairs_rel}/candidate_sku_review.jsonl",
                 "candidate": f"{pairs_rel}/candidate_sku_review.jsonl", "token": f"{pairs_rel}/candidate_sku_review.jsonl"}
    b.STATUS = dict(STATUS)
    sys.argv = ["build_raw_pair_inputs.py", args.status, "--out", args.out]
    b.main()


def series(manage: str) -> str:
    key = re.sub(r"\d+[a-z]*$", "", manage.casefold())
    return key if len(key) >= 2 else manage.casefold()


def groups(args) -> None:
    run = ROOT / args.run
    cases = read_jsonl(run / "inputs/cases.jsonl")
    contexts = {c["dossier_id"]: c for c in read_jsonl(run / "inputs/products.jsonl")}
    old_pairs = {p["pair_id"] for p in read_jsonl(OLD_PAIRS / "observed_product_pairs.jsonl")}
    old_au = {str(p.get("item_id")) for d in OLD_AU for p in read_jsonl(ROOT / d / "products.jsonl")}
    old_rk = {str(p.get("manage_number")) for f in glob.glob(str(ROOT / ".lab-output/sku-real-rakuten-2026*/**/products.jsonl"), recursive=True)
              for p in read_jsonl(Path(f))}
    # AU items paired with an already captured Rakuten product anywhere (old pairs or the current pairs) are not independent.
    linking = read_jsonl(OLD_PAIRS / "observed_product_pairs.jsonl") + (read_jsonl(ROOT / args.pairs / "observed_product_pairs.jsonl") if args.pairs else [])
    linked_au = {str(p["au_product_id"]) for p in linking if p.get("au_product_id") and p.get("rakuten_manage_number")
                 and p["pair_status"] in STATUS.values() and str(p["rakuten_manage_number"]) in old_rk}
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)
    for ctx in contexts.values():
        au_id, manage = ctx["pair_ref"].split("|")
        manage = manage.split(":", 1)[1]
        union(f"pair:{ctx['pair_ref']}", f"au:{au_id.split(':', 1)[1]}")
        union(f"pair:{ctx['pair_ref']}", f"rk:{manage}")
        union(f"rk:{manage}", f"series:{series(manage)}")
    rows = []
    for d, ctx in sorted(contexts.items()):
        au_id = ctx["pair_ref"].split("|")[0].split(":", 1)[1]
        manage = ctx["pair_ref"].split("|")[1].split(":", 1)[1]
        grain = ("old_dataset" if ctx["pair_ref"] in old_pairs else
                 "new_sku_existing_product" if au_id in old_au or manage in old_rk else "new_product_group")
        group = find(f"pair:{ctx['pair_ref']}")
        excluded = (None if not args.exclude_existing_rakuten else "existing_rakuten_url" if manage in old_rk else
                    "au_linked_to_existing_rakuten_url" if au_id in linked_au else None)
        rows.append({"dossier_id": d, "pair_ref": ctx["pair_ref"], "pair_status": ctx.get("pair_status"), "grain": grain,
                     "group_id": "g-" + hashlib.sha1(group.encode()).hexdigest()[:12], "excluded": excluded,
                     "split": "excluded" if excluded else "test" if int(hashlib.sha1(group.encode()).hexdigest(), 16) % 2 else "dev"})
    by_group = defaultdict(set)
    for r in rows:
        if not r["excluded"]:
            by_group[r["group_id"]].add(r["split"])
    assert all(len(v) == 1 for v in by_group.values())
    per_case = Counter()
    by_dossier = {r["dossier_id"]: r for r in rows}
    for c in cases:
        r = by_dossier[c["dossier_id"]]
        per_case[(r["grain"], r["split"])] += 1
    out = ROOT / args.out
    write_new(out / "groups.jsonl", jsonl(rows))
    summary = {"created_at_utc": now(), "inputs_sha256": {n: sha256(run / "inputs" / n) for n in ("products.jsonl", "cases.jsonl")},
               "grain_rule": {"old_dataset": "pair_id present in sku-observed-product-pairs-20261010-final-v4",
                              "new_sku_existing_product": "new pair whose AU item or Rakuten product was captured before 2026-10-11",
                              "new_product_group": "new pair with both products first captured on 2026-10-11"},
               "group_rule": "connected components over pair, AU item, Rakuten manage number and its series prefix "
                             "(manage number without trailing digits+letters); split = sha1(group) parity",
               "pairs_by_grain_split": {f"{g}/{s}": n for (g, s), n in sorted(Counter((r["grain"], r["split"]) for r in rows).items())},
               "cases_by_grain_split": {f"{g}/{s}": n for (g, s), n in sorted(per_case.items())},
               "excluded_pairs": dict(Counter(r["excluded"] for r in rows if r["excluded"])),
               "exclude_existing_rakuten": bool(args.exclude_existing_rakuten),
               "groups": len(by_group)}
    write_new(out / "groups-summary.json", json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def refs_for(ctx: dict, cases: list[dict]) -> tuple[list[str], dict]:
    """Render one product pair with reference ids; return lines and {ref: span-like source}."""
    refs, lines = {}, []
    au, rk = ctx["au_product"], ctx["rakuten_product"]
    refs["T_au"] = {"text": au["title"], "span": au.get("title_span")}
    refs["T_rk"] = {"text": rk["title"], "span": rk.get("title_span"), "raw_file": rk["raw_file"], "sha256": rk["sha256"]}
    lines += [f"AUの商品名 [T_au]: {au['title']}", f"楽天の商品名 [T_rk]: {rk['title']}", "AUの行（どれか1つを買う）:"]
    for i, row in enumerate(ctx["au_rows"], 1):
        ref = f"a{i}"
        text = " / ".join(f"{a['axis_name']}={a['value']}" for a in row["axes"])
        refs[ref] = {"text": text, "row_key": row["row_key"], "axes": [{"axis": a["axis_name"], "value": a["value"], "span": a.get("value_span")}
                                                                       for a in row["axes"]]}
        lines.append(f"  {ref}: {text}")
    lines.append("楽天のSKU（判定する対象）:")
    for i, case in enumerate(cases, 1):
        ref = f"s{i}"
        axes = case["rakuten_selected"]["axes"]
        text = " / ".join(f"{a['axis_label']}={a['value']}" for a in axes)
        refs[ref] = {"text": text, "case_id": case["case_id"], "axes": [{"axis": a["axis_label"], "value": a["value"], "span": a.get("value_span")}
                                                                        for a in axes]}
        lines.append(f"  {ref}: {text}")
    for key, prefix, title in (("au_purchase_option_lines", "ap", "AUの購入オプション"), ("au_description_lines", "ad", "AUの説明"),
                               ("rakuten_description_lines", "rd", "楽天の説明")):
        items = ctx.get(key) or []
        if items:
            lines.append(f"{title}:")
        for i, item in enumerate(items, 1):
            ref = f"{prefix}{i}"
            refs[ref] = {"text": item["text"], "span": item.get("span")}
            lines.append(f"  {ref}: {item['text']}")
    return lines, refs


def batches(args) -> None:
    run, out = ROOT / args.run, ROOT / args.out
    groups_rows = {r["dossier_id"]: r for r in read_jsonl(ROOT / args.groups)}
    contexts = {c["dossier_id"]: c for c in read_jsonl(run / "inputs/products.jsonl")}
    by_dossier = defaultdict(list)
    for c in read_jsonl(run / "inputs/cases.jsonl"):
        by_dossier[c["dossier_id"]].append(c)
    units, blocks = [], []
    order = sorted(by_dossier, key=lambda d: hashlib.sha1(f"{args.seed}:{d}".encode()).hexdigest())
    for i, d in enumerate(order, 1):
        if groups_rows[d].get("excluded") or (args.grain and groups_rows[d]["grain"] not in args.grain):
            continue
        uid = f"p{i:04d}"
        lines, refs = refs_for(contexts[d], by_dossier[d])
        units.append({"unit": uid, "dossier_id": d, "refs": refs})
        blocks.append((uid, len(by_dossier[d]), f"## {uid}\n" + "\n".join(lines)))
    batch, size, batch_no, manifest = [], 0, 0, {}
    for uid, n, text in blocks + [(None, 0, None)]:
        if uid is None or (batch and size + n > args.per_batch):
            if batch:
                batch_no += 1
                write_new(out / f"batch-{batch_no:03d}.md", "\n\n".join(t for _, t in batch) + "\n")
                manifest[f"batch-{batch_no:03d}"] = [u for u, _ in batch]
            batch, size = [], 0
        if uid is not None:
            batch.append((uid, text))
            size += n
    write_new(out / "private/units.jsonl", jsonl(units))
    write_new(out / "private/batches.json", json.dumps({"run": args.run, "seed": args.seed, "per_batch": args.per_batch,
                                                        "inputs_sha256": {n: sha256(run / "inputs" / n) for n in ("products.jsonl", "cases.jsonl")},
                                                        "batches": manifest}, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"units": len(units), "cases": sum(n for _, n, _ in blocks), "batches": batch_no}, ensure_ascii=False))


def check(args) -> None:
    out = ROOT / args.labels
    units = {u["unit"]: u for u in read_jsonl(out / "private/units.jsonl")}
    answers, issues = {}, Counter()
    for f in sorted(out.glob("answers-*.jsonl")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                a = json.loads(line)
            except json.JSONDecodeError:
                issues["broken_json"] += 1
                continue
            key = str(a.get("s", ""))
            if key in answers:
                issues["duplicate"] += 1
            answers[key] = {**a, "_file": f.name, "_line": n}
    resolved, problems = [], []
    for uid, unit in units.items():
        refs = unit["refs"]
        for ref, src in refs.items():
            if not ref.startswith("s"):
                continue
            key = f"{uid}-{ref}"
            a = answers.get(key)
            row = {"case_id": src["case_id"], "dossier_id": unit["dossier_id"], "unit": uid, "sku_ref": ref,
                   "label_source": "machinegold:claude-haiku-5-5", "is_independent_human_gold": False}
            if a is None:
                problems.append({**row, "problem": "missing"})
                continue
            expected, rows_ = a.get("expected"), a.get("au_rows") or []
            flag = a.get("data_quality_flag")
            bad = []
            if flag not in (None, "conflict", "unclear", "extraction_incomplete"):
                bad.append(f"flag:{flag}")
            if flag is None and expected not in ("accept", "drop"):
                bad.append(f"expected:{expected}")
            if expected == "accept" and not rows_:
                bad.append("accept_without_row")
            if expected == "drop" and rows_:
                bad.append("drop_with_row")
            row_keys = []
            for r in rows_:
                if r in refs and r.startswith("a"):
                    row_keys.append(refs[r]["row_key"])
                else:
                    bad.append(f"row_ref:{r}")
            evidence = []
            for item in a.get("rationale") or []:
                ref_id, quote = str(item.get("ref", "")), str(item.get("quote", ""))
                src_ref = refs.get(ref_id)
                if not src_ref or not quote or quote not in src_ref["text"]:
                    bad.append(f"quote_unresolved:{ref_id}")
                    continue
                evidence.append({"ref": ref_id, "quote": quote, "offset_in_text": src_ref["text"].index(quote),
                                 "source": {k: v for k, v in src_ref.items() if k != "text"}})
            if not evidence and flag is None:
                bad.append("no_resolved_rationale")
            row.update({"expected": expected if flag is None else None, "au_row_keys": row_keys, "data_quality_flag": flag,
                        "rationale": evidence, "answer_file": a["_file"], "answer_line": a["_line"]})
            if bad:
                problems.append({**row, "problem": bad})
            else:
                resolved.append(row)
    unknown = set(answers) - {f"{u}-{r}" for u, x in units.items() for r in x["refs"] if r.startswith("s")}
    report = {"cases": sum(1 for u in units.values() for r in u["refs"] if r.startswith("s")), "answers": len(answers),
              "resolved": len(resolved), "problems": len(problems), "unknown_answers": len(unknown), "issues": dict(issues),
              "problem_kinds": dict(Counter(p if isinstance(p, str) else p.split(":")[0]
                                            for x in problems for p in (x["problem"] if isinstance(x["problem"], list) else [x["problem"]]))),
              "expected": dict(Counter(r["expected"] for r in resolved)), "flags": dict(Counter(r["data_quality_flag"] for r in resolved))}
    (out / "private/resolved.jsonl").write_text(jsonl(resolved), encoding="utf-8")
    (out / "private/problems.jsonl").write_text(jsonl(problems), encoding="utf-8")
    (out / "private/check.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


def freeze(args) -> None:
    out = ROOT / args.labels
    meta = json.loads((out / "private/batches.json").read_text(encoding="utf-8"))
    files = sorted(out.glob("answers-*.jsonl"))
    manifest = {"frozen_at_utc": now(), "label_source": "machinegold:claude-haiku-5-5 (machine annotation, not independent human gold)",
                "run": meta["run"], "inputs_sha256": meta["inputs_sha256"],
                "answers_sha256": {f.name: sha256(f) for f in files},
                "resolved_sha256": sha256(out / "private/resolved.jsonl"), "check": json.loads((out / "private/check.json").read_text(encoding="utf-8")),
                "code_sha256": sha256(Path(__file__))}
    write_new(out / "private/label-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: manifest[k] for k in ("frozen_at_utc", "resolved_sha256")}, ensure_ascii=False))


def score(args) -> None:
    out = ROOT / args.labels
    manifest = json.loads((out / "private/label-manifest.json").read_text(encoding="utf-8"))
    if sha256(out / "private/resolved.jsonl") != manifest["resolved_sha256"]:
        raise RuntimeError("labels changed after freeze")
    labels = {r["case_id"]: r for r in read_jsonl(out / "private/resolved.jsonl")}
    groups_rows = {r["dossier_id"]: r for r in read_jsonl(ROOT / args.groups)}
    decisions = read_jsonl(ROOT / args.align / "align/decisions.jsonl")
    table = Counter()
    for x in decisions:
        g = groups_rows[x["dossier_id"]]
        lab = labels.get(x["case_id"])
        if lab is None:
            outcome = "unlabelled"
        elif lab["data_quality_flag"]:
            outcome = f"quality_flag:{lab['data_quality_flag']}"
        elif x["decision"] == "matched":
            outcome = ("accept_same_row" if lab["expected"] == "accept" and x["top_row_key"] in lab["au_row_keys"] else
                       "accept_other_row" if lab["expected"] == "accept" else "drop_but_adopted")
        else:
            outcome = f"{lab['expected']}/{x['reason']}"
        table[(g["split"], g["grain"], outcome)] += 1
    result = {"created_at_utc": now(), "labels": manifest["label_source"], "label_manifest_sha256": sha256(out / "private/label-manifest.json"),
              "align_summary": json.loads((ROOT / args.align / "align/summary.json").read_text(encoding="utf-8")),
              "by_split_grain_outcome": {"/".join(k): n for k, n in sorted(table.items())}}
    write_new(ROOT / args.out, json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(result["by_split_grain_outcome"], ensure_ascii=False, indent=1))


def main() -> None:
    sys.path.insert(0, str(HERE))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="step", required=True)
    p = sub.add_parser("pairs")
    p.add_argument("--out", required=True)
    p = sub.add_parser("token-pairs")
    p.add_argument("--pairs", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("inputs")
    p.add_argument("status", choices=sorted(STATUS))
    p.add_argument("--pairs", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("groups")
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--pairs", help="pairs dir whose links also mark AU items tied to existing Rakuten URLs")
    p.add_argument("--exclude-existing-rakuten", action="store_true",
                   help="exclude pairs with an already captured Rakuten URL and pairs whose AU item is linked to one")
    p = sub.add_parser("batches")
    p.add_argument("--run", required=True)
    p.add_argument("--groups", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--per-batch", type=int, default=150)
    p.add_argument("--seed", default="20261011")
    p.add_argument("--grain", action="append")
    for name in ("check", "freeze"):
        p = sub.add_parser(name)
        p.add_argument("--labels", required=True)
    p = sub.add_parser("score")
    p.add_argument("--labels", required=True)
    p.add_argument("--groups", required=True)
    p.add_argument("--align", required=True)
    p.add_argument("--out", required=True)
    args = parser.parse_args()
    {"pairs": pairs, "token-pairs": token_pairs, "inputs": inputs, "groups": groups, "batches": batches, "check": check, "freeze": freeze,
     "score": score}[args.step](args)


if __name__ == "__main__":
    main()
