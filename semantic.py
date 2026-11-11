"""Local ONNX semantic retrieval. Explicit model mode, offline inference, durable cache."""
import hashlib,json,math,threading
from pathlib import Path
ROOT=Path(__file__).parent
EMBED='BAAI/bge-small-zh-v1.5'
RERANK='BAAI/bge-reranker-v2-m3-int8'
class SemanticIndex:
 def __init__(self,db,allow_download=False):
  import numpy as np
  from fastembed import TextEmbedding
  from fastembed.rerank.cross_encoder import TextCrossEncoder
  self.np=np;self.db=db;self.lock=threading.RLock()
  opts=dict(cache_dir=str(ROOT/'data/models'),threads=4,local_files_only=not allow_download,providers=['CPUExecutionProvider'])
  manifest={m['model']:m for m in json.loads((ROOT/'data/model-manifest.json').read_text())}
  self.cache_key=EMBED+'@'+manifest[EMBED]['revision']
  self.embed=TextEmbedding(EMBED,specific_model_path=str(ROOT/manifest[EMBED]['path']),**opts)
  self.reranker=TextCrossEncoder(RERANK,specific_model_path=str(ROOT/manifest[RERANK]['path']),**opts)
  self.db.execute('CREATE TABLE IF NOT EXISTS embeddings(model TEXT, hash TEXT, vector BLOB, PRIMARY KEY(model,hash))')
  self.rows=[];self.matrix=None;self.last_error=None
 def build(self,chunks,docs):
  rows=[];missing={}
  for cid,c in chunks.items():
   if not docs[c['doc_id']]['trusted']:continue
   # Windows prevent the embedding model's 512-token limit from hiding long clauses.
   for start in range(0,len(c['text']),280):
    window=c['text'][start:start+360]
    if start and len(window)<40:continue
    text=docs[c['doc_id']]['title']+' / '+' / '.join(c.get('context',[]))+'\n'+window
    h=hashlib.sha256(text.encode()).hexdigest()
    row=self.db.execute('SELECT vector FROM embeddings WHERE model=? AND hash=?',(self.cache_key,h)).fetchone()
    v=self.np.frombuffer(row[0],dtype='float32').copy() if row else None
    rows.append(dict(cid=cid,hash=h,text=text,start=start,end=start+len(window),vector=v))
    if v is None:missing[h]=text
  if missing:
   keys=list(missing)
   vectors=list(self.embed.passage_embed([missing[h] for h in keys],batch_size=16))
   for h,v in zip(keys,vectors):
    v=self.np.asarray(v,dtype='float32');self.db.execute('INSERT OR REPLACE INTO embeddings VALUES (?,?,?)',(self.cache_key,h,v.tobytes()))
   self.db.commit()
   lookup=dict(zip(keys,vectors))
   for row in rows:
    if row['vector'] is None:row['vector']=lookup[row['hash']]
  self.rows=rows
  self.matrix=self.np.stack([r.pop('vector') for r in rows]) if rows else self.np.empty((0,512),dtype='float32')
  self.matrix/=self.np.maximum(self.np.linalg.norm(self.matrix,axis=1,keepdims=True),1e-12)
 def search(self,q):
  v=next(self.embed.query_embed(q));v=v/max(float(self.np.linalg.norm(v)),1e-12)
  sims=self.matrix@v;scores={};windows={}
  for i,row in enumerate(self.rows):
   cid=row['cid'];score=float(sims[i])
   if cid not in scores or score>scores[cid]:scores[cid]=score;windows[cid]=row
  return scores,windows
 def rerank(self,q,cids,windows):
  # Rank the best semantic window per clause; return the full source clause as evidence.
  ids=[cid for cid in cids if cid in windows]
  with self.lock:values=list(self.reranker.rerank(q,[windows[cid]['text'] for cid in ids],batch_size=1))
  return dict(zip(ids,values))
 def status(self):
  return dict(state='ready',embedding_model=EMBED,reranker_model=RERANK,dimension=512,index_windows=len(self.rows),execution='local CPU / offline',score_semantics='模型相关性 logits，不是事实正确率')
