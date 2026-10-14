import sys,json,pathlib,re,html,base64
sys.path.insert(0,'/workspace/radar-skills/research-repro-radar/scripts');import native_judge
p=pathlib.Path('/workspace/radar-private/20261014');r=pathlib.Path('/workspace/evening-radar/experiments/research-repro-radar/runs/2026-10-14-evening');rows=json.loads((r/'extra-input.json').read_text());urls={x['file']:x['url'] for x in json.loads((r/'extra-fetch-ledger.json').read_text())};payload=[]
for i,x in enumerate(rows,20):
 sources=[]
 for f in sorted((r/'review-sources').glob(f'{i:02d}-*.txt')):
  s=f.read_text();kind=f.stem.split('-',1)[1]
  if kind=='readme':s=base64.b64decode(json.loads(s)['content']).decode()
  elif kind in ['full','primary']:s=re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>',' ',re.sub(r'<(script|style)\b[^>]*>.*?</\1>','',s,flags=re.S))))
  elif kind=='github':
   d=json.loads(s);s=json.dumps({k:d.get(k) for k in ['full_name','description','created_at','pushed_at','stargazers_count','forks_count','license','archived','html_url']})
  sources.append({'url':urls[str(f.relative_to(r))],'text':s[:85000],'coverage':f'{min(len(s),85000)}/{len(s)} characters'})
 payload.append({'candidate':x,'sources':sources})
base=json.loads((p/'review-0-fixed.request.json').read_text());schema=base['schema'];schema['properties']['reviews']['minItems']=4;schema['properties']['reviews']['maxItems']=4
instruction=base['instruction'].replace('5候補','4候補')+' 追加trending読解。GPUは無料既存Kaggleが性能/メモリ問題の場合に許可済みでありGPUだけでrejectしない。検索用途はcoverage不足補完/前処理短縮/Archive不要到達が見込める場合のみ。rerank単独不要。RapidOCRは朝確認済み、重複実行しない。実在repo/同系列後継や更新を確認。quoteは提供URLそのまま、提供textの完全な部分文字列を20words以内で引用。新鮮さは日付だけで評価しない。'
req=native_judge.prepare_request(instruction,json.dumps(payload,ensure_ascii=False),schema);(p/'extra.request.json').write_text(json.dumps(req,ensure_ascii=False));print('extra request prepared')
