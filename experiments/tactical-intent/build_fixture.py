"""Hand-authored prospective fixture. Never adjust after model observation."""
import json,pathlib,hashlib
P=pathlib.Path(__file__).parent
PRESETS={
'hold': {'description':'Maintain the current balanced plan; gather more evidence without committing.', 'delta':[0,0,0,0,0,0]},
'pressure': {'description':'Approach and sustain safe pressure to condition the opponent to defend.', 'delta':[.4,.2,0,0,-.1,-.1]},
'break': {'description':'Exploit repeated stationary guarding with a committed guard-breaking attack.', 'delta':[.1,-.1,.7,0,0,-.3]},
'bait': {'description':'Stay just outside range and feint to invite a whiff; avoid committing first.', 'delta':[-.1,-.2,0,.7,0,.1]},
'catch': {'description':'Prepare a delayed tracking strike against the observed repeated dodge direction.', 'delta':[.1,0,0,0,.7,-.2]},
'conserve': {'description':'Abandon the commitment, restore distance and conserve stamina.', 'delta':[-.6,-.4,-.3,0,-.2,.7]}}
Q='Choose the best tactical preset for this snapshot. Pursue the character goal using only observed evidence; recent reversals override old habits. Sparse evidence warrants caution. Keep enough stamina for defense. Outputs change low-frequency coefficients, not immediate attacks. Personality may resolve a close choice, never override safety.'
def st(history,plan='hold',stamina=80,hp=80,enemy_hp=80,remaining=40,distance='mid'):
 return dict(history=history,current_plan=plan,stamina=stamina,hp=hp,enemy_hp=enemy_hp,seconds_remaining=remaining,distance=distance)
# Each pair below is (observable state, acceptable actions, clearly bad actions, rationale).
def c(s,a,b,r):return(s,a.split(),b.split(),r)
series={
'dev_lure':[
 c(st(['opponent chased two retreats with long swings','both swings missed']), 'bait hold','break','Preparation: invite another overreach without assuming it must occur.'),
 c(st(['opponent chased two retreats with long swings','our feint drew a missed swing; opponent is recovering'],plan='bait'), 'pressure hold','break catch','Observed opening supports safe pressure; no evidence of guarding/dodging.'),
 c(st(['two long swings missed earlier','opponent ignored the last three feints and waited'],plan='bait',stamina=24), 'conserve hold','break catch pressure','Abandon unsuccessful bait with limited resources.')],
'dev_condition':[
 c(st(['opponent backed away after safe jab','opponent guarded next jab']), 'pressure hold','break catch','Build evidence; one guard is insufficient for committed read.'),
 c(st(['last four pressure probes were stationary blocks'],plan='pressure'), 'break pressure','catch','Repeated guard supports breaking, or sustaining conditioning.'),
 c(st(['earlier four probes were blocks','last three probes now produced backward dodges'],plan='break'), 'catch hold','break','Switch away from stale guard read.')],
'eval_guard':[
 c(st(['last four approaches met stationary blocks']), 'break pressure','catch','Guard evidence makes break reasonable.'),
 c(st(['last four approaches met stationary blocks','another safe jab was blocked'],plan='pressure'), 'break pressure','catch','Maintain guard exploitation.'),
 c(st(['five blocks earlier','last three approaches produced outward dodges'],plan='break'), 'catch hold','break','Recent pattern reversal.')],
'eval_dodge':[
 c(st(['last four approaches met outward dodges']), 'catch bait','break','Same current state as guard pair; different observed history.'),
 c(st(['last four approaches met outward dodges','another safe jab caused outward dodge'],plan='pressure'), 'catch bait','break','Repeated escape direction.'),
 c(st(['five outward dodges earlier','last three approaches met stationary blocks'],plan='catch'), 'break pressure','catch','Reverse from dodge tracking to guard pressure.')],
'eval_sparse':[
 c(st(['one outward dodge observed']), 'hold pressure','break catch','Do not infer a stable dodge tendency from one sample.'),
 c(st(['one outward dodge','one stationary block','one forward jab']), 'hold bait pressure','catch break','Mixed small evidence; no reliable tendency.'),
 c(st(['one outward dodge','one stationary block','one forward jab','last three actions were outward dodges']), 'catch hold','break','Recent repeated evidence becomes useful.')],
'eval_resource':[
 c(st(['last four actions were stationary blocks'],stamina=14,plan='pressure'), 'conserve','break pressure catch','Insufficient stamina for committed attack.'),
 c(st(['last four actions were stationary blocks','we restored distance without attacking'],stamina=28,plan='conserve',hp=12,enemy_hp=80), 'conserve hold','break pressure catch','Low health and limited reserve override aggression.'),
 c(st(['last four actions were stationary blocks','we restored distance twice'],stamina=78,plan='conserve',hp=60), 'break pressure','catch','Resources recovered; resume purposeful pressure.')],
'eval_lure_abort':[
 c(st(['last three retreats drew overextended swings']), 'bait hold','break catch','Set up a whiff opportunity.'),
 c(st(['three retreats drew swings','last feint drew a whiff; opponent is recovering'],plan='bait'), 'pressure hold','break catch','Take safe opening after observed reaction.'),
 c(st(['three retreats drew swings earlier','last three feints ignored; opponent waits outside range'],plan='bait',stamina=36), 'hold pressure conserve','bait break catch','Abandon ineffective lure; do not assume hidden intent.')],
'eval_clock':[
 c(st(['last four approaches met stationary blocks'],hp=80,enemy_hp=15,remaining=4), 'conserve hold','break pressure','Ahead near time limit: protect lead.'),
 c(st(['last four approaches met stationary blocks'],hp=15,enemy_hp=80,remaining=4), 'break pressure','conserve','Behind near time limit with stamina: accept attack risk.'),
 c(st(['last four approaches met stationary blocks'],hp=15,enemy_hp=80,remaining=4,stamina=10), 'conserve hold','break pressure catch','Even urgent comeback does not create missing stamina.')]
}
rows=[];gold={}
for sid,scenes in series.items():
 for personality in ['patient','assertive']:
  for t,(state,allowed,bad,rationale) in enumerate(scenes):
   i=f'{sid}/{personality}/{t}';state=dict(state,personality=personality,goal='create a safe opening for a deliberate strike',character='patient counterfighter' if personality=='patient' else 'assertive pressure fighter')
   # Genuine personality distinction only at ambiguous setup (not arbitrary diversity).
   if sid=='eval_sparse' and t==0:allowed=['hold'] if personality=='patient' else ['hold','pressure']
   rows.append(dict(id=i,series=sid,split='dev' if sid.startswith('dev') else 'heldout',snapshot=t,state_version=t,observed_at_ms=1000*t,state=state,question=Q,choices={k:v['description'] for k,v in PRESETS.items()}))
   gold[i]=dict(acceptable=allowed,clearly_bad=bad,rationale=rationale)
for name,data in [('inputs.json',rows),('gold.json',gold),('presets.json',dict(coefficient_order=['approach','safe_pressure','guard_break','feint','tracking','reserve'],presets=PRESETS))]:
 (P/name).write_text(json.dumps(data,indent=2)+'\n')
(P/'fixture-manifest.json').write_text(json.dumps({n:hashlib.sha256((P/n).read_bytes()).hexdigest() for n in ['inputs.json','gold.json','presets.json']},indent=2)+'\n')
print('Frozen',len(rows),'outputs; 8 series, 2 dev, 6 heldout')
