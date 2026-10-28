import functools
import http.server
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import BrowserType
from practice_autofill.browser import launch_browser,runtime_environment
from practice_autofill.config import Config
from support import artifact_dir

ROOT=Path(__file__).resolve().parent.parent

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass

class BrowserProfileTests(unittest.TestCase):
    def test_local_login_state_survives_browser_restart(self):
        runtime_environment(ROOT/"runtime")
        server=http.server.ThreadingHTTPServer(("127.0.0.1",0),
            functools.partial(QuietHandler,directory=str(ROOT/"fixtures")))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        config=Config()
        config.run.runtime_dir=str(artifact_dir()/"profile-test")
        url=f"http://127.0.0.1:{server.server_port}/practice.html"
        original=BrowserType.launch_persistent_context
        def headless(browser_type,*args,**kwargs):
            kwargs["headless"]=True  # production launch remains visible
            return original(browser_type,*args,**kwargs)
        try:
            with patch.object(BrowserType,"launch_persistent_context",new=headless):
                with launch_browser(config) as (context,page):
                    page.goto(url)
                    page.evaluate("localStorage.setItem('fixture_login','saved')")
                    context.add_cookies([{"name":"fixture_cookie","value":"dummy","url":url}])
                with launch_browser(config) as (context,page):
                    page.goto(url)
                    self.assertEqual(page.evaluate("localStorage.getItem('fixture_login')"),"saved")
                    self.assertTrue(any(cookie["name"]=="fixture_cookie" for cookie in context.cookies()))
        finally:
            server.shutdown();server.server_close();thread.join()
