"""Evidence-first local GraphRAG. No network calls or generative claims at query time."""
import collections, hashlib, json, math, re, sqlite3, threading
from pathlib import Path
ROOT=Path(__file__).parent
DOMAIN=__import__('schemas').domain_profile()
TERMS=__import__('schemas').catalog()[0]['entity_types']
ALIASES=__import__('schemas').catalog()[0]['aliases']
# Reviewed mappings refer to complete clauses: conditions and exceptions remain in evidence.
FACTS=json.loads((ROOT/'data/reviewed-mappings.json').read_text())

def tokens(s):
 s=s.lower();return re.findall(r'[a-z]+|\d+(?:\.\d+)?',s)+[s[i:i+2] for i in range(len(s)-1) if re.fullmatch(r'[\u4e00-\u9fff]{2}',s[i:i+2])]

def split_doc(d):
 text=d['text'];pat=r'(?m)^(\d+(?:\.\d+)*)(?=[^\d.])' if d['id']=='S1' else (r'(?m)^[ \u3000]*(第[一二三四五六七八九十]+条)[ \u00a0]+' if d['id'] in ('S2','S3','S4') else r'(?m)^.+')
 matches=list(re.finditer(pat,text));chunks=[]
 for i,m in enumerate(matches):
  end=matches[i+1].start() if i+1<len(matches) else len(text)
  raw=text[m.start():end];value=raw.rstrip();
  if not value.strip() or (d['id'] in ('S1','S2','S3','S4') and len(value)<12):continue
  chunks.append(dict(id=d['id']+':'+(m.group(1) if d['id'] in ('S1','S2','S3','S4') else str(i+1)),doc_id=d['id'],section=m.group(1) if d['id'] in ('S1','S2','S3','S4') else str(i+1),text=value,start=m.start(),end=m.start()+len(value)))
 for c in chunks:
  ancestors=[]
  parts=c['section'].split('.')
  for i in range(1,len(parts)):
   parent='.'.join(parts[:i]);match=next((m for m in matches if m.group(1)==parent),None) if d['id']=='S1' else None
   if match:
    line=text[match.start():].split('\n')[0]
    if len(line)<60:ancestors.append(line)
  if d['id'].startswith('U'):
   pages=list(re.finditer(r'(?m)^\[PDF 第 (\d+) 页\]',text[:c['start']+1]))
   if pages:ancestors.append('PDF 第 '+pages[-1].group(1)+' 页')
  c['context']=ancestors
 return chunks

class Engine:
 def __init__(self,db=None,semantic=None):
  self.lock=threading.RLock();self.db=sqlite3.connect(db or ROOT/'data/knowledge.db',check_same_thread=False)
  self.db.execute('CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, payload TEXT)')
  for d in json.loads((ROOT/'data/sources.json').read_text()):self.db.execute('INSERT OR IGNORE INTO documents VALUES (?,?)',(d['id'],json.dumps(d,ensure_ascii=False)))
  from relations import init_tables
  init_tables(self.db)
  self.db.commit()
  self.semantic=None
  config=ROOT/'data/runtime.json'
  enabled=semantic if semantic is not None else (json.loads(config.read_text()).get('semantic',False) if config.exists() else False)
  if enabled:
   from semantic import SemanticIndex
   self.semantic=SemanticIndex(self.db)
  self.evidence_guard=None
  if enabled and config.exists() and json.loads(config.read_text()).get('evidence_guard',False):
   from evidence_guard import EvidenceGuard
   self.evidence_guard=EvidenceGuard()
  self.rebuild()
 def rebuild(self):
  self.docs={r[0]:json.loads(r[1]) for r in self.db.execute('SELECT * FROM documents')};self.chunks={c['id']:c for d in self.docs.values() for c in split_doc(d)}
  self.nodes={};self.edges=[];self.mentions=collections.defaultdict(set)
  def node(label,typ):
   self.nodes[label]={'id':label,'label':label,'type':typ}
  from schemas import for_document
  for c in self.chunks.values():
   for typ,terms in for_document(self.docs[c['doc_id']])['entity_types'].items():
    for t in terms:
     if t in c['text']:node(t,typ);self.mentions[t].add(c['id'])
   for ref in re.findall(r'(\d+(?:\.\d+){1,4})条款',c['text']):
    dest=c['doc_id']+':'+ref
    if dest in self.chunks:self.edges.append(dict(source=c['id'],target=dest,relation='条款引用',chunk_id=c['id'],kind='reference'))
  for c in self.chunks.values():
   for ref in re.findall(r'本办法(第[一二三四五六七八九十]+条)',c['text']):
    target=c['doc_id']+':'+ref
    if target in self.chunks and target!=c['id']:self.edges.append(dict(source=c['id'],target=target,relation='条款引用',chunk_id=c['id'],kind='reference'))
  for sec,s,r,t in FACTS:
   cid='S1:'+sec
   if cid in self.chunks:
    node(s,self.nodes.get(s,{}).get('type','对象'));node(t,self.nodes.get(t,{}).get('type','条件'));self.edges.append(dict(source=s,target=t,relation=r,chunk_id=cid,kind='reviewed'));self.mentions[s].add(cid);self.mentions[t].add(cid)
  extra_file=ROOT/'data/extra-relations.json'
  for edge in json.loads(extra_file.read_text()) if extra_file.exists() else []:
   if edge['chunk_id'] in self.chunks:
    for endpoint in ('source','target'):
     node(edge[endpoint],self.nodes.get(edge[endpoint],{}).get('type','许可'));self.mentions[edge[endpoint]].add(edge['chunk_id'])
    self.edges.append(edge)
  for row in self.db.execute('SELECT payload FROM relation_candidates'):
   c=json.loads(row[0]);d=self.docs.get(c['doc_id'])
   if c['status']=='accepted' and d and d['trusted'] and d['sha256']==c['source_hash'] and c['chunk_id'] in self.chunks:
    from relations import validate_candidate
    try:validate_candidate(c,self.chunks[c['chunk_id']],d)
    except ValueError:continue
    edge=dict(c,kind='reviewed',origin='local_model_reviewed')
    for field in ('source','target'):
     node(c[field],c.get(field+'_type') or self.nodes.get(c[field],{}).get('type','文档实体'));self.mentions[c[field]].add(c['chunk_id'])
    self.edges.append(edge)
  # Entity->clause incidence is a provenance edge, never a fabricated business relation.
  self.tf={cid:collections.Counter(tokens(c['text'])) for cid,c in self.chunks.items() if self.docs[c['doc_id']]['trusted']};self.df=collections.Counter(t for v in self.tf.values() for t in v)
  self.n=len(self.tf);self.avg=sum(sum(v.values()) for v in self.tf.values())/max(self.n,1)
  self.vec={cid:{t:(1+math.log(n))*math.log(1+self.n/(1+self.df[t])) for t,n in tf.items()} for cid,tf in self.tf.items()};self.norm={cid:math.sqrt(sum(x*x for x in v.values())) for cid,v in self.vec.items()}
  self.db.executescript('CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS edges(id INTEGER PRIMARY KEY,payload TEXT); DELETE FROM chunks; DELETE FROM edges;')
  self.db.executemany('INSERT INTO chunks VALUES (?,?)',[(k,json.dumps(v,ensure_ascii=False)) for k,v in self.chunks.items()]);self.db.executemany('INSERT INTO edges(payload) VALUES (?)',[(json.dumps(e,ensure_ascii=False),) for e in self.edges]);self.db.commit()
  if self.semantic:self.semantic.build(self.chunks,self.docs)
 def graph(self,cids=None):
  es=[e for e in self.edges if e['kind']=='reviewed' and (cids is None or e['chunk_id'] in cids)]
  ids={e[x] for e in es for x in ('source','target')}
  for edge in es:
   if 'edge_id' not in edge:edge['edge_id']='G'+hashlib.sha256(json.dumps([edge['source'],edge['relation'],edge['target'],edge['chunk_id']],ensure_ascii=False).encode()).hexdigest()[:16]
  return dict(nodes=[dict(self.nodes[i],chunk_ids=sorted(self.mentions[i])) for i in sorted(ids)],edges=es)
 def extractions(self):
  rows=[]
  for term,n in self.nodes.items():
   for cid in sorted(self.mentions[term]):
    c=self.chunks[cid];matches=list(re.finditer(re.escape(term),c['text']))
    for m in matches:
     rows.append(dict(entity=term,type=n['type'],chunk_id=cid,doc_id=c['doc_id'],start=c['start']+m.start(),end=c['start']+m.end(),method='reviewed_relation_span' if n['type']=='文档实体' else 'dictionary_exact'))
  return rows
 def stats(self):
  from relations import model_status
  return dict(documents=len(self.docs),chunks=len(self.chunks),entities=len(self.nodes),relations=sum(e['kind']=='reviewed' for e in self.edges),references=sum(e['kind']=='reference' for e in self.edges),engine='BM25 + 中文语义向量 + Agentic 证据图扩展 + 交叉编码重排' if self.semantic else 'BM25 + TF-IDF 稀疏向量 + 证据图扩展',generation='Agentic 逐句引用回答，逐条复核',agentic_retrieval=bool(self.semantic and self.evidence_guard),semantic_embedding=bool(self.semantic),evidence_verifier=bool(self.evidence_guard),extraction_model=model_status(),models=self.semantic.status() if self.semantic else {'state':'disabled','reason':'规则检索模式'})
 def review_document(self,id,trusted):
  if type(trusted) is not bool:raise ValueError('trusted 必须为布尔值')
  d=self.docs.get(id)
  if not d or not id.startswith('U'):raise ValueError('仅可核验用户导入资料')
  d=dict(d,trusted=trusted,version_status='用户已人工核验；时效性由核验者确认' if trusted else '待人工核验')
  self.db.execute('UPDATE documents SET payload=? WHERE id=?',(json.dumps(d,ensure_ascii=False),id));self.db.commit();self.rebuild();return d
 def delete_document(self,id):
  if not isinstance(id,str) or not id.startswith('U'):raise ValueError('内置来源只读')
  with self.db:
   self.db.execute('DELETE FROM documents WHERE id=?',(id,));self.db.execute('DELETE FROM relation_candidates WHERE doc_id=?',(id,))
  self.rebuild()
 def evidence(self,cid):
  c=self.chunks[cid];d=self.docs[c['doc_id']];assert d['text'][c['start']:c['end']]==c['text']
  extra={};meta=d.get('parse_metadata',{})
  if meta.get('file_id'):
   pages=list(re.finditer(r'(?m)^\[PDF 第 (\d+) 页\]',d['text'][:c['start']+1]))
   page=int(pages[-1].group(1)) if pages else 1
   extra=dict(pdf_url='/api/pdf-file?id='+meta['file_id']+'#page='+str(page),page=page,parse_method=meta.get('method'),edited=meta.get('edited',False))
  return dict(c,source=d['title'],url=d['url'],trusted=d['trusted'],version=d['version_status'],**extra)
 def query(self,q,limit=6,doc_id=None,progress=None,agentic=False):
  if not isinstance(q,str) or not 1<=len(q.strip())<=2000:raise ValueError('问题长度须为 1–2000 字符')
  if doc_id and doc_id not in self.docs:raise ValueError('查询文档不存在')
  original=q
  from schemas import catalog
  aliases={k:v for schema in catalog() for k,v in schema.get('aliases',{}).items()}
  for a,b in aliases.items():
   if a in q and b not in q and not (a=='消毒' and '消毒剂' in q):q=q.replace(a,b) if self.semantic else q+' '+b
  qt=collections.Counter(tokens(q));qv={t:(1+math.log(n))*math.log(1+self.n/(1+self.df[t])) for t,n in qt.items()};qn=math.sqrt(sum(v*v for v in qv.values())) or 1
  entities=[t for t in self.mentions if t in q and any(self.docs[self.chunks[c]['doc_id']]['trusted'] for c in self.mentions[t])];scores={};trace={}
  for cid,tf in self.tf.items():
   if doc_id and self.chunks[cid]['doc_id']!=doc_id:continue
   if not self.docs[self.chunks[cid]['doc_id']]['trusted']:continue
   dl=sum(tf.values());bm=sum(math.log(1+(self.n-self.df[t]+.5)/(self.df[t]+.5))*tf[t]*2.5/(tf[t]+1.5*(.25+.75*dl/self.avg)) for t in qt if tf[t])
   cos=sum(w*self.vec[cid].get(t,0) for t,w in qv.items())/(qn*(self.norm[cid] or 1));hit=sum(cid in self.mentions[t] for t in entities)
   fact_hit=sum(e['kind']=='reviewed' and e['chunk_id']==cid and e['source'] in entities for e in self.edges)
   score=bm+cos*8+hit*3+min(fact_hit,2)*12
   focus=[t for t in entities if t in TERMS['操作'] and t not in ('采购','烹饪','食品配送')]
   if focus and not any(cid in self.mentions[t] for t in focus):score*=.35
   scores[cid]=score;trace[cid]=dict(bm25=round(bm,3),vector=round(cos,3),entity_hits=hit,graph_bonus=0)
  if agentic and self.semantic and self.evidence_guard:
   from agentic import run
   planned=run(self,original,doc_id=doc_id,progress=progress,limit=limit)
   if planned and not planned.get('fallback'):
    planned['entities']=entities
    return planned
   if progress:progress('agent_fallback','Agent 规划不可用，回退到标准证据检索')
  if self.semantic:
   from retrieval import query
  return query(self,original,q,scores,trace,entities,limit,doc_id=doc_id,progress=progress)
  ranked=sorted(scores,key=scores.get,reverse=True)
  # Conservative topical gate. Scores measure retrieval relevance, never confidence probabilities.
  abstain=not ranked or scores[ranked[0]]<4 or not entities
  reason='当前已核验资料没有足够证据回答该问题，请补充相关资料或明确服装产品、供应链、质量或合规场景。'
  if any(t in original for t in ['今天','最新','现行','截至','2026','2025','2024','利润','股价','营收','治疗','肝纤维化']):abstain=True;reason='当前库只支持所收录版本的条款查询，不能证明最新政策状态，也没有经营财务或医学证据。'
  if abstain:return dict(question=original,status='abstained',answer=reason,evidence=[],graph=self.graph([]),trace=[],entities=entities)
  seeds=ranked[:3];expanded={}
  for cid in seeds:
   c=self.chunks[cid];prefix=c['section'].rsplit('.',1)[0]
   # Include sibling clauses as context only within narrow numbered sections.
   if c['doc_id']=='S1' and prefix.count('.')>=1:
    for other in self.chunks:
     if other.startswith('S1:'+prefix+'.') and other!=cid:expanded[other]=(cid,'同节上下文')
   for e in self.edges:
    if e['chunk_id']==cid:
     if e['kind']=='reference':expanded[e['target']]=(cid,'显式条款引用')
     elif e['kind']=='reviewed':
      for endpoint in ('source','target'):
       t=e[endpoint]
       if t in entities:
        for nextcid in self.mentions[t]:
         if nextcid not in seeds and self.docs[self.chunks[nextcid]['doc_id']]['trusted']:expanded[nextcid]=(cid,'实体证据扩展')
  for cid,path in expanded.items():
   if cid in scores:scores[cid]+=1.5;trace[cid]['graph_bonus']=1.5;trace[cid]['via']=path
  ordered=sorted(scores,key=scores.get,reverse=True)
  selected=[cid for cid in ordered if scores[cid]>=scores[ordered[0]]*.50][:limit]
  # Context specifically needed for quantitative leave-sample / reheat questions.
  required=[]
  # Domain-specific required-context rules can be added to a profile later.
  for sec in required:
   cid='S1:'+sec
   if cid not in selected:selected.append(cid)
  evidence=[dict(self.evidence(cid),citation='E'+str(i+1)) for i,cid in enumerate(selected)]
  return dict(question=original,status='evidence_found',answer='依据已收录版本，检索到以下原文条款。请连同适用对象、条件和例外阅读。',evidence=evidence,graph=self.graph(selected),trace=[dict(id=cid,score=round(scores[cid],3),**trace[cid]) for cid in selected],entities=entities)
 def ingest(self,title,text,url='',schema_id=None,kind='reference',parse_metadata=None):
  if not isinstance(title,str) or not isinstance(text,str) or not title.strip() or not 10<=len(text)<=200000:raise ValueError('标题必填，正文须为 10–200000 字符')
  if url and not re.match(r'^https?://',url):raise ValueError('来源链接须使用 http 或 https')
  from schemas import catalog
  schema_id = schema_id or catalog()[0]['id']
  if schema_id not in {s['id'] for s in catalog()}:raise ValueError('抽取场景不存在')
  if kind not in ('reference','operational_record','guidance'):raise ValueError('资料类型无效')
  digest=hashlib.sha256(text.encode()).hexdigest();id='U'+digest[:12]
  d=dict(id=id,title=title[:200],text=text,url=url,published='用户未提供',retrieved='本地导入',version_status='用户资料；待人工核验，不进入正式问答',sha256=digest,trusted=False,schema_id=schema_id,kind=kind,parse_metadata=parse_metadata or {'method':'text'})
  if id in self.docs:return self.docs[id]
  with self.lock:self.db.execute('INSERT OR IGNORE INTO documents VALUES (?,?)',(id,json.dumps(d,ensure_ascii=False)));self.db.commit();self.rebuild()
  return d
