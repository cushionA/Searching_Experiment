"""Typed atoms for selected SKU values, titles, and scoped page facts.

Atoms keep the literal substring and its offsets in the raw text they came
from. Normalization (NFKC, unit handling, lexicon crosswalks) only changes the
typed value, never the quote. Nothing here reads labels, prices, stock,
shipping, coupons, or model outputs.
"""
from __future__ import annotations

import re
import unicodedata

NAMED_SIZES = ("セミシングル", "シングル", "セミダブル", "ワイドダブル", "ダブル", "クイーン", "キング",
               "ハーフ", "クォーター")
# Standard bedding abbreviations. Method B additionally asks for a page citation.
SIZE_CODES = {"SS": "セミシングル", "S": "シングル", "SD": "セミダブル", "D": "ダブル",
              "Q": "クイーン", "K": "キング", "QT": "クォーター", "H": "ハーフ"}
_CODE_RE = r"SS|SD|QT|XS|S|D|Q|K|H"
TRANSPARENCY = ("半透明", "透明")
FABRIC = {"メッシュ": "メッシュ", "パイル": "パイル", "マイヤー": "マイヤー", "レザー調": "レザー調",
          "ベルベット": "ベルベット"}
SEAT_WIDTH = ("超ワイド", "ワイド", "レギュラー")
# Canonical components; the longest literal wins. "phrase" entries only block
# shorter spellings inside them (毛布になる布団カバー is a cover, not a blanket).
COMPONENTS = sorted([
    ("ミラーレースカーテン", "lace"), ("レースカーテン", "lace"), ("ミラーレース", "lace"), ("レース", "lace"),
    ("完全遮光カーテン", "drape"), ("遮光カーテン", "drape"), ("ドレープカーテン", "drape"), ("カーテン", "drape"),
    ("カーテンフック", "hook"), ("フック", "hook"), ("タッセル", "tassel"),
    ("ドアパーツ", "door"), ("ドア", "door"), ("パネル", "panel"),
    ("電気毛布", "electric_blanket"), ("毛布", "blanket"), ("毛布になる", "phrase"),
    ("ひじ掛け", "armrest"), ("肘掛け", "armrest"), ("肘掛", "armrest"), ("ひじ掛", "armrest"),
    ("天板", "top_board"), ("本棚", "bookshelf"), ("持ち手", "handle"), ("収納袋", "storage_bag"),
    ("布団カバー", "duvet_cover"), ("布団", "futon"), ("敷きパッド", "pad"), ("マットレス", "mattress"),
], key=lambda x: len(x[0]), reverse=True)
COMPONENT_WORDS = {word: name for word, name in COMPONENTS}
_COMPONENT_RE = "|".join(re.escape(w) for w, name in COMPONENTS if name != "phrase")
DIM_LABELS = {"幅": "width", "横幅": "width", "丈": "length", "長さ": "length", "高さ": "height",
              "奥行き": "depth", "奥行": "depth", "直径": "diameter", "径": "diameter",
              "厚さ": "thickness", "厚み": "thickness"}
_DIM_LABEL_RE = "|".join(sorted((re.escape(k) for k in DIM_LABELS), key=len, reverse=True))
_NUM = r"\d+(?:\.\d+)?"
_UNIT = r"(?:mm|cm|m)(?![a-zA-Z])"
DECORATIONS = re.compile(r"≪NEW≫|《NEW》|【新色】|新色\*|NEW|\(約\)|約|^[・\s]+|[。!！]+$")
_SEPARATOR = r"[\s/・×xX+＋()\[\]【】、,:：*~〜\-<>〈〉]"
SECTION_HEADINGS = {
    "内容": "contents", "セット内容": "contents", "付属品": "contents",
    "サイズ": "size", "サイズ(約)": "size", "サイズ/重さ": "size", "寸法": "size",
    "カラー": "color", "色": "color", "タイプ": "type", "特徴": "features",
    "素材": "material", "材質": "material", "重量": "weight", "重さ": "weight", "重量(約)": "weight",
    "耐荷重": "load", "最大耐荷重": "load", "保証期間": "ignore", "注意事項": "notes", "備考": "ignore",
    "送料": "ignore", "配送について": "ignore", "生地": "fabric", "選べる段数": "tier_options",
    "硬さ": "spec", "密度": "spec", "仕上げ加工": "spec", "固定用スナップ": "spec", "ポール径": "spec",
    "リクライニング": "spec", "折りたたみサイズ": "folded", "収納サイズ": "folded", "遮光等級": "spec",
    "生産国": "ignore", "毛布(オプション)": "option_size",
}
OBJECT_HEADINGS = ("パネル", "ドアパーツ", "ドア")
LABEL_HEADINGS = ("新色", "NEW", "1級遮光", "2級遮光")


def nfkc_map(raw: str) -> tuple[str, list[int]]:
    """NFKC text plus the raw index for each normalized character."""
    chars, index = [], []
    for i, ch in enumerate(raw):
        for out in unicodedata.normalize("NFKC", ch):
            chars.append(out)
            index.append(i)
    return "".join(chars), index


def compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _named_size_boundary(norm: str, start: int, end: int) -> bool:
    """Named sizes are words: ランキング does not contain the size キング."""
    before = norm[start - 1:start]
    after = norm[end:end + 3]
    return not re.match(r"[ァ-ヶー]", before) and (not re.match(r"[ァ-ヶー]", after) or after.startswith("サイズ"))


def _num(text: str, unit: str | None) -> float:
    value = float(text)
    unit = (unit or "cm").lower()
    return value / 10 if unit == "mm" else value * 100 if unit == "m" else value


class Text:
    """Normalized text with consumption tracking back to raw offsets."""

    def __init__(self, raw: str):
        self.raw = raw
        self.norm, self.index = nfkc_map(raw)
        self.used = [False] * len(self.norm)

    def raw_span(self, start: int, end: int) -> tuple[int, int]:
        return self.index[start], self.index[end - 1] + 1

    def raw_from(self, start: int) -> int:
        return self.index[start] if start < len(self.index) else len(self.raw)

    def take(self, start: int, end: int):
        for i in range(start, end):
            self.used[i] = True

    def free(self, start: int, end: int) -> bool:
        return not any(self.used[start:end])

    def atom(self, start: int, end: int, **fields) -> dict:
        s, e = self.raw_span(start, end)
        self.take(start, end)
        return {**fields, "quote": self.raw[s:e], "offset": [s, e]}

    def free_runs(self):
        start = None
        for i, ch in enumerate(self.norm + " "):
            usable = i < len(self.norm) and not self.used[i] and not re.match(_SEPARATOR, ch)
            if usable and start is None:
                start = i
            elif not usable and start is not None:
                yield start, i
                start = None

    def residue(self) -> str:
        return "".join(self.norm[s:e] for s, e in self.free_runs())


def _dims(text: Text, role_hint: str | None, out: list):
    seq = re.compile(rf"(?:({_DIM_LABEL_RE})\s*)?({_NUM})\s*({_UNIT})?"
                     rf"(?:\s*[×xX*]\s*(?:({_DIM_LABEL_RE})\s*)?({_NUM})\s*({_UNIT})?)?"
                     rf"(?:\s*[×xX*]\s*(?:({_DIM_LABEL_RE})\s*)?({_NUM})\s*({_UNIT})?)?")
    for m in seq.finditer(text.norm):
        if m.group(2) is None or not text.free(m.start(), m.end()):
            continue
        if m.start() > 0 and re.match(r"[A-Za-z\d.]", text.norm[m.start() - 1]):
            continue
        parts = [(m.group(i), m.group(i + 1), m.group(i + 2)) for i in (1, 4, 7) if m.group(i + 1)]
        units = [p[2] for p in parts if p[2]]
        labels = [DIM_LABELS.get(p[0]) if p[0] else None for p in parts]
        follow = text.norm[m.end():m.end() + 1]
        if len(parts) == 1 and (not units and not labels[0] or re.match(r"[枚段層個本点kgK%℃NDd]", follow)):
            continue
        unit = units[-1] if units else "cm"
        values = [_num(p[1], p[2] or unit) for p in parts]
        diameter_suffix = re.match(r"\s*\((?:直径|径)\)", text.norm[m.end():])
        end = m.end() + (diameter_suffix.end() if diameter_suffix else 0)
        if diameter_suffix and len(values) == 1:
            labels = ["diameter"]
        if any(labels):
            atom = {"type": "dimension", "role": "labeled", "labels": [x or "unlabeled" for x in labels],
                    "value": values}
        elif role_hint == "top_size":
            atom = {"type": "dimension", "role": "top_size", "value": values}
        elif role_hint in DIM_LABELS.values() and len(values) == 1:
            atom = {"type": "dimension", "role": "labeled", "labels": [role_hint], "value": values}
        else:
            atom = {"type": "dimension", "role": "generic", "value": values}
        out.append(text.atom(m.start(), end, **atom))


def atomize(raw: str, axis_label: str | None = None, color_vocab: frozenset = frozenset(),
            family: tuple = ()) -> dict:
    """Decompose one literal value into typed atoms with raw offsets.

    `family` is the tuple of sibling values on the same selection axis. It is
    only used to give `なし` on a generic option axis its explicit meaning: the
    absence of every component the sibling options add.
    """
    text = Text(raw)
    atoms: list[dict] = []
    label = compact(axis_label or "")
    for m in DECORATIONS.finditer(text.norm):
        text.take(m.start(), m.end())
    whole = compact(text.norm)
    axis_component = next((name for word, name in COMPONENTS if word in label and name != "phrase"), None)
    if whole in ("あり", "有り", "有", "付き", "なし", "無し", "無"):
        present = whole not in ("なし", "無し", "無")
        if axis_component:
            atoms.append(text.atom(0, len(text.norm), type="component_presence", component=axis_component,
                                   value=present, axis_label=axis_label))
        elif not present:
            added = sorted({a["component"] for sibling in family if compact(sibling) != whole
                            for a in atomize(sibling, axis_label, color_vocab)["atoms"]
                            if a["type"] == "component_presence" and a["value"]})
            for component in added:
                atoms.append(text.atom(0, len(text.norm), type="component_presence", component=component,
                                       value=False, derivation="none_option_excludes_sibling_addon",
                                       axis_label=axis_label))
    for m in re.finditer(r"(\d+)\s*枚\s*(?:セット|組|set)|\((\d+)枚(?:組|セット)?\)", text.norm):
        if text.free(m.start(), m.end()):
            count = int(m.group(1) or m.group(2))
            atoms.append(text.atom(m.start(), m.end(), type="piece_total", value=count))
    for m in re.finditer(rf"({_COMPONENT_RE})\s*[×xX]?\s*(\d+)\s*(枚|個|本|点|台)", text.norm):
        if text.free(m.start(), m.end()):
            atoms.append(text.atom(m.start(), m.end(), type="component_count",
                                   component=COMPONENT_WORDS[m.group(1)], value=int(m.group(2))))
    for m in re.finditer(r"パネルのみ", text.norm):
        if text.free(m.start(), m.end()):
            atoms.append(text.atom(m.start(), m.end(), type="component_presence", component="door", value=False,
                                   derivation="only_panels_excludes_door"))
    for m in re.finditer(rf"({_COMPONENT_RE})\s*(なし|無し|付き|付|あり|有り|セット)", text.norm):
        if text.free(m.start(), m.end()):
            atoms.append(text.atom(m.start(), m.end(), type="component_presence",
                                   component=COMPONENT_WORDS[m.group(1)], value=m.group(2) not in ("なし", "無し")))
    _dims(text, "height" if "高さ" in label else "top_size" if "天板" in label else None, atoms)
    for m in re.finditer(r"(\d+)\s*段(?!階|ギア)", text.norm):
        if text.free(m.start(), m.end()):
            atoms.append(text.atom(m.start(), m.end(), type="tier_count", value=int(m.group(1))))
    for size in NAMED_SIZES:
        for m in re.finditer(size, text.norm):
            if text.free(m.start(), m.end()) and _named_size_boundary(text.norm, m.start(), m.end()):
                atoms.append(text.atom(m.start(), m.end(), type="named_size", value=size))
    for word in TRANSPARENCY:
        for m in re.finditer(word, text.norm):
            if text.free(m.start(), m.end()):
                atoms.append(text.atom(m.start(), m.end(), type="transparency", value=word))
    for m in re.finditer(r"\(([^()]+)\)", text.norm):
        if not text.free(m.start(1), m.end(1)):
            continue
        inner = re.sub(r"生地$", "", m.group(1).strip())
        fabric = next((v for k, v in FABRIC.items() if inner == k or inner.startswith(k)), None)
        if fabric:
            atoms.append(text.atom(m.start(), m.end(), type="fabric", value=fabric))
        elif inner in SEAT_WIDTH:
            atoms.append(text.atom(m.start(), m.end(), type="seat_width", value=inner))
        else:
            atoms.append(text.atom(m.start(), m.end(), type="qualifier", value=inner))
    color_axis = any(w in label for w in ("カラー", "色"))
    for start, end in list(text.free_runs()):
        token = text.norm[start:end]
        typed_end = end
        suffix = re.search(r"タイプ$", token)
        value = token[:suffix.start()] if suffix else token
        if not value:
            text.take(start, end)
            continue
        if value in color_vocab or (color_axis and value not in FABRIC and not re.search(r"\d", value)):
            atoms.append(text.atom(start, typed_end, type="color", value=value))
        else:
            atoms.append(text.atom(start, typed_end, type="variant", value=value,
                                   typed=False))
    residue = text.residue()
    return {"raw": raw, "atoms": atoms, "residue": residue,
            "decomposition": "complete" if not residue else "partial"}


def color_base_vocab(values) -> frozenset:
    """Color words observed on explicit color axes, with qualifiers removed."""
    out = set()
    for value in values:
        norm = unicodedata.normalize("NFKC", value)
        norm = re.sub(r"\([^()]*\)", "", DECORATIONS.sub("", norm)).strip()
        if norm and not re.search(r"\d", norm) and norm not in TRANSPARENCY:
            out.add(norm)
    return frozenset(out)


def fact_family(atom: dict) -> str:
    kind = atom["type"]
    if kind == "dimension":
        labels = atom.get("labels") or []
        return f"dimension:{atom['role']}:{','.join(labels)}:{len(atom['value'])}"
    if kind in ("component_presence", "component_count", "component_material"):
        return f"{kind}:{atom['component']}"
    return kind


def atom_value_key(atom: dict):
    value = atom["value"]
    return tuple(value) if isinstance(value, list) else value


def group_facts(atoms: list[dict]) -> dict:
    families: dict[str, dict] = {}
    for atom in atoms:
        fam = families.setdefault(fact_family(atom), {"values": {}, "atoms": []})
        fam["atoms"].append(atom)
        fam["values"].setdefault(atom_value_key(atom), []).append(atom)
    for fam in families.values():
        fam["single_valued"] = len(fam["values"]) == 1
    return families


# Product-level facts from titles ---------------------------------------------------

def title_facts(title: str, color_vocab: frozenset, variant_tokens: tuple) -> dict:
    """Facts per family; a family with more than one value is series-level."""
    text = Text(title)
    found: list[dict] = []
    for m in re.finditer(r"(\d+)\s*枚\s*(?:セット|組)|(?<![\d.])(\d+)\s*枚(?!合わせ|\d)", text.norm):
        found.append(text.atom(m.start(), m.end(), type="piece_total", value=int(m.group(1) or m.group(2))))
    for m in re.finditer(r"(?<![\d.])(\d+)\s*段(?!階|ギア)", text.norm):
        found.append(text.atom(m.start(), m.end(), type="tier_count", value=int(m.group(1))))
    for m in re.finditer(rf"(?<![\d.])({_NUM})\s*(?:cm)?\s*[×xX]\s*({_NUM})\s*(cm)?", text.norm):
        if text.free(m.start(), m.end()):
            found.append(text.atom(m.start(), m.end(), type="dimension", role="generic",
                                   value=[float(m.group(1)), float(m.group(2))]))
    for m in re.finditer(rf"({_DIM_LABEL_RE})\s*({_NUM})\s*({_UNIT})?", text.norm):
        if text.free(m.start(), m.end()):
            found.append(text.atom(m.start(), m.end(), type="dimension", role="labeled",
                                   labels=[DIM_LABELS[m.group(1)]], value=[_num(m.group(2), m.group(3))]))
    for size in NAMED_SIZES:
        for m in re.finditer(size, text.norm):
            if text.free(m.start(), m.end()) and _named_size_boundary(text.norm, m.start(), m.end()):
                found.append(text.atom(m.start(), m.end(), type="named_size", value=size))
    for word in TRANSPARENCY:
        for m in re.finditer(word, text.norm):
            if text.free(m.start(), m.end()):
                found.append(text.atom(m.start(), m.end(), type="transparency", value=word))
    for m in re.finditer(rf"({_COMPONENT_RE})(セット|付き|付)?", text.norm):
        name = COMPONENT_WORDS[m.group(1)]
        # Bare 本棚/天板/ドア in a title is usually a use-case keyword; require 付き/セット.
        explicit = m.group(2) is not None or name in ("lace", "armrest")
        if name in ("lace", "armrest", "door", "top_board", "bookshelf") and explicit and text.free(m.start(), m.end()):
            found.append(text.atom(m.start(), m.end(), type="component_presence", component=name, value=True))
    _literal_tokens(text, variant_tokens, "variant", found)
    _literal_tokens(text, tuple(color_vocab), "color", found, word_boundary=True)
    return group_facts(found)


def _literal_tokens(text: Text, tokens: tuple, kind: str, out: list, word_boundary: bool = False):
    for token in sorted(set(tokens), key=len, reverse=True):
        norm_token = unicodedata.normalize("NFKC", token)
        for m in re.finditer(re.escape(norm_token), text.norm):
            if not text.free(m.start(), m.end()):
                continue
            if word_boundary:
                before = text.norm[m.start() - 1:m.start()]
                after = text.norm[m.end():m.end() + 1]
                # A color inside a longer katakana word is not a color claim.
                if re.match(r"[ァ-ヶー]", before) or re.match(r"[ァ-ヶー]", after):
                    continue
            out.append(text.atom(m.start(), m.end(), type=kind, value=token))


# Scoped description facts ---------------------------------------------------------

def parse_condition(raw: str, color_vocab: frozenset, variant_tokens: tuple) -> list[dict] | None:
    """Typed SKU conditions from a bracket heading or inline clause; None if not parseable."""
    norm = compact(raw)
    if not norm:
        return None
    code = re.fullmatch(rf"({_CODE_RE})(?:サイズ)?", norm)
    if code:
        name = SIZE_CODES.get(code.group(1))
        return [{"type": "named_size", "value": name, "code": code.group(1), "quote": raw}] if name else None
    cleaned = re.sub(r"(を|が)?選択(の場合|時|の方)?$", "", norm).replace("の", " ").replace("+", " ")
    parsed = atomize(cleaned, color_vocab=color_vocab)
    atoms = []
    for atom in parsed["atoms"]:
        if atom["type"] == "variant":
            if atom["value"] in variant_tokens:
                atoms.append(atom)
            elif atom["value"] in ("天板", "横", "枚", "セット"):
                continue
            else:
                return None
        else:
            atoms.append(atom)
    return atoms or None


def code_crosswalk(text: str) -> list[dict]:
    """Literal code-to-name crosswalks such as QT(クォーター) in one page line."""
    out = []
    t = Text(text)
    pattern = re.compile(rf"({_CODE_RE})\s*\(\s*(" + "|".join(NAMED_SIZES) + r")\s*\)")
    for m in pattern.finditer(t.norm):
        if SIZE_CODES.get(m.group(1)) == m.group(2):
            s, e = t.raw_span(m.start(), m.end())
            out.append({"code": m.group(1), "named_size": m.group(2), "offset": [s, e], "quote": text[s:e]})
    return out


def _shift(atoms: list[dict], shift: int) -> list[dict]:
    return [{**a, "offset": [a["offset"][0] + shift, a["offset"][1] + shift]} for a in atoms]


def _contents_item(raw: str) -> dict:
    text = Text(raw)
    for m in re.finditer(r"^[・\s]+", text.norm):
        text.take(m.start(), m.end())
    quantity = None
    q = re.search(r"[×xX]\s*(\d+)\s*(枚|個|本|点|袋|台)?\s*$|\s(\d+)\s*(枚|個|本|点|袋|台)\s*$", text.norm)
    if q:
        quantity = int(q.group(1) or q.group(3))
    components = []
    for word, name in COMPONENTS:
        for m in re.finditer(re.escape(word), text.norm):
            if text.free(m.start(), m.end()):
                if name != "phrase":
                    components.append(text.atom(m.start(), m.end(), type="component", component=name, value=word))
                else:
                    text.take(m.start(), m.end())
    named = []
    for size in NAMED_SIZES:
        for m in re.finditer(size, text.norm):
            if text.free(m.start(), m.end()) and _named_size_boundary(text.norm, m.start(), m.end()):
                named.append(text.atom(m.start(), m.end(), type="named_size", value=size))
    return {"quantity": quantity, "components": components, "named_sizes": named}


def describe_lines(lines: list[dict], color_vocab: frozenset, variant_tokens: tuple) -> list[dict]:
    """Turn ordered page lines into scoped facts.

    Each line dict has `text`, an optional verified literal `span`, and a
    `scope_tag`. Lines before the specification heading are promotional or
    lineup prose and contribute only explicit page declarations. Search-keyword,
    detail-policy, and sibling lineup blocks never contribute facts. Offsets in
    returned atoms are relative to the raw line.
    """
    facts: list[dict] = []
    in_spec, section, condition, object_role = False, None, None, None
    for index, line in enumerate(lines):
        raw = line["text"]
        norm = compact(raw)
        if not norm or line.get("scope_tag") in ("series_or_sibling_context", "search_keywords", "detail_comment"):
            continue
        base = {"line_index": index, "line": raw, "span": line.get("span"), "scope_tag": line.get("scope_tag")}
        decl = re.fullmatch(r"こちらのページは(.+?)です", norm)
        if decl:
            for atom in _declaration_atoms(raw, color_vocab, variant_tokens):
                facts.append({**base, "kind": "page_declaration", "scope": "product_page", "atom": atom,
                              "section": None, "condition": None})
            continue
        if norm == "商品詳細":
            in_spec, section, condition, object_role = True, None, None, None
            continue
        if not in_spec:
            continue
        heading = SECTION_HEADINGS.get(norm)
        if heading:
            section, condition, object_role = heading, None, None
            continue
        if norm.startswith("▼"):
            in_spec = False
            continue
        bracket = re.fullmatch(r"【([^】]+)】", norm)
        if bracket:
            inner = bracket.group(1)
            if inner in OBJECT_HEADINGS:
                object_role, condition = "object:" + inner, None
            elif inner in LABEL_HEADINGS or inner.endswith("生地"):
                condition = {"kind": "label", "text": inner}
            else:
                parsed = parse_condition(inner, color_vocab, variant_tokens)
                condition = {"kind": "sku_condition" if parsed else "unparsed", "text": inner,
                             "atoms": parsed or [], "line": raw, "span": line.get("span")}
            continue
        if section in (None, "ignore", "weight", "load", "spec", "color", "fabric"):
            continue
        normalized = unicodedata.normalize("NFKC", raw)
        inline = None
        selected = re.search(r"※(.+?)(?:を|が)?選択(?:の場合|時|の方)", normalized)
        if selected:
            parsed = parse_condition(selected.group(1), color_vocab, variant_tokens)
            inline = {"kind": "sku_condition" if parsed else "unparsed", "text": selected.group(1),
                      "atoms": parsed or []}
        scope = {"section": section, "condition": condition, "object_role": object_role, "inline_condition": inline}
        exception = re.search(r"(.+?)(?:には|は)(.+?)が(?:ありません|付属しません|付いていません|ございません)", normalized)
        if exception:
            cond = parse_condition(re.sub(r"^[※・\s]+", "", exception.group(1)), color_vocab, variant_tokens)
            comp = next((name for word, name in COMPONENTS if word in exception.group(2) and name != "phrase"), None)
            if comp:
                facts.append({**base, **scope, "kind": "conditional_exception", "scope": "conditional",
                              "condition": {"kind": "sku_condition" if cond else "unparsed",
                                            "text": exception.group(1), "atoms": cond or []},
                              "atom": {"type": "component_presence", "component": comp, "value": False,
                                       "quote": raw, "offset": [0, len(raw)]}})
            continue
        not_included = re.search(r"([^\s※・]+?)は付属しておりません", normalized)
        if not_included:
            comp = COMPONENT_WORDS.get(not_included.group(1))
            if comp and comp != "phrase":
                facts.append({**base, **scope, "kind": "explicit_absence", "scope": "product_page",
                              "atom": {"type": "component_presence", "component": comp, "value": False,
                                       "quote": raw, "offset": [0, len(raw)]}})
            continue
        if section == "notes" or normalized.lstrip().startswith("※"):
            continue
        if section == "contents":
            # An inline ※ clause scopes the line; it is not part of the item.
            cut = raw.find("※")
            facts.append({**base, **scope, "kind": "contents_item", "scope": "contents",
                          "item": _contents_item(raw[:cut] if cut > 0 else raw)})
            continue
        text = Text(raw)
        labeled = re.match(r"^[・\s]*([^：:]{1,16})[：:]\s*(.+)$", text.norm)
        label, value_start = None, 0
        if labeled:
            label = compact(labeled.group(1))
            value_start = text.raw_from(labeled.start(2))
        if section == "material":
            comp = COMPONENT_WORDS.get(label or "")
            if comp and comp != "phrase":
                value_raw = raw[value_start:].strip()
                facts.append({**base, **scope, "kind": "component_material", "scope": "spec",
                              "atom": {"type": "component_material", "component": comp, "value": compact(value_raw),
                                       "quote": raw, "offset": [0, len(raw)]}})
                facts.append({**base, **scope, "kind": "spec_presence", "scope": "spec",
                              "atom": {"type": "component_presence", "component": comp, "value": True,
                                       "quote": raw, "offset": [0, len(raw)]}})
            continue
        if section in ("size", "folded", "option_size"):
            role, label_condition = object_role, None
            if label:
                bare = label.strip("【】")
                code = re.fullmatch(rf"({_CODE_RE})(?:\((.+?)\))?", bare)
                if code:
                    label_condition = {"code": code.group(1), "name": SIZE_CODES.get(code.group(1)),
                                       "page_name": code.group(2)}
                elif re.sub(r"サイズ$", "", bare) in NAMED_SIZES:
                    label_condition = {"name": re.sub(r"サイズ$", "", bare)}
                elif "天板" in bare:
                    role = "top_size"
                elif not re.fullmatch(r"本体|サイズ", bare):
                    parsed = parse_condition(bare, color_vocab, variant_tokens)
                    if parsed and all(a["type"] in ("tier_count", "variant", "named_size", "piece_total") for a in parsed):
                        label_condition = {"atoms": parsed, "text": bare}
                    else:
                        role = "object:" + bare
            if section != "size":
                role = role or "object:" + section
            parsed = atomize(raw[value_start:], color_vocab=color_vocab)
            for atom in _shift(parsed["atoms"], value_start):
                if atom["type"] not in ("dimension", "named_size", "piece_total"):
                    continue
                if atom["type"] == "dimension" and role == "top_size":
                    atom = {**atom, "role": "top_size", "labels": None}
                facts.append({**base, **scope, "kind": "size_line", "scope": "spec", "atom": atom,
                              "label": label, "label_condition": label_condition,
                              "object_role": role if role and role.startswith("object:") else None})
            continue
        if section in ("type", "features", "tier_options"):
            parsed = atomize(raw, color_vocab=color_vocab)
            for atom in parsed["atoms"]:
                if atom["type"] == "variant" and atom["value"] not in variant_tokens:
                    continue
                if atom["type"] in ("variant", "tier_count", "component_presence"):
                    facts.append({**base, **scope, "kind": f"{section}_line", "scope": "spec", "atom": atom})
    return facts


def _declaration_atoms(raw: str, color_vocab, variant_tokens) -> list[dict]:
    text = Text(raw)
    m = re.search(r"こちらのページは\s*(.+?)\s*です", text.norm)
    if not m:
        return []
    body_start = m.start(1)
    body = text.norm[m.start(1):m.end(1)]
    code = re.fullmatch(rf"[【\[]?({_CODE_RE})サイズ[】\]]?", body)
    if code:
        name = SIZE_CODES.get(code.group(1))
        start = body_start + body.find(code.group(1))
        if not name:
            return []
        return [text.atom(start, start + len(code.group(1)), type="named_size", value=name,
                          code=code.group(1), derivation="size_code_lexicon")]
    raw_start = text.raw_from(body_start)
    raw_end = text.raw_from(m.end(1))
    parsed = atomize(raw[raw_start:raw_end].replace(":", " ").replace("：", " "), color_vocab=color_vocab)
    atoms = []
    for atom in _shift(parsed["atoms"], raw_start):
        if atom["type"] in ("named_size", "dimension", "piece_total", "tier_count"):
            atoms.append(atom)
        elif atom["type"] == "variant" and re.fullmatch(r"(.+)サイズ", atom["value"]):
            name = atom["value"][:-3]
            if name in NAMED_SIZES:
                atoms.append({**atom, "type": "named_size", "value": name})
    return atoms
