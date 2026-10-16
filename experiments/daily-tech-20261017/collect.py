"""Download public data only; never imports upstream code or pickle weights."""
import hashlib,json,pathlib,urllib.request,datetime
out=pathlib.Path('benchmarks/daily-tech-20261017'); out.mkdir(exist_ok=True,parents=True)
assets=pathlib.Path('/tmp/yolo17-context');assets.mkdir(exist_ok=True)
urls={
'release.json':'https://api.github.com/repos/ultralytics/assets/releases/tags/v8.4.0',
'repo.json':'https://api.github.com/repos/ultralytics/ultralytics',
'latest-release.json':'https://api.github.com/repos/ultralytics/ultralytics/releases/latest',
'yolo26.html':'https://docs.ultralytics.com/models/yolo26/',
'yolo27.html':'https://docs.ultralytics.com/models/yolo27/',
'license.txt':'https://raw.githubusercontent.com/ultralytics/ultralytics/f413c7b2a603469bfcd17cf01671baba3b030eb4/LICENSE',
'bus.jpg':'https://raw.githubusercontent.com/ultralytics/ultralytics/f413c7b2a603469bfcd17cf01671baba3b030eb4/ultralytics/assets/bus.jpg',
'zidane.jpg':'https://raw.githubusercontent.com/ultralytics/ultralytics/f413c7b2a603469bfcd17cf01671baba3b030eb4/ultralytics/assets/zidane.jpg',
'yolo26n.onnx':'https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.onnx',
'yolo11n.onnx':'https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo11n.onnx',
}
manifest=[]
for name,url in urls.items():
 req=urllib.request.Request(url,headers={'User-Agent':'research-poc/1.0'})
 with urllib.request.urlopen(req,timeout=60) as r: data=r.read(50_000_001)
 if len(data)>50_000_000: raise RuntimeError('single-file cap')
 target=(assets if name.endswith(('.onnx','.jpg')) else out)/name;target.write_bytes(data)
 manifest.append(dict(name=name,url=url,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),fetched_utc=datetime.datetime.now(datetime.UTC).isoformat()))
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(sum(x['bytes'] for x in manifest))
