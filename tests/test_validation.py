import json
import unittest
from practice_autofill.models import Question, Option
from practice_autofill.validation import validate_answer, AnswerError

class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.q = Question("q", "single", "2+2?", (Option("A", "3"), Option("B", "4")), 0)

    def test_valid_and_unknown(self):
        self.assertEqual(validate_answer('{"type":"single","answers":["B"]}', self.q).answers, ("B",))
        self.assertEqual(validate_answer('{"type":"unknown","answers":[]}', self.q).type, "unknown")

    def test_strict_rejections(self):
        bad = [
            "", chr(96)*3+"json\n{}\n"+chr(96)*3, '{"type":"single","answers":["B"]',
            '{"type":"single","answers":["B"],"extra":1}',
            '{"type":"single","type":"single","answers":["B"]}',
            '{"type":"single","answers":["C"]}', '{"type":"single","answers":[]}',
            '{"type":"single","answers":["B","B"]}', '{"type":"multiple","answers":["B"]}',
            '{"type":"unknown","answers":["A"]}', '{"type":"single","answers":[1]}',
            '{"type":"single","answers":"B"}', '[]',
        ]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(AnswerError):
                validate_answer(raw, self.q)

    def test_blanks_and_multiple(self):
        blank = Question("b", "blank", "two", (), 2)
        self.assertEqual(validate_answer('{"type":"blank","answers":["one","two"]}', blank).answers, ("one","two"))
        for answers in [["one"], ["one",""], ["one", 2]]:
            with self.assertRaises(AnswerError):
                validate_answer(json.dumps({"type":"blank","answers":answers}), blank)
        multi = Question("m", "multiple", "choose", self.q.options, 0)
        self.assertEqual(validate_answer('{"type":"multiple","answers":["B","A"]}', multi).answers, ("B","A"))

    def test_request_has_no_browser_metadata(self):
        self.assertEqual(set(self.q.payload()), {"type","stem","options","blank_count"})
        self.assertNotIn("question_id", json.dumps(self.q.payload()))
