#!/usr/bin/env python3
"""Probe generic classical text/units libraries on fixed Japanese snippets.

This is a capability probe only: no SKU labels, gold data, matchers, classifiers,
or neural models are read or used.  The optional Quantulum3 classifier is
explicitly disabled.  Outputs are written only beneath the selected .lab-output
directory; a .NET Recognizers-Text scaffold is emitted there without restoring
packages or invoking dotnet.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXAMPLES = [
    {"id": "metric_equivalents", "text": "100cm / 1m / 1000mm"},
    {"id": "dimensions_pair", "text": "幅100×丈80cm"},
    {"id": "weight_and_pack_count", "text": "250g×2袋"},
    {"id": "piece_count", "text": "4枚組"},
    {"id": "plus_minus", "text": "100±5cm"},
    {"id": "approximation", "text": "約100cm"},
    {"id": "negative_condition", "text": "レースなし/別売"},
    {"id": "color_synonyms", "text": "ブルー/青/水色"},
    {"id": "kanji_variant", "text": "附属/付属"},
    {"id": "unicode_variants", "text": "全角/①/Mvsｍ"},
]

PAIR_CHECKS = [
    ("ブルー", "青"),
    ("ブルー", "水色"),
    ("青", "水色"),
    ("附属", "付属"),
    ("M", "ｍ"),
]

PACKAGE_NAMES = {
    "pint": "Pint",
    "jaconv": "jaconv",
    "rapidfuzz": "RapidFuzz",
    "quantulum3": "quantulum3",
    "SudachiPy": "SudachiPy",
    "SudachiDict-core": "SudachiDict-core",
    "spacy": "spaCy",
}


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)


def capture_library(name: str, fn) -> dict[str, Any]:
    try:
        return {"status": "ok", "result": json_safe(fn())}
    except Exception as exc:  # API failures are evidence in this probe.
        error_type = type(exc).__name__
        return {
            "status": "unsupported" if error_type == "UndefinedUnitError" else "error",
            "error_type": error_type,
            "error": str(exc),
        }


def probe_pint() -> dict[str, Any]:
    try:
        import pint
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}

    ureg = pint.UnitRegistry()
    out: dict[str, Any] = {
        "status": "ok",
        "role": "unit parsing/conversion, no text span extraction",
        "raw_snippet_calls": [],
        "canonical_unit_calls": [],
        "offset_temperature_calls": [],
    }
    for example in EXAMPLES:
        outcome = capture_library(
            "pint raw string",
            lambda text=example["text"]: str(ureg(text)),
        )
        out["raw_snippet_calls"].append(
            {"example_id": example["id"], "input": example["text"], **outcome}
        )
    for expression in ("100 cm", "1 m", "1000 mm", "250 g"):
        def convert(expr: str = expression) -> dict[str, str]:
            quantity = ureg(expr)
            target = "meter" if "m" in expr and "g" not in expr else "gram"
            normalized = quantity.to(target)
            return {
                "input": expr,
                "normalized": str(normalized),
                "target_unit": target,
            }

        out["canonical_unit_calls"].append(capture_library("pint conversion", convert))
    out["offset_temperature_calls"].append(
        capture_library(
            "pint offset temperature quantity",
            lambda: {
                "input": "20 degC",
                "kelvin": str(ureg.Quantity(20, ureg.degC).to("kelvin")),
            },
        )
    )
    out["offset_temperature_calls"].append(
        capture_library("pint localized Celsius surface", lambda: str(ureg("20℃")))
    )
    return out


def probe_jaconv() -> dict[str, Any]:
    try:
        import jaconv
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    results = []
    for example in EXAMPLES:
        text = example["text"]
        results.append(
            {
                "example_id": example["id"],
                "input": text,
                "z2h_ascii_digit": jaconv.z2h(
                    text, kana=False, digit=True, ascii=True
                ),
                "unicode_nfkc": unicodedata.normalize("NFKC", text),
            }
        )
    return {
        "status": "ok",
        "role": "character normalization only; no entity or semantic extraction",
        "results": results,
    }


def probe_rapidfuzz() -> dict[str, Any]:
    try:
        from rapidfuzz.fuzz import ratio
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    return {
        "status": "ok",
        "role": "literal string similarity only; no semantic classifier",
        "pairs": [
            {"left": left, "right": right, "ratio": ratio(left, right)}
            for left, right in PAIR_CHECKS
        ],
    }


def probe_quantulum3() -> dict[str, Any]:
    try:
        from quantulum3 import parser
        import quantulum3.classifier as classifier_api
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    if not hasattr(classifier_api, "USE_CLF"):
        return {
            "status": "unsupported_api",
            "role": "quantity parser; cannot confirm a classifier-off control for this version",
            "parse_signature": str(__import__("inspect").signature(parser.parse)),
        }

    # quantulum3 v0.10 removed the old use_classifier keyword.  Its supported
    # control is the classifier module flag.  Force it off around all parse
    # calls, then restore the caller's setting.  This also prevents loading or
    # invoking any optional sklearn model if installed elsewhere.
    prior_use_clf = classifier_api.USE_CLF
    classifier_api.USE_CLF = False
    outputs = []
    try:
        for example in EXAMPLES:
            def parse_no_classifier(text: str = example["text"]) -> list[dict[str, Any]]:
                quantities = parser.parse(text, lang="en_US", classifier_path=None)
                normalized = []
                for q in quantities:
                    unit = getattr(q, "unit", None)
                    span = getattr(q, "span", None)
                    normalized.append(
                        {
                            "surface": getattr(q, "surface", None),
                            "value": getattr(q, "value", None),
                            "uncertainty": getattr(q, "uncertainty", None),
                            "unit": getattr(unit, "name", None),
                            "entity": getattr(getattr(unit, "entity", None), "name", None),
                            "span": span,
                        }
                    )
                return normalized

            outputs.append(
                {
                    "example_id": example["id"],
                    "input": example["text"],
                    "classifier_enabled": False,
                    **capture_library("quantulum3.parse", parse_no_classifier),
                }
            )
    finally:
        classifier_api.USE_CLF = prior_use_clf
    failures = sum(item["status"] != "ok" for item in outputs)
    return {
        "status": "ok" if failures == 0 else ("error" if failures == len(outputs) else "partial"),
        "role": "quantity parser; quantulum3.classifier.USE_CLF forced false during calls",
        "language_argument": "en_US (fixed; no claim of Japanese support)",
        "parse_signature": str(__import__("inspect").signature(parser.parse)),
        "classifier_flag_before": prior_use_clf,
        "classifier_flag_during_calls": False,
        "classifier_flag_restored": classifier_api.USE_CLF == prior_use_clf,
        "failed_case_count": failures,
        "results": outputs,
    }


def probe_sudachi() -> dict[str, Any]:
    try:
        from sudachipy import dictionary, tokenizer
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    try:
        mode = tokenizer.Tokenizer.SplitMode.C
        tok = dictionary.Dictionary().tokenizer()
    except Exception as exc:
        return {"status": "error", "error_type": type(exc).__name__, "error": str(exc)}
    outputs = []
    for example in EXAMPLES:
        def tokenize(text: str = example["text"]) -> list[dict[str, Any]]:
            return [
                {
                    "surface": m.surface(),
                    "normalized_form_candidate": m.normalized_form(),
                    "dictionary_form": m.dictionary_form(),
                    "begin": m.begin(),
                    "end": m.end(),
                    "part_of_speech": list(m.part_of_speech()),
                }
                for m in tok.tokenize(text, mode)
            ]

        outputs.append(
            {
                "example_id": example["id"],
                "input": example["text"],
                **capture_library("Sudachi tokenizer", tokenize),
            }
        )
    return {
        "status": "ok",
        "role": "morphological tokenization; normalized forms are candidates only",
        "split_mode": "C",
        "results": outputs,
    }


def probe_spacy_language(lang: str) -> dict[str, Any]:
    try:
        import spacy
        from spacy.matcher import Matcher
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}

    try:
        nlp = spacy.blank(lang)
        matcher = Matcher(nlp.vocab)
        units = ["cm", "mm", "kg", "g", "m"]
        matcher.add("NUMBER_WITH_UNIT", [[{"LIKE_NUM": True}, {"LOWER": {"IN": units}}]])
        matcher.add(
            "RANGE_WITH_UNIT",
            [[
                {"LIKE_NUM": True},
                {"TEXT": {"IN": ["±", "〜", "～", "~", "-", "–"]}},
                {"LIKE_NUM": True},
                {"LOWER": {"IN": units}},
            ]],
        )
    except Exception as exc:
        return {"status": "error", "error_type": type(exc).__name__, "error": str(exc)}
    outputs = []
    for example in EXAMPLES:
        def tokenize_and_match(text: str = example["text"]) -> dict[str, Any]:
            doc = nlp(text)
            matches = [
                {
                    "rule": nlp.vocab.strings[match_id],
                    "start_token": start,
                    "end_token": end,
                    "start_char": doc[start].idx,
                    "end_char": doc[end - 1].idx + len(doc[end - 1]),
                    "text": doc[start:end].text,
                }
                for match_id, start, end in matcher(doc)
            ]
            return {
                "tokens": [
                    {
                        "text": token.text,
                        "start_char": token.idx,
                        "end_char": token.idx + len(token),
                        "like_num": token.like_num,
                    }
                    for token in doc
                ],
                "matches": matches,
            }

        outputs.append(
            {
                "example_id": example["id"],
                "input": example["text"],
                **capture_library(f"spaCy blank {lang} + Matcher", tokenize_and_match),
            }
        )
    return {
        "status": "ok",
        "role": "blank tokenizer plus hand-written literal Matcher; no trained pipeline or neural model",
        "pipeline": f"spacy.blank('{lang}')",
        "trained_components": list(nlp.pipe_names),
        "matcher_rules": [
            "LIKE_NUM followed by cm/mm/kg/g/m token",
            "LIKE_NUM separator LIKE_NUM followed by cm/mm/kg/g/m token",
        ],
        "results": outputs,
    }


def probe_spacy() -> dict[str, Any]:
    return {
        "status": "ok",
        "role": "tokenizer + hand-written literal Matcher observations; no trained pipeline",
        "xx": probe_spacy_language("xx"),
        "ja": probe_spacy_language("ja"),
        "note": "xx is retained as a generic tokenizer control; ja exposes Japanese tokenization without a trained model.",
    }


RECOGNIZERS_CSPROJ = """<Project Sdk=\"Microsoft.NET.Sdk\">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include=\"Microsoft.Recognizers.Text.NumberWithUnit\" Version=\"1.8.4\" />
  </ItemGroup>
</Project>
"""

RECOGNIZERS_PROGRAM = r'''using System.Text.Json;
using Microsoft.Recognizers.Text;
using Microsoft.Recognizers.Text.NumberWithUnit;

var examples = new (string Id, string Text)[]
{
    ("metric_equivalents", "100cm / 1m / 1000mm"),
    ("dimensions_pair", "幅100×丈80cm"),
    ("weight_and_pack_count", "250g×2袋"),
    ("piece_count", "4枚組"),
    ("plus_minus", "100±5cm"),
    ("approximation", "約100cm"),
    ("negative_condition", "レースなし/別売"),
    ("color_synonyms", "ブルー/青/水色"),
    ("kanji_variant", "附属/付属"),
    ("unicode_variants", "全角/①/Mvsｍ"),
};

var results = examples.Select(example => new
{
    example.Id,
    example.Text,
    ClassifierEnabled = false,
    Results = NumberWithUnitRecognizer.RecognizeDimension(example.Text, Culture.Japanese)
        .Select(result => new
        {
            result.Text,
            result.Start,
            result.End,
            result.TypeName,
            result.Resolution,
        })
}).ToArray();

Console.WriteLine(JsonSerializer.Serialize(results, new JsonSerializerOptions { WriteIndented = true }));
'''


def write_recognizers_scaffold(base: Path) -> Path:
    target = base / "library-probes" / "recognizers"
    target.mkdir(parents=True, exist_ok=True)
    (target / "RecognizersProbe.csproj").write_text(RECOGNIZERS_CSPROJ, encoding="utf-8")
    (target / "Program.cs").write_text(RECOGNIZERS_PROGRAM, encoding="utf-8")
    (target / "README.txt").write_text(
        "Microsoft Recognizers-Text Japanese Dimension probe scaffold.\n"
        "No restore, build, network, or execution is performed by the Python probe.\n"
        "The official Japanese Dimension specs mark Python/JavaScript/Java as not supported by design;\n"
        "use this .NET probe after separately authorizing and provisioning the SDK/package.\n"
        "AST decisions such as pack count, range semantics, and product conditions are out of scope.\n",
        encoding="utf-8",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".lab-output/sku-classical-libraries-probe-v1"),
        help="safe output directory (default: .lab-output/sku-classical-libraries-probe-v1)",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    probe_results = {
        "pint": probe_pint(),
        "jaconv": probe_jaconv(),
        "rapidfuzz": probe_rapidfuzz(),
        "quantulum3": probe_quantulum3(),
        "sudachi": probe_sudachi(),
        "spacy": probe_spacy(),
    }
    recognizers_scaffold = write_recognizers_scaffold(output_dir)
    result = {
        "schema_version": "classical-library-probe-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "examples": EXAMPLES,
        "package_versions": {
            name: package_version(dist_name) for name, dist_name in PACKAGE_NAMES.items()
        },
        "policy": {
            "uses_sku_gold_or_labels": False,
            "uses_existing_matcher_or_parser": False,
            "uses_neural_model": False,
            "quantulum_classifier_enabled": False,
            "spacy_trained_pipeline_loaded": False,
            "sudachi_normalization_is_semantic_truth": False,
            "produces_pack_or_range_ast": False,
        },
        "probes": probe_results,
        "recognizers_text_dotnet_scaffold": {
            "status": "prepared_only_not_restored_or_run",
            "path": str(recognizers_scaffold),
            "target_framework": "net8.0",
            "package": "Microsoft.Recognizers.Text.NumberWithUnit 1.8.4",
            "culture": "ja-jp",
        },
        "interpretation_limit": (
            "Library outputs are direct API observations on the fixed examples, not SKU labels, "
            "semantic judgments, or a quality/accuracy evaluation."
        ),
    }
    output_path = output_dir / "probe.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
