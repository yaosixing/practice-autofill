import unittest
import test_chaoxing as base
from practice_autofill.filling import fill_answer, read_matches, FillError
from practice_autofill.inspection import inspect_page
from practice_autofill.config import Config


# Reuse the browser fixture, but not the original class's tests.
class CombinedTests(unittest.TestCase):
    setUpClass=classmethod(base.ChaoxingTests.setUpClass.__func__)
    tearDownClass=classmethod(base.ChaoxingTests.tearDownClass.__func__)
    tearDown=base.ChaoxingTests.tearDown
    targets=base.ChaoxingTests.targets
    answer=base.ChaoxingTests.answer

    def setUp(self):
        self.page=self.browser.new_page()
        self.adapter_path=base.ROOT/'selectors/chaoxing.work.toml'
        self.adapter=base.ChaoxingAdapter(self.adapter_path)
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}/chaoxing_combined.html')

    def test_combined_extraction_context_tables_and_unique_identity(self):
        targets,skips=self.targets()
        self.assertFalse(skips)
        self.assertEqual(set(targets),{'material-one::sub-one','material-one::sub-two','material-two::sub-one'})
        q=targets['material-one::sub-one'].question
        self.assertIn('铅笔 | 12',q.stem)
        self.assertIn('铅笔有多少支',q.stem)
        self.assertNotIn('橡皮有多少块',q.stem)
        self.assertEqual(q.type,'single')
        self.assertEqual(targets['material-two::sub-one'].question.type,'multiple')
        self.assertEqual(self.page.evaluate('window.clickCount'),0)

    def test_combined_clear_fill_is_scoped_and_repeatable(self):
        targets,_=self.targets()
        first=targets['material-one::sub-one']; second=targets['material-one::sub-two']; multi=targets['material-two::sub-one']
        fill_answer(first,self.answer(first,['A']),clear_first=True)
        self.assertTrue(read_matches(first,self.answer(first,['A'])))
        self.assertFalse(read_matches(second,self.answer(second,['A'])))
        fill_answer(second,self.answer(second,['A']),clear_first=True)
        fill_answer(multi,self.answer(multi,['A','B']),clear_first=True)
        self.assertTrue(read_matches(multi,self.answer(multi,['A','B'])))
        fill_answer(first,self.answer(first,['A']),clear_first=True)
        self.assertTrue(read_matches(second,self.answer(second,['A'])))

    def test_context_change_invalidates_target(self):
        targets,_=self.targets();target=targets['material-one::sub-one']
        self.page.locator('#material-one td').last.evaluate('(e)=>e.textContent="9"')
        with self.assertRaises(FillError):fill_answer(target,self.answer(target,['A']))
        self.assertEqual(self.page.evaluate('window.clickCount'),0)

    def test_unmatched_option_and_unknown_type_stop(self):
        self.page.locator('[itemid=sub-two] .read_type').evaluate('(e)=>e.textContent="(未知题型)"')
        targets,skips=self.targets()
        self.assertNotIn('material-one::sub-two',targets)
        self.assertTrue(skips)

    def test_option_cannot_be_silently_omitted(self):
        self.page.locator('[itemid=sub-two] .hoverDiv').last.evaluate('(e)=>e.classList.remove("hoverDiv")')
        targets,skips=self.targets()
        self.assertNotIn('material-one::sub-two',targets)
        self.assertIn('ambiguous_options',{s.code for s in skips})

    def test_duplicate_subidentity_and_parent_image_stop(self):
        targets,_=self.targets();target=targets['material-one::sub-one']
        self.page.locator('[itemid=sub-two]').evaluate('(e)=>e.setAttribute("itemid","sub-one")')
        with self.assertRaises(FillError):fill_answer(target,self.answer(target,['A']))
        self.page.locator('#material-one .mark_name').evaluate('(e)=>{const img=document.createElement("img");img.alt="人工处理";e.append(img)}')
        targets,skips=self.targets()
        self.assertNotIn('material-one::sub-one',targets)
        self.assertIn('image_requires_manual',{s.code for s in skips})

    def test_read_only_inspection(self):
        self.page.route('**/mooc-ans/mooc2/work/dowork',lambda route:route.fulfill(
            content_type='text/html',body=(base.ROOT/'fixtures/chaoxing_combined.html').read_text(encoding='utf-8')))
        self.page.goto('http://mooc1.chaoxing.com/mooc-ans/mooc2/work/dowork')
        config=Config();config.run.stable_seconds=.1;config.run.load_timeout_seconds=2
        result=inspect_page(self.page,config,self.adapter_path)
        self.assertEqual(result['question_count'],3)
        self.assertTrue(result['ready'])
        self.assertEqual(self.page.evaluate('window.clickCount'),0)


