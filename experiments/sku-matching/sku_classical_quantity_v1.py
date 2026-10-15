#!/usr/bin/env python3
"""Conservative quantity and unit parsing for classical SKU value comparison.

The parser keeps the complete source string and exact source spans. It does not
infer product categories, SKU identity, package construction, or aliases between
counting units. Physical point quantities are converted through Pint using
``Decimal`` magnitudes; all unknown words and material remain in the ordered
comparison stream.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

import jaconv
import pint


SCHEMA = "sku-classical-quantity-v1"
_UREG = pint.UnitRegistry(non_int_type=Decimal)

# Unit lexemes are exact and case-sensitive. Conversion is delegated to Pint;
# counters are deliberately separate opaque-count units, not dimensional units.
_PHYSICAL_UNITS = {
    "mm": "millimeter", "cm": "centimeter", "m": "meter", "km": "kilometer",
    "μm": "micrometer", "um": "micrometer",
    "mg": "milligram", "g": "gram", "kg": "kilogram",
    # Single-letter symbols are not certified without unit context: W/V/A can
    # be apparel or variant text, and L is commonly a clothing size.
    # Keep unambiguous prefixed forms and explicit Japanese unit names.
    "ml": "milliliter", "mL": "milliliter",
    "ミリメートル": "millimeter", "センチメートル": "centimeter",
    "センチ": "centimeter", "メートル": "meter", "キロメートル": "kilometer",
    "ミリグラム": "milligram", "グラム": "gram", "キログラム": "kilogram",
    "ミリリットル": "milliliter", "リットル": "liter",
    "インチ": "inch", "in": "inch", "ft": "foot",
    "kW": "kilowatt",
    "Hz": "hertz", "kHz": "kilohertz", "℃": "degree_Celsius",
}
_COUNTERS = tuple(sorted((
    "セット", "パック", "ケース", "ダース", "ペア", "組", "袋", "箱", "本",
    "枚", "個", "点", "着", "足", "粒", "杯", "回", "食", "台", "冊",
    "巻", "錠", "包", "缶", "瓶", "枚組", "個入", "本入", "袋入",
), key=len, reverse=True))
_UNITS = tuple(sorted((*_PHYSICAL_UNITS.keys(), *_COUNTERS), key=len, reverse=True))
_ROLE_WORDS = (
    "横幅", "奥行", "高さ", "厚さ", "厚み", "直径", "内径", "外径", "幅", "丈",
    "長さ", "縦", "横", "depth", "width", "height", "length", "diameter",
)
_NEGATION = {
    "付き": "positive", "あり": "positive", "有り": "positive", "含む": "positive",
    "なし": "negative", "無し": "negative", "無": "negative", "非対応": "negative",
    "含まない": "negative", "不含": "negative",
}
_MODIFIERS = {
    "約": "approx", "およそ": "approx", "およそ": "approx", "だいたい": "approx",
    "±": "tolerance", "+/-": "tolerance", "＋／－": "tolerance",
}
_RANGE_MARKERS = ("〜", "～", "~", "-", "−", "–", "—", "から")
_LIMITS = {
    "以下": "at_most", "以内": "at_most", "未満": "less_than",
    "以上": "at_least", "超": "greater_than", "より大きい": "greater_than",
    "より小さい": "less_than",
}
_NUMBER_RE = re.compile(r"[+-]?(?:(?:\d{1,3}(?:,\d{3})+)|\d+)(?:\.\d+)?")
_WORD_RE = re.compile(r"[A-Za-z]+[A-Za-z0-9._/-]*|[0-9]+[A-Za-z][A-Za-z0-9._/-]*|[\u3040-\u30ff\u3400-\u9fff々〆ヵヶー]+")
_SPACE_RE = re.compile(r"\s+")


def _halfwidth(text: str) -> tuple[str, list[int]]:
    """Convert only full-width ASCII/digits per source character, with mapping."""
    out: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text):
        converted = jaconv.z2h(char, kana=False, digit=True, ascii=True)
        out.append(converted)
        offsets.extend([index] * len(converted))
    return "".join(out), offsets


def _decimal(raw: str) -> str:
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal token: {raw}") from exc
    if not value.is_finite():
        raise ValueError("non-finite quantities are not supported")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _physical(raw_number: str, raw_unit: str) -> dict[str, Any]:
    unit = _PHYSICAL_UNITS[raw_unit]
    magnitude = Decimal(raw_number.replace(",", ""))
    quantity = _UREG.Quantity(magnitude, _UREG.Unit(unit)).to_base_units()
    base_unit = str(quantity.units)
    return {
        "magnitude": _decimal(str(quantity.magnitude)),
        "unit": base_unit,
        "dimension": str(quantity.dimensionality),
    }


def _src_span(mapping: list[int], start: int, end: int, source_len: int) -> list[int]:
    if start >= end:
        return [0, 0]
    return [mapping[start], min(source_len, mapping[end - 1] + 1)]


def _span_token(kind: str, norm: str, start: int, end: int, raw: str,
                mapping: list[int], source_len: int, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "text": norm, "quote": raw,
            "span": _src_span(mapping, start, end, source_len), **fields}


def _match_unit(text: str, pos: int) -> tuple[str | None, int]:
    # Whitespace between a number and unit is accepted, but is retained in quote.
    whitespace = _SPACE_RE.match(text, pos)
    unit_start = whitespace.end() if whitespace else pos
    for unit in _UNITS:
        if not text.startswith(unit, unit_start):
            continue
        unit_end = unit_start + len(unit)
        # Do not take a physical-unit prefix from a model/variant token such as
        # ``101mmfoo`` or ``2m3``. A unit may still be followed by Japanese prose.
        if unit_end < len(text) and text[unit_end].isascii() and text[unit_end].isalnum():
            continue
        return unit, unit_end
    return None, pos


def _role_before(tokens: list[dict[str, Any]]) -> str | None:
    for token in reversed(tokens[-3:]):
        if token["kind"] == "role":
            return token["role"]
        if token["kind"] == "word" and token["text"] in _ROLE_WORDS:
            return token["text"]
        if token["kind"] not in ("space", "modifier"):
            break
    return None


def _is_quantity_multiplication(text: str, pos: int,
                               tokens: list[dict[str, Any]]) -> bool:
    """Recognize multiplication only between quantity-expression components."""
    if not tokens or tokens[-1]["kind"] not in ("quantity", "quantity_range"):
        return False
    next_pos = pos + 1
    while next_pos < len(text) and text[next_pos].isspace():
        next_pos += 1
    if _NUMBER_RE.match(text, next_pos):
        return True
    for role in sorted(_ROLE_WORDS, key=len, reverse=True):
        if not text.startswith(role, next_pos):
            continue
        after_role = next_pos + len(role)
        if role.isascii() and after_role < len(text) and text[after_role].isascii() and text[after_role].isalpha():
            continue
        while after_role < len(text) and text[after_role].isspace():
            after_role += 1
        if _NUMBER_RE.match(text, after_role):
            return True
    return False


def _split_word(word: str) -> list[tuple[str, str]]:
    """Keep lexical material, but expose exact explicit polarity suffixes."""
    for marker in sorted(_NEGATION, key=len, reverse=True):
        if word == marker:
            return [("polarity", marker)]
        if word.endswith(marker) and len(word) > len(marker):
            prefix = word[:-len(marker)]
            return [("word", prefix), ("polarity", marker)]
    for marker in sorted(_LIMITS, key=len, reverse=True):
        if word == marker:
            return [("limit", marker)]
        if word.endswith(marker) and len(word) > len(marker):
            prefix = word[:-len(marker)]
            return [("word", prefix), ("limit", marker)]
    for role in sorted(_ROLE_WORDS, key=len, reverse=True):
        if word == role:
            return [("role", role)]
        if word.endswith(role) and len(word) > len(role):
            prefix = word[:-len(role)]
            return [("word", prefix), ("role", role)]
    return [("word", word)]


def _quantity_node(number: str, unit: str | None, start: int, end: int,
                   text: str, mapping: list[int], source: str,
                   role: str | None = None, inherited_unit: bool = False) -> dict[str, Any]:
    raw = source[_src_span(mapping, start, end, len(source))[0]:_src_span(mapping, start, end, len(source))[1]]
    numeric = _decimal(number)
    base = {"value": numeric, "value_raw": number, "role": role}
    if unit in _PHYSICAL_UNITS:
        base.update({"quantity_kind": "physical", "unit_raw": unit,
                     "canonical": _physical(number, unit)})
    elif unit in _COUNTERS:
        base.update({"quantity_kind": "count", "counter": unit,
                     "canonical": {"value": numeric, "counter": unit}})
    else:
        base.update({"quantity_kind": "scalar", "unit_raw": None,
                     "canonical": {"value": numeric, "unit": None}})
    if inherited_unit:
        base["unit_inherited_from_expression"] = True
    return _span_token("quantity", text[start:end], start, end, raw, mapping, len(source), **base)


def parse_value(text: str) -> dict[str, Any]:
    """Parse a value into source-bound numeric, unit, counter, and opaque tokens.

    Offsets are Python Unicode code-point offsets into ``raw_text``. No raw
    substring is discarded: unrecognized text is emitted as a ``word`` or
    ``opaque`` token. Full-width ASCII/digits are normalized selectively.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized, source_map = _halfwidth(text)
    tokens: list[dict[str, Any]] = []
    i = 0
    while i < len(normalized):
        if normalized[i].isspace():
            i += 1
            continue

        modifier = next((m for m in sorted(_MODIFIERS, key=len, reverse=True)
                         if normalized.startswith(m, i)), None)
        if modifier:
            j = i + len(modifier)
            tokens.append(_span_token("modifier", modifier, i, j,
                text[source_map[i]:source_map[j - 1] + 1], source_map, len(text),
                modifier=_MODIFIERS[modifier]))
            i = j
            continue

        if (normalized.startswith(("×", "x", "X", "*", "＊"), i)
                and _is_quantity_multiplication(normalized, i, tokens)):
            j = i + 1
            tokens.append(_span_token("operator", normalized[i:j], i, j,
                text[source_map[i]:source_map[j - 1] + 1], source_map, len(text), operator="multiply"))
            i = j
            continue

        if normalized[i] in ("~", "〜", "～", "−", "–", "—"):
            j = i + 1
            tokens.append(_span_token("range_marker", normalized[i:j], i, j,
                text[source_map[i]:source_map[j - 1] + 1], source_map, len(text), marker="range"))
            i = j
            continue

        number_match = _NUMBER_RE.match(normalized, i)
        if number_match:
            first_end = number_match.end()
            first = number_match.group(0)
            first_unit, first_unit_end = _match_unit(normalized, first_end)
            # Either endpoint may carry the shared unit: 10〜15cm,
            # 10cm〜15cm, and 10cm〜15cm all retain their exact source spans.
            marker_at = first_unit_end if first_unit else first_end
            while marker_at < len(normalized) and normalized[marker_at].isspace():
                marker_at += 1
            range_marker = next((m for m in sorted(_RANGE_MARKERS, key=len, reverse=True)
                                 if normalized.startswith(m, marker_at)), None)
            second_match = None
            if range_marker:
                second_start = marker_at + len(range_marker)
                while second_start < len(normalized) and normalized[second_start].isspace():
                    second_start += 1
                second_match = _NUMBER_RE.match(normalized, second_start)
            if second_match:
                second_unit, second_end = _match_unit(normalized, second_match.end())
                lower_unit, upper_unit = first_unit or second_unit, second_unit or first_unit
                compatible_units = True
                if lower_unit and upper_unit:
                    if lower_unit in _PHYSICAL_UNITS and upper_unit in _PHYSICAL_UNITS:
                        compatible_units = (_physical(first, lower_unit)["dimension"] ==
                                            _physical(second_match.group(0), upper_unit)["dimension"])
                    elif lower_unit in _COUNTERS and upper_unit in _COUNTERS:
                        compatible_units = lower_unit == upper_unit
                    else:
                        compatible_units = False
                if compatible_units and (first_unit or second_unit or range_marker):
                    range_end = second_end if second_unit else second_match.end()
                    start, end = i, range_end
                    role = _role_before(tokens)
                    raw = text[source_map[start]:source_map[end - 1] + 1]
                    range_unit = lower_unit or upper_unit
                    nodes = {
                        "kind": "quantity_range", "text": normalized[start:end], "quote": raw,
                        "span": _src_span(source_map, start, end, len(text)), "role": role,
                        "range_marker": range_marker,
                        "quantity_kind": ("physical" if range_unit in _PHYSICAL_UNITS else
                                           "count" if range_unit in _COUNTERS else "scalar"),
                        "unit_raw": range_unit if range_unit in _PHYSICAL_UNITS else None,
                        "lower_unit_raw": lower_unit, "upper_unit_raw": upper_unit,
                        "counter": range_unit if range_unit in _COUNTERS else None,
                        "lower": _decimal(first), "upper": _decimal(second_match.group(0)),
                        "lower_raw": first, "upper_raw": second_match.group(0),
                        "canonical": None,
                    }
                    if lower_unit in _PHYSICAL_UNITS and upper_unit in _PHYSICAL_UNITS:
                        nodes["lower_canonical"] = _physical(first, lower_unit)
                        nodes["upper_canonical"] = _physical(second_match.group(0), upper_unit)
                    tokens.append(nodes)
                    i = end
                    continue

            unit, unit_end = _match_unit(normalized, first_end)
            if unit:
                role = _role_before(tokens)
                quantity = _quantity_node(first, unit, i, unit_end, normalized,
                                          source_map, text, role)
                tokens.append(quantity)
                i = unit_end
                continue

            # A bare number attached to lexical text is more likely an opaque
            # model/variant label than a free numeric measurement. Keep it whole.
            attached_to_lexical = (
                i > 0 and (
                    normalized[i - 1].isalnum()
                    or "\u3040" <= normalized[i - 1] <= "\u9fff"
                )
            )
            # Explicit dimension labels bind to the following bare value:
            # ``幅100×丈80cm``. Other digits embedded in lexical/model strings
            # remain opaque instead of becoming measurements.
            if attached_to_lexical and _role_before(tokens) is None:
                end = first_end
                tokens.append(_span_token("opaque", normalized[i:end], i, end,
                    text[source_map[i]:source_map[end - 1] + 1], source_map, len(text)))
                i = end
                continue
            role = _role_before(tokens)
            tokens.append(_quantity_node(first, None, i, first_end, normalized,
                                         source_map, text, role))
            i = first_end
            continue

        word_match = _WORD_RE.match(normalized, i)
        if word_match:
            j = word_match.end()
            word = word_match.group(0)
            pieces = _split_word(word)
            for piece_kind, piece in pieces:
                offset = word.find(piece)
                pstart, pend = i + offset, i + offset + len(piece)
                original = text[source_map[pstart]:source_map[pend - 1] + 1]
                if piece_kind == "role":
                    tokens.append(_span_token("role", piece, pstart, pend, original,
                                              source_map, len(text), role=piece))
                elif piece_kind == "polarity":
                    tokens.append(_span_token("polarity", piece, pstart, pend, original,
                                              source_map, len(text), polarity=_NEGATION[piece]))
                elif piece_kind == "limit":
                    tokens.append(_span_token("limit", piece, pstart, pend, original,
                                              source_map, len(text), limit=_LIMITS[piece]))
                else:
                    tokens.append(_span_token("word", piece, pstart, pend, original,
                                              source_map, len(text)))
            i = j
            continue

        j = i + 1
        tokens.append(_span_token("opaque", normalized[i:j], i, j,
            text[source_map[i]:source_map[j - 1] + 1], source_map, len(text)))
        i = j

    # A trailing common physical unit in an ordered multiplication expression
    # applies to the preceding bare dimension too: 100×80cm == 1m×0.8m only
    # when the explicitly typed tree says so. Count units are never inherited.
    for index, token in enumerate(tokens):
        if token["kind"] != "quantity" or token.get("quantity_kind") != "physical":
            continue
        # Role labels may appear on either side of the multiplication sign:
        # ``幅100×丈80cm``. Skip those labels when finding the preceding scalar.
        operator_index = index - 1
        while operator_index >= 0 and tokens[operator_index]["kind"] == "role":
            operator_index -= 1
        if operator_index < 0 or tokens[operator_index]["kind"] != "operator":
            continue
        prior_index = operator_index - 1
        while prior_index >= 0 and tokens[prior_index]["kind"] == "role":
            prior_index -= 1
        if (prior_index >= 0 and tokens[prior_index]["kind"] == "quantity"
                and tokens[prior_index].get("quantity_kind") == "scalar"):
            prior = tokens[prior_index]
            physical = _physical(prior["canonical"]["value"], token["unit_raw"])
            prior.update({"quantity_kind": "physical", "unit_raw": token["unit_raw"],
                          "canonical": physical, "unit_inherited_from_expression": True})

    # Construction-count language is deliberately unresolved: its count is
    # retained but never treated as a sales/package quantity for conflict.
    for index, token in enumerate(tokens[:-1]):
        if token["kind"] == "quantity" and token.get("quantity_kind") == "count":
            following = tokens[index + 1]
            if following.get("kind") == "word" and following.get("text") in ("合わせ", "あわせ", "構造"):
                token["quantity_kind"] = "unresolved_count_context"
                token["canonical"] = {"value": token["canonical"]["value"],
                                       "counter": token["counter"], "role": "unresolved"}

    opaque_tokens = [t for t in tokens if t["kind"] == "opaque"]
    return {
        "schema": SCHEMA,
        "raw_text": text,
        "normalized_text": normalized,
        "normalized_to_source_offsets": source_map,
        "tokens": tokens,
        "typed_tree": [t for t in tokens if t["kind"] in
                       ("quantity", "quantity_range", "operator", "role", "modifier", "polarity", "limit", "range_marker")],
        "word_tokens": [t["text"] for t in tokens if t["kind"] == "word"],
        "opaque_tokens": [t["text"] for t in opaque_tokens],
        "opaque_spans": opaque_tokens,
        "unparsed_spans": opaque_tokens,
        "unresolved_tokens": [t for t in tokens if t.get("quantity_kind") == "unresolved_count_context"],
        "parse_status": "empty" if not text.strip() else "parsed",
    }


def _signature_token(token: dict[str, Any]) -> dict[str, Any]:
    kind = token["kind"]
    if kind == "quantity":
        return {"kind": kind, "quantity_kind": token.get("quantity_kind"),
                "canonical": token.get("canonical"), "role": token.get("role"),
                "unit_type": ("physical" if token.get("quantity_kind") == "physical" else
                              "count" if token.get("quantity_kind") in ("count", "unresolved_count_context") else "scalar")}
    if kind == "quantity_range":
        if token.get("quantity_kind") == "physical":
            return {"kind": kind, "quantity_kind": "physical",
                    "lower_canonical": token.get("lower_canonical"),
                    "upper_canonical": token.get("upper_canonical"), "role": token.get("role"),
                    "range_marker": "range"}
        return {"kind": kind, "quantity_kind": token.get("quantity_kind"),
                "lower": token.get("lower"), "upper": token.get("upper"),
                "counter": token.get("counter"), "role": token.get("role"),
                "range_marker": "range"}
    if kind == "operator":
        return {"kind": kind, "operator": token["operator"]}
    if kind == "role":
        return {"kind": kind, "role": token["role"]}
    if kind == "modifier":
        return {"kind": kind, "modifier": token["modifier"]}
    if kind == "polarity":
        return {"kind": kind, "polarity": token["polarity"], "surface": token["text"]}
    if kind == "limit":
        return {"kind": kind, "limit": token["limit"]}
    if kind == "range_marker":
        return {"kind": kind, "marker": token["marker"]}
    return {"kind": kind, "text": token["text"]}


def canonical_signature(parsed: dict[str, Any] | str) -> dict[str, Any]:
    """Return an ordered, JSON-serializable signature without source spans."""
    value = parse_value(parsed) if isinstance(parsed, str) else parsed
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("parsed value must come from parse_value")
    return {"tokens": [_signature_token(token) for token in value["tokens"]],
            "nonempty": value["parse_status"] != "empty"}


def _skeleton(parsed: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    skeleton, values = [], []
    for token in parsed["tokens"]:
        signed = _signature_token(token)
        if token["kind"] == "quantity":
            canonical = token.get("canonical") or {}
            if token.get("quantity_kind") == "physical":
                skeleton.append({"kind": "quantity", "quantity_kind": "physical",
                                 "unit": canonical.get("unit"), "dimension": canonical.get("dimension"),
                                 "role": token.get("role")})
                values.append(canonical.get("magnitude"))
            elif token.get("quantity_kind") == "count":
                skeleton.append({"kind": "quantity", "quantity_kind": "count",
                                 "counter": token.get("counter"), "role": token.get("role")})
                values.append(canonical.get("value"))
            elif token.get("quantity_kind") == "unresolved_count_context":
                skeleton.append({"kind": "unresolved_quantity", "counter": token.get("counter")})
                values.append(canonical.get("value"))
            else:
                skeleton.append({"kind": "quantity", "quantity_kind": "scalar", "role": token.get("role")})
                values.append(canonical.get("value"))
        elif token["kind"] == "quantity_range":
            skeleton.append({"kind": "quantity_range", "quantity_kind": token.get("quantity_kind"),
                             "unit_raw": token.get("unit_raw"), "counter": token.get("counter"),
                             "role": token.get("role")})
            values.extend([token.get("lower_canonical"), token.get("upper_canonical")])
        elif token["kind"] == "polarity":
            skeleton.append({"kind": "polarity"})
            values.append(token.get("polarity"))
        else:
            skeleton.append(signed)
    return skeleton, values


def compare_values(a: dict[str, Any] | str, b: dict[str, Any] | str) -> dict[str, Any]:
    """Conservatively compare parsed values, with explicit reasons for abstention."""
    left = parse_value(a) if isinstance(a, str) else a
    right = parse_value(b) if isinstance(b, str) else b
    if not isinstance(left, dict) or left.get("schema") != SCHEMA:
        raise ValueError("left value must be raw text or parse_value output")
    if not isinstance(right, dict) or right.get("schema") != SCHEMA:
        raise ValueError("right value must be raw text or parse_value output")
    if left["parse_status"] == "empty" or right["parse_status"] == "empty":
        return {"state": "unknown", "reasons": ["empty_value"]}
    left_signature, right_signature = canonical_signature(left), canonical_signature(right)
    if left_signature["tokens"] == right_signature["tokens"]:
        return {"state": "support", "reasons": ["ordered_typed_tree_and_opaque_tokens_equal"]}

    lp = [t.get("polarity") for t in left["tokens"] if t["kind"] == "polarity"]
    rp = [t.get("polarity") for t in right["tokens"] if t["kind"] == "polarity"]
    if lp and rp and lp != rp:
        l_without = [_signature_token(t) for t in left["tokens"] if t["kind"] != "polarity"]
        r_without = [_signature_token(t) for t in right["tokens"] if t["kind"] != "polarity"]
        if l_without == r_without:
            return {"state": "conflict", "reasons": ["explicit_negation_or_presence_differs"]}

    ls, lv = _skeleton(left)
    rs, rv = _skeleton(right)
    if ls == rs and len(lv) == len(rv) and lv != rv:
        if left["unresolved_tokens"] or right["unresolved_tokens"]:
            return {"state": "unknown", "reasons": ["construction_or_sales_quantity_role_unresolved"]}
        if any(t["kind"] == "quantity_range" or
               (t["kind"] == "modifier" and t.get("modifier") in ("approx", "tolerance"))
               for t in left["tokens"] + right["tokens"]):
            return {"state": "unknown", "reasons": ["approximate_or_range_values_not_point_comparable"]}
        if any(t["kind"] == "limit" for t in left["tokens"] + right["tokens"]):
            return {"state": "unknown", "reasons": ["bound_thresholds_are_not_exact_points"]}
        if any(t["kind"] == "quantity" and t.get("quantity_kind") == "unresolved_count_context"
               for t in left["tokens"] + right["tokens"]):
            return {"state": "unknown", "reasons": ["unresolved_quantity_context"]}
        for parsed in (left, right):
            for index, token in enumerate(parsed["tokens"]):
                if token["kind"] != "quantity" or token.get("quantity_kind") != "scalar":
                    continue
                part_of_ordered_measure = (
                    (index > 0 and parsed["tokens"][index - 1].get("operator") == "multiply") or
                    (index + 1 < len(parsed["tokens"])
                     and parsed["tokens"][index + 1].get("operator") == "multiply") or
                    (index > 0 and parsed["tokens"][index - 1]["kind"] == "limit") or
                    (index + 1 < len(parsed["tokens"])
                     and parsed["tokens"][index + 1]["kind"] == "limit"))
                if token.get("role") is None and not part_of_ordered_measure:
                    return {"state": "unknown", "reasons": ["unqualified_scalar_not_safely_comparable"]}
        return {"state": "conflict", "reasons": ["same_roles_and_units_with_different_exact_values"]}

    # Explicit presence/absence can conflict only if every other ordered token
    # is identical. The source text remains attached to its polarity token.
    if any(t.get("quantity_kind") == "unresolved_count_context" for t in left["tokens"] + right["tokens"]):
        return {"state": "unknown", "reasons": ["construction_or_sales_quantity_role_unresolved"]}
    return {"state": "unknown", "reasons": ["opaque_words_or_typed_structure_differ"]}

