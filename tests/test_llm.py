import unittest
from unittest.mock import Mock
from practice_autofill.models import Question, Option
from practice_autofill.config import LLMConfig
from practice_autofill.llm import Solver, Retryable, ModelFailure, FatalModelError
from practice_autofill.storage import Store

class ModelTests(unittest.TestCase):
    def setUp(self):
        self.q=Question("q","single","2+2", (Option("A","4"),),0)
        self.clock=0
        self.sleeps=[]
    def now(self):
        return self.clock
    def sleep(self,x):
        self.sleeps.append(x)
        self.clock+=x
    def solver(self,transport):
        return Solver(LLMConfig(),transport,sleep=self.sleep,clock=self.now)
    def test_rate_limit_timeout_then_success(self):
        transport=Mock(side_effect=[Retryable("rate_limited",7),Retryable("timeout"),
                                   '{"type":"single","answers":["A"]}'])
        self.assertEqual(self.solver(transport).solve(self.q).answers,("A",))
        self.assertEqual(transport.call_count,3)
        self.assertIn(7,self.sleeps)
    def test_bounded_invalid_and_fatal(self):
        transport=Mock(return_value="invalid")
        with self.assertRaises(ModelFailure):
            self.solver(transport).solve(self.q)
        self.assertEqual(transport.call_count,3)
        transport=Mock(side_effect=FatalModelError("authentication"))
        with self.assertRaises(FatalModelError):
            self.solver(transport).solve(self.q)
        self.assertEqual(transport.call_count,1)
    def test_unknown_and_interval(self):
        transport=Mock(return_value='{"type":"unknown","answers":[]}')
        solver=self.solver(transport)
        solver.solve(self.q); solver.solve(self.q)
        self.assertEqual(transport.call_count,2)
        self.assertEqual(self.sleeps,[2])
