"""Compose the prior curtain guard with the new selected adjustable repair."""
import copy, importlib.util, json, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
EXP=HERE.parents[1]
sys.path.insert(0,str(EXP))
import trial_luna_sku_matching as core
import sku_luna_curtain_dimension_alias as curtain
import sku_luna_adjustable_dimension_task as adjustable
spec=importlib.util.spec_from_file_location('v11_frozen_run',HERE/'run.py')
run=importlib.util.module_from_spec(spec);spec.loader.exec_module(run)

def install_guard():
    selected=set(core.read(HERE/'selection.json')['selected']);cases={c['case_id']:c for c in core.read(run.DIR/'inputs.json')};contexts=core.read(run.DIR/'source-contexts.json');registry={}
    for e in core.read(run.DIR/'signature-v3-manifest.json')['entries']:
        c=cases[e['case_id']];row=next(r for r in c['au_rows'] if r['row_key']==e['row_key']);sources=core.smoke.row_sources(c,row);scoped=copy.deepcopy(sources)
        for sid,ctx in contexts[c['case_id']].items():
            if sid in scoped:scoped[sid].update({k:ctx[k] for k in ('kind','scope')})
        registry[core.canonical([e['targets'],sources,row['conditions']])]=(c,scoped,contexts[c['case_id']])
    def guard(targets,answer,sources,conditions):
        c,scoped,ctx=registry[core.canonical([targets,sources,conditions])]
        if c['case_id'] in selected:
            return adjustable.guard_adjustable_dimensions(c,targets,answer,scoped,conditions,ctx)
        return curtain.guard_curtain_dimensions(c,targets,answer,scoped,conditions)
    core.guard_dimensions=guard

def main():
    run.verify(HERE/'guard-composition-freeze.json');run.install_guard=install_guard
    if len(sys.argv)!=2 or sys.argv[1] not in ('reviews-prepare','reviews-collect'):raise ValueError('expected review command')
    {'reviews-prepare':run.reviews_prepare,'reviews-collect':run.reviews_collect}[sys.argv[1]]()
if __name__=='__main__':main()
