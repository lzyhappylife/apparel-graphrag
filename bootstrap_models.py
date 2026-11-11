"""Download exact audited model revisions and verify artifacts before loading."""
import hashlib,json
from pathlib import Path
from huggingface_hub import snapshot_download
ROOT=Path(__file__).parent
for spec in json.loads((ROOT/'data/model-manifest.json').read_text()):
 path=Path(snapshot_download(repo_id=spec['repository'],revision=spec['revision'],allow_patterns=list(spec['files']),cache_dir=str(ROOT/'data/models')))
 for name,expected in spec['files'].items():
  actual=hashlib.sha256((path/name).read_bytes()).hexdigest()
  if actual!=expected:raise RuntimeError(f'Model checksum mismatch: {name}')
 print('Verified',spec['model'],spec['revision'])
from semantic import SemanticIndex
import sqlite3
s=SemanticIndex(sqlite3.connect(':memory:'))
print('Embedding dimension:',len(next(s.embed.query_embed('食品安全'))))
print('Reranker smoke test:',list(s.reranker.rerank('冷藏温度是多少',['冷藏环境温度为0℃～8℃。'])))
(ROOT/'data/runtime.json').write_text(json.dumps({'semantic':True}))
