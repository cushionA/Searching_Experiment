"""Generate fixed synthetic inputs; no browser, packages, network or credentials."""
from pathlib import Path
import json
ROOT = Path(__file__).resolve().parent
EXPECTED = {'title': 'Fixture article', 'text': '公開テスト本文 Alpha 42', 'links': ['/target?a=1&b=2', '/next']}
ARTICLE = '<article id="article"><h1>Fixture article</h1><p>公開テスト本文 Alpha 42</p><a href="/target?a=1&amp;b=2">Target</a><a href="/next">Next</a></article>'
CASES = {
 'static': ARTICLE,
 'js': '<div id="mount"></div><script>document.querySelector("#mount").innerHTML=' + json.dumps(ARTICLE) + '</script>',
 'wait': '<div id="mount"></div><script>setTimeout(()=>{document.querySelector("#mount").innerHTML=' + json.dumps(ARTICLE) + '},150)</script>',
 'click': '<button id="update">Update</button><div id="mount">Before</div><script>document.querySelector("#update").addEventListener("click",()=>{document.querySelector("#mount").innerHTML=' + json.dumps(ARTICLE) + '})</script>',
 'iframe': '<iframe id="child" src="/static.html"></iframe>',
 'fetch': '<div id="mount"></div><script>fetch("/payload.json").then(r=>r.json()).then(d=>{document.querySelector("#mount").innerHTML=d.html})</script>',
 'geometry': '<style>#box{position:absolute;left:30px;top:40px;width:120px;height:50px;margin:0;padding:0;border:0}</style><div id="box">Geometry</div>',
}
def generate():
 directory=ROOT/'fixtures'; directory.mkdir(exist_ok=True)
 for name,body in CASES.items():
  (directory/(name+'.html')).write_text('<!doctype html><meta charset="utf-8"><title>'+name+'</title>'+body+'\n')
 (directory/'payload.json').write_text(json.dumps({'html':ARTICLE},ensure_ascii=False)+'\n')
 expected={key:({'rect':{'x':30,'y':40,'width':120,'height':50},'tolerance_px':1} if key=='geometry' else EXPECTED) for key in CASES}
 (ROOT/'expected.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__': generate()
