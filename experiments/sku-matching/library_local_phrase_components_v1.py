#!/usr/bin/env python3
"""Input-local compound phrase decomposition with native spaCy PhraseMatcher."""
from __future__ import annotations

from collections import defaultdict
from importlib import metadata
from typing import Any

from spacy.matcher import PhraseMatcher


SCHEMA = "library-local-phrase-components-v1"


class LocalPhraseCatalog:
    """Match exact catalog phrases under spaCy NORM; infer no SKU semantics."""

    def __init__(self, nlp: Any, catalogs: dict[str, list[str]]):
        self.nlp = nlp
        self.matcher = PhraseMatcher(nlp.vocab, attr="NORM")
        self.patterns: list[dict[str, Any]] = []
        self.pattern_by_id: dict[str, dict[str, Any]] = {}
        seen: set[tuple[str, str]] = set()
        for axis_name in catalogs:
            values = catalogs[axis_name]
            if not isinstance(axis_name, str) or not isinstance(values, list):
                raise TypeError("catalogs must map string axis names to lists of strings")
            for raw_value in values:
                if not isinstance(raw_value, str):
                    raise TypeError("catalog option values must be strings")
                key = (axis_name, raw_value)
                if key in seen:
                    continue
                seen.add(key)
                pattern_doc = nlp.make_doc(raw_value)
                if not len(pattern_doc):
                    continue
                pattern_id = f"local_option_{len(self.patterns):06d}"
                norm_key = [token.norm_ for token in pattern_doc]
                entry = {"pattern_id": pattern_id, "axis_name": axis_name,
                         "raw_option_value": raw_value, "native_norm_key": norm_key,
                         "native_norm_key_text": " ".join(norm_key),
                         "pattern_tokens": [token.text for token in pattern_doc]}
                self.matcher.add(pattern_id, [pattern_doc])
                self.patterns.append(entry)
                self.pattern_by_id[pattern_id] = entry

        by_norm: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for entry in self.patterns:
            by_norm[tuple(entry["native_norm_key"])].append(entry)
        self.normalized_catalog_collisions = [
            {"native_norm_key": list(norm), "entries": entries,
             "axes": sorted({entry["axis_name"] for entry in entries}),
             "raw_values": sorted({entry["raw_option_value"] for entry in entries})}
            for norm, entries in by_norm.items()
            if len({(entry["axis_name"], entry["raw_option_value"]) for entry in entries}) > 1
        ]
        self.collisions_by_norm = {
            tuple(collision["native_norm_key"]): collision
            for collision in self.normalized_catalog_collisions
        }
        self.metadata = {
            "schema": SCHEMA, "spacy_version": metadata.version("spacy"),
            "matcher": "spacy.matcher.PhraseMatcher", "matcher_attr": "NORM",
            "pattern_count": len(self.patterns), "patterns": self.patterns,
            "normalized_catalog_collisions": self.normalized_catalog_collisions,
            "trained_components": [], "custom_grammar_or_alias_rules": False,
        }

    def analyze(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        doc = self.nlp.make_doc(text)
        raw_tokens = [{
            "token_index": index, "text": token.text, "norm": token.norm_,
            "start": token.idx, "end": token.idx + len(token.text),
            "source_span": [token.idx, token.idx + len(token.text)],
            "is_punct": bool(token.is_punct), "is_space": bool(token.is_space),
        } for index, token in enumerate(doc)]
        hits = []
        for match_id, start, end in self.matcher(doc):
            entry = self.pattern_by_id[self.nlp.vocab.strings[match_id]]
            token_indices = list(range(start, end))
            char_start, char_end = doc[start].idx, doc[end - 1].idx + len(doc[end - 1].text)
            norm_key = [token.norm_ for token in doc[start:end]]
            collision = self.collisions_by_norm.get(tuple(norm_key))
            axes_for_phrase = sorted({x["axis_name"] for x in collision["entries"]}) if collision else [entry["axis_name"]]
            hits.append({
                "axis_name": entry["axis_name"], "raw_option_value": entry["raw_option_value"],
                "native_norm_key": norm_key, "start": char_start, "end": char_end,
                "source_span": [char_start, char_end], "token_start": start, "token_end": end,
                "token_indices": token_indices, "quote": text[char_start:char_end],
                "pattern_id": entry["pattern_id"],
                "ambiguous_across_axes": len(axes_for_phrase) > 1,
                "axes_for_phrase": axes_for_phrase,
                "normalized_catalog_collision": collision is not None,
            })
        hits.sort(key=lambda hit: (hit["start"], hit["end"], hit["axis_name"], hit["raw_option_value"]))

        overlaps = []
        for left_index, left in enumerate(hits):
            for right_index in range(left_index + 1, len(hits)):
                right = hits[right_index]
                if right["start"] >= left["end"]:
                    break
                if (left["pattern_id"], left["start"], left["end"]) != (right["pattern_id"], right["start"], right["end"]):
                    overlaps.append({"left_hit_index": left_index, "right_hit_index": right_index,
                                     "span": [max(left["start"], right["start"]), min(left["end"], right["end"])],
                                     "left_pattern_id": left["pattern_id"], "right_pattern_id": right["pattern_id"]})

        values_by_axis: dict[str, set[str]] = defaultdict(set)
        for hit in hits:
            values_by_axis[hit["axis_name"]].add(hit["raw_option_value"])
        conflicting_axes = {axis for axis, values in values_by_axis.items() if len(values) > 1}
        ambiguous_hits = [h for h in hits if h["ambiguous_across_axes"] or h["normalized_catalog_collision"]]
        safe_hits = [h for h in hits if h not in ambiguous_hits and h["axis_name"] not in conflicting_axes]
        covered = {index for hit in safe_hits for index in hit["token_indices"]}
        unmatched = [token for token in raw_tokens
                     if not token["is_space"] and token["token_index"] not in covered]
        ignored_native_punctuation = [token for token in unmatched if token["is_punct"]]
        unmatched_nonpunctuation = [token for token in unmatched if not token["is_punct"]]

        reasons = []
        if any(hit["ambiguous_across_axes"] for hit in hits):
            reasons.append("same_native_phrase_maps_to_distinct_axes")
        if conflicting_axes:
            reasons.append("multiple_different_values_matched_on_same_axis")
        if any(hit["normalized_catalog_collision"] for hit in hits):
            reasons.append("matched_normalized_catalog_collision")
        if overlaps:
            reasons.append("overlapping_distinct_matches")
        if unmatched:
            reasons.append("unmatched_substantive_tokens")
        axes = sorted(values_by_axis)
        if len(axes) < 2:
            reasons.append("fewer_than_two_distinct_axes")
        unique_full_compound = (
            len(axes) >= 2 and not reasons and len(covered) == sum(not t["is_space"] for t in raw_tokens)
        )
        unique_full_compound_ignoring_native_punctuation = (
            len(axes) >= 2
            and not any(hit["ambiguous_across_axes"] for hit in hits)
            and not conflicting_axes
            and not any(hit["normalized_catalog_collision"] for hit in hits)
            and not overlaps
            and not unmatched_nonpunctuation
            and len(covered) == sum(not t["is_space"] and not t["is_punct"] for t in raw_tokens)
        )
        unmatched_spans = [{"start": token["start"], "end": token["end"],
                            "source_span": token["source_span"], "text": token["text"]}
                           for token in unmatched]
        return {
            "schema": SCHEMA, "text": text, "spacy_version": self.metadata["spacy_version"],
            "raw_tokens": raw_tokens, "matches": hits, "matched_atom_count": len({
                (hit["axis_name"], hit["raw_option_value"]) for hit in hits}),
            "matched_axes": [{"axis_name": axis, "raw_option_values": sorted(values)}
                             for axis, values in sorted(values_by_axis.items())],
            "unmatched_substantive_spans": unmatched_spans,
            "unmatched_substantive_text": [span["text"] for span in unmatched_spans],
            "ignored_native_punctuation_spans": [
                {"start": token["start"], "end": token["end"],
                 "source_span": token["source_span"], "text": token["text"]}
                for token in ignored_native_punctuation
            ],
            "overlapping_matches": overlaps,
            "normalized_catalog_collisions": self.normalized_catalog_collisions,
            "ambiguity_reasons": reasons, "unique_full_compound": unique_full_compound,
            "unique_full_compound_ignoring_native_punctuation": unique_full_compound_ignoring_native_punctuation,
            "lexical_components_only": True, "production_adoption_allowed": False,
            "catalog_metadata": self.metadata,
        }


__all__ = ["SCHEMA", "LocalPhraseCatalog"]
