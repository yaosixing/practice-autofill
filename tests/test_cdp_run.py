import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from practice_autofill.config import Config
from practice_autofill.cdp_run import process_page, run_cdp


class CDPRunTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.page = SimpleNamespace(url="https://mooc1.chaoxing.com/mooc-ans/mooc2/work/dowork")
        self.session = Mock()
        self.session.config = self.config
        self.session.ask.return_value = ""
        self.session.process.return_value = "verified"
        self.frame = object()
        self.targets = [SimpleNamespace(frame=self.frame, identity=str(i),
            question=SimpleNamespace(fingerprint=str(i))) for i in range(3)]

    def test_processes_each_unique_target_once(self):
        with patch("practice_autofill.cdp_run.scan", return_value=(self.targets + self.targets[:1], [])):
            report = process_page(self.page, self.config, None, self.session)
        self.assertEqual(self.session.process.call_count, 3)
        self.assertEqual(report, {"verified": 3})

    def test_stop_reasons_do_not_process_later_questions(self):
        for result in ("quit", "declined", "failed"):
            with self.subTest(result=result):
                self.session.process.reset_mock()
                self.session.process.side_effect = ["verified", result, "verified"]
                with patch("practice_autofill.cdp_run.scan", return_value=(self.targets, [])):
                    report = process_page(self.page, self.config, None, self.session)
                self.assertEqual(self.session.process.call_count, 2)
                self.assertEqual(report[result], 1)

    def test_manual_and_skipped_continue(self):
        self.session.process.side_effect = ["manual", "skipped", "verified"]
        with patch("practice_autofill.cdp_run.scan", return_value=(self.targets, [])):
            report = process_page(self.page, self.config, None, self.session)
        self.assertEqual(report, {"manual": 1, "skipped": 1, "verified": 1})

    def test_rescan_uses_fresh_targets_and_does_not_trust_progress(self):
        self.session.ask.side_effect = ["r", ""]
        with patch("practice_autofill.cdp_run.scan", side_effect=[(self.targets[:1], []), (self.targets[1:], [])]) as scan:
            report = process_page(self.page, self.config, None, self.session)
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(report, {"verified": 3})
        self.assertIs(self.session.process.call_args_list[1].args[0], self.targets[1])

    def test_navigation_outside_work_page_stops_before_scan(self):
        self.page.url = "https://example.com/"
        with patch("practice_autofill.cdp_run.scan") as scan:
            with self.assertRaises(ValueError):
                process_page(self.page, self.config, None, self.session)
        scan.assert_not_called()
        self.session.process.assert_not_called()

    def test_navigation_between_questions_stops(self):
        def change_page(target):
            self.page.url = "https://example.com/"
            return "verified"
        self.session.process.side_effect = change_page
        with patch("practice_autofill.cdp_run.scan", return_value=(self.targets, [])):
            with self.assertRaises(ValueError):
                process_page(self.page, self.config, None, self.session)
        self.assertEqual(self.session.process.call_count, 1)

    def test_auto_fill_processes_all_targets(self):
        self.config.run.auto_fill = True
        with patch("practice_autofill.cdp_run.scan", return_value=(self.targets, [])):
            report = process_page(self.page, self.config, None, self.session)
        self.assertEqual(report, {"verified": 3})
        self.assertEqual(self.session.process.call_count, 3)

    def test_attached_browser_never_closed(self):
        browser = Mock()
        browser.contexts = [SimpleNamespace(pages=[self.page])]
        with patch("practice_autofill.trial.sync_playwright") as pw, \
             patch("practice_autofill.cdp_run.process_page", return_value={"verified": 1}):
            pw.return_value.__enter__.return_value.chromium.connect_over_cdp.return_value = browser
            self.assertEqual(run_cdp(self.config, None, self.session), {"verified": 1})
        browser.close.assert_not_called()
