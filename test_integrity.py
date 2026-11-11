import unittest,json,hashlib
from engine import Engine,ROOT
class EvidenceIntegrity(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.engine=Engine(':memory:',semantic=False)
 def test_apparel_corpus_loaded(self):
  self.assertEqual(len(self.engine.docs),6)
  self.assertEqual(sum(c['doc_id']=='A6' for c in self.engine.chunks.values()),10)
  self.assertGreaterEqual(len(self.engine.edges),40)
 def test_source_hashes(self):
  for d in self.engine.docs.values():self.assertEqual(d['sha256'],hashlib.sha256(d['text'].encode()).hexdigest())
 def test_exact_entity_offsets(self):
  for item in self.engine.extractions():self.assertEqual(item['entity'],self.engine.docs[item['doc_id']]['text'][item['start']:item['end']])
 def test_edge_provenance(self):
  for edge in self.engine.edges:self.assertIn(edge['chunk_id'],self.engine.chunks)
 def test_semantic_artifacts_present(self):
  for model in json.loads((ROOT/'data/model-manifest.json').read_text()):
   for name in model['files']:self.assertTrue((ROOT/model['path']/name).is_file(),name)
 def test_short_import_lines_are_not_discarded(self):
  from engine import split_doc
  text='企业资料\n面料批次 F-1。\n需要复核。'
  chunks=split_doc(dict(id='Ushort',text=text))
  self.assertEqual([c['text'] for c in chunks],text.splitlines())
  for c in chunks:self.assertEqual(text[c['start']:c['end']],c['text'])
 def test_untrusted_documents_do_not_affect_lexical_statistics(self):
  e=Engine(':memory:',semantic=False); before=(e.n,dict(e.df))
  doc=e.ingest('待核验资料','XJ-UNTRUSTED 面料批次 色差 检测报告。')
  self.assertEqual(before,(e.n,dict(e.df)))
  e.review_document(doc['id'],True); self.assertEqual(e.n,before[0]+1)
  e.review_document(doc['id'],False); self.assertEqual(before,(e.n,dict(e.df)))
if __name__=='__main__':unittest.main()
