"""Fetch pinned public archives and GGUF into an empty dedicated runtime folder.
No repository code is executed. No HF token is read or supplied. No retries.
Reserves 400 MB of the total 2 GB budget for OS packages/base image/metadata.
"""
import hashlib
import io
import json
import pathlib
import sys
import tarfile
import urllib.request
ROOT=pathlib.Path(sys.argv[1]);ROOT.mkdir(parents=True,exist_ok=False)
ledger=[];remaining=1600000000

def fetch(url, target, expected_size=None, expected_hash=None):
    global remaining
    h=hashlib.sha256();n=0
    with urllib.request.urlopen(url,timeout=90) as r,target.open('wb') as f:
        while data:=r.read(4*1024**2):
            if len(data)>remaining: raise RuntimeError('download budget exceeded')
            remaining-=len(data);n+=len(data);h.update(data);f.write(data)
    ledger.append(dict(url=url,bytes=n,sha256=h.hexdigest()))
    (ROOT/'downloads.json').write_text(json.dumps(ledger,indent=2))
    if expected_size is not None: assert n==expected_size
    if expected_hash is not None: assert h.hexdigest()==expected_hash

for name,repo,sha,prefix in [
    ('engine','microsoft/BitNet','0b341e582afbf9e1011f24744b554c96a3477eb5',''),
    ('llama','isHuangXin/llama.cpp','390c307752ab78fd8189f359d6954c9ba1be74af','3rdparty/llama.cpp/')]:
    archive=ROOT/(name+'.tar.gz')
    fetch(f'https://codeload.github.com/{repo}/tar.gz/{sha}',archive)
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();top=members[0].name+'/'
        for m in members:
            if m.name.startswith(top) and m.name[len(top):]:
                m.name=prefix+m.name[len(top):]
                # Official cross-submodule links must remain within the complete project.
                tar.extract(m,ROOT/'BitNet',filter='data')
fetch('https://huggingface.co/microsoft/bitnet-b1.58-2B-4T-gguf/resolve/a1f2f1c765812aa8af3f6eda4a313707064bba15/ggml-model-i2_s.gguf',ROOT/'ggml-model-i2_s.gguf',1187801280,'4221b252fdd5fd25e15847adfeb5ee88886506ba50b8a34548374492884c2162')
