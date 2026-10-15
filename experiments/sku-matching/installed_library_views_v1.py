#!/usr/bin/env python3
"""Expose direct installed-library normalization/tokenizer observations."""
from __future__ import annotations

from importlib import metadata
from time import perf_counter
from typing import Any


SCHEMA = "installed-library-views-v1"


def _span(start: int, end: int) -> list[int]:
    return [start, end]


def build_views(values: list[str]) -> dict[str, Any]:
    """Run native APIs on full input strings and retain their source spans."""
    if any(not isinstance(value, str) for value in values):
        raise TypeError("values must contain only strings")

    init_start = perf_counter()
    import jaconv
    import spacy
    from sudachipy import dictionary, tokenizer

    sudachi = dictionary.Dictionary().create()
    mode_c = tokenizer.Tokenizer.SplitMode.C
    sudachi.tokenize("", mode_c)
    nlp = spacy.blank("ja")
    initialize_seconds = perf_counter() - init_start

    unique = list(dict.fromkeys(values))
    views: dict[str, dict[str, str]] = {
        "raw": {}, "width_jaconv": {}, "sudachi_norm": {}, "spacy_norm": {},
    }
    observations: dict[str, dict[str, Any]] = {name: {} for name in views}
    batch_start = perf_counter()
    for value in unique:
        width = jaconv.z2h(value, kana=False, ascii=True, digit=True)
        sudachi_morphemes = sudachi.tokenize(value, mode_c)
        spacy_doc = nlp(value)
        sudachi_tokens = [
            {"surface": morpheme.surface(),
             "normalized_form": morpheme.normalized_form(),
             "start": morpheme.begin(), "end": morpheme.end(),
             "source_span": _span(morpheme.begin(), morpheme.end())}
            for morpheme in sudachi_morphemes
        ]
        spacy_tokens = [
            {"surface": token.text, "norm": token.norm_, "start": token.idx,
             "end": token.idx + len(token.text),
             "source_span": _span(token.idx, token.idx + len(token.text))}
            for token in spacy_doc
        ]
        sudachi_norm = " ".join(token["normalized_form"] for token in sudachi_tokens)
        spacy_norm = " ".join(token["norm"] for token in spacy_tokens)
        outputs = {"raw": value, "width_jaconv": width,
                   "sudachi_norm": sudachi_norm, "spacy_norm": spacy_norm}
        for name, output in outputs.items():
            views[name][value] = output
        observations["raw"][value] = {
            "output": value,
            "tokens": [{"surface": value, "norm": value, "start": 0,
                        "end": len(value), "source_span": _span(0, len(value))}],
        }
        observations["width_jaconv"][value] = {
            "output": width,
            "tokens": [{"surface": value, "output": width, "start": 0,
                        "end": len(value), "source_span": _span(0, len(value))}],
        }
        observations["sudachi_norm"][value] = {
            "output": sudachi_norm, "tokens": sudachi_tokens,
        }
        observations["spacy_norm"][value] = {
            "output": spacy_norm, "tokens": spacy_tokens,
        }
    batch_seconds = perf_counter() - batch_start

    return {
        "schema": SCHEMA,
        "input_count": len(values),
        "unique_value_count": len(unique),
        "versions": {
            "jaconv": metadata.version("jaconv"),
            "SudachiPy": metadata.version("SudachiPy"),
            "spaCy": metadata.version("spacy"),
        },
        "timings_seconds": {
            "initialize": initialize_seconds, "batch": batch_seconds,
        },
        "trained_components": [],
        "views": views,
        "observations": observations,
    }


__all__ = ["SCHEMA", "build_views"]
