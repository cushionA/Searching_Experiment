"""Small CPU retrieval probe. Each arm runs in its own process; no remote code."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import sys
import time
import unicodedata
import urllib.request

REVISION = 'bbd883ae1fadb563056ff73b1061ae8172a3fcda'
MODEL_FILES = ['tokenizer.json', 'tokenizer_config.json', 'onnx_config.json', 'model.onnx', 'model_int8.onnx']

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()

def download(directory):
    directory.mkdir(parents=True, exist_ok=True)
    receipts = {}
    for name in MODEL_FILES:
        path = directory/name
        url = f'https://huggingface.co/lightonai/mLateOn/resolve/{REVISION}/{name}'
        if not path.exists():
            temp = path.with_suffix(path.suffix+'.partial')
            urllib.request.urlretrieve(url, temp)
            temp.rename(path)
        receipts[name] = {'url': url, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
    (directory/'manifest.json').write_text(json.dumps(receipts, indent=2)+'\n')

def terms(text):
    text = ''.join(c for c in unicodedata.normalize('NFKC', text).lower() if c.isalnum())
    return [text[i:i+2] for i in range(len(text)-1)] or [text]

class BM25:
    def __init__(self, texts):
        self.docs = [Counter(terms(t)) for t in texts]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lengths)/len(texts)
        df = Counter(t for d in self.docs for t in d)
        self.idf = {t: math.log(1+(len(texts)-n+0.5)/(n+0.5)) for t,n in df.items()}

    def score(self, text):
        result=[]
        for d,n in zip(self.docs,self.lengths):
            score=0.0
            for t in set(terms(text)):
                tf=d[t]
                score += self.idf.get(t,0)*tf*2.2/(tf+1.2*(0.25+0.75*n/self.avg))
            result.append(score)
        return result

def ranking_metrics(ranked, relevant, k=10):
    gains = [relevant.get(d,0) for d in ranked]
    dcg = sum((2**g-1)/math.log2(i+2) for i,g in enumerate(gains[:k]))
    ideal = sum((2**g-1)/math.log2(i+2) for i,g in enumerate(sorted(relevant.values(),reverse=True)[:k]))
    return {'ndcg@10': dcg/ideal if ideal else 0.0,
            'mrr@10': next((1/(i+1) for i,g in enumerate(gains[:k]) if g>0),0.0),
            'hit@1': float(gains[0]>0)}

def token_ids(tokenizer, text, prefix_id, max_length=512):
    # Match PyLate: encode stripped text with specials, then insert marker AFTER BOS.
    ids = tokenizer.encode(text.strip()).ids
    if len(ids)+1 > max_length:
        raise ValueError('Fixture exceeds token cap; refuse silent truncation')
    return ids[:1]+[prefix_id]+ids[1:]

class Encoder:
    def __init__(self, directory, arm, threads):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np=np
        self.config=json.loads((directory/'onnx_config.json').read_text())
        assert not self.config['do_query_expansion'] and not self.config['skiplist_words']
        self.tokenizer=Tokenizer.from_file(str(directory/'tokenizer.json'))
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()
        options=ort.SessionOptions()
        options.intra_op_num_threads=threads
        options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(directory/('model_int8.onnx' if arm=='int8' else 'model.onnx')),sess_options=options,providers=['CPUExecutionProvider'])
        self.lengths=[]

    def encode(self,text,is_query):
        np=self.np
        prefix=self.config['query_prefix_id' if is_query else 'document_prefix_id']
        ids=token_ids(self.tokenizer,text,prefix)
        data=np.array([ids],dtype=np.int64)
        output=self.session.run(None,{'input_ids':data,'attention_mask':np.ones_like(data)})[0][0]
        assert output.shape==(len(ids),128) and np.isfinite(output).all()
        assert np.allclose(np.linalg.norm(output,axis=1),1.0,atol=1e-4)
        self.lengths.append(len(ids))
        return output

def percentile(values, p):
    values=sorted(values)
    pos=(len(values)-1)*p
    lo=int(pos);hi=min(lo+1,len(values)-1)
    return values[lo]*(hi-pos)+values[hi]*(pos-lo) if hi!=lo else values[lo]

def run(args):
    started=time.perf_counter()
    data=json.loads(args.fixture.read_text())
    docs=data['documents'];queries=data['queries'];ids=[d['id'] for d in docs]
    assert len(ids)==len(set(ids))
    assert len({q['id'] for q in queries})==len(queries)
    assert all(q['relevant'] and set(q['relevant'])<=set(ids) for q in queries)
    load_start=time.perf_counter()
    if args.arm=='bm25':
        model=BM25([d['text'] for d in docs]);encode=None
        load_seconds=time.perf_counter()-load_start
        doc_seconds=0.0
        score=lambda q: model.score(q)
        index_bytes=len(json.dumps([dict(d) for d in model.docs],ensure_ascii=False).encode())
    else:
        import numpy as np
        model=Encoder(args.model_dir,args.arm,args.threads)
        load_seconds=time.perf_counter()-load_start
        t=time.perf_counter()
        embeddings=[model.encode(d['text'],False) for d in docs]
        if args.arm=='mean-ablation':
            embeddings=[e.mean(0) for e in embeddings]
            embeddings=[e/np.linalg.norm(e) for e in embeddings]
        doc_seconds=time.perf_counter()-t
        index_bytes=sum(e.nbytes for e in embeddings)
        def score(q):
            e=model.encode(q,True)
            if args.arm=='mean-ablation':
                e=e.mean(0);e=e/np.linalg.norm(e)
                return [float(e@d) for d in embeddings]
            return [float((e@d.T).max(axis=1).sum()) for d in embeddings]
    # Warm only the query path, not part of measured latency. Batch=1 everywhere.
    score(queries[0]['text'])
    predictions=[];latencies=[]
    for q in queries:
        samples=[]
        for _ in range(args.repeats):
            t=time.perf_counter();scores=score(q['text']);samples.append((time.perf_counter()-t)*1000)
        ranked=sorted(range(len(ids)),key=lambda i:(-scores[i],ids[i]))
        ranked_ids=[ids[i] for i in ranked]
        predictions.append({'query_id':q['id'],'group':q['group'],'ranking':ranked_ids,
                            'scores_by_document':dict(zip(ids,scores)), 'latency_ms':samples,
                            **ranking_metrics(ranked_ids,q['relevant'])})
        latencies.extend(samples)
    metrics={}
    for group in ['all']+sorted({q['group'] for q in queries}):
        rows=[p for p in predictions if group=='all' or p['group']==group]
        metrics[group]={k:sum(p[k] for p in rows)/len(rows) for k in ['ndcg@10','mrr@10','hit@1']}
    result={'arm':args.arm,'model_revision':REVISION if args.arm!='bm25' else None,
            'fixture_sha256':sha256(args.fixture),'counts':{'documents':len(docs),'queries':len(queries)},
            'metrics':metrics,'latency_ms':{'median':percentile(latencies,.5),'p95':percentile(latencies,.95),'samples':len(latencies)},
            'load_seconds':load_seconds,'document_encode_seconds':doc_seconds,'index_payload_bytes':index_bytes,
            'peak_process_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
            'elapsed_seconds':time.perf_counter()-started,
            'environment':{'python':sys.version,'platform':platform.platform(),'cpu':next((l.split(':',1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines() if l.startswith('model name')),None),'cpu_count':os.cpu_count(),'affinity_count':len(os.sched_getaffinity(0)),'threads':args.threads,'batch_size':1,'max_tokens':512,'repeats':args.repeats,'packages':{p:importlib.metadata.version(p) for p in ['numpy','onnxruntime','tokenizers']}},
            'predictions':predictions}
    if args.arm!='bm25':result['token_lengths']={'min':min(model.lengths),'max':max(model.lengths)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['predictions','environment']},ensure_ascii=False),flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download',action='store_true')
    parser.add_argument('--model-dir',type=Path,default=Path('/tmp/mlateon-model'))
    parser.add_argument('--fixture',type=Path,default=Path(__file__).with_name('fixture.json'))
    parser.add_argument('--arm',choices=['bm25','fp32','int8','mean-ablation'])
    parser.add_argument('--output',type=Path)
    parser.add_argument('--threads',type=int,default=2)
    parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args()
    if args.download:download(args.model_dir)
    else:
        if not args.arm or not args.output or args.repeats<1 or args.threads<1:parser.error('arm/output and positive repeats/threads required')
        run(args)

if __name__=='__main__':main()
