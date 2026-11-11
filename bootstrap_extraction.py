"""Install the pinned local Qwen revision, checksum all files, smoke-test MLX."""
import hashlib, json
from pathlib import Path
from huggingface_hub import snapshot_download

ROOT=Path(__file__).parent
spec=json.loads((ROOT/'data/extraction-model-manifest.json').read_text())
path=Path(snapshot_download(repo_id=spec['model'],revision=spec['revision'],
    allow_patterns=list(spec['files']),cache_dir=str(ROOT/'data/models')))
for name,expected in spec['files'].items():
    h=hashlib.sha256()
    with (path/name).open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    if h.hexdigest()!=expected:raise RuntimeError('模型校验失败：'+name)
print('Verified',spec['model'],spec['revision'])
from evidence_guard import EvidenceGuard
guard=EvidenceGuard()
result=guard.select('此处冷藏温度是多少？',[
    {'id':'smoke:1','text':'冷藏环境温度的范围应在0℃～8℃。','context':[]},
    {'id':'smoke:2','text':'食品留样不少于125g。','context':[]}])
guard.close()
if result!=['smoke:1']:raise RuntimeError('本地模型冒烟测试未通过：'+str(result))
runtime=json.loads((ROOT/'data/runtime.json').read_text())
runtime['evidence_guard']=True
(ROOT/'data/runtime.json').write_text(json.dumps(runtime))
print('本地关系抽取及证据复核模型就绪。')
