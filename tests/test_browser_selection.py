import json
import unittest
import http.server
import threading
from pathlib import Path
from unittest.mock import Mock,patch
from practice_autofill.config import Config,validate_config
from practice_autofill.browser_selection import BrowserChoice,BrowserError,choose_browser,probe_cdp,unique_candidates

class BrowserSelectionTests(unittest.TestCase):
    def setUp(self):
        self.config=Config()
        self.chrome=BrowserChoice('launch','chrome','E:/chrome.exe',evidence='test')
        self.edge=BrowserChoice('launch','edge','E:/edge.exe',evidence='test')

    def choose(self,**kwargs):
        return choose_browser(self.config,probe=lambda *_:('available',{'Browser':'Chrome/test'}),
                              discover=lambda *_:[self.chrome,self.edge],**kwargs)

    def test_default_available_9527_before_discovery(self):
        choice=self.choose()
        self.assertEqual(choice.mode,'attach')
        self.assertEqual(choice.endpoint,'http://127.0.0.1:9527')

    def test_explicit_kind_overrides_default_attach(self):
        self.config.browser.kind='edge'
        self.assertEqual(self.choose(),self.edge)

    def test_unavailable_default_falls_back_to_detected(self):
        choice=choose_browser(self.config,probe=lambda *_:('unavailable',{}),discover=lambda *_:[self.chrome])
        self.assertEqual(choice,self.chrome)

    def test_explicit_bad_path_never_silent_fallback(self):
        self.config.browser.executable_path='E:/missing/browser.exe'
        with self.assertRaisesRegex(BrowserError,'invalid_executable'):
            self.choose()

    def test_explicit_bad_endpoint_never_silent_fallback(self):
        self.config.browser.cdp_url='http://127.0.0.1:1234'
        with self.assertRaisesRegex(BrowserError,'non_cdp_service'):
            choose_browser(self.config,probe=lambda *_:('non_cdp_service',{}),discover=Mock())

    def test_force_launch_never_attaches(self):
        self.assertEqual(self.choose(force_launch=True).mode,'launch')

    def test_deduplicates_windows_paths(self):
        second=BrowserChoice('launch','chrome','e:/CHROME.exe',evidence='registry')
        self.assertEqual(len(unique_candidates([self.chrome,second,self.edge])),2)

    def test_invalid_browser_settings(self):
        for name,value in [('kind','firefox'),('mode','bad'),('connect_timeout_seconds',float('nan')),
                           ('cdp_url','http://example.com:9527'),('cdp_url','http://localhost:99999')]:
            with self.subTest(name=name,value=value):
                c=Config();setattr(c.browser,name,value)
                with self.assertRaises(ValueError): validate_config(c)

    def test_non_cdp_port_is_classified(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"healthy"}')
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            status,_=probe_cdp(f'http://127.0.0.1:{server.server_port}',1)
            self.assertEqual(status,'non_cdp_service')
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__': unittest.main()
