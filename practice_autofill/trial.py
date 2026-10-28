"""One-question trial in an attached Chrome; never closes the user browser."""
from contextlib import contextmanager
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from .extraction import scan, is_chaoxing
from .inspection import validate_cdp_endpoint


def trial_page(page, config, selectors_path, session, question_index=1):
    # Enforce confirmation even when a config file enables normal automatic filling.
    if config.run.auto_fill or session.config.run.auto_fill:
        raise ValueError("trial_requires_manual_confirmation")
    if type(question_index) is not int or question_index < 1:
        raise ValueError("invalid_question_index")
    targets, skips = scan(page, config, selectors_path)
    if question_index > len(targets):
        raise ValueError("question_index_out_of_range")
    target = targets[question_index - 1]
    session.say(f"本次仅处理已识别题目中的第 {question_index} 题，共识别 {len(targets)} 题。")
    session.say("编号按可识别题目的页面顺序计算；请核对随后显示的题干，不符时跳过。")
    # Show precisely what may leave the machine before Session asks for consent.
    from .cli import terminal_text
    session.say(terminal_text(target.question.stem))
    for option in target.question.options:
        session.say(terminal_text(option.id + ": " + option.text))
    return session.process(target)


def is_work_page(page):
    parsed = urlsplit(page.url)
    return (parsed.scheme in {"http", "https"}
            and is_chaoxing(parsed.hostname or "")
            and parsed.path == "/mooc-ans/mooc2/work/dowork")


@contextmanager
def attached_work_page(endpoint, timeout_seconds=10):
    validate_cdp_endpoint(endpoint)
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(endpoint, timeout=int(timeout_seconds*1000))
        except Exception:
            from .browser_selection import BrowserError
            raise BrowserError("connection_failed") from None
        pages = [page for context in browser.contexts for page in context.pages if is_work_page(page)]
        if len(pages) != 1:
            raise ValueError("target_page_not_unique")
        # Disconnect only; never close Chrome, snapshot credentials, or open a new tab.
        yield pages[0]


def trial_cdp(config, selectors_path, session, endpoint="http://127.0.0.1:9527", question_index=1):
    validate_cdp_endpoint(endpoint)
    if config.run.auto_fill or session.config.run.auto_fill:
        raise ValueError("trial_requires_manual_confirmation")
    with attached_work_page(endpoint,config.browser.connect_timeout_seconds) as page:
        return trial_page(page, config, selectors_path, session, question_index)
