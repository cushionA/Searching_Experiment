#!/usr/bin/env python3
"""independent_eval.py token-pairs with confirmed shop aliases.

independent_eval.token_pairs keeps a candidate_review pair only when the manage number is the unique token of the AU
primary image and the ASCII letters of the AU shop name equal the Rakuten store slug (a name without ASCII passes).
Shops confirmed by collect_multishop.py qualify2 (manage-number token plus title agreement on two items) use a slug that
differs from their name (or a mixed name), so this copy also accepts a pair when the slug equals the confirmed alias of
the AU shop id. Everything else is the same as the original function, and the output has the same layout.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import collect_multishop as ms  # noqa: E402
import independent_eval as ie  # noqa: E402

ROOT = ie.ROOT


def aliases() -> dict[str, str]:
    out = {}
    for cat in ms.CATEGORIES:
        for sid, t in ms.qualified(cat).items():
            out.setdefault(sid, (t.get("slug") or ms.slug_of(t["shop_name"])).casefold())
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import build_observed_pair_records as b
    alias = aliases()
    src, out = ROOT / args.pairs, ROOT / args.out
    if out.exists():
        raise FileExistsError(out)
    command = json.loads((src / "build-command.json").read_text(encoding="utf-8"))
    raku, _ = b.rake_rows_from_roots([ROOT / d for d in command["rakuten_dirs"]])
    sku_products = {str(r["manage_number"]): r for r in raku if r.get("manage_number") and int(r.get("sku_count") or 0) > 0}
    rows = ie.read_jsonl(src / "candidate_sku_review.jsonl")
    by_pair = defaultdict(list)
    for r in rows:
        if r["pair_status"] == ie.STATUS["candidate"]:
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
        confirmed_alias = alias.get(str(info.get("shopUserId", ""))) == slug
        if shop and shop != re.sub(r"[^a-z0-9]", "", slug) and not confirmed_alias:
            skipped["shop_mismatch"] += 1
            continue
        ev = {"primary_image_url": images[0], "primary_image_json_path": "$.itemInfo.productImageUrls[0]",
              "au_raw_item": recs[0]["provenance"]["au_raw_item"], "primary_image_basename": base, "manage_number_token": manage,
              "rakuten_store_slug": slug, "au_shop_name": info.get("shopName"), "shop_alias_confirmed": confirmed_alias,
              "mapping_basis": "unique Rakuten SKU-product manage token in AU primary image basename + same shop "
                               "(name rule or collect_multishop qualify2 alias); no product-type term; review mapping, "
                               "identity checked by the annotator"}
        evidence.append({"pair_id": pair_id, "sku_records": len(recs), **ev})
        kept += [{**r, "pair_status": ie.STATUS["token"], "source_pair_status": r["pair_status"], "token_evidence": ev} for r in recs]
    out.mkdir(parents=True)
    arrays = (src / "au_product_sku_arrays.jsonl").read_bytes()
    (out / "au_product_sku_arrays.jsonl").write_bytes(arrays)
    ie.write_new(out / "candidate_sku_review.jsonl", ie.jsonl(kept))
    ie.write_new(out / "token_pairs.jsonl", ie.jsonl(evidence))
    manifest = {"created_at_utc": ie.now(), "source_pairs_dir": args.pairs, "pair_status": ie.STATUS["token"],
                "candidate_review_pairs_examined": len(by_pair), "pairs_kept": len(evidence), "sku_records_kept": len(kept),
                "skipped": dict(skipped), "rakuten_sku_products": len(sku_products),
                "alias_pairs": sum(1 for e in evidence if e["shop_alias_confirmed"]),
                "sha256": {"source_candidate_sku_review.jsonl": ie.sha256(src / "candidate_sku_review.jsonl"),
                           "au_product_sku_arrays.jsonl": hashlib.sha256(arrays).hexdigest(),
                           "candidate_sku_review.jsonl": ie.sha256(out / "candidate_sku_review.jsonl")},
                "code_sha256": ie.sha256(Path(__file__))}
    ie.write_new(out / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: manifest[k] for k in ("candidate_review_pairs_examined", "pairs_kept", "sku_records_kept", "alias_pairs", "skipped")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
