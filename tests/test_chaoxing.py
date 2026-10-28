import functools
import http.server
import os
import threading
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from practice_autofill.adapters.chaoxing import ChaoxingAdapter
from practice_autofill.adapters.base import AdapterNotConfigured
from practice_autofill.browser import runtime_environment
from practice_autofill.filling import fill_answer, read_matches, FillError
from practice_autofill.validation import validate_answer

ROOT=Path(__file__).resolve().parent.parent

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass

class ChaoxingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime_environment(ROOT/"runtime")
        cls.server=http.server.ThreadingHTTPServer(("127.0.0.1",0),
            functools.partial(QuietHandler,directory=str(ROOT/"fixtures")))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.pw=sync_playwright().start()
        options={"headless":True}
        if os.environ.get("PRACTICE_BROWSER_EXECUTABLE"):
            options["executable_path"]=os.environ["PRACTICE_BROWSER_EXECUTABLE"]
        cls.browser=cls.pw.chromium.launch(**options)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.page=self.browser.new_page()
        self.page.goto(f"http://127.0.0.1:{self.server.server_port}/chaoxing_work.html")
        self.adapter=ChaoxingAdapter(ROOT/"selectors/chaoxing.work.toml")

    def tearDown(self):
        self.assertEqual(self.page.evaluate("window.submitCount"),0)
        self.page.close()

    def targets(self):
        targets,skips=self.adapter.extract(self.page.main_frame)
        return {t.question.question_id:t for t in targets},skips

    def answer(self,target,answers):
        import json
        return validate_answer(json.dumps({"type":target.question.type,"answers":answers}),target.question)

    def test_extract_real_letters_text_and_skip_blanks(self):
        targets,skips=self.targets()
        self.assertEqual(set(targets),{"fixture-single","fixture-multiple"})
        self.assertIn("unsupported_question_type",{s.code for s in skips})
        q=targets["fixture-single"].question
        self.assertEqual(q.type,"single")
        self.assertEqual([(o.id,o.text) for o in q.options],[("A","3"),("B","4"),("C","5")])
        self.assertEqual(targets["fixture-multiple"].question.type,"multiple")
        self.assertNotIn("fixture-single",str(q.payload()))
        self.assertEqual(self.page.evaluate("window.clickCount"),0)

    def test_single_multi_and_repeated_fill(self):
        targets,_=self.targets()
        single=targets["fixture-single"];multi=targets["fixture-multiple"]
        a=self.answer(single,["B"]);b=self.answer(multi,["A","B"])
        self.assertFalse(read_matches(single,a))
        self.assertFalse(read_matches(multi,b))
        fill_answer(single,a);fill_answer(multi,b)
        self.assertTrue(read_matches(single,a))
        self.assertTrue(read_matches(multi,b))
        count=self.page.evaluate("window.clickCount")
        fill_answer(single,a);fill_answer(multi,b)
        self.assertEqual(self.page.evaluate("window.clickCount"),count)
        self.assertFalse(self.page.locator("#fixture-multiple .num_option_dx").nth(2).evaluate(
            "(e)=>e.classList.contains('check_answer_dx')"))

    def test_option_ids_survive_reorder_but_old_target_stops(self):
        targets,_=self.targets()
        target=targets["fixture-single"]
        self.page.locator("#fixture-single .stem_answer").evaluate("(e)=>e.prepend(e.lastElementChild)")
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["B"]))
        new,_=self.targets()
        self.assertEqual([o.id for o in new["fixture-single"].question.options],["C","A","B"])
        self.assertEqual(self.page.evaluate("window.clickCount"),0)

    def test_duplicate_and_changed_questions_stop(self):
        targets,_=self.targets()
        target=targets["fixture-single"]
        self.page.locator("#fixture-single").evaluate("(e)=>e.after(e.cloneNode(true))")
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["B"]))
        self.assertEqual(self.page.evaluate("window.clickCount"),0)

    def test_unreliable_option_mapping_is_skipped(self):
        self.page.locator("#fixture-single .num_option").nth(1).evaluate("(e)=>e.textContent='A'")
        targets,skips=self.targets()
        self.assertNotIn("fixture-single",targets)
        self.assertIn("ambiguous_options",{s.code for s in skips})

    def test_multiple_selected_single_is_skipped(self):
        self.page.locator("#fixture-single .num_option").nth(1).evaluate("(e)=>e.classList.add('check_answer')")
        targets,skips=self.targets()
        self.assertNotIn("fixture-single",targets)
        self.assertIn("ambiguous_checked_state",{s.code for s in skips})

    def test_disagreeing_aria_state_and_unknown_handler_stop(self):
        self.page.locator("#fixture-single .answerBg").first.evaluate("(e)=>e.setAttribute('aria-checked','false')")
        targets,skips=self.targets()
        self.assertNotIn("fixture-single",targets)
        self.assertIn("ambiguous_checked_state",{s.code for s in skips})
        self.page.locator("#fixture-multiple .answerBg").first.evaluate("(e)=>e.setAttribute('onclick','otherHandler(this)')")
        targets,skips=self.targets()
        self.assertNotIn("fixture-multiple",targets)
        self.assertIn("unsupported_choice_handler",{s.code for s in skips})

    def test_replaced_element_never_retargets(self):
        from unittest.mock import patch
        target=self.targets()[0]["fixture-single"]
        original=self.adapter.resolve
        def replaced(t):
            result=original(t)
            self.page.locator("#fixture-single").evaluate("""e=>{
                const next=e.cloneNode(true);
                next.querySelector('.mark_name').textContent='Different question';
                e.replaceWith(next);
            }""")
            return result
        with patch.object(self.adapter,"resolve",side_effect=replaced),self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["B"]))
        self.assertEqual(self.page.evaluate("window.clickCount"),0)

    def test_readback_failure_stops_without_retry(self):
        target=self.targets()[0]["fixture-single"]
        self.page.evaluate("() => { window.addChoice=()=>{window.clickCount++}; }")
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["B"]))
        self.assertEqual(self.page.evaluate("window.clickCount"),1)

    def test_unverified_config_still_rejected(self):
        with self.assertRaises(AdapterNotConfigured):
            ChaoxingAdapter(ROOT/"selectors/chaoxing.example.toml")

    def test_inspection_report_has_no_question_text_or_url_and_does_not_write(self):
        import json
        from practice_autofill.config import Config
        from practice_autofill.inspection import inspect_page
        url="http://mooc1.chaoxing.com/mooc-ans/mooc2/work/dowork?token=DO_NOT_LOG"
        self.page.route("**/mooc-ans/mooc2/work/dowork?*",lambda route: route.fulfill(
            content_type="text/html",body=(ROOT/"fixtures/chaoxing_work.html").read_text(encoding="utf-8")))
        self.page.goto(url)
        before=self.page.content()
        config=Config()
        config.run.stable_seconds=.1
        config.run.load_timeout_seconds=2
        report=inspect_page(self.page,config,ROOT/"selectors/chaoxing.work.toml")
        self.assertTrue(report["ready"])
        self.assertEqual(report["question_count"],2)
        self.assertEqual(report["types"],{"single":1,"multiple":1})
        self.assertEqual(report["resolved_count"],2)
        self.assertEqual(report["skips"],{"unsupported_question_type":1})
        self.assertFalse(report["live_filling_verified"])
        self.assertNotIn("DO_NOT_LOG",json.dumps(report,ensure_ascii=False))
        self.assertNotIn("等于多少",json.dumps(report,ensure_ascii=False))
        self.assertEqual(self.page.content(),before)
        self.assertEqual(self.page.evaluate("window.clickCount"),0)

    def test_image_and_missing_marker_stop_extraction(self):
        self.page.locator("#fixture-single").evaluate("(e)=>e.insertAdjacentHTML('beforeend','<img alt=manual>')")
        targets,skips=self.targets()
        self.assertNotIn("fixture-single",targets)
        self.assertIn("image_requires_manual",{s.code for s in skips})
        self.page.locator("#fixture-multiple .num_option_dx").first.evaluate("(e)=>e.remove()")
        targets,skips=self.targets()
        self.assertNotIn("fixture-multiple",targets)
        self.assertIn("ambiguous_options",{s.code for s in skips})

    def test_visible_aria_hidden_text_is_preserved_but_css_hidden_is_excluded(self):
        self.page.locator(".mark_name,.num_option,.num_option_dx,.answer_p").evaluate_all(
            "(els)=>els.forEach(e=>e.setAttribute('aria-hidden','true'))")
        self.page.locator("#fixture-single .mark_name").evaluate(
            """e=>e.insertAdjacentHTML('beforeend','<span style="display:none">DO_NOT_READ</span>')""")
        targets,skips=self.targets()
        self.assertEqual(set(targets),{"fixture-single","fixture-multiple"})
        question=targets["fixture-single"].question
        self.assertEqual(question.stem,"本地单选：2 + 2 等于多少？")
        self.assertNotIn("DO_NOT_READ",str(question.payload()))
        self.assertEqual(question.options[1].text,"4")
        self.assertEqual(question.options[1].id,"B")

    def test_batch_auto_fill_cache_resume_and_no_submit(self):
        import json
        from unittest.mock import Mock
        from support import artifact_dir
        from practice_autofill.config import Config
        from practice_autofill.cli import Session
        from practice_autofill.cdp_run import process_page
        from practice_autofill.storage import Store
        self.page.route("**/mooc-ans/mooc2/work/dowork", lambda route: route.fulfill(
            content_type="text/html", body=(ROOT/"fixtures/chaoxing_work.html").read_text(encoding="utf-8")))
        self.page.goto("http://mooc1.chaoxing.com/mooc-ans/mooc2/work/dowork")
        config = Config()
        config.answering.mode = "single"  # Regression: preserve original per-question protocol.
        config.run.auto_fill = True
        config.run.stable_seconds = .1
        config.run.load_timeout_seconds = 2
        config.llm.min_interval_seconds = .001
        transport = Mock(side_effect=lambda payload: json.dumps({"type": payload["type"],
            "answers": ["B"] if payload["type"] == "single" else ["A", "B"]}))
        ask = Mock(side_effect=["", ""])
        with Store(artifact_dir()/"batch.sqlite") as store:
            session = Session(config, store, ask=ask, say=lambda _: None, transport_factory=lambda: transport)
            try:
                report = process_page(self.page, config, ROOT/"selectors/chaoxing.work.toml", session)
                self.assertEqual(report, {"verified": 2})
                self.assertEqual(transport.call_count, 2)
                first_clicks = self.page.evaluate("window.clickCount")
                self.assertGreater(first_clicks, 0)
                # Second pass uses cache but deliberately clears existing answers before reselecting.
                self.assertEqual(process_page(self.page, config, ROOT/"selectors/chaoxing.work.toml", session), report)
                self.assertEqual(transport.call_count, 2)
                self.assertGreater(self.page.evaluate("window.clickCount"), first_clicks)
                # Only two end-of-page prompts; no external or per-question confirmation.
                self.assertEqual(ask.call_count, 2)
                self.assertFalse(any("输入 y 回填" in call.args[0] for call in ask.call_args_list))
                targets, _ = self.targets()
                self.assertTrue(read_matches(targets["fixture-single"], self.answer(targets["fixture-single"], ["B"])))
                self.assertTrue(read_matches(targets["fixture-multiple"], self.answer(targets["fixture-multiple"], ["A", "B"])))
            finally:
                session.close()
        self.assertEqual(self.page.evaluate("window.submitCount"), 0)

    def test_clear_selected_choices_before_reselecting(self):
        targets,_=self.targets()
        for name,answers in (("fixture-single",["A"]),("fixture-multiple",["C"])):
            target=targets[name]
            before=self.page.evaluate("window.clickCount")
            fill_answer(target,self.answer(target,answers),clear_first=True)
            self.assertEqual(self.page.evaluate("window.clickCount"),before+2)
            self.assertTrue(read_matches(target,self.answer(target,answers)))

    def test_non_toggleable_single_stops_after_clear_attempt(self):
        targets,_=self.targets()
        self.page.evaluate("() => { window.addChoice=()=>{window.clickCount++}; }")
        with self.assertRaises(FillError):
            fill_answer(targets["fixture-single"],self.answer(targets["fixture-single"],["B"]),clear_first=True)
        self.assertEqual(self.page.evaluate("window.clickCount"),1)
        self.assertTrue(read_matches(targets["fixture-single"],self.answer(targets["fixture-single"],["A"])))
