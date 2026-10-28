"""Private JSON-lines bridge to the unchanged CLI execution flow."""
import builtins
import json
import queue
import sys
import threading
import uuid


def run():
    wire = sys.stdout
    incoming = sys.stdin
    write_lock = threading.Lock()
    cancelled = threading.Event()
    replies = queue.Queue()

    def emit(kind, **value):
        with write_lock:
            wire.write(json.dumps(dict(kind=kind, **value), ensure_ascii=False) + '\n')
            wire.flush()

    initial = json.loads(incoming.readline())

    def receive():
        try:
            for line in incoming:
                value = json.loads(line)
                if value.get('cancel'):
                    cancelled.set()
                    replies.put(None)
                else:
                    replies.put(value)
        finally:
            cancelled.set()
            replies.put(None)

    threading.Thread(target=receive, daemon=True).start()

    def check():
        if cancelled.is_set():
            raise KeyboardInterrupt()

    def ask(text=''):
        check()
        if '完整答题 URL' in text and initial.get('url'):
            return initial.pop('url')
        identity = uuid.uuid4().hex
        emit('prompt', id=identity, text=text, sensitive='URL' in text)
        while True:
            reply = replies.get()
            check()
            if reply and reply.get('id') == identity:
                return reply['reply']

    class Output:
        encoding = 'utf-8'

        def __init__(self):
            self.buffer = ''

        def write(self, text):
            self.buffer += text
            while '\n' in self.buffer:
                line, self.buffer = self.buffer.split('\n', 1)
                if line.strip():
                    warning=line.startswith(('批量失败','单题失败','拆分失败','该题回退','网络/限流','单题重试耗尽'))
                    emit('output', text=line, level='warning' if warning else 'info')
            return len(text)

        def flush(self):
            if self.buffer:
                emit('output', text=self.buffer)
                self.buffer = ''

        def isatty(self):
            return False

    builtins.input = ask
    sys.stdout = Output()
    # Import after replacing input: Session default arguments bind here.
    from . import cli, menu, filling
    from .config import _section, Config, LLMConfig, RunConfig, Selectors, AnsweringConfig, BrowserConfig, PacingConfig, validate_config
    menu.hidden_input = ask
    sections = initial['config']
    config = validate_config(Config(*[_section(cls, sections[name]) for cls, name in [
        (LLMConfig, 'llm'), (RunConfig, 'run'), (Selectors, 'selectors'),
        (AnsweringConfig, 'answering'), (BrowserConfig, 'browser'), (PacingConfig, 'pacing')]]))
    original_resolve = filling.resolve

    def resolve(target):
        check()
        return original_resolve(target)

    filling.resolve = resolve
    original_scan = cli.scan

    def scan(*args, **kwargs):
        check()
        emit('phase', stage='scanning')
        result = original_scan(*args, **kwargs)
        emit('scan', count=len(result[0]))
        return result

    cli.scan = scan
    original_prepare = cli.Session.prepare
    original_process = cli.Session.process

    def prepare(self, targets):
        check()
        emit('phase', stage='solving')
        return original_prepare(self, targets)

    def process(self, target):
        check()
        emit('phase', stage='filling')
        result = original_process(self, target)
        emit('result', status=result)
        return result

    cli.Session.prepare = prepare
    cli.Session.process = process
    try:
        code = cli.main(initial['arguments'], config_override=config) or 0
        return 130 if cancelled.is_set() else code
    except (KeyboardInterrupt, EOFError):
        return 130
    except Exception:
        emit('output', text='任务异常停止。请检查环境与页面状态；未输出原始敏感异常。')
        return 1
    finally:
        sys.stdout.flush()


if __name__ == '__main__':
    raise SystemExit(run())
