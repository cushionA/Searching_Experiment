#!/usr/bin/env python3
"""Export frozen Gemini SKU inference calls as a portable, hashed task package."""
from __future__ import annotations
import hashlib, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / 'experiments/sku-matching/task-packages/20261011-gemini-sku-low'
RUNS = [
    ('latest4', '20261011T011921Z-gemini-thinking-off-errors4'),
    ('baseline50', '20261011T002300Z-gemini-luna50-replay-v2'),
]

def sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def readj(path: Path): return json.loads(path.read_text(encoding='utf-8'))
def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
def check_freeze(runroot: Path):
    # Latest run's grading freeze and baseline's completed-artifacts freeze certify source files.
    candidates = [runroot/'completed-artifacts-freeze.json', runroot/'grading-freeze.json']
    freeze = next((p for p in candidates if p.exists()), None)
    if freeze is None: raise RuntimeError(f'missing freeze for {runroot}')
    obj = readj(freeze); hashes = obj.get('sha256', {})
    if not hashes: raise RuntimeError(f'empty freeze {freeze}')
    for rel, expected in hashes.items():
        p = ROOT / 'experiments/sku-matching' / rel
        if not p.is_file() or sha(p.read_bytes()) != expected:
            raise RuntimeError(f'freeze mismatch: {p}')
    return freeze

def safe_metadata(response: dict):
    cand = (response.get('candidates') or [{}])[0]
    result = {k: response[k] for k in ('modelVersion','usageMetadata','elapsed_seconds','http_status') if k in response}
    if 'finishReason' in cand: result['finishReason'] = cand['finishReason']
    return result

def call_text(body):
    return '\n'.join(part['text'] for content in body.get('contents', []) for part in content.get('parts', []) if isinstance(part,dict) and isinstance(part.get('text'),str))
def extract(body, marker):
    text = call_text(body); pos = text.find(marker)
    if pos < 0: raise RuntimeError(f'missing {marker}')
    prompt = text[:pos].rstrip()
    payload_text = text[pos+len(marker):].strip()
    return prompt, json.loads(payload_text)

def main():
    if PKG.exists() and any(PKG.iterdir()): raise SystemExit(f'refusing to overwrite non-empty package: {PKG}')
    PKG.mkdir(parents=True, exist_ok=True)
    sources = {}
    mapping = []
    total = 0
    for label, runid in RUNS:
        runroot = ROOT/'experiments/sku-matching/results'/runid
        freeze_path=check_freeze(runroot)
        sources[freeze_path.relative_to(ROOT).as_posix()] = sha(freeze_path.read_bytes())
        # Copy only the local input/context snapshots required to interpret the calls.
        local = PKG/label/'local'
        for rel in ('derived/inputs.json','derived/source-contexts.json'):
            src = runroot/rel
            if src.exists():
                dst = local/Path(rel).name; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(src.read_bytes())
                sources[src.relative_to(ROOT).as_posix()] = sha(src.read_bytes())
        for stage, manifest_path, request_folder, answer_folder in [
            ('matching','matching-dispatch-manifest.json','matching-chunk-requests','matching-chunk-answers'),
            ('signature','signature-dispatch-manifest.json',None,None),
            ('review','review-dispatch-manifest.json','review-requests','review-answers')]:
            mp=runroot/manifest_path
            if not mp.exists(): continue
            manifest=readj(mp)
            for i, entry in enumerate(manifest.get('entries', []),1):
                if stage=='matching':
                    req_rel=entry['request']; ans_rel=entry['answer']; body_obj=readj(runroot/req_rel)['body']; response_i=i
                elif stage=='signature':
                    req_rel=entry['request']; ans_rel=entry['answer']; body_obj=readj(runroot/req_rel)['body']; response_i=i
                else:
                    req_rel=entry.get('body_request') or ('review-vertex-bodies/%02d.json'%i)
                    ans_rel=entry['answer']; body_obj=readj(runroot/req_rel); response_i=i
                req_src=runroot/req_rel; ans_src=runroot/ans_rel
                text=call_text(body_obj)
                marker='CASE_JSON=' if stage=='review' else 'INPUT='
                prompt, payload=extract(body_obj,marker)
                gen=body_obj.get('generationConfig',{})
                schema=gen.get('responseSchema')
                if not schema: raise RuntimeError(f'missing response schema: {req_src}')
                response=readj(runroot/f'provider/{stage}/{response_i:03}.response.json')
                out=PKG/label/stage/f'{i:03d}'
                dump(out/'request-body.json',body_obj)
                (out/'prompt.txt').parent.mkdir(parents=True,exist_ok=True); (out/'prompt.txt').write_text(text,encoding='utf-8')
                dump(out/'input.json',payload); dump(out/'response-schema.json',schema)
                dump(out/'answer.json',readj(ans_src)); dump(out/'provider-metadata.json',safe_metadata(response))
                for src in (req_src,ans_src,runroot/f'provider/{stage}/{response_i:03}.response.json',mp):
                    sources[src.relative_to(ROOT).as_posix()] = sha(src.read_bytes())
                caseid=entry.get('original_case_id') or entry.get('case_id') or (entry.get('case_ids') or [None])[0]
                mapping.append({'run':label,'stage':stage,'call':i,'case_id':caseid,'source_request':req_src.relative_to(ROOT).as_posix(),'source_answer':ans_src.relative_to(ROOT).as_posix(),'request_body_sha256':sha(json.dumps(body_obj,ensure_ascii=False,separators=(',',':')).encode())})
                total+=1
    dump(PKG/'manifest.json',{'format':'gemini-sku-task-package-v1','calls':mapping,'source_sha256':dict(sorted(sources.items())),'call_count':total})
    if total != 147: raise RuntimeError(f'expected 147 calls, got {total}')
    write_docs()

def write_docs():
    (PKG/'README.md').write_text('''# Gemini SKU task package\n\nこのpackageは凍結済み2 runでproviderへ送った全147 callを保存する。今回のLOW4は既存SKUを再試験した14 call（matching 9 / signature 1 / review 4）で、新規SKU・labelは0。比較用baseline50はMINIMAL50（matching 70 / signature 13 / review 50、133 calls）で、matchingのprior-byte再利用を含むが全answerを収録。147はcall数であり、異なるSKUが147件という意味ではない。LOW4とbaseline50の条件を混同しないこと。\n\n各callに`request-body.json`（generationConfig込みの送信body）、`prompt.txt`（実際に送信した全文。入力JSONも含む）、`input.json`（INPUT= / CASE_JSON=以降）、`response-schema.json`、`answer.json`、安全なprovider metadataを置いた。思考テキストは保存していない。review理由は独立レビューの短い理由欄であり、思考過程ではない。構造検査の成功は意味的な正しさを示さない。\n\n`manifest.json`は全source fileのSHA-256とcallごとのcase/stage/source対応を記録する。ローカル入力とsource contextは各runの`local/`にある。元case IDと引用元文脈の復元はローカル専用データに依存する。\n\n仕様の参照先: [source scope and local rules](../../../../experiments/sku-matching/README.md)、[matching/review stages](../../../../experiments/sku-matching/sku_gemini_replay_stages.py)、[LOW subset runner](../../../../experiments/sku-matching/trial_gemini_thinking_off_subset.py)、[LOW run report](../../../../experiments/sku-matching/results/20261011T011921Z-gemini-thinking-off-errors4/report.md)。生成基盤コードはコピーしていない。\n''',encoding='utf-8')
    with (PKG/'README.md').open('a', encoding='utf-8') as guide:
        guide.write("""
## 入り口と3段階

| 段階 | 入力 | 実出力 | ローカル処理 |
|---|---|---|---|
| matching | 楽天の選択値・属性、AU候補行、固定商品原文 | rows → rakuten_checks / au_checks → status / source_ids | 全行・全条件・現在行の根拠IDを検証して候補を絞る |
| signature | 現在の候補行、選択済み寸法、固定商品根拠 | checks → status / source_ids | 単位・部位・寸法の一致と候補回答hashを検証 |
| review | 原文、今回の候補・照合trace・寸法結果 | 1要素の配列：case_id / verdict / row_key / reason / review_evidence_ids | confirmの候補・寸法・根拠条件を検証して採用。無効reviewは未評価 |

まず [task-contract.json](task-contract.json) を読むと、3段階の実Schema代表とローカル採否条件をまとめて確認できる。

- [今回LOWの照合](latest4/matching/001/prompt.txt) / [Schema](latest4/matching/001/response-schema.json) / [実出力](latest4/matching/001/answer.json)
- [今回LOWの寸法確認](latest4/signature/001/prompt.txt) / [Schema](latest4/signature/001/response-schema.json) / [実出力](latest4/signature/001/answer.json)
- [今回LOWの意味レビュー](latest4/review/001/prompt.txt) / [Schema](latest4/review/001/response-schema.json) / [実出力](latest4/review/001/answer.json)

`prompt.txt` は入力を含む送信全文。指示と入力を分けて読む場合は `input.json` を使う。再現には `request-body.json` をそのまま参照する。全パラメーターは各bodyのgenerationConfigに含まれる。LOW4ではthinkingLevel=LOWを明示、baseline50はthinkingConfig未指定（公式既定MINIMAL）。完全OFFの試験は行っていない。

matching/signatureは理由文や引用文を返さずstatus/source_idsだけを返す。A0/A1は現在AU行の条件、S番号は固定原文の参照ID。元ID・引用原文・出典はlocalデータから復元する。AU skuIdはグリッドIDであり、選択セルはrow_indexとcolumn_indexで特定する。

本文contextの重複除去、カーテン内容表、伸縮寸法などのタスク分岐もbaseline50の実プロンプトに含まれる。古い回答と今回の回答を混ぜて救済しない。無効な実回答もそのまま収録し、Schemaに合っているだけで正答とは扱わない。
""")
    dump(PKG/'task-contract.json',{
      'purpose':'Gemini SKU matching, signature verification, and independent semantic review',
      'provider_request':{'project':'groundingsearch','location':'global','model':'gemini-3.5-flash-lite','operation':'generateContent','method':'POST'},
      'response_schemas':{stage:readj(PKG/'latest4'/stage/'001/response-schema.json') for stage in ('matching','signature','review')},
      'statuses':{'matching':['support','contradiction','unknown'],'review':['confirm','reject','unresolved']},
      'required_fields':{'matching':{'root':'object','rows':'array','row_item':{'rakuten_checks':'array','au_checks':'array'},'check_item':{'status':'support|contradiction|unknown','source_ids':'array'}},'signature':{'root':'object','required':['checks'],'checks':'array','check_item':{'status':'support|contradiction|unknown','source_ids':'array'}},'review':{'root':'array','min_items':1,'max_items':1,'item':{'case_id':'string','verdict':'confirm|reject|unresolved','row_key':'string|null','reason':'string','review_evidence_ids':'array'}}},
      'array_order':{'matching.rows':'au_rows input order','matching.row_item.rakuten_checks':'rakuten conditions order','matching.row_item.au_checks':'current AU row conditions order','signature.checks':'selected_dimensions or targets input order','review':'exactly one object'},
      'row_scope':'Current AU candidate row compared with selected Rakuten SKU; review is one case only.',
      'local_gate':{
        'confirm':'Nonempty valid evidence IDs; matching decision must be candidate; dimensions must pass; review row must equal matching and dimension row; dimension candidate-answer hash must equal the current matching answer hash; AU condition evidence must belong to this row.',
        'reject':'Valid review with row_key null and nonempty allowed fixed-product evidence IDs leads to exclude. The local validator does not independently prove that every AU row is contradicted; the reviewer is responsible for that semantic conclusion.',
        'unresolved':'A valid unresolved review leads to pending unless matching is already not_evaluated.',
        'invalid_review':'Missing or invalid review, including confirm_gate_failed, leads to not_evaluated; never silently counts as exclude or correct.',
        'meaning':'These are actual local gates; schema-valid evidence can still be semantically wrong.'},
      'source_scope':'Only fixed_product source content can support or contradict; linked products and search-term lists cannot establish identity.',
      'semantic_caveat':'Schema-valid structured output does not establish semantic correctness.'})

if __name__=='__main__': main()
