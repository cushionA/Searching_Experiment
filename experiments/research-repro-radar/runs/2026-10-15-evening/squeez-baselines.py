#!/usr/bin/env python3
"""Recompute character-only baselines for the fixed synthetic Squeez cases."""
import json, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
DATA = ROOT / "experiments/research-repro-radar/staging/squeez-poc/synthetic-tool-logs.json"
SAMPLES = json.loads(DATA.read_text())
STOP = {"find","that","with","from","into","what","when","where","why","how","this","these","those","test","tests","task","failing","related","cause","about","than","the","and","for","are","was","its"}
def select_keywords(sample):
    keys = [w for w in re.findall(r"[a-z0-9]+", sample["question"].lower()) if len(w) >= 4 and w not in STOP]
    return [line for line in sample["context"].splitlines() if any(k in line.lower() for k in keys)]
def compute(kind, selector):
    rows=[]
    for s in SAMPLES:
        if s["kind"] != kind: continue
        selected=selector(s); gold=set(s["gold_lines"]); chosen=set(selected)
        hit=len(gold & chosen); fp=len(chosen-gold); fn=len(gold-chosen)
        chars=len("\n".join(selected)); total=len(s["context"])
        rows.append({"id":s["id"],"input_chars":total,"retained_chars":chars,"char_retention":chars/max(1,total),"selected_line_count":len(selected),"gold_line_hits":hit,"gold_line_count":len(gold),"precision":hit/(hit+fp) if hit+fp else 0,"recall":hit/(hit+fn) if hit+fn else 0})
    total=sum(x["input_chars"] for x in rows); kept=sum(x["retained_chars"] for x in rows); hits=sum(x["gold_line_hits"] for x in rows); gold=sum(x["gold_line_count"] for x in rows)
    return {"n":len(rows),"cases":rows,"input_chars_total":total,"retained_chars_total":kept,"char_retention":kept/max(1,total),"compression_ratio":1-kept/max(1,total),"gold_line_hits":hits,"gold_lines":gold,"gold_recall":hits/max(1,gold)}
def full(s): return s["context"].splitlines()
print(json.dumps({kind:{"full_context":compute(kind,full),"task_conditioned_substring_keywords":compute(kind,select_keywords)} for kind in ("clean","noisy")},ensure_ascii=False,indent=2))
