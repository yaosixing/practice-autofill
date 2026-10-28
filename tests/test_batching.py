import json
import unittest
from practice_autofill.batching import BatchSolver, validate_batch
from practice_autofill.config import Config
from practice_autofill.llm import Retryable, FatalModelError
from practice_autofill.models import Question, Option
from practice_autofill.validation import AnswerError


def questions():
    return [Question(str(i),'single',str(i),(Option('A','one'),Option('B','two')),0) for i in range(3)]


def response(ids):
    return json.dumps({'answers':[{'id':i,'type':'single','answers':['B']} for i in ids]})

class BatchTests(unittest.TestCase):
    def setUp(self):
        self.config=Config(); self.config.llm.min_interval_seconds=.001
        self.config.llm.max_retries=1
        self.qs=questions()

    def solver(self,transport):
        return BatchSolver(self.config.llm,self.config.answering,transport,sleep=lambda _:None)

    def test_whole_page_one_request_and_out_of_order(self):
        calls=[]
        def transport(payload):
            calls.append(payload)
            return json.dumps({'answers':[{'id':q['id'],'type':'single',
                'answers':['B' if q['id']=='q0002' else 'A']} for q in reversed(payload['questions'])]})
        answers=self.solver(transport).solve(self.qs)
        self.assertEqual(len(calls),1)
        self.assertEqual([answers[q.fingerprint].answers for q in self.qs],[('A',),('B',),('A',)])
        self.assertNotIn('question_id',json.dumps(calls))

    def test_partial_retry_sends_only_missing(self):
        calls=[]
        def transport(payload):
            ids=[q['id'] for q in payload['questions']]; calls.append(ids)
            return response(ids[:1] if len(calls)==1 else ids)
        self.assertEqual(len(self.solver(transport).solve(self.qs)),3)
        self.assertEqual(calls,[['q0001','q0002','q0003'],['q0002','q0003']])

    def test_unknown_not_retried(self):
        calls=[]
        def transport(payload):
            calls.append(payload)
            return json.dumps({'answers':[{'id':q['id'],'type':'unknown','answers':[]} for q in payload['questions']]})
        self.assertTrue(all(a.type=='unknown' for a in self.solver(transport).solve(self.qs).values()))
        self.assertEqual(len(calls),1)

    def test_network_exhaustion_does_not_split(self):
        calls=[]
        def transport(payload):
            calls.append(payload); raise Retryable('timeout')
        self.assertEqual(self.solver(transport).solve(self.qs),{})
        self.assertEqual(len(calls),2)

    def test_fatal_stops_immediately(self):
        with self.assertRaises(FatalModelError):
            self.solver(lambda _: (_ for _ in ()).throw(FatalModelError('authentication_or_access'))).solve(self.qs)

    def test_bad_json_splits_then_single_fallback(self):
        calls=[]
        def transport(payload):
            calls.append(payload)
            return 'truncated' if 'questions' in payload else '{"type":"single","answers":["B"]}'
        self.assertEqual(len(self.solver(transport).solve(self.qs)),3)
        self.assertLess(len(calls),20)

    def test_batch_size_and_budget(self):
        self.config.answering.batch_size=2
        calls=[]
        def transport(payload):
            calls.append(payload); return response([q['id'] for q in payload['questions']])
        self.solver(transport).solve(self.qs)
        self.assertEqual([len(p['questions']) for p in calls],[2,1])

    def test_duplicate_id_invalidates_only_that_question(self):
        raw=response(['q0001','q0001','q0002'])
        valid,issues=validate_batch(raw,dict(zip(['q0001','q0002','q0003'],self.qs)))
        self.assertEqual(set(valid),{'q0002'})
        self.assertIn('duplicate_id',issues)
        self.assertIn('missing_answer',issues)

    def test_strict_outer_json(self):
        for raw in ['{"answers":[],"extra":1}','{"answers":[],"answers":[]}','{"answers":NaN}']:
            with self.subTest(raw=raw),self.assertRaises(AnswerError):
                validate_batch(raw,{'q0001':self.qs[0]})

if __name__=='__main__': unittest.main()
