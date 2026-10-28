from contextlib import contextmanager
import os
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

def validate_url(url):
    try:
        parsed=urlsplit(url)
        if parsed.scheme not in ("http","https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("invalid_page_url")
        parsed.port
    except (ValueError,TypeError):
        raise ValueError("invalid_page_url") from None
    return url

def runtime_environment(runtime: Path):
    runtime.mkdir(parents=True,exist_ok=True)
    tmp=runtime/"tmp";tmp.mkdir(exist_ok=True)
    os.environ["TEMP"]=str(tmp)
    os.environ["TMP"]=str(tmp)
    os.environ["TMPDIR"]=str(tmp)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",str(runtime/"browsers"))

@contextmanager
def launch_browser(config, choice=None):
    runtime_environment(config.runtime)
    from .browser_selection import choose_browser, BrowserError
    legacy = choice is None and config.browser.mode=="auto" and config.browser.kind=="auto" and not config.browser.executable_path and not config.browser.cdp_url
    if choice is None and not legacy:
        choice=choose_browser(config,force_launch=True)
    with sync_playwright() as p:
        opts={"headless":False,"accept_downloads":False}
        if choice is not None and choice.executable_path:
            opts["executable_path"]=choice.executable_path
        elif choice is not None and choice.channel:
            opts["channel"]=choice.channel
        elif config.run.executable_path and legacy:
            opts["executable_path"]=config.run.executable_path
        elif config.run.channel and legacy:
            opts["channel"]=config.run.channel
        if legacy:
            profile=config.runtime/"browser-profile"
            state_path=config.runtime/"storage-state.json"
        else:
            import hashlib
            identity=hashlib.sha256((choice.executable_path or choice.channel or choice.kind).casefold().encode()).hexdigest()[:12]
            profile=config.runtime/"browser-profiles"/(choice.kind+"-"+identity)
            state_path=profile/"practice-storage-state.json"
        try:
            context=p.chromium.launch_persistent_context(str(profile),**opts)
        except Exception:
            raise BrowserError("launch_failed") from None
        try:
            # Chromium profile alone may drop session cookies at shutdown.
            # Origins/localStorage remain in the dedicated profile; restore cookies before navigation.
            if state_path.exists():
                saved=json.loads(state_path.read_text(encoding="utf-8"))
                context.add_cookies(saved["cookies"])
            context.set_default_timeout(5000)
            page=context.pages[0] if context.pages else context.new_page()
            try:
                yield context,page
            finally:
                from playwright.sync_api import Error
                try:
                    state=context.storage_state()
                    next_path=state_path.with_suffix(".next.json")
                    next_path.write_text(json.dumps(state,ensure_ascii=False),encoding="utf-8")
                    next_path.replace(state_path)
                except Error:
                    # If the user already closed the browser, preserve the previous snapshot.
                    from .logging_utils import event
                    event("session_snapshot_unavailable")
        finally:
            context.close()


@contextmanager
def open_browser(config,choice):
    """Attach disconnects only; launch owns and closes only its dedicated instance."""
    if choice.mode=="launch":
        with launch_browser(config,choice) as pair:
            yield pair
        return
    from .browser_selection import BrowserError,validate_endpoint
    from types import SimpleNamespace
    validate_endpoint(choice.endpoint)
    with sync_playwright() as p:
        try:
            browser=p.chromium.connect_over_cdp(choice.endpoint,timeout=int(config.browser.connect_timeout_seconds*1000))
        except Exception:
            raise BrowserError("connection_failed") from None
        pages=[page for context in browser.contexts for page in context.pages if not page.is_closed()]
        if not pages:
            raise BrowserError("connection_failed")
        # Expose pages across contexts without touching contexts, cookies or storage state.
        yield SimpleNamespace(pages=pages),pages[0]
