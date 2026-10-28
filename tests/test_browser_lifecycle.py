import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from playwright.sync_api import BrowserType
from practice_autofill.browser import open_browser,launch_browser,runtime_environment
from practice_autofill.browser_selection import BrowserChoice,BrowserError,discover_browsers
from practice_autofill.config import Config,ROOT
from support import artifact_dir

class BrowserLifecycleTests(unittest.TestCase):
    def test_attaching_never_closes_or_snapshots_user_browser(self):
        config=Config();page=Mock();page.is_closed.return_value=False
        context=Mock();context.pages=[page]
        browser=Mock();browser.contexts=[context]
        with patch('practice_autofill.browser.sync_playwright') as pw:
            pw.return_value.__enter__.return_value.chromium.connect_over_cdp.return_value=browser
            with open_browser(config,BrowserChoice('attach','auto',endpoint='http://127.0.0.1:9527')) as (visible,current):
                self.assertIs(current,page)
                self.assertEqual(visible.pages,[page])
        browser.close.assert_not_called();context.close.assert_not_called()
        context.storage_state.assert_not_called();context.cookies.assert_not_called()

    def test_connection_errors_have_fixed_safe_code(self):
        with patch('practice_autofill.browser.sync_playwright') as pw:
            pw.return_value.__enter__.return_value.chromium.connect_over_cdp.side_effect=RuntimeError('PRIVATE_URL_TOKEN')
            with self.assertRaisesRegex(BrowserError,'^connection_failed$'):
                with open_browser(Config(),BrowserChoice('attach','auto',endpoint='http://localhost:9527')):
                    pass

    def test_cli_explicit_defaults_override_saved_endpoint(self):
        from practice_autofill.cli import cdp_endpoint,main
        config=Config();config.browser.cdp_url='http://localhost:9222'
        self.assertEqual(cdp_endpoint('http://127.0.0.1:9527',config),'http://127.0.0.1:9527')
        self.assertEqual(cdp_endpoint('__default_cdp__',config),'http://localhost:9222')
        with patch('practice_autofill.cli.browser_report',return_value={}) as report,patch('builtins.print'):
            self.assertEqual(main(['--detect-browsers','--browser-kind','edge'],config_override=config),0)
        self.assertEqual(report.call_args.args[0].browser.kind,'edge')

    def test_installed_browsers_launch_with_isolated_project_profiles(self):
        runtime_environment(ROOT/'runtime')
        candidates=discover_browsers(ROOT/'runtime')
        self.assertTrue(candidates,'No installed browser available for local launch verification')
        directory=artifact_dir();records=[]
        original=BrowserType.launch_persistent_context
        def headless(browser_type,*args,**kwargs):
            kwargs['headless']=True
            return original(browser_type,*args,**kwargs)
        profiles=[]
        for choice in candidates:
            with self.subTest(kind=choice.kind):
                config=Config();config.run.runtime_dir=str(directory)
                with patch.object(BrowserType,'launch_persistent_context',new=headless):
                    with launch_browser(config,choice) as (_,page):
                        page.set_content('<title>Local browser verification</title><input id="test">')
                        page.locator('#test').fill(choice.kind)
                        self.assertEqual(page.locator('#test').input_value(),choice.kind)
                snapshots=list((directory/'browser-profiles').glob('*/practice-storage-state.json'))
                self.assertEqual(len(snapshots),len(profiles)+1)
                profiles=snapshots
                records.append({'kind':choice.kind,'file_exists':True,'headless_launch_verified':True})
        (directory/'browser-launch-validation.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
        self.assertEqual(len({p.parent for p in profiles}),len(candidates))

if __name__=='__main__': unittest.main()
