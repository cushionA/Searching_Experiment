#!/usr/bin/env python3
"""Pinned pretrained PARSeq Tiny on the same public offline OCR images."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from PIL import Image
from evaluate_public_text import load_and_check_manifest, summarize, levenshtein, atomic_json, atomic_jsonl

SOURCE_COMMIT='1902db043c029a7e03a3818c616c06600af574be'


class ParseqOCR:
    def __init__(self, source: Path, weight: Path):
        import torch
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        self.torch=torch
        commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
        if commit != SOURCE_COMMIT:
            raise RuntimeError(f'PARSeq source revision must be {SOURCE_COMMIT}')
        if subprocess.run(['git','-C',str(source),'diff','--quiet']).returncode:
            raise RuntimeError('PARSeq source has modified tracked files')
        digest=hashlib.sha256(weight.read_bytes()).hexdigest()
        if not digest.startswith('e7a21b54'):
            raise ValueError('PARSeq Tiny weight hash differs from the official release filename')
        sys.path.insert(0,str(source.resolve()))
        from strhub.models.utils import create_model
        from strhub.data.module import SceneTextDataModule
        started=time.perf_counter()
        self.model=create_model('parseq-tiny',pretrained=False,decode_ar=True,refine_iters=1)
        state=torch.load(weight,map_location='cpu',weights_only=True)
        self.model.model.load_state_dict(state,strict=True)
        self.model.eval().to('cpu')
        self.transform=SceneTextDataModule.get_transform(tuple(self.model.hparams.img_size))
        self.load_seconds=time.perf_counter()-started
        self.providers=['PyTorchCPU']
        self.metadata={'model':'PARSeq Tiny','source_url':'https://github.com/baudm/parseq',
                       'source_commit':commit,'weight_url':'https://github.com/baudm/parseq/releases/download/v1.0.0/parseq_tiny-e7a21b54.pt',
                       'filename':weight.name,'sha256':digest,'bytes':weight.stat().st_size,
                       'parameters':sum(p.numel() for p in self.model.parameters()),
                       'runtime_versions':{n:importlib.metadata.version(n) for n in ['torch','torchvision','timm','pytorch-lightning','numpy','Pillow']},
                       'options':{'device':'cpu','dtype':'float32','threads':2,'interop_threads':1,
                                  'img_size':list(self.model.hparams.img_size),'transform':'official get_transform; RGB, bicubic resize, tensor, normalize0.5',
                                  'decode_ar':True,'refine_iters':1,'max_label_length':self.model.hparams.max_label_length,
                                  'batch_size':1,'charset_adapter':None,'normalization':None,
                                  'confidence_filter':None,'charset_train':self.model.hparams.charset_train}}

    def predict(self,image:Image.Image)->tuple[str,float]:
        inp=self.transform(image.convert('RGB')).unsqueeze(0)
        with self.torch.inference_mode():
            logits=self.model(inp)
            answers,probs=self.model.tokenizer.decode(logits.softmax(-1))
        if len(answers)!=1:
            raise RuntimeError('expected one decoded string')
        confidence=float(probs[0].prod().item())
        if not math.isfinite(confidence):
            raise RuntimeError('nonfinite confidence')
        return answers[0],confidence


def evaluate(manifest_path:Path,output:Path,source:Path,weight:Path)->None:
    paths=[output.with_suffix('.json'),output.with_suffix('.jsonl')]
    if output.exists() or any(p.exists() for p in paths):raise FileExistsError(output)
    manifest,samples=load_and_check_manifest(manifest_path.resolve());manifest['manifest_path']=str(manifest_path.resolve())
    output.parent.mkdir(parents=True,exist_ok=True)
    ocr=ParseqOCR(source,weight)
    with Image.open(samples[0]['path']) as image:ocr.predict(image)
    rows=[]
    for i,sample in enumerate(samples,1):
        error=None;answer='';confidence=None
        start=time.perf_counter()
        try:
            with Image.open(sample['path']) as image:
                start=time.perf_counter();image.load();answer,confidence=ocr.predict(image)
        except Exception as exc:
            error={'type':type(exc).__name__,'message':str(exc)}
        latency=(time.perf_counter()-start)*1000;label=sample['label']
        rows.append({k:sample[k] for k in ['id','path','source','sha256','label']}|
                    {'answer':answer,'exact':answer==label,'case_insensitive_exact':answer.casefold()==label.casefold(),
                     'distance':levenshtein(answer,label),'characters':len(label),'latency_ms':latency,
                     'confidence':confidence,'error':error})
        if i%100==0 or i==len(samples):
            atomic_jsonl(paths[1],rows);result=summarize(rows,manifest,ocr.load_seconds,ocr.providers)
            result.update({'completed':i,'total_samples':len(samples),'model':ocr.metadata,'ocr_backend':'PARSeq/PyTorch CPU',
                           'manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                           'predictions_sha256':hashlib.sha256(paths[1].read_bytes()).hexdigest(),
                           'evaluator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           'failed_samples':sum(r['error'] is not None for r in rows),
                           'status':'complete' if i==len(samples) else 'incomplete','warmup':'first image once excluded'})
            atomic_json(paths[0],result);print(f"checkpoint {i}/{len(samples)} errors={result['failed_samples']}",flush=True)
    print(json.dumps(result['by_source'],ensure_ascii=False))


def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['manifest','output','source-dir','weight']:p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();evaluate(a.manifest,a.output,a.source_dir,a.weight)


if __name__=='__main__':main()
