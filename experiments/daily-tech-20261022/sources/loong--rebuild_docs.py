#!/usr/bin/env python3
"""Rebuild / verify the LOONG source documents.

The LOONG documents are not redistributed in this repo (see NOTICE). Obtain them from
the Loong benchmark source, place them under --docs-root preserving the `doc_ref` paths
from doc_manifest.jsonl, and run this script to verify your copy matches by SHA-256.

How to obtain the documents:
  Loong benchmark: https://github.com/MozerWang/Loong  (financial-report subset).
  Follow its data-download instructions, then arrange files so that each manifest
  `doc_ref` resolves to <docs-root>/<doc_ref>.

Usage:
  python rebuild_docs.py --docs-root ./loong_docs
"""
import argparse, hashlib, json, os


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=os.path.join(os.path.dirname(__file__), "doc_manifest.jsonl"))
    ap.add_argument("--docs-root", required=True, help="Directory where you placed the rebuilt documents.")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.manifest) if l.strip()]
    ok = missing = mismatch = 0
    for r in rows:
        p = os.path.join(args.docs_root, r["doc_ref"])
        if not os.path.exists(p):
            missing += 1; print(f"MISSING  {r['doc_ref']}"); continue
        if r.get("sha256") and sha256(p) != r["sha256"]:
            mismatch += 1; print(f"MISMATCH {r['doc_ref']}"); continue
        ok += 1
    print(f"\n{ok} ok, {missing} missing, {mismatch} mismatch (of {len(rows)}).")
    print("All ok -> your documents match ours; evidence_offsets.jsonl offsets are valid."
          if missing == mismatch == 0 else "Fix missing/mismatched files before using the offsets.")
    return 0 if missing == mismatch == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
