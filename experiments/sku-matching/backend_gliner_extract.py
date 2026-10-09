#!/usr/bin/env python3
"""Local GLiNER2.5 attribute extraction adapter for SKU experiments.

The adapter returns candidate attributes alongside their source spans and the
unaltered GLiNER2 output. It does not canonicalize product values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Literal


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODEL_DIR = ROOT / ".deps" / "sku-gliner-extract-model"
REPO = "fastino/gliner2.5-multi-v1"
REVISION = "cf5593a5d45e3bbf204b9df621b13b1c0cf25ee3"

# HF model API/tree values at REVISION. The LFS SHA-256 values were independently
# verified while downloading; regular Git files use downloaded-content SHA-256.
FILES: dict[str, dict[str, Any]] = {
    "model.safetensors": {
        "size_bytes": 1_149_461_028,
        "sha256": "c1ff4ec0bc00031c15530b8f3c33d3677f27949e6a0cb52e1247a6224b6c5395",
    },
    "tokenizer.json": {
        "size_bytes": 16_035_853,
        "sha256": "c62446df87ae18ec98b133f8f84fc449a07cc89bbf8ef192a4cb5f9c53777a7a",
    },
    "config.json": {
        "size_bytes": 4_104,
        "sha256": "3e4a96bf01da1094180f2d8998411c1a9fd6e2ae4b5b3e4bc6950318cee95bb7",
    },
    "encoder_config/config.json": {
        "size_bytes": 857,
        "sha256": "fa4f9ef2903b5369ab172333aae4574e6a476511d7465845cf59f8360ee18716",
    },
    "tokenizer_config.json": {
        "size_bytes": 645,
        "sha256": "0bf3ea0873234bd9bfdd3853c440395009ac6365a925b91654daed5396d655e1",
    },
}

FIELDS = ("width_cm", "height_cm", "color", "lace", "pieces")
ENTITY_DESCRIPTIONS = {
    "ja": {
        "width_cm": "選択されたSKUの幅寸法。幅×丈の2値表記は最初の値だけを対象にし、シリーズ全体のサイズ候補を列挙した説明から選択外の幅を拾わない。",
        "height_cm": "選択されたSKUの丈または高さ寸法。幅×丈の2値表記は2番目の値だけを対象にし、シリーズ全体のサイズ候補を列挙した説明から選択外の丈を拾わない。",
        "color": "商品の色名、色柄の名前。色違いを区別する原文の範囲を抽出する。",
        "lace": "この選択SKUにレースカーテンが含まれるかの明示的な選択肢。あり・なし、付属・非付属などの状態語だけを抽出する。レースカーテンという商品種類名だけから有無を推定しない。",
        "pieces": "商品に含まれる枚数、個数、組数、セット数。数量を示す原文の範囲を抽出する。",
    },
    "en": {
        "width_cm": "The width dimension of the selected SKU. For a width-by-drop pair, use only the first component. Do not extract other size choices listed for the wider product series.",
        "height_cm": "The curtain drop or height dimension of the selected SKU. For a width-by-drop pair, use only the second component. Do not extract other size choices listed for the wider product series.",
        "color": "A product color or color-pattern name. Extract the exact source span that distinguishes color variants.",
        "lace": "An explicit selected-SKU option stating whether a lace curtain is included or not included. Extract an inclusion or exclusion status only. Never infer inclusion from a product type such as lace curtain or sheer curtain.",
        "pieces": "The number of pieces, panels, items, or sets included in the product. Extract the exact source span that gives the quantity.",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_snapshot(model_dir: Path = MODEL_DIR) -> dict[str, dict[str, Any]]:
    """Require every expected local artifact to match its pinned size and SHA-256."""
    verified: dict[str, dict[str, Any]] = {}
    for name, expected in FILES.items():
        path = model_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"Pinned local model file is missing: {path}")
        size, digest = path.stat().st_size, _sha256(path)
        if size != expected["size_bytes"] or digest != expected["sha256"]:
            raise RuntimeError(f"Pinned local model integrity check failed: {name}")
        verified[name] = {"size_bytes": size, "sha256": digest}
    return verified


def download_snapshot(model_dir: Path = MODEL_DIR) -> dict[str, Any]:
    """Fetch only the checkpoint, tokenizer, and configs at the pinned revision."""
    model_dir.mkdir(parents=True, exist_ok=True)
    verified: dict[str, Any] = {}
    total_bytes = sum(item["size_bytes"] for item in FILES.values())
    if total_bytes >= 1_300_000_000:
        raise RuntimeError(f"Pinned download exceeds the 1.3 GB cap: {total_bytes}")

    for name, expected in FILES.items():
        target = model_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            size, digest = target.stat().st_size, _sha256(target)
            if size != expected["size_bytes"] or digest != expected["sha256"]:
                raise RuntimeError(f"Existing file does not match pinned revision: {target}")
        else:
            url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
            request = urllib.request.Request(url, headers={"User-Agent": "sku-gliner-attribute-extract/1"})
            partial = target.with_name(target.name + ".partial")
            digest = hashlib.sha256()
            size = 0
            with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
                while chunk := response.read(8 * 1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            actual = digest.hexdigest()
            if size != expected["size_bytes"] or actual != expected["sha256"]:
                partial.unlink(missing_ok=True)
                raise RuntimeError(f"Pinned integrity check failed for {name}: {size} bytes sha256={actual}")
            os.replace(partial, target)
            size, digest = target.stat().st_size, actual
            print(f"verified {name}: {size:,} bytes sha256={digest}", flush=True)
        verified[name] = {"size_bytes": size, "sha256": digest}

    manifest = {"repo": REPO, "revision": REVISION, "files": verified}
    (model_dir / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _schema(model: Any, label_style: Literal["ja", "en"] = "ja") -> Any:
    if label_style not in ENTITY_DESCRIPTIONS:
        raise ValueError("label_style must be 'ja' or 'en'")
    return model.create_schema().entities(ENTITY_DESCRIPTIONS[label_style])


class GLiNERAttributeExtractor:
    """Batch extraction with evidence spans; pass an already loaded model if desired."""

    def __init__(
        self,
        model_dir: Path = MODEL_DIR,
        *,
        label_style: Literal["ja", "en"] = "ja",
        model: Any | None = None,
    ) -> None:
        if label_style not in ENTITY_DESCRIPTIONS:
            raise ValueError("label_style must be 'ja' or 'en'")
        self.model_dir = Path(model_dir)
        self.label_style = label_style
        if model is None:
            verify_snapshot(self.model_dir)
            from gliner2 import AutoExtractor

            # Local configs include encoder_config; prevent implicit Hub lookup.
            model = AutoExtractor.from_pretrained(str(self.model_dir), local_files_only=True)
        self.model = model
        self.schema = _schema(self.model, label_style)

    def extract(self, texts: list[str], batch_size: int = 8) -> list[dict[str, Any]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if any(not isinstance(text, str) for text in texts):
            raise TypeError("texts must contain only strings")
        if not texts:
            return []

        raw_outputs = self.model.batch_extract(
            texts,
            self.schema,
            batch_size=batch_size,
            include_spans=True,
            include_confidence=True,
        )
        results: list[dict[str, Any]] = []
        for text, raw in zip(texts, raw_outputs, strict=True):
            entities = raw.get("entities", {})
            values: dict[str, list[str]] = {}
            evidence: dict[str, list[dict[str, Any]]] = {}
            for field in FIELDS:
                entries = entities.get(field, []) or []
                values[field] = [entry["text"] if isinstance(entry, dict) else str(entry) for entry in entries]
                evidence[field] = []
                for entry in entries:
                    if isinstance(entry, dict):
                        span = {
                            key: entry[key]
                            for key in ("text", "start", "end", "confidence")
                            if key in entry
                        }
                        if "start" in span and "end" in span:
                            span["source_text"] = text[span["start"] : span["end"]]
                        evidence[field].append(span)
                    else:
                        value = str(entry)
                        start = text.find(value)
                        evidence[field].append(
                            {"text": value, "start": start if start >= 0 else None,
                             "end": start + len(value) if start >= 0 else None,
                             "source_text": value if start >= 0 else None}
                        )
            results.append({
                "attributes": values,
                "evidence": evidence,
                "raw_output": raw,
            })
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="download and verify the pinned local snapshot")
    parser.add_argument("--label-style", choices=("ja", "en"), default="ja")
    parser.add_argument("--text", action="append", default=[], help="input description; may be repeated")
    args = parser.parse_args()
    if args.download:
        print(json.dumps(download_snapshot(), ensure_ascii=False, indent=2))
    if args.text:
        extractor = GLiNERAttributeExtractor(label_style=args.label_style)
        print(json.dumps(extractor.extract(args.text), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
