import unittest
from engine import Engine
class QuerySafety(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.e=Engine(':memory:',semantic=True)
  if not cls.e.evidence_guard:raise unittest.SkipTest('需要启用本地证据复核模型')
 @classmethod
 def tearDownClass(cls):
  if cls.e.evidence_guard:cls.e.evidence_guard.close()
  cls.e.db.close()
 def test_specific_record_requires_verified_source(self):
  question='某工厂在2026年11月9日的色差记录是多少？'
  self.assertEqual(self.e.query(question)['status'],'abstained')
  doc=self.e.ingest('质量记录，非真实企业事实','某工厂在2026年11月9日的色差记录为0.8。')
  self.assertEqual(self.e.query(question)['status'],'abstained')
  self.e.review_document(doc['id'],True)
  result=self.e.query(question)
  self.assertTrue(result['evidence'])
  self.e.review_document(doc['id'],False)
  self.assertEqual(self.e.query(question)['status'],'abstained')
  self.e.delete_document(doc['id'])
 def test_verifier_failure_does_not_fall_back_to_unchecked_answer(self):
  class FailedGuard:
   def select(self,*args):raise TimeoutError('intentional test timeout')
  guard=self.e.evidence_guard
  try:
   self.e.evidence_guard=FailedGuard(); result=self.e.query('REACH 是否适用于服装产品？')
   self.assertEqual(result['status'],'abstained'); self.assertEqual(result['evidence'],[]); self.assertIn('error',result['verification'])
  finally:self.e.evidence_guard=guard
if __name__=='__main__':unittest.main()
