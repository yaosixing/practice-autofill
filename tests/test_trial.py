import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from practice_autofill.config import Config
from practice_autofill.inspection import validate_cdp_endpoint
from practice_autofill.trial import trial_page


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.session = Mock()
        self.session.config = self.config
        self.session.process.return_value = "verified"
        self.targets = [SimpleNamespace(question=SimpleNamespace(stem="题目", options=[])) for _ in range(3)]

    def test_only_requested_question_processed(self):
        with patch("practice_autofill.trial.scan", return_value=(self.targets, [])):
            self.assertEqual(trial_page(None, self.config, None, self.session, 2), "verified")
        self.session.process.assert_called_once_with(self.targets[1])

    def test_decline_does_not_continue(self):
        self.session.process.return_value = "declined"
        with patch("practice_autofill.trial.scan", return_value=(self.targets, [])):
            self.assertEqual(trial_page(None, self.config, None, self.session), "declined")
        self.session.process.assert_called_once_with(self.targets[0])

    def test_auto_fill_rejected_before_scan(self):
        self.config.run.auto_fill = True
        with patch("practice_autofill.trial.scan") as scan:
            with self.assertRaises(ValueError):
                trial_page(None, self.config, None, self.session)
        scan.assert_not_called()
        self.session.process.assert_not_called()

    def test_invalid_index_never_processes(self):
        with patch("practice_autofill.trial.scan", return_value=(self.targets, [])):
            for index in (0, -1, 4, True):
                with self.assertRaises(ValueError):
                    trial_page(None, self.config, None, self.session, index)
        self.session.process.assert_not_called()

    def test_local_endpoint_only(self):
        validate_cdp_endpoint("http://127.0.0.1:9527")
        for value in ("http://example.com:9527", "http://localhost:9527/?token=x", "http://user:pass@localhost:9527"):
            with self.assertRaises(ValueError):
                validate_cdp_endpoint(value)
