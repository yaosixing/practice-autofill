"""Process an existing Chrome work page; automation requires explicit configuration."""
from collections import Counter

from .extraction import scan
from .logging_utils import event
from .trial import attached_work_page, is_work_page


def process_page(page, config, selectors_path, session):
    totals = Counter()
    while True:
        if not is_work_page(page):
            raise ValueError("work_page_changed")
        targets, skips = scan(page, config, selectors_path)
        reasons = sorted({skip.code for skip in skips})
        for reason in reasons:
            event("scan_skip", error=reason)
        session.say(f"识别到 {len(targets)} 题；跳过/暂停原因：" + ", ".join(reasons))
        if not session.prepare(targets):
            totals["declined"] += 1
            return dict(totals)
        seen = set()
        for target in targets:
            # Do not send another question after navigation outside the supported page.
            if not is_work_page(page):
                raise ValueError("work_page_changed")
            identity = (id(target.frame), target.identity, target.question.fingerprint)
            if identity in seen:
                continue
            seen.add(identity)
            result = session.process(target)
            totals[result] += 1
            # A failed write may be partial: stop this run and require manual inspection.
            if result in {"quit", "declined", "failed"}:
                return dict(totals)
        if session.ask("可人工翻页或处理访问限制。输入 r 重新扫描，其余退出：").strip().lower() != "r":
            return dict(totals)
        # New scan creates new targets. Cache/progress never replaces live state readback.


def run_cdp(config, selectors_path, session, endpoint="http://127.0.0.1:9527"):
    with attached_work_page(endpoint,config.browser.connect_timeout_seconds) as page:
        return process_page(page, config, selectors_path, session)
