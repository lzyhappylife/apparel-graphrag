"""Supervised local model process; fail closed on timeout or malformed IDs."""
import atexit, hashlib, json, queue, subprocess, sys, threading
from pathlib import Path

ROOT = Path(__file__).parent

class EvidenceGuard:
    def __init__(self):
        self.process = None
        self.lock = threading.RLock()
        self.cache = {}
        atexit.register(self.close)

    def close(self):
        with self.lock:
            p = self.process
            self.process = None
            if p and p.poll() is None:
                p.terminate()
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            if p:
                reader=getattr(self,'reader',None)
                if reader:reader.join(timeout=2)
                for stream in (p.stdin,p.stdout):
                    if stream:
                        try:stream.close()
                        except (BrokenPipeError,OSError):pass

    def _read(self):
        try:
            line = self.messages.get(timeout=90)
            if line is None:raise RuntimeError('本地证据复核模型进程已退出')
            result = json.loads(line)
            if 'error' in result:raise RuntimeError(result['error'])
            return result
        except queue.Empty:
            self.close()
            raise RuntimeError('本地证据复核模型响应超时')

    def request(self,request):
        key = hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        with self.lock:
            if key in self.cache:return self.cache[key]
            if self.process is None or self.process.poll() is not None:
                self.messages = queue.Queue()
                p = subprocess.Popen([sys.executable,str(ROOT/'evidence_worker.py')],stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,cwd=ROOT)
                self.process = p
                messages=self.messages
                def drain():
                    for line in p.stdout:messages.put(line)
                    messages.put(None)
                self.reader=threading.Thread(target=drain,daemon=True)
                self.reader.start()
                if self._read().get('event')!='ready':raise RuntimeError('证据复核模型未就绪')
            self.process.stdin.write(json.dumps(request,ensure_ascii=False)+'\n')
            self.process.stdin.flush()
            result = self._read()
            if len(self.cache)>=64:self.cache.pop(next(iter(self.cache)))
            self.cache[key] = result
            return result

    def select(self,question,candidates,focus=None):
        request = {'question':question,'candidates':candidates}
        if focus:request['focused_operation']=focus
        result=self.request(request)
        ids=result.get('selected');allowed={c['id'] for c in candidates}
        if not isinstance(ids,list) or any(not isinstance(i,str) or i not in allowed for i in ids):
            raise RuntimeError('证据复核输出含不存在的条款，已停止作答')
        return list(dict.fromkeys(ids))
