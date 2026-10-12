"""Package frozen SKU-gate runs into a portable checkpoint ZIP.

The ZIP keeps `.lab-output/...` relative paths (extract at the repository root
with `unzip -n`). An embedded manifest lists every payload SHA-256; an outer
manifest and `.sha256` sidecar pin the archive itself.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
EMBEDDED = "SKU_GATE_CHECKPOINT.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", help="run directories or single files relative to the repository root")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--label-content", required=True,
                        help="state exactly which label-derived content the runs contain")
    args = parser.parse_args()
    archive = ROOT / args.archive
    for path in (archive, archive.with_suffix(".manifest.json"), Path(str(archive) + ".sha256")):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path}")
    files = sorted({p for run in args.runs
                    for p in ([ROOT / run] if (ROOT / run).is_file() else (ROOT / run).rglob("*")) if p.is_file()})
    if not files:
        raise FileNotFoundError("Nothing to package")
    entries = {p.relative_to(ROOT).as_posix(): {"size_bytes": p.stat().st_size, "sha256": sha256(p)} for p in files}
    embedded = {"schema_version": 1, "purpose": args.purpose, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "extract_at": "repository root; preserve .lab-output relative paths; use unzip -n",
                "raw_label_files_included": False, "label_derived_content": args.label_content,
                "weights_venvs_credentials_included": False,
                "payload_file_count": len(entries), "uncompressed_payload_bytes": sum(e["size_bytes"] for e in entries.values()),
                "entries": entries}
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in entries:
            zf.write(ROOT / rel, rel)
        zf.writestr(EMBEDDED, json.dumps(embedded, ensure_ascii=False, indent=2) + "\n")
    with zipfile.ZipFile(archive) as zf:
        if zf.testzip() is not None:
            raise RuntimeError("CRC check failed")
        for rel, meta in entries.items():
            if hashlib.sha256(zf.read(rel)).hexdigest() != meta["sha256"]:
                raise RuntimeError(f"Payload SHA mismatch: {rel}")
        embedded_sha = hashlib.sha256(zf.read(EMBEDDED)).hexdigest()
    digest = sha256(archive)
    outer = {"archive": archive.name, "size_bytes": archive.stat().st_size, "sha256": digest,
             "embedded_manifest": EMBEDDED, "embedded_manifest_sha256": embedded_sha,
             "payload_file_count": len(entries), "uncompressed_payload_bytes": embedded["uncompressed_payload_bytes"],
             "crc_and_all_payload_sha256_verified": True, "checkpoint": {k: v for k, v in embedded.items() if k != "entries"}}
    archive.with_suffix(".manifest.json").write_text(json.dumps(outer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(str(archive) + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(json.dumps({k: outer[k] for k in ("archive", "size_bytes", "sha256", "payload_file_count",
                                            "uncompressed_payload_bytes")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
