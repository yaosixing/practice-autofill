import threading
import time
import unittest
import json
import os
import queue
import socket
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from support import artifact_dir
from practice_autofill.browser import runtime_environment
from practice_autofill.browser_selection import discover_browsers
from practice_autofill.config import Config, ROOT
from practice_autofill.web import Console, ConsoleServer


class WebBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime_environment(ROOT / 'runtime')
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        config = Config()
        # Isolate every test's profiles, cache and logs; reuse installed browser binary.
        candidates = discover_browsers(config.runtime)
        config.browser.executable_path = next(c.executable_path for c in candidates if c.kind == 'chromium')
        config.browser.kind = 'chromium'
        config.run.runtime_dir = str(artifact_dir())
        config.run.stable_seconds = .2
        config.run.auto_fill = True
        self.console = Console(config)
        self.server = ConsoleServer(('127.0.0.1', 0), self.console)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1080})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.server.origin)
        self.page.get_by_text('本机已连接', exact=True).wait_for()

    def tearDown(self):
        self.console.close()
        if self.console.thread:
            self.console.thread.join(timeout=10)
        self.context.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.assertEqual(self.errors, [])

    def test_ui_configuration_clear_responsive_and_no_injection(self):
        self.page.screenshot(path=str(ROOT / 'runtime/web-preview-desktop.png'), full_page=True, animations='disabled')
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.screenshot(path=str(ROOT / 'runtime/web-preview-mobile.png'), full_page=True, animations='disabled')
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.set_viewport_size({'width': 1440, 'height': 1080})
        self.page.get_by_role('button', name='API 配置', exact=False).first.click()
        form = self.page.locator('#api-form')
        form.locator('[name=model]').fill('ui-test-model')
        self.page.locator('#api-key').fill('ui-test-secret')
        form.get_by_role('button', name='应用 API 配置').click()
        self.page.get_by_text('API Key 已配置', exact=True).wait_for()
        self.assertEqual(self.console.config.llm.model, 'ui-test-model')
        self.assertNotIn('ui-test-secret', self.page.locator('body').inner_text())
        self.page.locator('#clear-settings').click()
        self.page.get_by_role('button', name='取消', exact=True).click()
        self.assertTrue(self.console.snapshot()['key_set'])
        self.page.locator('#clear-settings').click()
        self.assertTrue(self.page.locator('#confirm-clear').is_disabled())
        self.page.locator('#clear-confirmation').fill('CLEAR')
        self.page.locator('#confirm-clear').click()
        self.page.get_by_text('API 未配置', exact=True).wait_for()
        self.assertEqual(self.console.config.llm.model, 'deepseek-flash')
        self.page.locator('[data-page=settings]').click()
        self.page.screenshot(path=str(ROOT / 'runtime/web-preview-settings.png'), full_page=True, animations='disabled')
        self.page.locator('input[name="pacing.mode"][value=custom]').check()
        self.page.locator('[name="pacing.question_delay_seconds"]').fill('0.7')
        self.page.locator('[name="pacing.control_delay_seconds"]').fill('0.2')
        self.page.get_by_role('button', name='应用答题与节奏').click()
        expect(self.page.locator('#summary-pace')).to_have_text('自定义 ↗')
        self.assertEqual(self.console.config.pacing.question_delay_seconds, .7)
        self.page.locator('[data-page=workspace]').click()
        self.console.add_output('<img src=x onerror="window.pwned=1">')
        self.page.locator('.log-line').filter(has_text='<img src=x onerror="window.pwned=1">').wait_for()
        self.assertIsNone(self.page.evaluate('window.pwned'))
        self.page.locator('[data-page=browser]').click()
        self.page.locator('#detect-browsers').click()
        self.page.get_by_text('CDP：', exact=False).wait_for()
        self.assertGreater(self.page.locator('.browser-result').count(), 0)

    def test_real_demo_job_reply_rescan_and_safe_stop(self):
        self.page.locator('#task-action').select_option('demo')
        self.page.locator('#start-task').click()
        self.page.locator('#prompt-text').filter(has_text='手动登录').wait_for(timeout=30000)
        self.assertTrue(self.console.running)
        self.page.locator('#prompt-enter').click()
        self.page.locator('#prompt-text').filter(has_text='重新扫描').wait_for(timeout=60000)
        snapshot = self.console.snapshot()
        self.assertEqual(snapshot['counts']['verified'], 9)
        self.assertEqual(snapshot['counts']['failed'], 0)
        self.assertEqual(snapshot['counts']['manual'], 0)
        self.page.screenshot(path=str(ROOT / 'runtime/web-preview-running.png'), full_page=True, animations='disabled')
        # Stop while waiting for human input; never kill or close an attached user browser.
        self.page.locator('#stop-task').click()
        self.page.get_by_text('已停止', exact=True).wait_for(timeout=15000)
        self.assertFalse(self.console.running)
        self.assertEqual(self.console.returncode, 130)

    def test_worker_mock_attach_fills_all_types_without_submit_or_closing_browser(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        browser = self.pw.chromium.launch(headless=True, args=[f'--remote-debugging-port={port}'])
        page = browser.new_page()
        page.goto(self.server.origin + '/practice.html')
        config = self.console.config
        config.browser.mode = 'attach'
        config.browser.executable_path = ''
        config.browser.cdp_url = f'http://127.0.0.1:{port}'
        env = os.environ.copy()
        env.pop('DEEPSEEK_API_KEY', None)
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
        process = subprocess.Popen([sys.executable, '-B', '-u', '-m', 'practice_autofill.web_worker'],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1)
        messages = queue.Queue()
        reader = threading.Thread(target=lambda: [messages.put(json.loads(line)) for line in process.stdout], daemon=True)
        reader.start()

        def send(value):
            process.stdin.write(json.dumps(value) + '\n')
            process.stdin.flush()

        send({'config': asdict(config), 'arguments': ['--run', '--mock']})
        results = []
        reached_end = False
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                message = messages.get(timeout=15)
                if message['kind'] == 'result':
                    results.append(message['status'])
                if message['kind'] == 'prompt':
                    if '重新扫描' in message['text']:
                        reached_end = True
                        self.assertEqual(page.evaluate('window.submitCount'), 0)
                        self.assertTrue(page.locator('#single input[value=b]').is_checked())
                        self.assertFalse(page.locator('#multi-c').is_checked())
                        self.assertEqual(page.locator('#blanks input').nth(1).input_value(), '北京')
                        self.assertEqual(page.locator('#rich [contenteditable]').inner_text(), 'world')
                        self.assertEqual(page.frame_locator('iframe[data-blank]').locator('body').inner_text(), 'test')
                        self.assertEqual(page.frame_locator('iframe[title="题目框架"]').frame_locator('iframe').locator('input').input_value(), '8')
                        send({'cancel': True})
                        break
                    send({'id': message['id'], 'reply': ''})
            self.assertTrue(reached_end)
            self.assertEqual(results, ['verified'] * 9)
            self.assertEqual(process.wait(timeout=10), 130)
            self.assertTrue(browser.is_connected())
            self.assertEqual(page.evaluate('window.submitCount'), 0)
        finally:
            if process.poll() is None:
                send({'cancel': True})
                process.wait(timeout=10)
            process.stdin.close()
            reader.join(timeout=2)
            process.stdout.close()
            browser.close()


if __name__ == '__main__':
    unittest.main()
