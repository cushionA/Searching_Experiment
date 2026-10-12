#!/usr/bin/env python3
"""Read frozen PR26 ZIPs; never overwrite source labels. Build a bounded real smoke."""
import collections, hashlib, html, json, math, re, subprocess, zipfile
from pathlib import Path
from html.parser import HTMLParser
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'benchmarks/gliner-sku-evidence-20261012'
RESULTS=ROOT/'experiments/sku-matching/results'
BASE='20261010-claude-task-checkpoint.zip'
ALIGN='20261012-sku-align-v6-v7-outputs.zip'
OLD='20261010-sku-gate-tasks.zip'
SELECT=['309329265','337595272','483077897','509691375','524251610','597651839','653618158','695392874','704280046','704500131','704502086','778809347']
def sha(b):return hashlib.sha256(b).hexdigest()
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def lines(b):return [json.loads(l) for l in b.splitlines() if l]
def clean(x):
 if isinstance(x,float) and not math.isfinite(x):return None
 if isinstance(x,dict):return {k:clean(v) for k,v in x.items()}
 if isinstance(x,list):return [clean(v) for v in x]
 return x
def leaf(obj,path):
 for key,idx in re.findall(r'\.([^.\[\]]+)|\[(\d+)\]',path[1:]): obj=obj[key] if key else obj[int(idx)]
 return obj
class Render(HTMLParser):
 def __init__(self,raw):
  super().__init__(convert_charrefs=False);self.raw=raw;self.parts=[];self.links=0;self.skip=0;self.offsets=[0]
  for m in re.finditer('\n',raw):self.offsets.append(m.end())
 def handle_starttag(self,tag,attrs):
  if tag=='a':self.links+=1
  if tag in ('script','style'):self.skip+=1
 def handle_endtag(self,tag):
  if tag=='a':self.links=max(0,self.links-1)
  if tag in ('script','style'):self.skip=max(0,self.skip-1)
 def handle_data(self,data):
  if not self.skip and data.strip():
   line,col=self.getpos(); start=self.offsets[line-1]+col
   # Keep original substrings, including spaces, to preserve exact source coordinates.
   self.parts.append({'text':data,'raw_start':start,'raw_end':start+len(data),'inside_anchor':bool(self.links)})
 def entity(self,prefix):
  line,col=self.getpos(); start=self.offsets[line-1]+col
  text=self.raw[start:start+len(prefix)]
  if self.raw[start+len(prefix):start+len(prefix)+1]==';':text+=';'
  self.handle_data(text)
 def handle_entityref(self,name):self.entity('&'+name)
 def handle_charref(self,name):self.entity('&#'+name)
def walk(x):
 if isinstance(x,dict):
  if {'raw_file','sha256','locator','start','end','quote'}<=x.keys():yield x
  for v in x.values():yield from walk(v)
 elif isinstance(x,list):
  for v in x:yield from walk(v)
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 z=zipfile.ZipFile(RESULTS/BASE);az=zipfile.ZipFile(RESULTS/ALIGN);oz=zipfile.ZipFile(RESULTS/OLD)
 hpath='.lab-output/sku-align-20261012-v7/legacy/align/handoff.jsonl'
 hb=az.read(hpath);hs=lines(hb)
 labels=lines(z.read('.lab-output/sku-real-luna-labels-20261010-v3/labels.jsonl'));lm={x['case_id']:x for x in labels}
 old=lines(oz.read('.lab-output/sku-gate-tasks-20261010-v2/inputs/products.jsonl'));pm={p['dossier_id']:p for p in old}
 spans={json.dumps(s,sort_keys=True):s for r in hs for s in walk(r)};bad=[]
 for s in spans.values():
  try:
   raw=z.read(s['raw_file']); assert sha(raw)==s['sha256']
   loc=s['locator'];v=raw.decode(loc['encoding']) if loc['kind']=='html_text' else leaf(json.loads(raw),loc['json_path'])
   assert v[s['start']:s['end']]==s['quote']
  except Exception as e:bad.append({'span':s,'error':type(e).__name__})
 # Directly validate machine quotes against their claimed dossier leaf, not a document-wide search.
 label_audit=collections.Counter();label_issues=[]
 for r in labels:
  for ev in r.get('evidence',[]):
   if '#$' not in ev.get('source_ref',''):continue
   f,path=ev['source_ref'].split('#',1)
   try:
    v=leaf(json.loads(z.read('.lab-output/sku-real-luna-annotation-inputs-20261010-v3/'+f)),path)
    if not isinstance(v,str):v=json.dumps(v,ensure_ascii=False)
    ok=ev['quote'] in v
    label_audit['valid' if ok else 'quote_not_in_claimed_leaf']+=1
    if not ok:label_issues.append({'case_id':r['case_id'],'evidence':ev})
   except Exception as e:
    label_audit['unresolved']+=1;label_issues.append({'case_id':r['case_id'],'evidence':ev,'error':type(e).__name__})
 # Detect split structure; this is deliberately development smoke, with no test claims.
 dossiers={r['dossier_id'] for r in hs};urls={r['rakuten_sku']['url'] for r in hs}
 products=[];selected=[];references=[]
 for pid in SELECT:
  rows=sorted((r for r in hs if r['au_product_id']==pid),key=lambda r:(r['case_id'],r['au_row_key']))
  # Select one actual row per predeclared product stratum without using label outcomes.
  r=rows[0];selected.append(clean(r));raw=z.read(r['au_product_source']['raw_file']);assert sha(raw)==r['au_product_source']['sha256']
  obj=json.loads(raw);info=obj['itemInfo'];oldp=pm[r['dossier_id']]
  # The old product is used ONLY to enumerate raw-verified AU rows, not as v10 input.
  assert oldp['au_product']['title_span']['sha256']==sha(raw)
  title_path=oldp['au_product']['title_span']['locator']['json_path']
  fields=[title_path,'$.itemInfo.itemComment','$.itemInfo.extraItemComment','$.itemInfo.detailComment']
  text='';segments=[]
  for path in fields:
   v=leaf(obj,path)
   if not isinstance(v,str) or not v:continue
   parser=Render(v);parser.feed(v)
   for seg in parser.parts:
    assert v[seg['raw_start']:seg['raw_end']]==seg['text']
    st=len(text);text+=seg['text']+'\n';segments.append({**seg,'start':st,'end':len(text)-1,'json_path':path,'raw_file':r['au_product_source']['raw_file'],'raw_sha256':sha(raw)})
  # Entire preserved description text; no retrieval or truncation before inference.
  au_rows=oldp['au_rows']
  for row in au_rows:
   for s in walk(row):
    b=z.read(s['raw_file']); assert sha(b)==s['sha256']; assert leaf(json.loads(b),s['locator']['json_path'])[s['start']:s['end']]==s['quote']
  products.append({'product_id':pid,'dossier_id':r['dossier_id'],'text':text,'text_sha256':sha(text.encode()),'segments':segments,'source':r['au_product_source'],'all_au_rows':au_rows,'selected_case_id':r['case_id'],'selected_au_row_key':r['au_row_key'],'unresolved_conditions':[c for c in clean(r['conditions']) if c['status']!='aligned'],'title_path':title_path})
  # Partial weak reference only: existing machine citations for the selected row.
  for ev in lm[r['case_id']].get('evidence',[]):
   if ev['source'] not in ('au_title','au_description'):continue
   hits=[{'start':m.start(),'end':m.end()} for m in re.finditer(re.escape(ev['quote']),text)]
   references.append({'product_id':pid,'case_id':r['case_id'],'machine_decision':lm[r['case_id']]['decision'],'evidence':ev,'hits':hits,'reference_kind':'existing_machine_quote_not_independent_gold'})
 inventory=[]
 for p in sorted(RESULTS.glob('*.zip')):
  if any(t in p.name for t in ['independent-real-eval','v6-v7']):
   zz=zipfile.ZipFile(p)
   entries=[]
   for n in zz.namelist():
    if n.endswith(('/cases.jsonl','/products.jsonl','/handoff.jsonl','/groups.jsonl')):
     b=zz.read(n);entries.append({'path':n,'rows':len(b.splitlines()),'sha256':sha(b)})
   inventory.append({'archive':p.name,'sha256':sha(p.read_bytes()),'entries':entries})
 protocol={'task':'real AU title/full-description field/span extraction ONLY; no condition classifier or SKU acceptance', 'selection':SELECT,'selection_rule':'one lexicographically first v7 unresolved row per 12 explicitly stratified AU products; no accessory prefilter, no label selection','strata':['dimensions','mattress type and size','bedding optional bundle','chair type','compound residual','step count','panel/door counts','diameter','blanket size/type','curtain model_candidate and reverse count residual'],'label_kind':'Luna machine annotations; development data previously used by multiple sessions','train_test':'no training/split/held-out accuracy claimed; sibling Rakuten URLs and repeated AU descriptions must stay grouped in future splits','fields':{'dimension':'Exact size, width, height, length, diameter or dimensions of a product, including units.','piece_count':'Exact numbers of included pieces, panels, doors, sets, or components with their names.','material':'Material or fabric of a product.','color':'Color name or pattern of a product.','product_type':'Product type, model variant or construction style.','included_item':'Items stated to be included or excluded, including conditional inclusion.'},'threshold':0.5,'splitter':'whitespace (official checkpoint default)','threads':2,'first_smoke_products':2,'max_products':12,'baseline':'literal unresolved selected value matches in same full text; citations are candidates only, never support','metrics':'output shape, exact source offsets, partial machine-quote overlap/retention; no independent precision/recall or semantic three-state accuracy','reverse_residual_policy':'AU-side residuals remain untested; require Rakuten evidence','chunk_policy':'token-budgeted complete scan with overlap; record every chunk and validate coverage'}
 for name,data in [('input.json',products),('selected-handoff.json',selected),('weak-references.json',references),('protocol.json',protocol)]:dump(OUT/name,data)
 counts={'handoff_rows':len(hs),'handoff_unique_case_row':len({(r['case_id'],r['au_row_key']) for r in hs}),'handoff_cases':len({r['case_id'] for r in hs}),'handoff_products':len(dossiers),'handoff_rakuten_urls':len(urls),'machine_labels':len(labels),'machine_decisions':dict(collections.Counter(r['decision'] for r in labels)),'nonstandard_NaN_tokens':len(re.findall(rb'\bNaN\b',hb)),'condition_states':dict(collections.Counter(c['status'] for r in hs for c in r['conditions'])),'unique_source_spans':len(spans),'invalid_source_spans':len(bad),'machine_dossier_citation_check':dict(label_audit),'selected_products':len(products),'selected_au_rows_total':sum(len(p['all_au_rows']) for p in products),'selected_row_counts':{p['product_id']:len(p['all_au_rows']) for p in products},'weak_reference_quotes':len(references),'weak_reference_found':sum(bool(x['hits']) for x in references)}
 provenance={'git_base':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'source_archives':{n:sha((RESULTS/n).read_bytes()) for n in [BASE,ALIGN,OLD]},'handoff_path':hpath,'handoff_sha256':sha(hb),'selected_hashes':{n:sha((OUT/n).read_bytes()) for n in ['input.json','selected-handoff.json','weak-references.json','protocol.json']},'legacy_v10_exact_inputs':'not found in PR26 ZIPs or inspected Codex results ZIPs; do not substitute v2 for v10','au_pool_provenance':'v2 pool independently revalidated against captured AU raw spans; not asserted byte-identical to v10 pool','creation':'2026-10-10 captured AU item JSON / Rakuten HTML and Luna labels; 2026-10-12 v7 alignment reuses prior similarities; current smoke derived read-only'}
 dump(OUT/'data-audit.json',counts);dump(OUT/'audit-issues.json',{'invalid_spans':bad,'machine_citation_issues':label_issues});dump(OUT/'archive-inventory.json',inventory);dump(OUT/'provenance.json',provenance)
 print(json.dumps(counts,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
