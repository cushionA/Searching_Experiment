"""Conservative, label-free SKU atom/fact extensions.

The upstream atomizer is injected so this experiment can be compared against
the exact frozen base implementation. New quantities retain the upstream
``type``/``value`` convention for old atom kinds and use a separate ``quantity``
kind when units carry meaning that ``piece_total`` cannot represent.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable

Atomizer = Callable[..., dict[str, Any]]
FactFamily = Callable[[dict[str, Any]], str]


def _norm(raw: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    offsets: list[int] = []
    for i, char in enumerate(raw):
        for normalized in unicodedata.normalize("NFKC", char):
            chars.append(normalized)
            offsets.append(i)
    return "".join(chars), offsets


def _atom(raw: str, norm: str, index: list[int], start: int, end: int, **fields) -> dict:
    left, right = index[start], index[end - 1] + 1
    return {**fields, "quote": raw[left:right], "offset": [left, right]}


_QTY = re.compile(r"(?<![\d.])(?P<n>\d+(?:\.\d+)?)\s*(?P<u>枚|本|畳|段)(?![A-Za-z0-9])")
_WIDTH = re.compile(r"(?<![\d.])(?P<n>\d+(?:\.\d+)?)\s*(?P<u>mm|cm|m)(?![A-Za-z])")


def _quantity_role(raw: str, start: int, end: int, unit: str, axis_label: str | None) -> str:
    before, after = raw[max(0, start - 16):start], raw[end:end + 12]
    axis = unicodedata.normalize("NFKC", axis_label or "")
    if unit == "段":
        return "tier_count"
    if unit == "畳":
        return "area_capacity"
    if unit == "枚":
        return "package_total" if ("セット" in after or "セット" in axis) else "axis_value"
    if unit == "本":
        return "package_total" if ("セット" in after or any(x in axis for x in ("本数", "セット数"))) else "unscoped_count"
    return "axis_value"


def atomize(raw: str, axis_label: str | None = None, color_vocab=frozenset(), family=(), *,
            base_atomize: Atomizer | None = None) -> dict[str, Any]:
    """Run the injected base parser, then append narrowly typed numeric facts.

    Base fields and residue are preserved verbatim. This function never turns
    a missing word into a negative claim.
    """
    base = base_atomize(raw, axis_label, color_vocab, family) if base_atomize else {
        "raw": raw, "atoms": [], "residue": raw, "decomposition": "partial"
    }
    result = {**base, "atoms": list(base.get("atoms", []))}
    norm, index = _norm(raw)
    normalized_label = unicodedata.normalize("NFKC", axis_label or "")
    added_atoms: list[dict[str, Any]] = []
    for m in _QTY.finditer(norm):
        unit, number = m.group("u"), float(m.group("n"))
        atom_end = m.end() + (1 if unit == "畳" and norm[m.end():m.end() + 1] == "用" else 0)
        start, end = index[m.start()], index[atom_end - 1] + 1
        # A dimension array such as 10/15cm is not a discrete item count.
        following = norm[m.end():m.end() + 8]
        if unit == "段" and re.match(r"\s*(?:階|ギア)", following):
            continue
        # The base vocabulary already has stable public types for these.
        # Do not create a second quantity family for the same literal.
        base_kind = "piece_total" if unit == "枚" else "tier_count" if unit == "段" else None
        if base_kind and any(a.get("type") == base_kind and a.get("value") == int(number)
                             and _contains(a.get("offset"), [start, end]) for a in result["atoms"]):
            continue
        if unit in ("枚", "本") and any(a.get("type") == "component_count" and a.get("value") == int(number)
                                          and _contains(a.get("offset"), [start, end])
                                          for a in result["atoms"]):
            continue
        normalized_number = int(number) if number.is_integer() else number
        raw_offset = [index[m.start()], index[m.end() - 1] + 1]
        if any(a.get("type") == "quantity" and a.get("value") == normalized_number
               and a.get("unit") == unit and _contains(a.get("offset"), raw_offset) for a in result["atoms"]):
            continue
        nearby = norm[max(0, m.start() - 8):m.start()]
        component = ("corner_side_piece" if re.search(r"角付き\s*$", nearby) else
                     "noncorner_side_piece" if re.search(r"角なし\s*$", nearby) else None)
        role = _quantity_role(norm, m.start(), m.end(), unit, normalized_label)
        if component:
            role = "component_count"
        atom = _atom(
            raw, norm, index, m.start(), atom_end, type="quantity",
            value=int(number) if number.is_integer() else number, unit=unit,
            component=component, quantity_role=role,
        )
        result["atoms"].append(atom)
        added_atoms.append(atom)
    if any(x in normalized_label for x in ("幅", "横幅")):
        for m in _WIDTH.finditer(norm):
            n = float(m.group("n")); unit = m.group("u").lower()
            cm = n / 10 if unit == "mm" else n * 100 if unit == "m" else n
            key = (("width",), (cm,))
            span = [index[m.start()], index[m.end() - 1] + 1]
            generic = next((a for a in result["atoms"] if a.get("type") == "dimension"
                            and a.get("role") == "generic" and len(a.get("value", [])) == 1
                            and a["value"][0] == cm and a.get("offset") == span), None)
            if generic:
                generic.update(role="labeled", labels=["width"], unit="cm")
                added_atoms.append(generic)
            elif not any(a.get("type") == "dimension" and a.get("role") == "labeled"
                         and a.get("labels") == ["width"] and a.get("value") == [cm]
                         and a.get("offset") == span for a in result["atoms"]):
                atom = _atom(raw, norm, index, m.start(), m.end(), type="dimension",
                    role="labeled", labels=["width"], value=[cm], unit="cm")
                result["atoms"].append(atom)
                added_atoms.append(atom)
    # Explicit option wording is affirmative/negative evidence. No inference
    # is made from a bare absence of 天板 or from sibling values.
    if "天板" in normalized_label and re.fullmatch(r"\s*(?:天板)?\s*(あり|有り|有|付き|なし|無し|無)\s*", norm):
        neg = norm.strip().endswith(("なし", "無し", "無"))
        if not any(a.get("type") == "component_presence" and a.get("component") == "top_board"
                   for a in result["atoms"]):
            result["atoms"].append(_atom(raw, norm, index, 0, len(norm), type="component_presence",
                component="top_board", value=not neg, axis_label=axis_label))
    # Replace only generic literals fully covered by one of the new typed facts.
    result["atoms"] = [a for a in result["atoms"] if not (
        (a.get("type") == "variant" and any(_contains(x.get("offset"), a.get("offset"))
                                               for x in added_atoms))
        or (a.get("type") == "qualifier" and any(_wrapped_qualifier_matches(a, x)
                                                   for x in added_atoms))
    )]
    # Base residue is usually normalized text. Remove only typed quoted spans;
    # any remaining explanation or other residue stays unresolved.
    residue = result.get("residue")
    if isinstance(residue, str):
        for atom in added_atoms:
            token = unicodedata.normalize("NFKC", atom.get("quote", ""))
            if token and token in residue:
                residue = residue.replace(token, "", 1)
        result["residue"] = residue
        result["decomposition"] = "complete" if not residue else "partial"
    return result


def _contains(outer, inner) -> bool:
    return (isinstance(outer, (list, tuple)) and isinstance(inner, (list, tuple)) and len(outer) == len(inner) == 2
            and outer[0] <= inner[0] and inner[1] <= outer[1])


def _wrapped_qualifier_matches(qualifier: dict[str, Any], typed: dict[str, Any]) -> bool:
    """Allow only balanced wrappers around an otherwise exact typed literal."""
    pairs = (("(", ")"), ("[", "]"), ("（", "）"), ("［", "］"), ("【", "】"))
    quote = unicodedata.normalize("NFKC", str(qualifier.get("quote", "")))
    value = unicodedata.normalize("NFKC", str(typed.get("quote", "")))
    for left, right in pairs:
        normalized_left = unicodedata.normalize("NFKC", left)
        normalized_right = unicodedata.normalize("NFKC", right)
        if quote.startswith(normalized_left) and quote.endswith(normalized_right):
            inner = quote[len(normalized_left):-len(normalized_right)]
            return bool(value) and inner == value and _contains(qualifier.get("offset"), typed.get("offset"))
    return False


def title_facts(title: str, color_vocab=frozenset(), variant_tokens=(), *,
                base_title_facts: Callable[..., dict] | None = None,
                base_atomize: Atomizer | None = None) -> dict[str, Any]:
    """Wrap base title facts with explicit top-board evidence and safe negatives."""
    facts = base_title_facts(title, color_vocab, variant_tokens) if base_title_facts else {}
    facts = {k: dict(v) for k, v in facts.items()}
    norm, index = _norm(title)
    parsed = atomize(title, color_vocab=color_vocab, base_atomize=base_atomize)
    for atom in parsed["atoms"]:
        if atom.get("type") != "quantity":
            continue
        family = fact_family(atom)
        bucket = facts.setdefault(family, {"values": {}, "atoms": []})
        if any(a.get("type") == "quantity" and a.get("value") == atom.get("value")
               and a.get("unit") == atom.get("unit") and a.get("offset") == atom.get("offset")
               for a in bucket.get("atoms", [])):
            continue
        bucket.setdefault("atoms", []).append(atom)
        bucket.setdefault("values", {}).setdefault(atom["value"], []).append(atom)
        bucket["single_valued"] = len(bucket["values"]) == 1
    quantity_atoms = [a for bucket in facts.values() for a in bucket.get("atoms", [])
                      if a.get("type") == "quantity"]
    variant_bucket = facts.get("variant")
    if variant_bucket:
        original = variant_bucket.get("atoms", [])
        kept = [a for a in original if not any(
            _contains(q.get("offset"), a.get("offset")) for q in quantity_atoms)]
        if len(kept) != len(original):
            variant_bucket["atoms"] = kept
            values = {}
            for atom in kept:
                values.setdefault(atom["value"], []).append(atom)
            variant_bucket["values"] = values
            variant_bucket["single_valued"] = len(values) == 1
            if not kept:
                facts.pop("variant", None)
    # A closed explicit phrase is enough; a bare mention (e.g. 天板サイズ) is not.
    mentions = list(re.finditer(r"天板\s*(?:なし|無し|無|付き|付|あり|有り|有)|"
                                r"天板\s*[&＆+]\s*[^、,\s】）)]{1,16}付き", norm))
    explicit_top_board = []
    for m in mentions:
        negative = bool(re.search(r"(?:なし|無し|無)$", m.group(0)))
        atom = _atom(title, norm, index, m.start(), m.end(), type="component_presence",
                     component="top_board", value=not negative)
        explicit_top_board.append(atom)
    if explicit_top_board:
        values = {}
        for atom in explicit_top_board:
            values.setdefault(atom["value"], []).append(atom)
        facts["component_presence:top_board"] = {
            "values": values, "atoms": explicit_top_board, "single_valued": len(values) == 1}
    return facts


def fact_family(atom: dict[str, Any], *, base_fact_family: FactFamily | None = None) -> str:
    """Keep legacy families intact; separate quantities by unit/role/component."""
    if atom.get("type") == "quantity":
        return "quantity:{}:{}:{}".format(atom.get("unit", ""), atom.get("component") or "",
                                          atom.get("quantity_role", ""))
    if base_fact_family is not None:
        return base_fact_family(atom)
    kind = atom["type"]
    if kind == "dimension":
        labels = atom.get("labels") or []
        return f"dimension:{atom['role']}:{','.join(labels)}:{len(atom['value'])}"
    if kind in ("component_presence", "component_count", "component_material"):
        return f"{kind}:{atom['component']}"
    return kind


def extract_quantity_facts(text: str, *, source: str = "text", scope_tag: str = "product_page",
                           base_atomize: Atomizer | None = None) -> list[dict]:
    """Extract unit-bearing quantities with literal offsets from one source line."""
    if scope_tag in {"search_keywords", "series_or_sibling_context", "detail_comment"}:
        return []
    parsed = atomize(text, base_atomize=base_atomize)
    return [{"kind": "quantity_fact", "scope": "product_page", "source": source,
             "atom": atom} for atom in parsed["atoms"] if atom.get("type") == "quantity"]

