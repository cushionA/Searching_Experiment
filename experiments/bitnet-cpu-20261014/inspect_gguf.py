"""Read-only GGUF metadata inspection; never loads or executes a model."""
import json
import struct
import sys

with open(sys.argv[1],'rb') as f:
 def scalar(fmt): return struct.unpack('<'+fmt,f.read(struct.calcsize('<'+fmt)))[0]
 def string(keep=True):
  n=scalar('Q')
  if keep: return f.read(n).decode('utf-8')
  f.seek(n,1)
 def value(kind,keep=True):
  if kind==8: return string(keep)
  if kind==9:
   subtype=scalar('I'); count=scalar('Q')
   for _ in range(count): value(subtype,False)
   return {'array_count':count,'element_type':subtype}
  return scalar({0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}[kind])
 assert f.read(4)==b'GGUF'
 version=scalar('I');assert version in (2,3)
 tensors=scalar('Q');count=scalar('Q');keys=[];selected={}
 for _ in range(count):
  key=string();keys.append(key);kind=scalar('I');keep=key in ['general.architecture','general.name','tokenizer.ggml.model','tokenizer.ggml.pre','tokenizer.chat_template','tokenizer.ggml.bos_token_id','tokenizer.ggml.eos_token_id']
  v=value(kind,keep)
  if keep: selected[key]=v
 print(json.dumps({'version':version,'tensor_count':tensors,'metadata_count':count,'metadata_keys':keys,'selected':selected,'pre_tokenizer_key_present':'tokenizer.ggml.pre' in keys},ensure_ascii=False,indent=2))
