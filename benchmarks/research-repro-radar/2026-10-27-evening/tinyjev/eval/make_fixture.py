"""Fixed synthetic cases. No model output is used to define labels or scoring."""
import json, pathlib, hashlib
p=pathlib.Path(__file__).parent
items=[]; gold={}
texts={
 'billing':['My card was charged twice for one purchase.','The invoice includes a fee I never agreed to.','Please return the extra money you took from my account.'],
 'delivery':['My parcel has not arrived after ten days.','Tracking says delivered but nothing is at my door.','The courier took my box to the wrong address.'],
 'access':['I forgot my password and cannot sign in.','The login page rejects my verification code.','Please unlock my account so I can get in again.'],
 'technical':['The application crashes whenever I open a document.','The screen freezes after the newest update.','Clicking export produces an empty file.']}
criteria={'billing':'Charges, invoices, payments and refunds','delivery':'Shipment tracking, missing parcels and couriers','access':'Login, passwords, verification and account access','technical':'Application bugs, crashes, freezing and broken features'}
for label, group in texts.items():
 for j,text in enumerate(group):
  for noisy in (False,True):
   ident=f'nlp-{label}-{j}-'+('noisy' if noisy else 'clean')
   s='<div>MSG &nbsp; '+text+' </div>\n-- Sent from mobile ###' if noisy else text
   items.append({'id':ident,'domain':'nlp','variant':'noisy' if noisy else 'clean','state':s,'questions':{'decision':{'type':'choice','instructions':'Which support team should handle this message? Choose its main issue.','criteria':criteria}}});gold[ident]=label
# These labels are manually fixed from the declared policy before inference.
# Priority: health<=25 -> retreat; else visible enemy and ammo>0 -> attack;
# else ammo==0 -> reload; else explore. Rules are supplied to the model too.
cases=[(10,True,5,'retreat'),(25,False,0,'retreat'),(5,True,0,'retreat'),
 (80,True,8,'attack'),(26,True,1,'attack'),(100,True,30,'attack'),
 (80,False,0,'reload'),(26,True,0,'reload'),(100,False,0,'reload'),
 (80,False,8,'explore'),(26,False,1,'explore'),(100,False,30,'explore')]
policy='Choose the next game action. Apply in this priority order: if health is at most 25, retreat. Otherwise if an enemy is visible and ammo is positive, attack. Otherwise if ammo is zero, reload. Otherwise explore.'
opts={'retreat':'Move away to safety','attack':'Shoot the visible enemy','reload':'Refill ammunition','explore':'Move forward and look for enemies'}
for j,(hp,enemy,ammo,label) in enumerate(cases):
 for variant in ('structured','prose'):
  ident=f'game-{j}-{variant}';s={'health':hp,'enemy_visible':enemy,'ammo':ammo} if variant=='structured' else f'Health is {hp}. '+('An enemy is visible.' if enemy else 'No enemy is visible.')+f' Ammunition remaining: {ammo}.'
  # Reverse option order on alternate cases; fixed before predictions.
  cs=dict(reversed(list(opts.items()))) if j%2 else opts
  items.append({'id':ident,'domain':'game','variant':variant,'state':s,'questions':{'decision':{'type':'choice','instructions':policy,'criteria':cs}}});gold[ident]=label
(p/'inputs.json').write_text(json.dumps(items,indent=2)+'\n')
(p/'gold.json').write_text(json.dumps(gold,indent=2)+'\n')
(p/'fixture-manifest.json').write_text(json.dumps({'count':len(items),'created_before_inference':True,'files':{n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in ('inputs.json','gold.json')},'independence':'Labels fixed manually from support intent and declared game policy; no candidate output used. Synthetic diagnostics, not representative external benchmark.'},indent=2)+'\n')
