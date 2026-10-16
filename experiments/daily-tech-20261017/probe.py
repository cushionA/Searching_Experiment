"""Fixed-size CPU ONNX probe; no runtime fetches, custom ops, or upstream imports."""
import hashlib,json,os,platform,resource,statistics,sys,time
import numpy as np
import onnxruntime as ort
from PIL import Image,ImageOps
model=sys.argv[1]
opts=ort.SessionOptions();opts.intra_op_num_threads=1;opts.inter_op_num_threads=1
start=time.perf_counter();sess=ort.InferenceSession('/app/'+model+'.onnx',sess_options=opts,providers=['CPUExecutionProvider']);load=time.perf_counter()-start
result=dict(model=model,python=platform.python_version(),ort=ort.__version__,numpy=np.__version__,providers=sess.get_providers(),load_s=load,metadata=sess.get_modelmeta().custom_metadata_map,inputs=[dict(name=x.name,shape=x.shape,type=x.type) for x in sess.get_inputs()],images=[])
def decode(out):
 a=out[0][0]
 if a.shape[-1]==6: return a[a[:,4]>=.25], 'embedded_end_to_end'
 assert a.shape[0]==84,a.shape
 a=a.T; labels=a[:,4:].argmax(axis=1);scores=a[:,4:].max(axis=1);keep=scores>=.25;a=a[keep];labels=labels[keep];scores=scores[keep]
 boxes=np.column_stack((a[:,0]-a[:,2]/2,a[:,1]-a[:,3]/2,a[:,0]+a[:,2]/2,a[:,1]+a[:,3]/2))
 order=scores.argsort()[::-1];chosen=[]
 while len(order):
  i=order[0];chosen.append(i);order=order[1:]
  if not len(order): break
  lo=np.maximum(boxes[i,:2],boxes[order,:2]);hi=np.minimum(boxes[i,2:],boxes[order,2:]);inter=np.maximum(hi-lo,0).prod(axis=1)
  area=np.maximum(boxes[:,2:]-boxes[:,:2],0).prod(axis=1);iou=inter/np.maximum(area[i]+area[order]-inter,1e-9)
  order=order[(iou<=.7)|(labels[order]!=labels[i])]
 return np.column_stack((boxes[chosen],scores[chosen],labels[chosen])), 'class_aware_nms_iou_0.7'
for name in ['bus.jpg','zidane.jpg']:
 t=time.perf_counter();im=Image.open('/app/'+name).convert('RGB');im=ImageOps.pad(im,(640,640),method=Image.Resampling.BILINEAR,color=(114,114,114));x=np.asarray(im,dtype=np.float32).transpose(2,0,1)[None]/255.;x=np.ascontiguousarray(x);pre=time.perf_counter()-t
 for _ in range(5): sess.run(None,{sess.get_inputs()[0].name:x})
 times=[];post=[]
 for _ in range(30):
  t=time.perf_counter();out=sess.run(None,{sess.get_inputs()[0].name:x});times.append(time.perf_counter()-t)
  t=time.perf_counter();detections,mode=decode(out);post.append(time.perf_counter()-t)
 assert all(np.isfinite(a).all() for a in out)
 result['images'].append(dict(name=name,tensor_sha256=hashlib.sha256(x.tobytes()).hexdigest(),preprocess_s=pre,output_shapes=[list(a.shape) for a in out],mode=mode,inference_s=times,median_s=statistics.median(times),p95_s=float(np.quantile(times,.95)),post_median_s=statistics.median(post),detections=detections.tolist()))
result['peak_rss_kib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
result['cpu_s']=resource.getrusage(resource.RUSAGE_SELF).ru_utime+resource.getrusage(resource.RUSAGE_SELF).ru_stime
print(json.dumps(result))
