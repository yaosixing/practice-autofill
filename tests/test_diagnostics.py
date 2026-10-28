import io
import json
import logging
import unittest
from practice_autofill.logging_utils import LOGGER, event
from practice_autofill.config import Config
from practice_autofill.batching import BatchSolver
from practice_autofill.llm import ResponseError
from practice_autofill.models import Question, Option


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.buffer = io.StringIO()
        self.handler = logging.StreamHandler(self.buffer)
        LOGGER.addHandler(self.handler)
        LOGGER.setLevel(logging.INFO)

    def tearDown(self):
        LOGGER.removeHandler(self.handler)

    def test_metadata_is_allowlisted_and_timestamped(self):
        event('model_response', request_id='a' * 32, finish_reason='length',
              completion_tokens=2048, reasoning_tokens=1800, elapsed_ms=123,
              api_key='DO_NOT_LOG', content='SECRET_CONTENT', url='https://x/?secret=1')
        value = json.loads(self.buffer.getvalue())
        self.assertIn('time', value)
        self.assertIn('run_id', value)
        self.assertEqual(value['completion_tokens'], 2048)
        self.assertEqual(value['finish_reason'], 'length')
        self.assertNotIn('DO_NOT_LOG', self.buffer.getvalue())
        self.assertNotIn('SECRET_CONTENT', self.buffer.getvalue())
        self.assertNotIn('secret=1', self.buffer.getvalue())

    def test_split_reason_fallback_attempt_and_summary_are_visible(self):
        config = Config()
        config.llm.max_retries = 0
        config.llm.min_interval_seconds = .001
        messages = []
        questions = [Question(str(i), 'single', str(i), (Option('A', 'yes'),), 0) for i in range(2)]
        def transport(payload):
            if 'questions' in payload:
                raise ResponseError('incomplete_response', {'finish_reason':'length',
                    'completion_tokens':2048, 'max_tokens':2048})
            return '{"type":"single","answers":["A"]}'
        result = BatchSolver(config.llm, config.answering, transport,
                             sleep=lambda _:None, say=messages.append).solve(questions)
        text = '\n'.join(messages)
        self.assertEqual(len(result), 2)
        self.assertIn('finish_reason=length', text)
        self.assertIn('2048/2048', text)
        self.assertIn('单题请求', text)
        self.assertIn('模型请求 5 次', text)
        self.assertIn('b0001_l', text)
        self.assertIn('未取得答案 0', text)

    def test_unexpected_reason_is_not_misreported_as_token_limit(self):
        from practice_autofill.llm import diagnostic_text
        text = diagnostic_text('incomplete_response', {'finish_reason':'content_filter'})
        self.assertIn('content_filter', text)
        self.assertNotIn('达到输出上限', text)
        self.assertNotIn('SECRET', diagnostic_text('SECRET https://x?token=1', {'finish_reason':'SECRET'}))
