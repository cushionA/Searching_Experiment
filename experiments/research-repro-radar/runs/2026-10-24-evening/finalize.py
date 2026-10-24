"""Persist reading-only evidence without completing unexecuted candidates."""
import datetime as dt
import hashlib
import json
from pathlib import Path

R = Path(__file__).parent
ROOT = R.parents[3]
S = Path('/workspace/radar-skills/research-repro-radar')

def read(p):
    return json.loads(p.read_text())

def write(p, x):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2) + '\n')

def key(x):
    return str(x.get('doi') or x.get('arxiv_id') or x.get('id') or x['url']).casefold()

rows = read(R/'all-candidates.json')
ranking = read(R/'ranking.json')
reviews = read(R/'reviews.json')
updates = read(R/'updated-reviews.json')
by = {x['candidate_id']: x for x in reviews + updates}
assert {x['id'] for x in ranking['review_pool']} <= set(by)
previous = read(R/'queue-before.json')
current = read(S/'state/queue.json')
seen = read(S/'state/seen.json')['reported']
historical = set()
for p in R.parent.glob('*/report.json'):
    if p.parent != R:
        historical.update(read(p).get('completed_ids', []))
pending = {key(x): x for x in previous['candidates']}
for x in current['candidates']:
    ident = key(x)
    if ident not in historical and ident not in seen:
        pending[ident] = x
for x in rows:
    ident = key(x)
    if ident in pending or ident in historical or ident in seen:
        continue
    y = dict(x)
    y.setdefault('carried_over_since', '2026-10-24')
    y.setdefault('carry_count', 1)
    y['pending_reason'] = 'Not executed or source-confirmed rejected; digest is not completion.'
    pending[ident] = y
current['candidates'] = list(pending.values())
assert {key(x) for x in previous['candidates']} <= set(pending)
write(S/'state/queue.json', current)
write(R/'unfinished-before-record.json', current)

specs = [
 ('2610.30067','GoiBERT','26M/49Mのベトナム語encoder。分類・抽出の小型化に直結。','現行TwoheartsTechnologies配布はtask headなし。商用は組織全体100人未満かつ有料顧客1,000未満等の条件付きで、上流条件も別途適用。学習へ拡大せず保留。'),
 ('2610.31329','WeVisOne-VL','検出・OCR・マスクを統合。HF229票、公式2B/4Bと外部名義demoを確認。','2B版のF32重み9,767,053,328 bytesでDL2GB超。CPU速度・公式小型量子化版未確認。demoの独立性は未確定。'),
 ('2610.31372','RIDAR','研究エージェントの証拠・評価手順の逸脱を監査。MITコードと公開データあり。','公式起動はAnalyzer/Verifier認証を要求。無課金経路未確認。権限緩和例を実行せず、公開軌跡の静的設計確認まで。'),
 ('2610.31198','T²Quant','拡散LMの時刻ごとに精度を配分し、同一bit予算で品質改善を狙う。','公式LLaDA-8B経路はCUDA40GB以上・4–5時間。今回上限外、Kaggleへ迂回しない。'),
 ('2610.25835','Diffusion Language Models Change Speed Across Tasks and Languages','入力のコピー・編集と新規生成で推論量が変わる。要約・抽出のコスト評価設計に関係。','著者の2モデル評価のみ。小型CPU実装・日本語入力での速度/品質未確認。'),
 ('2610.04184','EvalResearchBench','エージェントが作る評価器の順位一致と低コストな手動標本を比較する。','API依存とlicense未確認。通常CI保守ではなくAI開発の評価設計候補、実行保留。'),
 ('2610.30418','Agent Mosaic','単独では許される文書の組合せから秘匿結論が漏れる現象を合成例で評価。','公開実装・商用条件・無料の再現経路未確認。私有文書を渡さず保留。'),
 ('2610.23987','GAFU / What Generation Knows','生成側の視覚表現を理解へ再利用し、細部の認識を改善する設計。','公式repoを確認したが後学習が中心。即利用checkpoint・商用条件・CPU資源未確認、学習しない。'),
]
digest = [{'id':i,'title':t,'url':'https://arxiv.org/abs/'+i,'assessment':'B','why_picked':w,'deferred_reason':d,'verification_level':'reading','measured':False} for i,t,w,d in specs]
report = {
 'run_id':'2026-10-24-evening-native','date':'2026-10-24',
 'status':'reading_completed_no_candidate_execution',
 'recommended':[],'digest':digest,'completed_ids':[],
 'counts':{'collected_unique':len(rows),'ranking_input_with_carry':ranking['counts']['input'],
           'ranked':ranking['counts']['ranked'],'review_pool_read':20,'primary_read_total':len(by),
           'native_followup_candidates':4,'candidate_code_executions':0,'candidate_model_inferences':0,
           'pending_after_record_expected':len(pending)},
 'host_requested_model':'gpt-6-luna','service_runtime_model_id':'unknown',
 'budget':{'money_jpy':0,'candidate_cpu_seconds':0,'weights_downloaded_bytes':0,'gpu_seconds':0,'candidate_repairs':0,
           'started_at':'2026-10-24T08:57:00Z','deadline':'2026-10-24T09:27:00Z'},
 'limitations':['No candidate code or inference: credential-isolation hold remains pending; no repeat question or bypass.',
  'Native JSON success is not candidate reproduction. Author quality and speed are not our measurements.',
  'All unexecuted, interrupted and digest-only candidates retained; native reject labels alone do not complete candidates.',
  'OpenReview 5 searches HTTP403; no bypass. HF returns complete lists without next Link but backend ceiling is not exhaustive.',
  'Crossref uses 100-result relevance windows; arXiv continued beyond first pages within a 240-second source budget. See page ledger.',
  'Initial 7 quotations failed source matching and were not adopted. Four followup quotes repaired once within shared 180-second limit.',
  'Independent two-signal deep-dive threshold not confirmed. No current price comparison or cost advantage claim.',
  'Morning and previously reported candidates suppressed without material new evidence. No SKU, Moli, PR19, email, SMTP, cron, training or paid backend changes.']}
write(R/'report.json', report)
write(ROOT/'benchmarks/research-repro-radar/2026-10-24-evening.json', {
 'run_id':report['run_id'],'measurement_level':'reading_only','candidate_code_executions':0,'candidate_model_inferences':0,
 'cpu_seconds':None,'peak_ram_bytes':None,'quality':None,'throughput_items_per_minute':None,
 'estimated_10000_items_cpu_seconds':None,'weights_downloaded_bytes':0,'gpu_seconds':0,'money_jpy':0,
 'blocker':{'action':'external candidate inference','target':'new candidate PoCs','status':'pending/not attempted',
            'reason':'Existing approved credential/personal-file isolation has not been established; no resume authorization.'},
 'expected_difference':'Author throughput, declared checkpoint size, native judgment success and actual inference are separate.',
 'author_claims_only':{'GoiBERT':{'cpu_ms_8_threads_fp32_128_tokens':{'BamiBERT':31.0,'S':14.2,'M':19.4},
   'scope':'Encoder only; excludes tokenizer/task heads; not a pure architecture comparison; test feedback influenced model size.'},
   'T2Quant':{'source':'official README','gsm8k_first500_accuracy_pct':{'uniform_W4A4':46.6,'T2Quant':51.4},'sign_test_p':0.055}},
 'artifact_metadata_only':{'GoiBERT-S':{'id':'TwoheartsTechnologies/GoiBERT-S','parameters':26397696,'safetensors_bytes':105609040},
   'GoiBERT-M':{'id':'TwoheartsTechnologies/GoiBERT-M','parameters':49366016,'safetensors_bytes':197486112},
   'WeVisOne-VL-2B':{'safetensors_bytes':9767053328},'WeVisOne-VL-4B':{'safetensors_bytes':19322413240}}
})
targets = {x['id']:x for x in read(R/'review-targets.json')}
lines = ['# 一次資料読解台帳','','初回native草案には引用不一致7件があり不採用。追加4件の訂正判定を優先。候補実行0、全件未完了。','']
for ident,v in by.items():
    row=targets[ident]
    lines += ['## '+row['title'],row['url'],'','判定草案: '+v['decision']+' / '+v['verification_level'],v['reason'],
              '','確認状況: '+' / '.join(v['unconfirmed']),'','次: '+str(v['next_check']),'']
(R/'reading-ledger.md').write_text('\n'.join(lines).rstrip()+'\n')
print(json.dumps(report['counts']))
