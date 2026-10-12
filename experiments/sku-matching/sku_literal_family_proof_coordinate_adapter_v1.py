"""Coordinate-aware source helpers around the unchanged ef00 literal proof functions."""
from __future__ import annotations
from collections import Counter
from types import FunctionType
import re
from urllib.parse import unquote,urlsplit

import sku_literal_family_proof_v1 as frozen

_AU_VALUE_PATH = re.compile(r"^\$\.itemInfo\.skuInfo\.(row|column)Names\[(0|[1-9][0-9]*)\]$")
_RAK_LABEL_PATH = re.compile(r"^variantSelectors\[(0|[1-9][0-9]*)\]\.label$")
_RAK_VALUE_PATH = re.compile(r"^sku\[variantId=([^]]+)\]\.selectorValues\[(0|[1-9][0-9]*)\]$")
_AU_TASK_AXIS = re.compile(r"^au-axis-index:(0|[1-9][0-9]*)$")
PROOF_VERSION = frozen.PROOF_VERSION
sources = frozen.sources
_one = frozen._one
_normalized_span = frozen._normalized_span
_verified_span = frozen._verified_span
_raw_rel = frozen._raw_rel
_need = frozen._need


def _rakuten_family(store, selected: dict, axis: dict):
    rel=_raw_rel(selected.get("raw_file", ""));digest=selected.get("sha256")
    _need(bool(rel) and isinstance(digest,str),"Rakuten raw source identity missing")
    store.raw(rel,digest)
    label_span=axis.get("axis_label_span") or {};selected_span=axis.get("value_span") or {}
    label_path=((label_span.get("locator") or {}).get("path"));value_path=((selected_span.get("locator") or {}).get("path"))
    lm=_RAK_LABEL_PATH.fullmatch(label_path or "");vm=_RAK_VALUE_PATH.fullmatch(value_path or "")
    _need(lm is not None and vm is not None,"Rakuten spans lack exact selector coordinates")
    selector_index=int(lm.group(1));variant_id,value_index=vm.groups()
    _need(int(value_index)==selector_index,"Rakuten label/value selector indexes differ")
    _need(_raw_rel(label_span.get("raw_file", ""))==rel and _raw_rel(selected_span.get("raw_file", ""))==rel
          and label_span.get("sha256")==digest==selected_span.get("sha256"),"Rakuten coordinate spans have different raw identity")
    encoding=(selected_span.get("locator") or {}).get("encoding") or "EUC-JP"
    selectors=sources.rakuten_variant_selectors(store,rel,encoding)
    _need(selector_index<len(selectors),"Rakuten selector index outside source")
    selector=selectors[selector_index]
    _need(selector.get("key")==axis.get("axis_key"),"Rakuten selector key differs from exact coordinate")
    sku_key=selected.get("source_sku_key")
    parsed_key=urlsplit(sku_key) if isinstance(sku_key,str) else None
    native_variant_id=unquote(parsed_key.fragment.split(":",1)[0]) if parsed_key and parsed_key.fragment else None
    _need(bool(native_variant_id) and variant_id==native_variant_id,"Rakuten variant ID differs from selected source SKU key")
    _need(selector.get("label")==axis.get("axis_label"),"Rakuten caption differs from exact coordinate")
    # Bind the provided label locator, offset, quote, raw file and SHA to the independently rederived selector span.
    _need(_normalized_span(label_span)==_normalized_span(selector.get("label_span")),
          "Rakuten label span differs from exact selector coordinate")
    _verified_span(store,label_span,axis.get("axis_label"),"Rakuten exact selector label")
    selected_values=sources.rakuten_selected_values(store,rel,encoding,variant_id)
    _need(selector_index<len(selected_values),"selected Rakuten variant lacks this selector index")
    _need(_normalized_span(selected_values[selector_index])==_normalized_span(selected_span),
          "Rakuten selected value span differs from exact variant/index source span")
    _verified_span(store,selected_span,axis.get("value"),"Rakuten exact selected value")
    family=selector.get("values")
    _need(isinstance(family,list) and family and all(isinstance(v,str) and v for v in family),"Rakuten complete family missing or malformed")
    _need(len(family)==len(set(family)),"duplicate raw Rakuten option values")
    _need(axis.get("family_values")==family,"complete Rakuten family differs from frozen axis context")
    spans=[]
    for value in family:
        span=sources.rakuten_selector_value_span(store,rel,encoding,selector_index,selector["key"],value)
        _verified_span(store,span,value,"Rakuten complete-family member")
        _need(span.get("sha256")==digest,"Rakuten family member SHA differs")
        spans.append(span)
    _need(axis.get("value") in family and family.count(axis.get("value"))==1,"Rakuten selected value is not unique in complete family")
    return family,spans,label_span,selected_span


def _au_family(store, product: dict, axis_name: str, selected_value: str, selected_span: dict):
    au=product.get("au_product") or {};rel=_raw_rel(au.get("raw_file", ""));digest=au.get("sha256")
    _need(bool(rel) and isinstance(digest,str),"AU raw product identity missing")
    store.raw(rel,digest)
    locator=(selected_span.get("locator") or {}) if isinstance(selected_span,dict) else {}
    match=_AU_VALUE_PATH.fullmatch(locator.get("json_path", ""))
    _need(match is not None,"AU selected value lacks exact row/column JSON coordinate")
    kind,index_text=match.groups();sku=(store.json(rel).get("itemInfo") or {}).get("skuInfo") or {}
    name=(sku.get("optionName") or {}).get(kind);family=sku.get(kind+"Names")
    _need(name==axis_name,"AU caption differs from exact selected-value coordinate")
    _need(isinstance(family,list) and family and all(isinstance(v,str) and v for v in family),"AU complete family missing or malformed")
    _need(len(family)==len(set(family)),"duplicate AU raw option values")
    index=int(index_text);_need(index<len(family) and family[index]==selected_value,"AU selected value differs from exact coordinate")
    _need(_raw_rel(selected_span.get("raw_file", ""))==rel and selected_span.get("sha256")==digest,"AU selected span source identity differs")
    label_span=sources.json_leaf_span(store,rel,f"$.itemInfo.skuInfo.optionName.{kind}",axis_name)
    _verified_span(store,label_span,axis_name,"AU coordinate-derived axis label")
    spans=[]
    for i,value in enumerate(family):
        span=sources.json_leaf_span(store,rel,f"$.itemInfo.skuInfo.{kind}Names[{i}]",value)
        _verified_span(store,span,value,"AU whole-family member")
        _need(span.get("sha256")==digest,"AU family member SHA differs")
        spans.append(span)
    _verified_span(store,selected_span,selected_value,"AU selected value")
    _need(selected_value in family and family.count(selected_value)==1,"AU selected value is not unique in complete family")
    return family,spans,label_span,selected_span


def _preflight_pair(forward,reverse,source_case,source_product,store):
    case=source_case.get("record") or source_case;product=source_product.get("record") or source_product
    selected=case.get("rakuten_selected") or {};fref=forward.get("raw_condition_ref") or {};rref=reverse.get("raw_condition_ref") or {}
    raxis=_one(selected.get("axes") or [],lambda x:x.get("axis_key")==fref.get("axis_key"),"exact Rakuten source axis")
    _need(raxis.get("axis_label")==fref.get("axis_name")==rref.get("axis_name"),"Rakuten exact coordinate caption differs")
    _rakuten_family(store,selected,raxis)
    am=_AU_TASK_AXIS.fullmatch(str(reverse.get("axis_key", "")))
    _need(am is not None,"reverse task lacks exact AU axis index")
    row=_one(product.get("au_rows") or [],lambda x:x.get("row_key")==reverse.get("au_row_key"),"exact AU source row")
    idx=int(am.group(1));axes=row.get("axes") or []
    _need(idx<len(axes),"AU task index outside source row")
    aaxis=axes[idx]
    _need(aaxis.get("axis_name")==rref.get("axis_name"),"AU coordinate caption differs from paired task")
    family,spans,label_span,selected_span=_au_family(store,product,rref["axis_name"],rref["value"],rref["value_span"])
    # Require the frozen row's original name span to be the same coordinate as the selected value path.
    _need(_normalized_span(aaxis.get("axis_name_span"))==_normalized_span(label_span),
          "AU row axis-name span differs from selected-value coordinate")
    _need(_normalized_span(aaxis.get("value_span"))==_normalized_span(selected_span),
          "AU row selected-value span differs from exact task span")


def _delegated_pair(forward,reverse,source_case,source_product,store,source_pins=None):
    try: _preflight_pair(forward,reverse,source_case,source_product,store)
    except (KeyError,TypeError,ValueError,OSError,UnicodeDecodeError) as exc:
        return {"proof_version":PROOF_VERSION,"forward_task_id":forward.get("task_id"),"reverse_task_id":reverse.get("task_id"),"case_id":forward.get("case_id"),"au_row_key":forward.get("au_row_key"),"status":"unknown","reason":str(exc)}
    return prove_task_pair(forward,reverse,source_case,source_product,store,source_pins)


# Reuse the frozen functions' bytecode and semantic checks. Only their source-family
# resolvers and pair preflight are bound in this private globals dictionary.
_PRIVATE_GLOBALS=dict(frozen.__dict__)
_PRIVATE_GLOBALS["_au_family"]=_au_family
_PRIVATE_GLOBALS["_rakuten_family"]=_rakuten_family
prove_task_pair=FunctionType(frozen.prove_task_pair.__code__,_PRIVATE_GLOBALS,frozen.prove_task_pair.__name__,frozen.prove_task_pair.__defaults__,frozen.prove_task_pair.__closure__)
_PRIVATE_GLOBALS["prove_task_pair"]=_delegated_pair
build_proofs=FunctionType(frozen.build_proofs.__code__,_PRIVATE_GLOBALS,frozen.build_proofs.__name__,frozen.build_proofs.__defaults__,frozen.build_proofs.__closure__)
