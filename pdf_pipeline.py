"""PDF text/table extraction and local Apple Vision OCR with durable provenance."""
import base64
import copy
import hashlib
import importlib.util
import json
import platform
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

ROOT=Path(__file__).parent
UPLOADS=ROOT/'data/uploads'
BUILD_LOCK=threading.Lock()


def capabilities():
    return dict(text=bool(importlib.util.find_spec('pdfplumber')),tables=bool(importlib.util.find_spec('pdfplumber')),
                ocr=platform.system()=='Darwin' and bool(shutil.which('swiftc')),
                parser='pdfplumber',ocr_engine='Apple Vision · 本机离线',max_mb=20,max_pages=200,max_ocr_pages=30)


def ocr_binary():
    binary=ROOT/'.runtime/mac-ocr';source=ROOT/'mac_ocr.swift'
    with BUILD_LOCK:
        if binary.exists() and binary.stat().st_mtime>=source.stat().st_mtime:return binary
        if not capabilities()['ocr']:raise ValueError('本机没有 Apple Vision OCR；请先 OCR 后导入文本，或在 Mac 安装 Swift 工具链')
        binary.parent.mkdir(exist_ok=True)
        result=subprocess.run(['swiftc',str(source),'-o',str(binary)],capture_output=True,text=True,timeout=120)
        if result.returncode:raise RuntimeError('OCR 编译失败：'+result.stderr[-500:])
    return binary


def parse_pdf(raw, mode='auto', progress=None):
    emit=progress or (lambda *args:None)
    if mode not in ('auto','text','ocr'):raise ValueError('PDF 解析模式无效')
    if not raw.startswith(b'%PDF-') or len(raw)>20_000_000:raise ValueError('需要 20 MB 以内的有效 PDF')
    try:import pdfplumber
    except ImportError:raise ValueError('缺少 pdfplumber，请运行 setup-semantic.sh')
    digest=hashlib.sha256(raw).hexdigest();UPLOADS.mkdir(exist_ok=True)
    path=UPLOADS/(digest+'.pdf');path.write_bytes(raw)
    pages=[];ocr_indices=[];warnings=[]
    with pdfplumber.open(path) as pdf:
        if not 1<=len(pdf.pages)<=200:raise ValueError('PDF 须为 1–200 页')
        for i,page in enumerate(pdf.pages):
            emit(i,len(pdf.pages),'读取文字与表格，第 '+str(i+1)+' 页')
            text=page.extract_text(x_tolerance=2,y_tolerance=3) or ''
            large_image=any((im['x1']-im['x0'])*(im['bottom']-im['top'])>page.width*page.height*.4 for im in page.images)
            needs_ocr=mode=='ocr' or (mode=='auto' and (len(re.sub(r'\s','',text))<20 or (large_image and len(text)<80)))
            tables=[]
            if needs_ocr:
                ocr_indices.append(i);text=''
            else:
                found=page.find_tables()
                for table in found:
                    rows=[[str(c or '').replace('\n',' ').strip() for c in row] for row in table.extract()]
                    if len(rows)>=2 and max(map(len,rows),default=0)>=2:tables.append(dict(bbox=list(table.bbox),rows=rows))
                if tables:
                    def outside(obj):
                        x=(obj.get('x0',0)+obj.get('x1',0))/2;y=(obj.get('top',0)+obj.get('bottom',0))/2
                        return not any(t['bbox'][0]<=x<=t['bbox'][2] and t['bbox'][1]<=y<=t['bbox'][3] for t in tables)
                    text=page.filter(outside).extract_text(x_tolerance=2,y_tolerance=3) or ''
            pages.append(dict(page=i+1,method='vision_ocr' if needs_ocr else 'pdf_text',text=text,tables=tables,width=page.width,height=page.height))
            page.close()
    if len(ocr_indices)>30:raise ValueError('本地 OCR 每次最多 30 页，请拆分扫描 PDF')
    if ocr_indices:
        emit(0,len(ocr_indices),'本机 OCR 识别中')
        run=subprocess.run([str(ocr_binary()),str(path),','.join(map(str,ocr_indices))],capture_output=True,text=True,timeout=max(120,len(ocr_indices)*30))
        if run.returncode:raise ValueError(run.stderr[-500:] or 'OCR 失败')
        results=[json.loads(line) for line in run.stdout.splitlines() if line.strip()]
        if len(results)!=len(ocr_indices):raise ValueError('OCR 未返回全部页面，未导入不完整结果')
        for result in results:
            p=pages[result['page']-1];p['lines']=result['lines'];p['text']='\n'.join(line['text'] for line in result['lines'])
            if not p['text'].strip():warnings.append('第 '+str(p['page'])+' 页没有识别到文字，请检查是否为空白页或图像')
        warnings.append('OCR 文字和扫描表格的阅读顺序须人工核对；本版不自动重建扫描表格结构。')
    text=''
    for p in pages:
        p['start']=len(text);text+='[PDF 第 '+str(p['page'])+' 页]\n'+p['text'].strip()+'\n'
        for t_index,t in enumerate(p['tables']):
            # Repeat headers in every row so a retrieval chunk never loses column semantics.
            header=t['rows'][0]
            text+='[表 '+str(t_index+1)+' 列：'+' | '.join(header)+']\n'
            for n,row in enumerate(t['rows'][1:],2):
                fields=[(header[k] or '第'+str(k+1)+'列')+'='+v for k,v in enumerate(row) if v and k<len(header)]
                text+='表 '+str(t_index+1)+' 第 '+str(n)+' 行：'+'；'.join(fields)+'。\n'
        p['end']=len(text);text+='\n'
    if len(re.sub(r'\[PDF.*?\]|\s','',text))<10:raise ValueError('未提取到足够正文，请检查 PDF 或使用 OCR 模式')
    if len(text)>200000:raise ValueError('解析后超过 20 万字符，请拆分 PDF')
    emit(len(pages),len(pages),'解析完成，请核对正文与表格')
    metadata=dict(method='pdfplumber + Apple Vision' if ocr_indices else 'pdfplumber',file_id=digest,pages=pages,warnings=warnings,text_hash=hashlib.sha256(text.encode()).hexdigest())
    return dict(text=text,metadata=metadata)


class PDFJobs:
    def __init__(self):self.jobs={};self.lock=threading.Lock()
    def get(self,jid):
        with self.lock:
            if jid not in self.jobs:raise ValueError('PDF 任务不存在或已过期')
            return copy.deepcopy(self.jobs[jid])
    def start(self,encoded,mode='auto'):
        try:raw=base64.b64decode(encoded,validate=True)
        except Exception:raise ValueError('PDF 编码无效')
        if len(raw)>20_000_000:raise ValueError('PDF 超过 20 MB')
        with self.lock:
            if any(j['state']=='running' for j in self.jobs.values()):raise ValueError('已有 PDF 正在解析')
            jid='P'+uuid.uuid4().hex[:16];self.jobs[jid]=dict(id=jid,state='running',completed=0,total=0,message='准备解析')
            while len(self.jobs)>8:del self.jobs[next(iter(self.jobs))]
        def progress(completed,total,message):
            with self.lock:self.jobs[jid].update(completed=completed,total=total,message=message)
        def run():
            try:
                result=parse_pdf(raw,mode,progress)
                with self.lock:self.jobs[jid].update(state='complete',result=result)
            except Exception as ex:
                with self.lock:self.jobs[jid].update(state='failed',error=str(ex))
        threading.Thread(target=run,daemon=True).start()
        return self.get(jid)
