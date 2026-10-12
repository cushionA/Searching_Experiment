#!/usr/bin/env python3
"""Run build_observed_pair_records.py on the folded multi-shop selection, with the selection's links as review candidates.

The pair builder only proposes a candidate for product types in its fixed term lists, so fashion products from other
shops all end as `unmatched`. This runner leaves that file unchanged and replaces one function for the run: a pair is a
`candidate_review` candidate when it is a kept link of the selection (the collector found the Rakuten product by the
manage-number token of the AU primary image, and the AU shop name equals the Rakuten store slug). The candidate stays a
review mapping; independent_eval.py token-pairs then re-checks the token and shop, and an annotator checks identity.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_observed_pair_records as builder  # noqa: E402

ROOT = HERE.parents[1]


def link_raw(sel: Path) -> None:
    """The pair builder reads <au dir>/raw/<id>-item.json; hard-link the originals in (same bytes, nothing is moved)."""
    run = ROOT / ".lab-output/sku-independent-real-20261011"
    dest = ROOT / sel / "au" / "raw"
    dest.mkdir(parents=True, exist_ok=True)
    ids = {str(json.loads(l)["item_id"]) for l in (ROOT / sel / "au/products.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    for item_id in ids:
        for suffix in ("item", "options"):
            target = dest / f"{item_id}-{suffix}.json"
            if target.exists():
                continue
            source = next((p for cat in ("apparel", "food", "beauty") if (p := run / cat / "au/raw" / f"{item_id}-{suffix}.json").exists()), None)
            if source is not None:
                os.link(source, target)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selection", required=True, help="folder with links.jsonl, au/ and rakuten/ (select_multishop.py output)")
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()
    sel = Path(args.selection)
    kept = {(str(r["au_item_id"]), r["rakuten_url"]) for r in
            (json.loads(l) for l in (ROOT / sel / "links.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())
            if r["decision"] == "kept"}
    link_raw(sel)
    original = builder.candidate_reasons

    def candidate_reasons(au, raku, au_root):
        if (str(au.get("item_id")), raku.get("source_url")) in kept:
            return ["multishop selection link: Rakuten product found by the AU primary-image manage-number token in the same shop"]
        return original(au, raku, au_root)

    builder.candidate_reasons = candidate_reasons
    argv = ["build_observed_pair_records.py", "--output-dir", args.output_dir,
            "--au-dir", (sel / "au").as_posix(), "--rakuten-dir", (sel / "rakuten").as_posix()]
    sys.argv = argv
    code = builder.main()
    (ROOT / args.output_dir / "build-command.json").write_text(json.dumps(
        {"cmd": [sys.executable, "-B", "experiments/sku-matching/build_multishop_pairs.py", "--selection", args.selection,
                 "--output-dir", args.output_dir],
         "au_dirs": [(sel / "au").as_posix()], "rakuten_dirs": [(sel / "rakuten").as_posix()],
         "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
