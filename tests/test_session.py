import io
import logging
from support import artifact_dir
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from practice_autofill.cli import Session,terminal_text
from practice_autofill.models import Question,Option,Target
from practice_autofill.config import Config
from practice_autofill.storage import Store,cache_key
from practice_autofill.validation import validate_answer
from practice_autofill.logging_utils import LOGGER,event

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.q=Question("id","single","Test", (Option("A","yes"),),0)
        self.target=Target(self.q,None,0,(),"id","",None)
        self.test_dir=artifact_dir()
        self.store=Store(self.test_dir/"db")
        self.config=Config()
    def tearDown(self):
        self.store.__exit__()
    def test_declining_external_data_makes_no_request(self):
        self.config.run.confirm_external_data=True
        factory=Mock()
        session=Session(self.config,self.store,ask=lambda _:"no",say=lambda _:None,transport_factory=factory)
        self.assertEqual(session.process(self.target),"declined")
        self.assertEqual(session.process(self.target),"declined")
        factory.assert_not_called()
    def test_declining_fill_makes_no_write_and_cache_avoids_request(self):
        answer=validate_answer('{"type":"single","answers":["A"]}',self.q)
        key=cache_key(self.q,self.config.llm.base_url,self.config.llm.model)
        self.store.put(key,self.q,answer)
        factory=Mock()
        session=Session(self.config,self.store,ask=lambda _:"n",say=lambda _:None,transport_factory=factory)
        with patch("practice_autofill.cli.fill_answer") as fill:
            self.assertEqual(session.process(self.target),"skipped")
            fill.assert_not_called()
        factory.assert_not_called()
    def test_resume_rechecks_page_instead_of_trusting_progress(self):
        answer=validate_answer('{"type":"single","answers":["A"]}',self.q)
        self.store.put(cache_key(self.q,self.config.llm.base_url,self.config.llm.model),self.q,answer)
        self.store.progress(self.q,"verified")
        session=Session(self.config,self.store,ask=lambda _:"y",say=lambda _:None)
        with patch("practice_autofill.cli.read_matches",side_effect=[False,True]) as read, \
             patch("practice_autofill.cli.fill_answer") as fill:
            self.assertEqual(session.process(self.target),"verified")
            self.assertEqual(read.call_count,2)
            fill.assert_called_once()
    def test_logs_reject_secret_and_url(self):
        stream=io.StringIO()
        handler=logging.StreamHandler(stream)
        LOGGER.addHandler(handler);LOGGER.setLevel(logging.INFO)
        try:
            event("test",self.q.fingerprint,"https://host/path?token=secret_key")
            self.assertNotIn("secret_key",stream.getvalue())
            self.assertNotIn("https",stream.getvalue())
            self.assertIn("redacted",stream.getvalue())
        finally:
            LOGGER.removeHandler(handler)
    def test_terminal_control_sanitization(self):
        self.assertNotIn("\x1b",terminal_text("stem \x1b[31m"))

    def test_default_external_permission_needs_no_prompt(self):
        self.assertFalse(self.config.run.confirm_external_data)
        self.config.run.auto_fill = True
        self.config.run.clear_before_fill = False
        ask = Mock(side_effect=AssertionError("unexpected_confirmation"))
        transport = Mock(return_value='{"type":"single","answers":["A"]}')
        messages = []
        session = Session(self.config, self.store, ask=ask, say=messages.append,
                          transport_factory=lambda: transport)
        with patch("practice_autofill.cli.read_matches", return_value=True), \
             patch("practice_autofill.cli.fill_answer") as fill:
            self.assertEqual(session.process(self.target), "verified")
            self.assertEqual(session.process(self.target), "verified")
        ask.assert_not_called()
        transport.assert_called_once()
        fill.assert_not_called()
        self.assertTrue(any("不发送账号" in line for line in messages))

    def test_default_clears_even_when_answer_already_matches(self):
        self.assertTrue(self.config.run.clear_before_fill)
        self.config.run.auto_fill=True
        answer=validate_answer('{"type":"single","answers":["A"]}',self.q)
        self.store.put(cache_key(self.q,self.config.llm.base_url,self.config.llm.model),self.q,answer)
        session=Session(self.config,self.store,ask=Mock(side_effect=AssertionError("unexpected_prompt")),say=lambda _:None)
        with patch("practice_autofill.cli.read_matches",return_value=True), \
             patch("practice_autofill.cli.fill_answer") as fill:
            self.assertEqual(session.process(self.target),"verified")
        fill.assert_called_once_with(self.target,answer,clear_first=True)
    def test_fast_declined_preparation_makes_no_request(self):
        self.config.run.confirm_external_data=True
        factory=Mock()
        session=Session(self.config,self.store,ask=lambda _:"no",say=lambda _:None,transport_factory=factory)
        self.assertFalse(session.prepare([self.target]))
        factory.assert_not_called()

    def test_prepared_failure_does_not_make_hidden_single_request(self):
        self.config.llm.max_retries=0
        self.config.answering.fallback_to_single=False
        transport=Mock(return_value='{"answers":[]}')
        session=Session(self.config,self.store,say=lambda _:None,transport_factory=lambda:transport)
        self.assertTrue(session.prepare([self.target]))
        self.assertEqual(session.process(self.target),"manual")
        self.assertEqual(transport.call_count,1)
    def test_fast_reuses_legacy_single_cache_without_transport(self):
        answer=validate_answer('{"type":"single","answers":["A"]}',self.q)
        self.store.put(cache_key(self.q,self.config.llm.base_url,self.config.llm.model),self.q,answer)
        factory=Mock()
        session=Session(self.config,self.store,say=lambda _:None,transport_factory=factory)
        self.assertTrue(session.prepare([self.target]))
        self.assertEqual(session.prepared[self.q.fingerprint],answer)
        factory.assert_not_called()

    def test_single_mode_preparation_leaves_original_protocol(self):
        self.config.answering.mode='single'
        factory=Mock()
        session=Session(self.config,self.store,say=lambda _:None,transport_factory=factory)
        self.assertTrue(session.prepare([self.target]))
        self.assertIsNone(session.prepared)
        factory.assert_not_called()
    def test_question_pacing_occurs_only_after_confirmed_answers(self):
        self.config.pacing.mode='custom'
        self.target.frame=Mock()
        answer=validate_answer('{"type":"single","answers":["A"]}',self.q)
        self.store.put(cache_key(self.q,self.config.llm.base_url,self.config.llm.model),self.q,answer)
        session=Session(self.config,self.store,ask=lambda _:'n',say=lambda _:None)
        self.assertEqual(session.process(self.target),'skipped')
        self.target.frame.page.wait_for_timeout.assert_not_called()
        self.config.run.auto_fill=True
        with patch('practice_autofill.cli.read_matches',return_value=True),patch('practice_autofill.cli.fill_answer'):
            self.assertEqual(session.process(self.target),'verified')
            self.target.frame.page.wait_for_timeout.assert_not_called()
            self.assertEqual(session.process(self.target),'verified')
        self.target.frame.page.wait_for_timeout.assert_called_once_with(500)

    def test_fast_clear_avoids_redundant_session_reads(self):
        self.config.pacing.mode='fast'
        self.config.run.auto_fill=True
        answer=validate_answer('{"type":"single","answers":["A"]}',self.q)
        self.store.put(cache_key(self.q,self.config.llm.base_url,self.config.llm.model),self.q,answer)
        session=Session(self.config,self.store,say=lambda _:None)
        with patch('practice_autofill.cli.read_matches') as read,patch('practice_autofill.cli.fill_answer') as fill:
            self.assertEqual(session.process(self.target),'verified')
        read.assert_not_called()
        fill.assert_called_once_with(self.target,answer,clear_first=True)

    def test_fast_preserves_fill_verification_failure(self):
        from practice_autofill.filling import FillError
        self.config.pacing.mode='fast'
        self.config.run.auto_fill=True
        answer=validate_answer('{"type":"single","answers":["A"]}',self.q)
        self.store.put(cache_key(self.q,self.config.llm.base_url,self.config.llm.model),self.q,answer)
        session=Session(self.config,self.store,say=lambda _:None)
        with patch('practice_autofill.cli.fill_answer',side_effect=FillError('readback_mismatch')):
            self.assertEqual(session.process(self.target),'failed')
