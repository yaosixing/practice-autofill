import json
import os
import threading
import unittest
from copy import deepcopy
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
from unittest.mock import patch

from practice_autofill.config import Config
from practice_autofill.web import Console, ConsoleServer


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.console = Console(Config())

    def tearDown(self):
        self.console.close()

    def test_atomic_config_validation_and_no_key_echo(self):
        original = deepcopy(self.console.config)
        with self.assertRaises(ValueError):
            self.console.configure({'llm': {'max_retries': -1}, 'key': 'test-secret'})
        self.assertEqual(self.console.config, original)
        self.console.configure({'key': 'test-secret', 'pacing': {'mode': 'slow'}})
        state = self.console.snapshot()
        self.assertTrue(state['key_set'])
        self.assertNotIn('test-secret', json.dumps(state))

    def test_clear_scope_and_explicit_confirmation(self):
        self.console.configure({'key': 'test-secret', 'llm': {'model': 'changed'},
                                'pacing': {'mode': 'slow'}})
        with self.assertRaises(ValueError):
            self.console.clear('')
        self.assertTrue(self.console.snapshot()['key_set'])
        self.console.clear('CLEAR')
        self.assertEqual(self.console.config.llm.model, 'deepseek-flash')
        self.assertEqual(self.console.config.pacing.mode, 'slow')
        self.assertFalse(self.console.snapshot()['key_set'])

    def test_unknown_fields_and_stale_prompt_rejected(self):
        for value in [{'run': {'runtime_dir': 'elsewhere'}}, {'anything': {}}, {'key': 23}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.console.configure(value)
        with self.assertRaises(ValueError):
            self.console.reply({'id': 'old', 'value': 'yes'})

    def test_redaction(self):
        self.console.configure({'key': 'test-secret'})
        self.console.add_output('test-secret https://example.test/work?token=private')
        text = json.dumps(self.console.snapshot(), ensure_ascii=False)
        self.assertNotIn('test-secret', text)
        self.assertNotIn('token=private', text)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.console = Console(Config())
        self.server = ConsoleServer(('127.0.0.1', 0), self.console)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.client = build_opener(ProxyHandler({}))
        with self.client.open(self.origin + '/') as response:
            self.cookie = response.headers['Set-Cookie'].split(';')[0]

    def tearDown(self):
        self.console.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, **headers):
        base = {'Cookie': self.cookie}
        if data is not None:
            base.update({'Content-Type': 'application/json', 'Origin': self.origin,
                         'X-Console-Token': self.server.csrf})
        base.update(headers)
        return self.client.open(Request(self.origin + path,
            data=None if data is None else json.dumps(data).encode(), headers=base))

    def test_missing_auth_and_foreign_origin_cannot_write(self):
        with self.assertRaises(HTTPError) as error:
            self.client.open(self.origin + '/api/state')
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/config', {}, Origin='https://foreign.test')
        self.assertEqual(error.exception.code, 403)

    def test_configuration_endpoint_and_static_assets(self):
        with self.request('/api/config', {'pacing': {'mode': 'normal'}}) as response:
            self.assertTrue(json.load(response)['ok'])
        for path in ['/app.js', '/style.css']:
            with self.request(path) as response:
                self.assertEqual(response.status, 200)
        with self.request('/api/state') as response:
            self.assertEqual(json.load(response)['config']['pacing']['mode'], 'normal')

    def test_bad_payload_has_safe_error(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/config', {'llm': {'max_retries': 'secret-error'}})
        self.assertEqual(error.exception.code, 400)
        self.assertNotIn('secret-error', error.exception.read().decode())


class LaunchChoiceTests(unittest.TestCase):
    def test_default_terminal_and_explicit_web(self):
        import bootstrap
        self.assertEqual(bootstrap.choose_interface(ask=lambda _: ''), ['--menu'])
        self.assertEqual(bootstrap.choose_interface(ask=lambda _: '1'), ['--web'])
        answers = iter(['bad', '2'])
        self.assertEqual(bootstrap.choose_interface(ask=lambda _: next(answers)), ['--menu'])

    def test_cli_web_entry_does_not_force_open_with_no_open(self):
        from practice_autofill.cli import main
        with patch('practice_autofill.web.serve_console', return_value=0) as serve:
            self.assertEqual(main(['--web', '--no-open', '--web-port', '8787']), 0)
        self.assertEqual(serve.call_args.kwargs, {'port': 8787, 'open_page': False})
        self.assertTrue(serve.call_args.args[0].run.auto_fill)

    def test_cancelled_interface_selection_does_not_launch(self):
        import bootstrap
        self.assertIsNone(bootstrap.choose_interface(ask=lambda _: 'q'))
