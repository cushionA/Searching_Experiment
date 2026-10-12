"""Bounded CPU smoke: one public image plus four derived corruptions, NOT accuracy."""
import hashlib
import io
import json
from pathlib import Path
import resource
import statistics
import time

started=time.perf_counter()
import numpy as np
import paddle
from PIL import Image, ImageFilter

paddle.set_device('cpu')
# Reviewed official architecture; only repository utility/hook imports are adapted.
# Fixed numeric constructor arguments mean its string-eval convenience is never used.
source=Path('/app/pp_lcnet.py').read_text()
source=source.replace('from .custom_devices_layers import AdaptiveAvgPool2D','from paddle.nn import AdaptiveAvgPool2D')
source=source.replace('from ..base.theseus_layer import TheseusLayer','from paddle.nn import Layer as TheseusLayer')
source=source.replace('from ....utils.save_load import load_dygraph_pretrain','def load_dygraph_pretrain(*args, **kwargs):\n    raise RuntimeError("automatic downloads disabled")')
namespace={}
exec(compile(source,'reviewed_pp_lcnet_adapter.py','exec'),namespace)
load_start=time.perf_counter()
model=namespace['PPLCNet'](scale=0.5)
weights=paddle.load('/app/weights.pdparams')
missing,unexpected=model.set_state_dict(weights)
assert not missing and not unexpected, (missing,unexpected)
model.eval()
load_seconds=time.perf_counter()-load_start
labels=Path('/app/labels.txt').read_text().splitlines()
base=Image.open('/app/dog.jpg').convert('RGB')
buf=io.BytesIO();base.save(buf,format='JPEG',quality=10);buf.seek(0)
rng=np.random.default_rng(20261012)
noise=Image.fromarray(np.clip(np.asarray(base).astype(float)+rng.normal(0,20,np.asarray(base).shape),0,255).astype('uint8'))
variants={'original':base,'jpeg_quality_10':Image.open(buf).convert('RGB'),'blur_radius_3':base.filter(ImageFilter.GaussianBlur(3)),'pixelate_32':base.resize((32,32)).resize(base.size),'gaussian_noise_sd20':noise}
def preprocess(im):
    width,height=im.size
    im=im.resize((round(width*256/min(width,height)),round(height*256/min(width,height))),Image.Resampling.BILINEAR)
    left=(im.width-224)//2;top=(im.height-224)//2
    x=np.asarray(im.crop((left,top,left+224,top+224)),dtype='float32')/255
    x=(x-np.array([.485,.456,.406],dtype='float32'))/np.array([.229,.224,.225],dtype='float32')
    return paddle.to_tensor(x.transpose(2,0,1)[None])
results=[]
with paddle.no_grad():
    for name,im in variants.items():
        x=preprocess(im)
        for _ in range(3): model(x)
        timings=[]
        for _ in range(20):
            t=time.perf_counter();logits=model(x).numpy();timings.append(time.perf_counter()-t)
        assert logits.shape==(1,1000) and np.isfinite(logits).all()
        top=np.argsort(logits[0])[-5:][::-1]
        results.append({'input':name,'top5':[{'class_id':int(i),'label':labels[i]} for i in top],
                        'median_seconds':statistics.median(timings),'p95_seconds':float(np.quantile(timings,.95)),
                        'repeats':20})
median=statistics.median(x['median_seconds'] for x in results)
usage=resource.getrusage(resource.RUSAGE_SELF)
report={'label':'simplified functional check, not reproduction or accuracy benchmark','paddle':paddle.__version__,
        'model':'PPLCNet_x0_5 official weights with repository-import adapter','weight_bytes':Path('/app/weights.pdparams').stat().st_size,
        'parameters':sum(int(p.numel()) for p in model.parameters()),'model_load_seconds':load_seconds,
        'peak_rss_mib':usage.ru_maxrss/1024,'cpu_seconds':usage.ru_utime+usage.ru_stime,'wall_seconds':time.perf_counter()-started,
        'independent_images':1,'derived_inputs':5,'baseline_comparison':False,'accuracy':None,
        'results':results,'throughput_items_per_minute':60/median,'projected_10000_inference_seconds':10000*median,
        'projection_caveat':'inference-only warmed sequential batch1; excludes preprocessing, I/O, scheduling, startup and cloud CPU differences',
        'permissions':'Docker: network none, no host mounts or credentials, non-root, read-only root, cap-drop ALL, 2 CPUs, 8GiB RAM'}
print(json.dumps(report,ensure_ascii=False,indent=2))
