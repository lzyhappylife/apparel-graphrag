import argparse, atexit, hashlib, json, mimetypes, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from engine import Engine, ROOT
from relations import RelationJobs, candidates, review, audit
from workbench import QueryJobs, workflows
from synthesis import enrich
from pdf_pipeline import PDFJobs, capabilities, UPLOADS
from schemas import catalog, save as save_schema, domain_profile

engine=Engine(); jobs=RelationJobs(engine); query_jobs=QueryJobs(engine); pdf_jobs=PDFJobs()
atexit.register(jobs.shutdown)

class Handler(BaseHTTPRequestHandler):
    def send(self,data,status=200,kind='application/json; charset=utf-8'):
        raw=json.dumps(data,ensure_ascii=False).encode() if kind.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(raw)))
        self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Cache-Control','no-store')
        self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass

    def do_GET(self):
        p=urlparse(self.path);q=parse_qs(p.query)
        try:
            # Job status does not wait for the model/index lock.
            if p.path=='/api/query-status':return self.send(query_jobs.get(q.get('id',[''])[0]))
            if p.path=='/api/pdf-status':return self.send(pdf_jobs.get(q.get('id',[''])[0]))
            if p.path=='/api/capabilities':return self.send(capabilities())
            if p.path=='/api/schemas':return self.send(catalog())
            if p.path=='/api/pdf-file':
                key=q.get('id',[''])[0]
                if not re.fullmatch(r'[a-f0-9]{64}',key):raise ValueError('PDF 标识无效')
                path=UPLOADS/(key+'.pdf')
                if not path.is_file():return self.send({'error':'PDF 不存在'},404)
                return self.send(path.read_bytes(),kind='application/pdf')
            with engine.lock:
                if p.path=='/api/workflow':return self.send(workflows(engine,jobs))
                if p.path=='/api/relations':return self.send(candidates(engine,q.get('doc_id',[None])[0]))
                if p.path=='/api/review-audit':return self.send(audit(engine,q.get('candidate_id',[None])[0]))
                if p.path=='/api/jobs':return self.send(jobs.list())
                if p.path=='/api/stats':return self.send(engine.stats())
                if p.path=='/api/extractions':return self.send(engine.extractions())
                if p.path=='/api/graph':return self.send(engine.graph())
                if p.path=='/api/documents':return self.send([dict(d,chunks=sum(c['doc_id']==d['id'] for c in engine.chunks.values())) for d in engine.docs.values()])
                if p.path=='/api/export':return self.send(dict(documents=list(engine.docs.values()),chunks=list(engine.chunks.values()),graph=engine.graph(),entities=engine.extractions(),candidates=candidates(engine),review_audit=audit(engine),schemas=catalog(),workflow=workflows(engine,jobs),stats=engine.stats()))
                if p.path=='/api/evidence':
                    cid=q.get('id',[''])[0]
                    return self.send(engine.evidence(cid)) if cid in engine.chunks else self.send({'error':'证据不存在'},404)
            if p.path not in ['/','/app.js','/graph.js','/style.css']:return self.send({'error':'不存在'},404)
            path=ROOT/'static'/('index.html' if p.path=='/' else p.path[1:])
            return self.send(path.read_bytes(),kind=mimetypes.guess_type(path)[0]+'; charset=utf-8')
        except (ValueError,KeyError) as ex:self.send({'error':str(ex)},400)
        except Exception as ex:self.send({'error':'读取失败：'+type(ex).__name__},500)

    def do_POST(self):
        host=self.headers.get('Host','');origin=self.headers.get('Origin')
        if host not in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)) or (origin and origin!='http://'+host):return self.send({'error':'仅允许同源本地请求'},403)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>28_000_000:raise ValueError('请求大小超限')
            data=json.loads(self.rfile.read(n));path=urlparse(self.path).path
            if not isinstance(data,dict):raise ValueError('请求须为 JSON 对象')
            if path=='/api/ask':return self.send(query_jobs.start(data.get('question',''),data.get('doc_id'),data.get('agentic',True)))
            if path in ('/api/pdf','/api/parse-pdf'):return self.send(pdf_jobs.start(data.get('base64',''),data.get('mode','auto')))
            with engine.lock:
                if path=='/api/schema':
                    result=save_schema(data);engine.rebuild();return self.send(result)
                if path=='/api/extract':return self.send(jobs.start(data.get('id')))
                if path=='/api/cancel-job':return self.send(jobs.cancel(data.get('id')))
                if path=='/api/review-relation':return self.send(review(engine,data.get('id'),data.get('decision'),data.get('source_hash'),data.get('note','')))
                if path=='/api/query':return self.send(enrich(engine,engine.query(data.get('question',''),doc_id=data.get('doc_id'),agentic=data.get('agentic',False))))
                if path=='/api/ingest':
                    metadata=None
                    if data.get('parse_job_id'):
                        job=pdf_jobs.get(data['parse_job_id'])
                        if job['state']!='complete':raise ValueError('PDF 尚未解析成功')
                        metadata=job['result']['metadata'];metadata['edited']=job['result']['text']!=data.get('text')
                    return self.send(engine.ingest(data.get('title',''),data.get('text',''),data.get('url',''),data.get('schema_id'),data.get('kind','reference'),metadata))
                if path=='/api/review':return self.send(engine.review_document(data.get('id'),data.get('trusted')))
                if path=='/api/delete':
                    for job in jobs.list():
                        if job['doc_id']==data.get('id') and job['state'] in ('queued','running'):jobs.cancel(job['id'])
                    engine.delete_document(data.get('id'));return self.send({'ok':True})
            self.send({'error':'不存在'},404)
        except (ValueError,KeyError,TypeError) as ex:self.send({'error':str(ex)},400)
        except Exception as ex:self.send({'error':'处理失败：'+type(ex).__name__},500)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);args=p.parse_args()
    print(f"{domain_profile().get('name','证据工作台')} http://127.0.0.1:{args.port}",flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
