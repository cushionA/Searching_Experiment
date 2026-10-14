"""Staged, NOT integration-validated. Standard-library local llama-server benchmark.
Run only in an approved isolated container. Does not install/download anything.
"""
import argparse
import hashlib
import json
import pathlib
import random
import resource
import statistics
import subprocess
import time
import urllib.error
import urllib.request

SEED = 42
MODEL_SHA = '4221b252fdd5fd25e15847adfeb5ee88886506ba50b8a34548374492884c2162'

def score(text, gold):
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return {'format_ok': False, 'exact': False, 'field_hits': 0, 'fields': len(gold)}
    valid = (isinstance(obj, dict) and obj.keys() == gold.keys()
             and all(type(obj[k]) is type(v) for k, v in gold.items()))
    hits = sum(type(obj.get(k)) is type(v) and obj.get(k) == v for k, v in gold.items()) if isinstance(obj, dict) else 0
    return {'format_ok': valid, 'exact': valid and obj == gold, 'field_hits': hits, 'fields': len(gold)}

def read_cases(path):
    raw = path.read_bytes()
    cases = [json.loads(line) for line in raw.splitlines()]
    assert len(cases) == 24 and len({c['id'] for c in cases}) == 24
    assert {(c['language'], c['task']) for c in cases} == {(l,t) for l in ['ja','en'] for t in ['classification','extraction']}
    return cases, hashlib.sha256(raw).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', type=pathlib.Path, default=pathlib.Path(__file__).with_name('cases.jsonl'))
    ap.add_argument('--server', type=pathlib.Path)
    ap.add_argument('--model', type=pathlib.Path)
    ap.add_argument('--out', type=pathlib.Path)
    ap.add_argument('--threads', type=int, default=4, choices=[1,2,4])
    ap.add_argument('--repeats', type=int, default=3, choices=[1,2,3])
    ap.add_argument('--budget-seconds', type=int, default=600, choices=range(60,1201))
    ap.add_argument('--validate-only', action='store_true')
    args = ap.parse_args()
    cases, input_hash = read_cases(args.cases)
    if args.validate_only:
        print(json.dumps({'cases':len(cases),'input_sha256':input_hash,'inference_runs':0})); return
    if not all([args.model, args.server, args.out]):
        ap.error('--server --model --out required')
    with args.model.open('rb') as f:
        actual = hashlib.file_digest(f, 'sha256').hexdigest()
    assert actual == MODEL_SHA, 'Model hash differs'
    args.out.mkdir(parents=True, exist_ok=False)
    command = [str(args.server.resolve()), '-m', str(args.model.resolve()), '-t', str(args.threads), '-tb', str(args.threads), '-c', '2048', '-n', '96', '-ngl', '0', '-np', '1', '--host', '127.0.0.1', '--port', '18080']
    # Never send these synthetic prompts through environment-configured HTTP proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request('http://127.0.0.1:18080'+path, data=data, headers={'Content-Type':'application/json'})
        with opener.open(req, timeout=min(120,max(1,deadline-time.monotonic()))) as response:
            return json.load(response)
    start = time.monotonic(); deadline = start+args.budget_seconds
    records=[]; summary={'input_sha256':input_hash,'model_sha256':actual,'command':command,'seed':SEED,'status':'running'}
    with (args.out/'server.log').open('w') as log:
        proc = subprocess.Popen(command, stdout=log, stderr=log)
        try:
            while True:
                if proc.poll() is not None: raise RuntimeError('server exited; see server.log')
                if time.monotonic()-start>180: raise TimeoutError('server startup')
                try:
                    request('/health'); break
                except (urllib.error.URLError, TimeoutError): time.sleep(.1)
            summary['startup_ready_s']=time.monotonic()-start
            def run(case, phase, rep):
                if time.monotonic()>=deadline: raise TimeoutError('20 minute compute limit')
                prompt='User: '+case['prompt']+'<|eot_id|>Assistant: '
                t=time.monotonic()
                raw=request('/completion',{'prompt':prompt,'n_predict':96,'temperature':0,'seed':SEED,'cache_prompt':False,'stop':['<|eot_id|>'],'stream':False})
                row={'id':case['id'],'language':case['language'],'task':case['task'],'phase':phase,'repeat':rep,'wall_s':time.monotonic()-t,'raw':raw,**score(raw.get('content',''),case['gold'])}
                records.append(row)
                with (args.out/'raw.jsonl').open('a') as f: f.write(json.dumps(row,ensure_ascii=False)+'\n')
            # First request is measured separately; no claim of physical cold disk cache.
            run(cases[0], 'first_request', 0)
            run(cases[-1], 'warmup', 0)
            for rep in range(args.repeats):
                ordered=cases.copy(); random.Random(SEED+rep).shuffle(ordered)
                for case in ordered: run(case, 'steady', rep)
            summary['groups']={}
            for lang in ['ja','en']:
                for task in ['classification','extraction']:
                    group=[r for r in records if r['phase']=='steady' and r['language']==lang and r['task']==task]
                    # Quality counts unique examples once; repeats only characterize latency.
                    quality=[r for r in group if r['repeat']==0]
                    summary['groups'][lang+'/'+task]={'unique_examples':len(quality),'requests':len(group),'format_rate':statistics.mean(r['format_ok'] for r in quality),'exact_rate':statistics.mean(r['exact'] for r in quality),'field_accuracy':sum(r['field_hits'] for r in quality)/sum(r['fields'] for r in quality),'median_wall_s':statistics.median(r['wall_s'] for r in group),'max_wall_s':max(r['wall_s'] for r in group)}
            summary['status']='complete_smoke_only'
        except Exception as error:
            summary['status']='failed'; summary['error']=repr(error)
            raise
        finally:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
            summary['server_peak_rss_bytes']=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024
            summary['peak_scope']='Linux server child RSS; excludes Python, container, shared page-cache accounting'
            cg=pathlib.Path('/sys/fs/cgroup/memory.peak')
            summary['cgroup_memory_peak_bytes']=int(cg.read_text()) if cg.exists() else None
            summary['elapsed_s']=time.monotonic()-start
            (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__': main()
