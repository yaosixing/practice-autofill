"""Loopback-only, session-local web console. No additional dependencies."""
from copy import deepcopy
from dataclasses import asdict, fields
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone

from .config import ROOT, validate_config

ASSETS = Path(__file__).with_name('web_assets')
EDITABLE = {
    'llm': None, 'answering': None, 'browser': None, 'pacing': None,
    'run': {'auto_fill', 'confirm_external_data', 'clear_before_fill',
            'load_timeout_seconds', 'stable_seconds'},
    'selectors': None,
}


class Console:
    def __init__(self, config):
        self.config = deepcopy(config)
        self.initial_llm = deepcopy(config.llm)
        self.key = os.environ.get('DEEPSEEK_API_KEY', '')
        self.lock = threading.RLock()
        self.process = None
        self.thread = None
        self.running = False
        self.cancelling = False
        self.prompt = None
        self.events = []
        self.sequence = 0
        self.stage = 'idle'
        self.returncode = None
        self.counts = {'total': 0, 'verified': 0, 'manual': 0, 'skipped': 0, 'failed': 0}

    def clean(self, value):
        value = str(value)
        if self.key:
            value = value.replace(self.key, '[密钥已隐藏]')
        value = re.sub(r'(https?://[^\s?\x22]+)\?[^\s\x22]*', r'\1?[参数已隐藏]', value)
        return ''.join(c for c in value if c in '\n\t' or ord(c) >= 32 and not 127 <= ord(c) <= 159)

    def add_output(self, text, level='info'):
        with self.lock:
            self.sequence += 1
            self.events.append({'id': self.sequence, 'time':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                'text': self.clean(text)[:8000], 'level': level})
            self.events = self.events[-400:]

    def snapshot(self):
        with self.lock:
            return {'config': asdict(self.config), 'key_set': bool(self.key),
                    'running': self.running, 'cancelling': self.cancelling,
                    'stage': self.stage, 'returncode': self.returncode,
                    'prompt': deepcopy(self.prompt), 'events': list(self.events),
                    'counts': dict(self.counts)}

    def configure(self, payload):
        if not isinstance(payload, dict) or set(payload) - (set(EDITABLE) | {'key'}):
            raise ValueError('invalid_config')
        with self.lock:
            if self.running:
                raise ValueError('busy')
            draft = deepcopy(self.config)
            for section, changes in payload.items():
                if section == 'key':
                    continue
                target = getattr(draft, section)
                allowed = EDITABLE[section] or {f.name for f in fields(target)}
                if not isinstance(changes, dict) or set(changes) - allowed:
                    raise ValueError('invalid_fields')
                for name, value in changes.items():
                    setattr(target, name, value)
            key = payload.get('key', self.key)
            if (not isinstance(key, str) or len(key) > 4096 or
                    any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in key)):
                raise ValueError('invalid_key')
            validate_config(draft)
            self.config, self.key = draft, key

    def clear(self, confirmation):
        with self.lock:
            if self.running or confirmation != 'CLEAR':
                raise ValueError('clear_not_confirmed')
            self.config.llm = deepcopy(self.initial_llm)
            self.key = ''

    def start(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'action', 'question', 'selectors_path', 'url'}:
            raise ValueError('invalid_job')
        action = payload.get('action', 'run')
        if action not in {'run', 'inspect', 'trial', 'demo'}:
            raise ValueError('invalid_action')
        question = payload.get('question', 1)
        path = payload.get('selectors_path', '')
        url = payload.get('url', '')
        if type(question) is not int or question < 1 or not isinstance(path, str):
            raise ValueError('invalid_job')
        if not isinstance(url, str) or len(url) > 16000:
            raise ValueError('invalid_job')
        if url:
            from .browser import validate_url
            validate_url(url)
        with self.lock:
            if self.running:
                raise ValueError('busy')
            if action in {'run', 'trial'} and not self.key:
                raise ValueError('missing_key')
            config = deepcopy(self.config)
            arguments = ['--run']
            if action == 'inspect':
                arguments = ['--inspect-cdp']
            elif action == 'trial':
                arguments = ['--trial-cdp', '--question', str(question)]
                config.run.auto_fill = False
            elif action == 'demo':
                arguments = ['--run', '--mock']
                config.browser.mode = 'launch'
                config.browser.cdp_url = ''
                config.run.auto_fill = True
            if action in {'inspect', 'trial'}:
                config.browser.mode = 'attach'
                config.browser.executable_path = ''
            if path and action != 'demo':
                arguments += ['--chaoxing-selectors', path]
            validate_config(config)
            env = os.environ.copy()
            env.pop('DEEPSEEK_API_KEY', None)
            if self.key:
                env['DEEPSEEK_API_KEY'] = self.key
            runtime = config.runtime
            for name, folder in [('TEMP', 'tmp'), ('TMP', 'tmp'), ('TMPDIR', 'tmp'),
                                 ('PLAYWRIGHT_BROWSERS_PATH', 'browsers')]:
                env[name] = str(runtime / folder)
            (runtime / 'tmp').mkdir(parents=True, exist_ok=True)
            env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
            process = subprocess.Popen([sys.executable, '-B', '-u', '-m', 'practice_autofill.web_worker'],
                cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1)
            self.process = process
            self.running, self.cancelling = True, False
            self.prompt, self.returncode = None, None
            self.events = []
            self.stage = 'connecting'
            self.counts = dict.fromkeys(self.counts, 0)
            initial = {'config': asdict(config), 'arguments': arguments, 'url': url}
            try:
                process.stdin.write(json.dumps(initial, ensure_ascii=False) + '\n')
                process.stdin.flush()
            except OSError:
                self.running = False
                self.stage = 'failed'
                raise ValueError('worker_unavailable') from None
            self.thread = threading.Thread(target=self._pump, args=(process,), daemon=True)
            self.thread.start()

    def _pump(self, process):
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    kind = message.get('kind')
                    if kind == 'output':
                        level=message.get('level','info')
                        self.add_output(message.get('text', ''),level if level in {'info','warning'} else 'info')
                    elif kind == 'prompt':
                        self.prompt = {k: message[k] for k in ('id', 'text', 'sensitive')}
                        self.prompt['text'] = self.clean(self.prompt['text'])
                    elif kind == 'phase':
                        self.stage = message['stage']
                    elif kind == 'scan':
                        self.counts['total'] = message['count']
                    elif kind == 'result':
                        status = message['status']
                        if status in self.counts:
                            self.counts[status] += 1
            code = process.wait()
        except (OSError, ValueError, KeyError):
            code = 1
        finally:
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
        with self.lock:
            cancelled = self.cancelling
            self.returncode = code
            self.running = False
            self.cancelling = False
            self.prompt = None
            self.stage = 'cancelled' if cancelled or code == 130 else ('finished' if code == 0 else 'failed')
            self.add_output('操作已结束。工具未执行提交。' if code == 0 else
                            '操作已停止。已发生的填写不会自动撤销，请检查页面。',
                            'info' if code == 0 else 'warning')

    def reply(self, payload):
        with self.lock:
            if (not isinstance(payload, dict) or set(payload) != {'id', 'value'} or
                    not self.running or not self.prompt or payload['id'] != self.prompt['id'] or
                    not isinstance(payload['value'], str) or len(payload['value']) > 16000):
                raise ValueError('stale_prompt')
            self._send({'reply': payload['value'], 'id': payload['id']})
            self.prompt = None

    def _send(self, value):
        try:
            self.process.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
            self.process.stdin.flush()
        except (OSError, ValueError, AttributeError):
            raise ValueError('worker_unavailable') from None

    def stop(self):
        with self.lock:
            if self.running and not self.cancelling:
                self._send({'cancel': True})
                self.cancelling = True
                self.prompt = None
                self.add_output('正在安全停止；若模型请求正在等待，将在请求返回或超时后停止。')

    def close(self):
        self.stop()
        if self.thread:
            self.thread.join(timeout=2)


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, console):
        if address[0] != '127.0.0.1':
            raise ValueError('loopback_only')
        self.console = console
        self.cookie = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        super().__init__(address, Handler)

    @property
    def origin(self):
        return 'http://127.0.0.1:' + str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Do not persist HTTP paths, credentials or prompt replies.

    def respond(self, status, content, mime='application/json', cookie=False, fixture=False):
        raw = content if isinstance(content, bytes) else json.dumps(content, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if not fixture:
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', 'practice_console=' + self.server.cookie + '; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def trusted_host(self):
        return self.headers.get('Host') == self.server.origin.removeprefix('http://')

    def authenticated(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            value = cookie.get('practice_console')
            return value is not None and secrets.compare_digest(value.value, self.server.cookie)
        except Exception:
            return False

    def do_GET(self):
        if not self.trusted_host():
            return self.respond(403, {'error': '仅允许本机控制台访问。'})
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if self.path in assets:
            name, mime = assets[self.path]
            return self.respond(200, (ASSETS / name).read_bytes(), mime, cookie=self.path == '/')
        fixtures = {'/practice.html': 'practice.html', '/frame.html': 'frame.html',
                    '/frame.html?nested': 'frame.html', '/editor.html': 'editor.html'}
        if self.path in fixtures:
            return self.respond(200, (ROOT / 'fixtures' / fixtures[self.path]).read_bytes(), 'text/html; charset=utf-8', fixture=True)
        if not self.authenticated():
            return self.respond(403, {'error': '请从本机控制台首页重新进入。'})
        if self.path == '/api/state':
            value = self.server.console.snapshot()
            value['csrf'] = self.server.csrf
            value['demo_url'] = self.server.origin + '/practice.html'
            return self.respond(200, value)
        self.respond(404, {'error': '页面不存在。'})

    def do_POST(self):
        if (not self.trusted_host() or not self.authenticated() or
                self.headers.get('Origin') != self.server.origin or
                not secrets.compare_digest(self.headers.get('X-Console-Token', ''), self.server.csrf)):
            return self.respond(403, {'error': '请求未授权，请刷新本机控制台。'})
        try:
            if self.headers.get('Content-Type') != 'application/json':
                raise ValueError('json_required')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                raise ValueError('invalid_size')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('invalid_payload')
            console = self.server.console
            if self.path == '/api/config':
                console.configure(data)
            elif self.path == '/api/clear':
                console.clear(data.get('confirmation'))
            elif self.path == '/api/start':
                console.start(data)
            elif self.path == '/api/reply':
                console.reply(data)
            elif self.path == '/api/stop':
                console.stop()
            elif self.path == '/api/detect':
                from .cli import browser_report
                with console.lock:
                    report_config = deepcopy(console.config)
                return self.respond(200, {'ok': True, 'report': browser_report(report_config)})
            else:
                return self.respond(404, {'error': '操作不存在。'})
            self.respond(200, {'ok': True})
        except (ValueError, TypeError, OSError):
            messages = {
                'busy': '任务正在运行，请先停止后再修改配置或启动新任务。',
                'missing_key': '请先在 API 配置中填写密钥；本地演练不需要密钥。',
                'stale_prompt': '此交互已失效，请等待最新提示或刷新页面。',
                'clear_not_confirmed': '未清除：请停止任务并输入 CLEAR 确认。',
            }
            # Never echo submitted values or raw provider/browser exceptions.
            error = sys.exception()
            self.respond(400, {'error': messages.get(str(error), '操作未执行：请检查字段格式、数值范围或浏览器配置是否冲突。')})
        except Exception:
            self.respond(500, {'error': '操作失败，请检查运行环境；未输出敏感异常。'})


def serve_console(config, *, port=0, open_page=True):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('invalid_web_port')
    console = Console(config)
    with ConsoleServer(('127.0.0.1', port), console) as server:
        print('网页控制台：' + server.origin)
        print('仅在本机访问。配置与 Key 仅本次有效；Ctrl+C 关闭控制台。')
        print('终端菜单可使用 Start.cmd --menu 单独启动；两种入口的临时配置相互独立。')
        if open_page:
            if not webbrowser.open(server.origin):
                print('未能自动打开页面，请手动打开上方地址。')
        try:
            server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:
            print('\n正在停止网页任务；已发生的填写不会自动撤销。')
        finally:
            console.close()
    return 0
