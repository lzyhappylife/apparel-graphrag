"""Candidate extraction jobs, source-grounding validation and auditable review."""
import hashlib,json,re,subprocess,sys,threading,uuid,os,platform,importlib.util
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).parent
PREDICATES={'要求','禁止','建议','允许','包括','属于','负责','依据','期限','温度','数量'}
def now():return datetime.now(timezone.utc).isoformat()
def model_status():
 path=ROOT/'data/extraction-model-manifest.json'
 if platform.system()!='Darwin' or platform.machine()!='arm64':return dict(state='unsupported',message='MLX 本地模型需要 Apple Silicon Mac')
 if not path.exists() or importlib.util.find_spec('mlx_lm') is None:return dict(state='missing',message='请运行 bash setup-extraction.sh 安装本地模型')
 spec=json.loads(path.read_text())
 if not all((ROOT/spec['path']/name).is_file() for name in spec['files']):return dict(state='missing',message='模型文件缺失，请运行 bash setup-extraction.sh')
 return dict(state='ready',model=spec['model'],revision=spec['revision'],message='本地 Qwen 模型已安装')
def validate_candidate(row,chunk,doc):
 if not isinstance(row,dict):raise ValueError('候选项必须为对象')
 for key in ('source','relation','target','quote'):
  if not isinstance(row.get(key),str) or not row[key].strip():raise ValueError('缺少字符串字段 '+key)
 s,r,t,q=[row[k].strip() for k in ('source','relation','target','quote')]
 from schemas import for_document
 schema=for_document(doc)
 if r not in schema['relations']:raise ValueError('当前场景不支持此关系类型')
 types={}
 for key in ('source_type','target_type'):
  if row.get(key):
   if row[key] not in schema['entity_types']:raise ValueError('实体类型不在场景定义中')
   types[key]=row[key]
 if not 1<=len(s)<=100 or not 1<=len(t)<=160 or len(q)>2000:raise ValueError('字段长度超限')
 if s==t:raise ValueError('主客体相同')
 if s not in q or t not in q:raise ValueError('主客体未逐字出现在证据中')
 starts=[m.start() for m in re.finditer(re.escape(q),chunk['text'])]
 if len(starts)!=1:raise ValueError('证据不在原文中，或位置不唯一')
 # Quote alignment is necessary, not proof of semantic entailment; approval is still required.
 start=chunk['start']+starts[0];end=start+len(q)
 if doc['text'][start:end]!=q:raise ValueError('原文偏移校验失败')
 key=json.dumps([doc['id'],doc['sha256'],chunk['id'],s,r,t,start,end],ensure_ascii=False)
 return dict(id='R'+hashlib.sha256(key.encode()).hexdigest()[:24],source=s,relation=r,target=t,quote=q,start=start,end=end,chunk_id=chunk['id'],doc_id=doc['id'],source_hash=doc['sha256'],status='pending',kind='model_candidate',method='Qwen3 local MLX',created_at=now(),schema_id=schema['id'],schema_version=schema['version'],**types)
def init_tables(db):
 db.execute('CREATE TABLE IF NOT EXISTS relation_candidates(id TEXT PRIMARY KEY,doc_id TEXT,payload TEXT)')
 db.execute('CREATE TABLE IF NOT EXISTS extraction_jobs(id TEXT PRIMARY KEY,doc_id TEXT,payload TEXT)')
 db.execute('CREATE TABLE IF NOT EXISTS review_audit(id INTEGER PRIMARY KEY,event TEXT)')
 db.commit()
def candidates(e,doc_id=None):
 result=[]
 for row in e.db.execute('SELECT payload FROM relation_candidates ORDER BY rowid DESC'):
  c=json.loads(row[0]);d=e.docs.get(c['doc_id'])
  if doc_id and c['doc_id']!=doc_id:continue
  c['eligible']=bool(d and d['trusted'] and d['sha256']==c['source_hash'])
  result.append(c)
 return result
def audit(e,candidate_id=None):
 rows=[json.loads(r[0]) for r in e.db.execute('SELECT event FROM review_audit ORDER BY id DESC')]
 return [r for r in rows if not candidate_id or r['candidate_id']==candidate_id]
def review(e,id,decision,source_hash,note=''):
 if decision not in ('accepted','rejected','pending'):raise ValueError('审核结果无效')
 if not isinstance(note,str) or len(note)>1000:raise ValueError('审核备注须为 1000 字以内的文字')
 row=e.db.execute('SELECT payload FROM relation_candidates WHERE id=?',(id,)).fetchone()
 if not row:raise ValueError('候选关系不存在')
 c=json.loads(row[0]);d=e.docs.get(c['doc_id']);chunk=e.chunks.get(c['chunk_id'])
 if not d or not chunk or d['sha256']!=source_hash or c['source_hash']!=source_hash:raise ValueError('原文已变化，请重新抽取')
 if decision=='accepted' and not d['trusted']:raise ValueError('请先核验文档，再批准关系')
 validate_candidate(c,chunk,d)
 before=c['status'];c['status']=decision;c['reviewed_at']=now();c['review_note']=note
 with e.db:
  e.db.execute('UPDATE relation_candidates SET payload=? WHERE id=?',(json.dumps(c,ensure_ascii=False),id))
  e.db.execute('INSERT INTO review_audit(event) VALUES (?)',(json.dumps(dict(candidate_id=id,doc_id=c['doc_id'],from_status=before,to_status=decision,source_hash=source_hash,note=note,at=now(),actor='local reviewer'),ensure_ascii=False),))
 e.rebuild();return c
class RelationJobs:
 def __init__(self,engine):
  self.e=engine;self.processes={};self.cancelled=set();init_tables(engine.db)
  # A previous process cannot survive the owning server. Mark only persisted nonterminal jobs interrupted.
  with engine.db:
   for jid,payload in engine.db.execute('SELECT id,payload FROM extraction_jobs').fetchall():
    j=json.loads(payload)
    if j['state'] in ('queued','running'):
     j.update(state='interrupted',error='服务进程已重新启动，原任务中断；可重新发起。',updated_at=now());engine.db.execute('UPDATE extraction_jobs SET payload=? WHERE id=?',(json.dumps(j,ensure_ascii=False),jid))
 def _read(self,id):
  row=self.e.db.execute('SELECT payload FROM extraction_jobs WHERE id=?',(id,)).fetchone()
  if not row:raise ValueError('任务不存在')
  return json.loads(row[0])
 def _write(self,j):
  j['updated_at']=now();self.e.db.execute('INSERT OR REPLACE INTO extraction_jobs VALUES (?,?,?)',(j['id'],j['doc_id'],json.dumps(j,ensure_ascii=False)));self.e.db.commit()
 def shutdown(self):
  with self.e.lock:
   for id in list(self.processes):self.cancel(id)
 def list(self):return [json.loads(r[0]) for r in self.e.db.execute('SELECT payload FROM extraction_jobs ORDER BY rowid DESC LIMIT 30')]
 def start(self,doc_id):
  d=self.e.docs.get(doc_id)
  if not d:raise ValueError('文档不存在')
  status=model_status()
  if status['state']!='ready':raise ValueError(status['message'])
  if any(p.poll() is None for p in self.processes.values()) or any(j['state'] in ('queued','running') for j in self.list()):raise ValueError('已有抽取任务正在运行，请等待或取消')
  windows=[]
  for c in self.e.chunks.values():
   if c['doc_id']!=doc_id:continue
   for start in range(0,len(c['text']),800):
    text=c['text'][start:start+1000]
    if start and len(text)<30:continue
    windows.append(dict(chunk_id=c['id'],start=start,text=text))
  if not windows:raise ValueError('文档没有可抽取片段')
  id='J'+uuid.uuid4().hex[:16]
  job=dict(id=id,doc_id=doc_id,title=d['title'],source_hash=d['sha256'],state='queued',total=len(windows),completed=0,candidates=0,invalid=0,failed_windows=0,errors=[],created_at=now())
  from schemas import for_document
  job['schema']=for_document(d)
  self._write(job)
  threading.Thread(target=self._run,args=(id,windows),daemon=True).start()
  return job
 def cancel(self,id):
  j=self._read(id)
  if j['state'] not in ('queued','running'):return j
  self.cancelled.add(id)
  p=self.processes.get(id)
  if p and p.poll() is None:p.terminate()
  j['state']='cancelled';self._write(j);return j
 def _run(self,id,windows):
  try:
   with self.e.lock:
    j=self._read(id)
    if j['state']=='cancelled':return
    j['state']='running';self._write(j)
    p=subprocess.Popen([sys.executable,str(ROOT/'extraction_worker.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=str(ROOT));self.processes[id]=p
   # Drain stderr concurrently to prevent a model warning from filling the OS pipe.
   stderr=[]
   def drain():
    for line in p.stderr:
     stderr.append(line[-500:]);stderr[:]=stderr[-10:]
   threading.Thread(target=drain,daemon=True).start()
   p.stdin.write(json.dumps({'windows':windows,'parent_pid':os.getpid(),'schema':j.get('schema')},ensure_ascii=False));p.stdin.close()
   for line in p.stdout:
    event=json.loads(line)
    if event.get('event')=='loaded':continue
    with self.e.lock:
     j=self._read(id)
     if id in self.cancelled:break
     d=self.e.docs.get(j['doc_id'])
     if not d or d['sha256']!=j['source_hash']:
      j.update(state='stale',error='文档已删除或变化');self._write(j);p.terminate();break
     if event.get('event')=='chunk_error':
      j['failed_windows']+=1;j['errors']=(j['errors']+[event['error']])[-10:]
     elif event.get('event')=='chunk':
      chunk=self.e.chunks[event['chunk_id']]
      for row in event.get('relations',[])[:6]:
       try:
        c=validate_candidate(row,chunk,d)
        cur=self.e.db.execute('INSERT OR IGNORE INTO relation_candidates VALUES (?,?,?)',(c['id'],c['doc_id'],json.dumps(c,ensure_ascii=False)))
        j['candidates']+=cur.rowcount
       except (ValueError,KeyError,TypeError) as ex:
        j['invalid']+=1;j['errors']=(j['errors']+[str(ex)])[-10:]
     else:continue
     j['completed']+=1;self._write(j)
   code=p.wait(timeout=15)
   with self.e.lock:
    j=self._read(id)
    if j['state'] not in ('cancelled','stale'):
     if code!=0:j.update(state='failed',error='模型进程失败：'+''.join(stderr)[-1000:])
     elif j['completed']!=j['total']:j.update(state='failed',error='模型未处理所有片段')
     else:j['state']='partial' if j['failed_windows'] or j['invalid'] else 'complete'
     self._write(j)
  except Exception as ex:
   with self.e.lock:
    j=self._read(id)
    if j['state']!='cancelled':j.update(state='failed',error=type(ex).__name__+': '+str(ex)[:500]);self._write(j)
   p=self.processes.get(id)
   if p and p.poll() is None:p.terminate()
  finally:self.processes.pop(id,None)
