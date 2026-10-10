"""Restore bounded full-contents evidence locally beside unmodified exported links."""
from pathlib import Path
from datetime import datetime, timezone
from html.parser import HTMLParser
from zipfile import ZipFile
import re
import sys
import json
HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]
sys.path.insert(0, str(EXP))
import trial_luna_sku_matching as core
import sku_luna_closed_contents_task as closed
import sku_luna_inputs as inputs


class _VisibleLines(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = []
        self.parts = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "br":
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def finish(self):
        text = "".join(self.parts)
        return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _source_raw_field(case, source_id):
    record = closed._source_record(case, source_id)
    if not isinstance(record, dict):
        raise ValueError("S30 provenance record missing")
    provenance = record.get("source")
    if isinstance(provenance, list):
        provenance = next((x for x in provenance if x.get("locator") == "$.itemInfo.extraItemComment"), None)
    if not isinstance(provenance, dict) or provenance.get("locator") != "$.itemInfo.extraItemComment":
        raise ValueError("S30 is not bound to the fixed AU extraItemComment field")
    raw_file, expected = provenance.get("raw_file"), provenance.get("sha256")
    if not isinstance(raw_file, str) or not isinstance(expected, str):
        raise ValueError("S30 raw source hash/path missing")
    with ZipFile(inputs.DEFAULT_ZIP) as archive:
        raw = inputs._ArchiveSources(archive).raw(raw_file, expected)
    document = json.loads(raw.decode("utf-8"))
    html = inputs._ArchiveSources._resolve_json_path(document, "$.itemInfo.extraItemComment")
    if not isinstance(html, str):
        raise ValueError("extraItemComment is not text")
    return raw_file, expected, html


def _proof_details(case, contexts, source):
    sid = source["source_id"]
    raw_file, field_sha, html = _source_raw_field(case, sid)
    row_rx = re.compile(r"<tr\b[^>]*>.*?</tr\s*>", re.I | re.S)
    rows = list(row_rx.finditer(html))
    matches = [i for i, m in enumerate(rows) if m.group(0) == source["raw_bounded_html_row"]]
    if len(matches) != 1:
        raise ValueError("bounded HTML row is not uniquely present in hashed source field")
    idx = matches[0]
    row = rows[idx]
    if idx + 1 >= len(rows) or not re.search(r"<b\b[^>]*>\s*カラー\s*</b\s*>", rows[idx + 1].group(0), re.I):
        raise ValueError("bounded contents row lacks the expected next-section boundary")
    next_row = rows[idx + 1]
    # The source-context builder's visible row must normalize to this exact bounded HTML row.
    source_ctx = contexts[sid]
    context_row = " ".join(source_ctx.get("contexts", []))
    visible = re.sub(r"^tr:\s*", "", context_row)
    raw_visible = _VisibleLines(); raw_visible.feed(row.group(0))
    normalized_visible = " ".join(raw_visible.finish())
    normalized_context = re.sub(r"\s+", " ", visible).strip()
    if normalized_visible != normalized_context:
        raise ValueError("S30 visible context does not match bounded raw row")
    cells = re.findall(r"<td\b[^>]*>(.*?)</td\s*>", row.group(0), re.I | re.S)
    if len(cells) < 2:
        raise ValueError("contents row does not contain label and value cells")
    content_parser = _VisibleLines(); content_parser.feed(cells[1])
    lines = content_parser.finish()
    if not lines:
        raise ValueError("bounded contents row has no component lines")
    line_bindings = []
    for line in lines:
        fragment_ids = []
        for fid, fragment in contexts.items():
            if fid == sid or fragment.get("text") != line or context_row not in fragment.get("contexts", []):
                continue
            provenance = fragment.get("provenance", [])
            if any(x.get("sha256") == field_sha and x.get("locator") == "$.itemInfo.extraItemComment" for x in provenance):
                fragment_ids.append(fid)
        if not fragment_ids:
            raise ValueError(f"component line lacks source-context binding: {line}")
        line_bindings.append({"text": line, "source_ids": fragment_ids,
                              "source_scopes": {fid: contexts[fid].get("scope") for fid in fragment_ids},
                              "context_sha256": core.sha(core.canonical([contexts[fid] for fid in fragment_ids])),
                              "context_contains_exact_bounded_row": all(context_row in contexts[fid].get("contexts", []) for fid in fragment_ids)})
    next_source_ids = []
    for fid, fragment in contexts.items():
        if fragment.get("text") != "カラー" or not any(
                x.get("sha256") == field_sha and x.get("locator") == "$.itemInfo.extraItemComment"
                for x in fragment.get("provenance", [])):
            continue
        if any(re.sub(r"\s+", " ", value).strip().startswith("tr: カラー")
               for value in fragment.get("contexts", [])):
            next_source_ids.append(fid)
    if not next_source_ids:
        raise ValueError("next-section boundary lacks a source-context fragment")
    return {
        "raw_source_file": raw_file,
        "raw_source_sha256": field_sha,
        "json_field_path": "$.itemInfo.extraItemComment",
        "field_unicode_sha256": core.sha(html.encode("utf-8")),
        "field_span_unicode_codepoints": {"start": row.start(), "end": row.end()},
        "bounded_row_sha256": core.sha(row.group(0).encode("utf-8")),
        "bounded_row_exact_match": True,
        "next_section": {"label": "カラー", "source_ids": next_source_ids,
                          "field_span_unicode_codepoints": {"start": next_row.start(), "end": next_row.end()},
                          "raw_row_sha256": core.sha(next_row.group(0).encode("utf-8"))},
        "source_context_canonical_sha256": core.sha(core.canonical(source_ctx)),
        "source_context_visible_text_sha256": core.sha(visible.encode("utf-8")),
        "visible_context_matches_bounded_row": True,
        "component_lines": line_bindings,
        "component_source_ids": sorted({sid for item in line_bindings for sid in item["source_ids"]}),
        "component_context_bindings_all_exact": all(item["context_contains_exact_bounded_row"] for item in line_bindings),
        "coverage_binding": {"contents_label_source_id": sid,
                             "fragment_ids_cover_all_value_cell_lines": True,
                             "note": "enumerates the bounded contents cell; completeness/no-lace meaning remains a semantic-review judgment"}
    }


def main():
    out = HERE / "links-contents-proof.json"
    if out.exists(): raise RuntimeError("refusing overwrite contents proof")
    cases = {c["case_id"]: c for c in core.read(HERE / "repaired/inputs.json")}
    contexts = core.read(HERE / "repaired/source-contexts.json")
    link_path = HERE / "links-v8.jsonl"
    link_lines = [line for line in link_path.read_bytes().splitlines(keepends=True) if line.strip()]
    links = [json.loads(line) for line in link_lines]
    proofs = []
    for link, link_line in zip(links, link_lines, strict=True):
        cid = link["case_id"]
        case = cases[cid]
        source = closed._raw_contents_block(case, contexts[cid])
        row = next(r for r in case["au_rows"] if r["row_key"] == link["au_row_key"])
        lace = next(r for r in case["rakuten_conditions"] if r["axis"] == "レースカーテン")
        lace_check = next((check for check in link.get("evidence", {}).get("rakuten_checks", [])
                           if check.get("condition_id") == lace.get("condition_id")), None)
        if not lace_check or lace_check.get("status") != "support":
            raise ValueError(f"exported lace check must support the current selected condition {cid}")
        proof_details = _proof_details(case, contexts[cid], source)
        cited_source_ids = {entry.get("source_id") for entry in lace_check.get("evidence", [])}
        covered_source_ids = {source["source_id"], *proof_details["component_source_ids"], *proof_details["next_section"]["source_ids"]}
        positive_ids = {ref for item in proof_details["component_lines"] if "レースカーテン" in item["text"] for ref in item["source_ids"]}
        if lace["value"] == "なし" and not cited_source_ids.intersection({source["source_id"], *proof_details["component_source_ids"]}):
            raise ValueError(f"negative lace lacks a citation whose exact context is the bounded complete contents {cid}")
        if lace["value"] == "あり" and not positive_ids.intersection(cited_source_ids):
            raise ValueError(f"positive lace lacks an explicit lace-component citation {cid}")
        auxiliary = {}
        for ref in cited_source_ids - covered_source_ids:
            if ref in contexts[cid] and contexts[cid][ref]["scope"] == "fixed_product":
                auxiliary[ref] = contexts[cid][ref]
            elif isinstance(ref, str) and ref.startswith("A") and ref[1:].isdigit() and int(ref[1:]) < len(row["conditions"]):
                auxiliary[ref] = row["conditions"][int(ref[1:])]
            else:
                raise ValueError(f"auxiliary lace evidence not bound to the fixed product/current row {cid}")
        proofs.append({"case_id": cid, "link_line_sha256": core.sha(link_line),
           "au_row_key": row["row_key"],
           "selected_lace": {"condition_id": lace["condition_id"], "axis": lace["axis"], "value": lace["value"]},
           "lace_matching_evidence": lace_check,
           "current_au_conditions": [{"condition_id": c["condition_id"], "axis": c["axis"], "value": c["value"], "source": c["source"]} for c in row["conditions"]],
           "matching_answer_sha256": link["answer_sha256"], "semantic_review_file_sha256": link["review_file_sha256"],
           "bounded_contents": source,
           "bounded_contents_proof": proof_details,
           "lace_evidence_sources_resolved": True,
           "auxiliary_fixed_product_or_current_row_sources": auxiliary,
           "full_contents_context_anchor_ids": sorted(cited_source_ids.intersection({source["source_id"], *proof_details["component_source_ids"]})),
           "local_visible_context": contexts[cid][source["source_id"]],
           "interpretation": "component enumeration is bound to the complete bounded contents row and next-section boundary; whether this excludes lace remains a semantic-review judgment, not an inference from missing wording alone"})
    if len({p["case_id"] for p in proofs}) != len(proofs):
        raise ValueError("duplicate case link in exported links")
    core.save(out, {"createdUTC": datetime.now(timezone.utc).isoformat(), "link_count": len(links),
        "links_sha256": core.sha(link_path.read_bytes()),
        "provider_output_preserved": True, "restored_locally": True, "proofs": proofs})
    print(f"local bounded contents proof restored for {len(proofs)} links")
if __name__ == "__main__": main()
