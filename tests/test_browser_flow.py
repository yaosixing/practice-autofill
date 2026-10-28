import functools
import http.server
import os
import threading
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from practice_autofill.config import Config
from practice_autofill.extraction import scan
from practice_autofill.filling import fill_answer, read_matches, FillError
from practice_autofill.validation import validate_answer
from practice_autofill.llm import MockTransport, Solver
from practice_autofill.adapters.chaoxing import ChaoxingAdapter, AdapterNotConfigured

ROOT=Path(__file__).resolve().parent.parent

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass

class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from practice_autofill.browser import runtime_environment
        runtime_environment(ROOT/"runtime")
        cls.server=http.server.ThreadingHTTPServer(("127.0.0.1",0),
            functools.partial(QuietHandler,directory=str(ROOT/"fixtures")))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.pw=sync_playwright().start()
        opts={"headless":True}
        if os.environ.get("PRACTICE_BROWSER_EXECUTABLE"):
            opts["executable_path"]=os.environ["PRACTICE_BROWSER_EXECUTABLE"]
        cls.browser=cls.pw.chromium.launch(**opts)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        self.page=self.browser.new_page()
        self.page.goto(f"http://127.0.0.1:{self.server.server_port}/practice.html")
        self.config=Config()
        self.config.run.stable_seconds=.2
        self.config.run.load_timeout_seconds=4
        self.targets,self.skips=scan(self.page,self.config)
        self.by_id={t.question.question_id:t for t in self.targets}

    def tearDown(self):
        self.page.close()

    def answer(self,target,answers):
        import json
        return validate_answer(json.dumps({"type":target.question.type,"answers":answers}),target.question)

    def test_mock_pipeline_idempotent_and_no_submit(self):
        self.assertEqual(set(self.by_id),{"single","multi","blanks","textarea","rich","delayed","frame","nested","editor"})
        self.assertIn("image_requires_manual",{s.code for s in self.skips})
        solver=Solver(self.config.llm,MockTransport(ROOT/"fixtures/mock_answers.json"))
        for target in self.targets:
            answer=solver.solve(target.question)
            self.assertNotEqual(answer.type,"unknown",target.question.question_id)
            fill_answer(target,answer)
            self.assertTrue(read_matches(target,answer))
            fill_answer(target,answer)
            self.assertTrue(read_matches(target,answer))
        self.assertEqual(self.page.evaluate("window.submitCount"),0)
        self.assertFalse(self.page.locator("#multi-c").is_checked())

    def test_reorder_is_safe_but_changed_question_stops(self):
        target=self.by_id["single"]
        self.page.evaluate("document.querySelector('form').prepend(document.querySelector('#single'))")
        fill_answer(target,self.answer(target,["B"]))
        self.page.locator("#single legend").evaluate("(e)=>e.textContent='Changed'")
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["A"]))

    def test_duplicate_controls_removed_controls_navigation(self):
        target=self.by_id["single"]
        self.page.locator("#single").evaluate("(e)=>e.after(e.cloneNode(true))")
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["B"]))
        target=self.by_id["blanks"]
        self.page.locator("#blanks input").first.evaluate("(e)=>e.remove()")
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["x","y"]))
        target=self.by_id["textarea"]
        self.page.reload()
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["x"]))

    def test_readback_failure_and_no_submit(self):
        target=self.by_id["textarea"]
        self.page.locator("#textarea textarea").evaluate("(e)=>e.addEventListener('input',()=>e.value='changed')")
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["answer"]))
        self.assertEqual(self.page.evaluate("window.submitCount"),0)

    def test_blocker_pauses(self):
        self.page.evaluate("document.body.insertAdjacentHTML('afterbegin','<input type=password>')")
        targets,skips=scan(self.page,self.config)
        self.assertEqual(targets,[])
        self.assertIn("manual_access_required",{s.code for s in skips})

    def test_chaoxing_unconfigured(self):
        with self.assertRaises(AdapterNotConfigured):
            ChaoxingAdapter(None)

    def test_aria_and_formula_text(self):
        self.page.locator("form").evaluate("""e=>e.insertAdjacentHTML('afterbegin',
            '<fieldset id="aria"><legend>选择 <math><semantics><mi>x</mi><annotation encoding="application/x-tex">x^2</annotation></semantics></math>。<br>第二行</legend><div role="radiogroup"><div role="radio" aria-checked="false">one</div><div role="radio" aria-checked="true">two</div></div></fieldset>'
        )""")
        self.page.locator("#aria [role=radio]").evaluate_all("""els=>els.forEach(e=>e.addEventListener('click',()=>{
            els.forEach(x=>x.setAttribute('aria-checked',String(x===e)));
        }))""")
        targets,_=scan(self.page,self.config)
        target=next(t for t in targets if t.question.question_id=="aria")
        self.assertIn("x^2",target.question.stem)
        self.assertIn("\n",target.question.stem)
        answer=self.answer(target,["A"])
        fill_answer(target,answer)
        self.assertTrue(read_matches(target,answer))
        self.assertEqual(self.page.evaluate("window.submitCount"),0)

    def test_aria_submit_button_and_unassociated_group_skipped(self):
        self.page.locator("form").evaluate("""e=>e.insertAdjacentHTML('afterbegin',
            '<fieldset id="unsafe-button"><legend>Unsafe</legend><button role="radio" aria-checked="false">x</button></fieldset>'+
            '<fieldset id="ambiguous-group"><legend>Ambiguous</legend><div role="radio" aria-checked="false">x</div><div role="radio" aria-checked="false">y</div></fieldset>'
        )""")
        targets,skips=scan(self.page,self.config)
        ids={t.question.question_id for t in targets}
        self.assertNotIn("unsafe-button",ids)
        self.assertNotIn("ambiguous-group",ids)

    def test_session_preview_confirmation_with_actual_dom(self):
        from practice_autofill.cli import Session
        from practice_autofill.storage import Store
        from support import artifact_dir
        self.config.llm.min_interval_seconds=.001
        target=self.by_id["single"]
        messages=[]
        with Store(artifact_dir()/"answers.sqlite") as store:
            session=Session(self.config,store,mock=True,ask=lambda _:"n",say=messages.append)
            self.assertEqual(session.process(target),"skipped")
            self.assertFalse(self.page.locator("#single input").nth(1).is_checked())
            self.assertTrue(any("答案" in line for line in messages))
            session.ask=lambda _:"y"
            self.assertEqual(session.process(target),"verified",target.question.question_id)
            self.assertTrue(self.page.locator("#single input").nth(1).is_checked())
            self.assertEqual(session.transport.calls,1)  # second pass used validated cache
            session.close()
        self.assertEqual(self.page.evaluate("window.submitCount"),0)

    def test_new_blocker_before_fill_stops_write(self):
        target=self.by_id["single"]
        answer=self.answer(target,["B"])
        self.page.evaluate("document.body.insertAdjacentHTML('afterbegin','<div data-automation-blocked>Manual</div>')")
        with self.assertRaises(FillError):
            fill_answer(target,answer)
        self.assertFalse(self.page.locator("#single input").nth(1).is_checked())

    def test_unknown_mock_and_anonymous_duplicate(self):
        from practice_autofill.models import Question
        mock=MockTransport(ROOT/"fixtures/mock_answers.json")
        self.assertEqual(Solver(self.config.llm,mock).solve(Question("new","blank","Unseen",(),1)).type,"unknown")
        self.page.locator("#single").evaluate("(e)=>e.removeAttribute('id')")
        targets,_=scan(self.page,self.config)
        target=next(t for t in targets if t.question.stem=="2 + 2 等于多少？")
        self.page.locator("fieldset").first.evaluate("(e)=>e.after(e.cloneNode(true))")
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["B"]))

    def test_replaced_element_is_not_retargeted(self):
        from unittest.mock import patch
        target=self.by_id["textarea"]
        original=target.adapter.resolve
        replaced=False
        def resolving(t):
            nonlocal replaced
            result=original(t)
            if not replaced:
                replaced=True
                self.page.locator("#textarea").evaluate("""e=>{
                    const copy=e.cloneNode(true);
                    copy.querySelector('legend').textContent='Different question';
                    e.replaceWith(copy);
                }""")
            return result
        with patch.object(target.adapter,"resolve",side_effect=resolving),self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["MUST_NOT_WRITE"]))
        self.assertEqual(self.page.locator("#textarea textarea").input_value(),"")

    def test_nested_shadow_controls_require_adapter(self):
        self.page.locator("#blanks").evaluate("""e=>{
            const host=document.createElement('div');e.prepend(host);
            host.attachShadow({mode:'open'}).innerHTML='<input aria-label="unrelated">';
        }""")
        targets,skips=scan(self.page,self.config)
        self.assertNotIn("blanks",{t.question.question_id for t in targets})
        self.assertIn("shadow_dom_requires_adapter",{s.code for s in skips})

    def test_clear_before_fill_native_and_all_blanks(self):
        single=self.by_id["single"]
        fill_answer(single,self.answer(single,["B"]))
        self.page.locator("#single").evaluate("""e=>{
            window.resetEvents=[];
            e.addEventListener('change',event=>window.resetEvents.push(event.target.checked));
        }""")
        fill_answer(single,self.answer(single,["B"]),clear_first=True)
        self.assertIn(False,self.page.evaluate("window.resetEvents"))
        self.assertTrue(read_matches(single,self.answer(single,["B"])))
        blank=self.by_id["blanks"]
        fill_answer(blank,self.answer(blank,["old1","old2"]))
        self.page.locator("#blanks").evaluate("""e=>{
            window.blankEvents=[];
            e.addEventListener('input',()=>window.blankEvents.push(Array.from(e.querySelectorAll('input')).map(c=>c.value)));
        }""")
        fill_answer(blank,self.answer(blank,["new1","new2"]),clear_first=True)
        events=self.page.evaluate("window.blankEvents")
        self.assertIn(["",""],events)
        self.assertLess(events.index(["",""]),events.index(["new1",""]))
        self.assertTrue(read_matches(blank,self.answer(blank,["new1","new2"])))
        self.assertEqual(self.page.evaluate("window.submitCount"),0)

    def test_clear_failure_never_writes_final_answer(self):
        target=self.by_id["blanks"]
        fill_answer(target,self.answer(target,["keep","old"]))
        self.page.locator("#blanks input").first.evaluate("""e=>e.addEventListener('input',()=>{
            if(e.value==='') e.value='blocked';
        })""")
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,["new1","new2"]),clear_first=True)
        self.assertNotEqual(self.page.locator("#blanks input").first.input_value(),"new1")
        self.assertEqual(self.page.evaluate("window.submitCount"),0)
    def test_fast_prepare_uses_one_request_and_cache_then_page_order(self):
        from practice_autofill.cli import Session
        from practice_autofill.storage import Store
        from support import artifact_dir
        self.config.run.auto_fill=True
        self.config.llm.min_interval_seconds=.001
        with Store(artifact_dir()/"batch.sqlite") as store:
            session=Session(self.config,store,mock=True,say=lambda _:None)
            self.assertTrue(session.prepare(self.targets))
            self.assertEqual(session.transport.calls,1)
            for target in self.targets:
                self.assertEqual(session.process(target),"verified",target.question.question_id)
            self.assertEqual(session.transport.calls,1)
            self.assertTrue(session.prepare(self.targets))
            self.assertEqual(session.transport.calls,1)
            for target in self.targets:
                self.assertEqual(session.process(target),"verified",target.question.question_id)
            session.close()
        self.assertEqual(self.page.evaluate("window.submitCount"),0)

    def test_fast_prepared_old_target_stops_after_navigation(self):
        from practice_autofill.cli import Session
        from practice_autofill.storage import Store
        from support import artifact_dir
        self.config.run.auto_fill=True
        with Store(artifact_dir()/"batch.sqlite") as store:
            session=Session(self.config,store,mock=True,say=lambda _:None)
            self.assertTrue(session.prepare(self.targets))
            self.page.reload()
            self.assertEqual(session.process(self.targets[0]),"failed")
            self.assertEqual(session.transport.calls,1)
            session.close()
        self.assertEqual(self.page.evaluate("window.submitCount"),0)
    def test_cli_fast_and_single_modes_on_local_page(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from unittest.mock import patch
        from practice_autofill.cli import main,Session
        from support import artifact_dir
        self.config.run.runtime_dir=str(artifact_dir())
        self.config.llm.min_interval_seconds=.001
        sessions=[]
        def factory(*args,**kwargs):
            session=Session(*args,**kwargs);sessions.append(session);return session
        @contextmanager
        def opened(*args):
            yield SimpleNamespace(pages=[self.page]),self.page
        for mode,calls in [('fast',1),('single',9)]:
            with self.subTest(mode=mode),patch('practice_autofill.cli.open_browser',side_effect=opened), \
                 patch('practice_autofill.cli.prompt_url',return_value=self.page.url), \
                 patch('practice_autofill.cli.Session',side_effect=factory), \
                 patch('builtins.input',side_effect=['','']),patch('builtins.print') as output:
                self.assertIsNone(main(['--mock','--auto-fill','--answer-mode',mode,'--browser-mode','launch',
                                        '--browser-kind','chromium'],config_override=self.config))
                self.assertEqual(sessions[-1].transport.calls,calls)
                self.assertTrue(any('"verified": 9' in str(call) for call in output.call_args_list))
                self.assertEqual(self.page.evaluate('window.submitCount'),0)
    def test_out_of_order_mixed_batch_answers_fill_the_correct_local_controls(self):
        import json
        from practice_autofill.cli import Session
        from practice_autofill.storage import Store
        from support import artifact_dir
        self.config.run.auto_fill=True
        fixture=MockTransport(ROOT/'fixtures/mock_answers.json')
        def reversed_response(payload):
            obj=json.loads(fixture(payload));obj['answers'].reverse()
            return json.dumps(obj,ensure_ascii=False)
        with Store(artifact_dir()/'out-of-order.sqlite') as store:
            session=Session(self.config,store,say=lambda _:None,transport_factory=lambda:reversed_response)
            self.assertTrue(session.prepare(self.targets))
            for target in self.targets:
                self.assertEqual(session.process(target),'verified',target.question.question_id)
            self.assertEqual(fixture.calls,1)
            session.close()
        self.assertEqual(self.page.evaluate('window.submitCount'),0)
    def test_fill_pacing_includes_clear_and_fill_without_submitting(self):
        from unittest.mock import patch
        from practice_autofill.pacing import FillPacer
        target=self.by_id['blanks']
        fill_answer(target,self.answer(target,['old1','old2']))
        self.config.pacing.mode='custom';self.config.pacing.control_delay_seconds=.01
        pacer=FillPacer(self.config.pacing);pacer.before_question(target)
        with patch.object(self.page,'wait_for_timeout',wraps=self.page.wait_for_timeout) as wait:
            fill_answer(target,self.answer(target,['new1','new2']),clear_first=True,
                        before_control=lambda:pacer.before_control(target))
        self.assertEqual([c.args[0] for c in wait.call_args_list],[10,10,10])
        self.assertTrue(read_matches(target,self.answer(target,['new1','new2'])))
        self.assertEqual(self.page.evaluate('window.submitCount'),0)

    def test_navigation_during_pacing_stops_before_stale_write(self):
        from practice_autofill.pacing import FillPacer
        target=self.by_id['blanks']
        self.config.pacing.mode='normal'
        pacer=FillPacer(self.config.pacing);pacer.before_question(target)
        def pause():
            if pacer.control_started:
                self.page.reload()
            pacer.before_control(target)
        with self.assertRaises(FillError):
            fill_answer(target,self.answer(target,['must-not-complete','must-not-complete']),
                        clear_first=True,before_control=pause)
        self.assertEqual(self.page.locator('#blanks input').first.input_value(),'')
        self.assertEqual(self.page.evaluate('window.submitCount'),0)
