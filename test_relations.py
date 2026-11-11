import unittest,json,copy
from engine import Engine
from relations import validate_candidate,review,candidates,RelationJobs
class RelationsTest(unittest.TestCase):
 def setUp(self):
  self.e=Engine(':memory:',semantic=False)
  self.d=self.e.ingest('测试制度','蓝禾门店的冷链管理员应当每天检查冷藏柜温度并填写温度记录。\n蓝禾门店的冷链管理员不得使用破损的温度计。')
  self.c=next(c for c in self.e.chunks.values() if c['doc_id']==self.d['id'])
  self.row=dict(source='蓝禾门店的冷链管理员',relation='要求',target='每天检查冷藏柜温度并填写温度记录',quote=self.c['text'])
 def insert(self):
  c=validate_candidate(self.row,self.c,self.d)
  self.e.db.execute('INSERT INTO relation_candidates VALUES (?,?,?)',(c['id'],c['doc_id'],json.dumps(c,ensure_ascii=False)));self.e.db.commit();return c
 def test_reject_hallucinated_quote(self):
  self.row['quote']='蓝禾门店的冷链管理员每天填写记录。'
  with self.assertRaises(ValueError):validate_candidate(self.row,self.c,self.d)
 def test_reject_nonverbatim_endpoint(self):
  self.row['target']='每天校验冷藏柜温度'
  with self.assertRaises(ValueError):validate_candidate(self.row,self.c,self.d)
 def test_approval_needs_document_trust(self):
  c=self.insert()
  with self.assertRaises(ValueError):review(self.e,c['id'],'accepted',self.d['sha256'])
 def test_review_graph_lifecycle(self):
  c=self.insert();self.e.rebuild();self.assertNotIn(c['id'],[x.get('id') for x in self.e.graph()['edges']])
  self.e.review_document(self.d['id'],True);review(self.e,c['id'],'accepted',self.d['sha256'])
  self.assertIn(c['id'],[x.get('id') for x in self.e.graph()['edges']])
  self.assertEqual(self.d['text'][c['start']:c['end']],c['quote'])
  self.e.review_document(self.d['id'],False);self.assertNotIn(c['id'],[x.get('id') for x in self.e.graph()['edges']])
  self.e.review_document(self.d['id'],True);review(self.e,c['id'],'pending',self.d['sha256']);self.assertNotIn(c['id'],[x.get('id') for x in self.e.graph()['edges']])
  self.assertEqual(self.e.db.execute('SELECT count(*) FROM review_audit').fetchone()[0],2)
 def test_stale_hash_rejected(self):
  c=self.insert();self.e.review_document(self.d['id'],True)
  with self.assertRaises(ValueError):review(self.e,c['id'],'accepted','bad-hash')
 def test_delete_cascades(self):
  self.insert();self.e.delete_document(self.d['id']);self.assertEqual(candidates(self.e),[])
 def test_strict_boolean(self):
  with self.assertRaises(ValueError):self.e.review_document(self.d['id'],'false')
 def test_restart_marks_interrupted(self):
  j=dict(id='Jtest',doc_id=self.d['id'],state='running')
  self.e.db.execute('INSERT INTO extraction_jobs VALUES (?,?,?)',(j['id'],j['doc_id'],json.dumps(j)));self.e.db.commit()
  jobs=RelationJobs(self.e);self.assertEqual(jobs.list()[0]['state'],'interrupted')
if __name__=='__main__':unittest.main()
