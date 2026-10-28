import json, pathlib, hashlib
p=pathlib.Path(__file__).parent
items=[];gold={}
examples=[
('明日の勉強会は予定どおり開催します。','held'),
('明日の勉強会は中止します。','cancelled'),
('明日の勉強会を開催するかは未定です。','unknown'),
('中止という噂は誤りです。明日の勉強会は開催します。','held'),
('開催すると案内しましたが撤回します。明日の勉強会は中止です。','cancelled'),
('明日の勉強会は開催予定でしたが、実施するかどうか再検討中です。','unknown'),
('昨日の講演会は中止でした。明日の勉強会は開催します。','held'),
('昨日の講演会は開催しました。明日の勉強会は中止します。','cancelled'),
('明日の勉強会について、会場だけ決まりました。開催するかどうかはまだ決まっていません。','unknown'),
('荒天でも明日の勉強会をオンラインで開催することが確定しました。','held'),
('中止しないという昨日の案内を訂正します。明日の勉強会は取りやめます。','cancelled'),
('「明日の勉強会を開催してほしい」は参加者の希望です。主催者は実施可否を発表していません。','unknown')]
choices=[['held','開催 — 明日の勉強会は実施が確定'],['cancelled','中止 — 明日の勉強会は取りやめが確定'],['unknown','未定 — 明日の勉強会の実施可否は不明']]
for i,(text,label) in enumerate(examples):
 for variant in ['clean','noisy']:
  ident=f'nlp-{i}-{variant}';state=text if variant=='clean' else '<div>最新のお知らせ</div>\n'+text+'\n<!--広告--> 関連記事:中止/開催/未定 | 共有…'
  items.append({'id':ident,'group':'nlp','variant':variant,'state':state,'question':'明日の勉強会の開催状況は？','choices':choices if i%2==0 else list(reversed(choices))});gold[ident]=label
rules='ゲームの規則: 扉が開いていれば進む。扉が閉じていて鍵を持っていれば鍵を使う。扉が閉じていて鍵を持っていなければ待つ。'
for i,(door,key) in enumerate([(True,True),(True,False),(False,True),(False,False)]):
 for variant in ['structured','prose']:
  ident=f'game-{i}-{variant}';state=json.dumps({'扉が開いている':door,'鍵を持っている':key},ensure_ascii=False) if variant=='structured' else f'扉は{"開いて" if door else "閉じて"}います。鍵は{"持っています" if key else "持っていません"}。'
  items.append({'id':ident,'group':'game','variant':variant,'state':rules+'\n現在の状態: '+state,'question':'規則に従って次に選ぶ行動は？','choices':[['advance','進む'],['unlock','鍵を使う'],['wait','待つ']]});gold[ident]='advance' if door else 'unlock' if key else 'wait'
for name,data in [('inputs.json',items),('gold.json',gold)]: (p/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
(p/'fixture-manifest.json').write_text(json.dumps({'created_before_inference':True,'count':len(items),'nlp':24,'game':8,'independent_base_scenarios':16,'synthetic':True,'not_representative':True,'hashes':{n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in ['inputs.json','gold.json']}},indent=2))
