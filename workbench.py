"""Observable query jobs and per-document workflow, using actual index state."""
import copy
import threading
import time
import uuid
from schemas import for_document
from synthesis import enrich


class QueryJobs:
    def __init__(self, engine):
        self.e = engine; self.lock = threading.Lock(); self.jobs = {}

    def start(self, question, doc_id=None, agentic=True):
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000: raise ValueError('问题长度须为 1–2000 字符')
        with self.lock:
            if sum(j['state'] in ('queued', 'running') for j in self.jobs.values()) >= 2: raise ValueError('已有问答正在处理，请稍候')
            jid = 'Q'+uuid.uuid4().hex[:16]
            self.jobs[jid] = dict(id=jid, state='queued', question=question, steps=[], started=time.time(), agentic=bool(agentic))
            while len(self.jobs) > 20:
                old=next((k for k,v in self.jobs.items() if v['state'] not in ('queued','running')),None)
                if old is None:break
                del self.jobs[old]
        threading.Thread(target=self._run, args=(jid,question,doc_id,bool(agentic)), daemon=True).start()
        return self.get(jid)

    def get(self, jid):
        with self.lock:
            if jid not in self.jobs: raise ValueError('问答任务不存在或已过期')
            return copy.deepcopy(self.jobs[jid])

    def _run(self, jid, question, doc_id, agentic):
        def progress(key, label):
            with self.lock:
                j=self.jobs[jid]
                for step in j['steps']:
                    if step['state']=='running':step['state']='complete'
                j['steps'].append(dict(key=key,label=label,state='running',elapsed_ms=round((time.time()-j['started'])*1000)))
                j['state']='running'
        try:
            with self.e.lock:
                result = enrich(self.e, self.e.query(question,doc_id=doc_id,progress=progress,agentic=agentic), progress)
            with self.lock:
                j=self.jobs[jid]
                for step in j['steps']:step['state']='complete'
                j.update(state='complete',result=result,elapsed_ms=round((time.time()-j['started'])*1000))
        except Exception as ex:
            with self.lock:
                j=self.jobs[jid]
                for step in j['steps']:
                    if step['state']=='running':step['state']='failed'
                j.update(state='failed',error=str(ex))


def workflows(engine, relation_jobs):
    jobs=relation_jobs.list(); result=[]
    from relations import candidates
    rows=candidates(engine)
    for d in engine.docs.values():
        cidset={c['id'] for c in engine.chunks.values() if c['doc_id']==d['id']}
        selected=[r for r in rows if r['doc_id']==d['id']]
        latest=next((j for j in jobs if j['doc_id']==d['id']),None)
        graph_edges=sum(e['kind']=='reviewed' and e['chunk_id'] in cidset for e in engine.edges)
        windows=sum(r['cid'] in cidset for r in engine.semantic.rows) if engine.semantic else 0
        schema=for_document(d)
        result.append(dict(id=d['id'],title=d['title'],schema_id=schema['id'],schema_name=schema['name'],schema_version=schema['version'],trusted=d['trusted'],chunks=len(cidset),
            parse=dict(state='complete',method=d.get('parse_metadata',{}).get('method','text'),pages=len(d.get('parse_metadata',{}).get('pages',[])),warnings=d.get('parse_metadata',{}).get('warnings',[])),
            extraction=dict(state=latest['state'] if latest else ('reviewed_mapping' if graph_edges else 'not_started'),job=latest),
            review=dict(pending=sum(r['status']=='pending' for r in selected),accepted=sum(r['status']=='accepted' for r in selected),rejected=sum(r['status']=='rejected' for r in selected)),
            graph=dict(state='ready' if graph_edges else 'empty',edges=graph_edges),
            vector=dict(state='ready' if windows else ('disabled' if not engine.semantic else 'awaiting_review'),windows=windows),
            qa=dict(state='ready' if d['trusted'] and (windows or not engine.semantic) else 'awaiting_review')))
    return result
