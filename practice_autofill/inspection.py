"""Read-only structural inspection of the observed Chaoxing work layout.

Never launches a browser, asks the model, touches storage, or clicks controls.
Disconnecting Playwright must not close the user's attached Chrome.
"""
from collections import Counter
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from .extraction import scan, is_chaoxing
from .filling import resolve, FillError


def inspect_page(page, config, selectors_path):
    targets, skips = scan(page, config, selectors_path)
    rows = []
    for index, target in enumerate(targets, 1):
        row = {
            "index": index,
            "type": target.question.type,
            "option_ids": [o.id for o in target.question.options],
            "stem_nonempty": bool(target.question.stem.strip()),
            "resolved": False,
        }
        try:
            with resolve(target) as resolved:
                if resolved.state_reader is None:
                    raise ValueError("unsupported_inspection_layout")
                states = [
                    resolved.state_reader(control, kind)
                    for control, kind in zip(resolved.controls, resolved.kinds, strict=True)
                ]
                row["resolved"] = True
                row["selected_count"] = sum(states)
        except (FillError, ValueError):
            pass
        rows.append(row)
    resolved_count = sum(row["resolved"] for row in rows)
    return {
        "mode": "read_only",
        "ready": bool(targets) and resolved_count == len(targets),
        "question_count": len(targets),
        "types": dict(Counter(t.question.type for t in targets)),
        "resolved_count": resolved_count,
        "skips": dict(Counter(s.code for s in skips)),
        "questions": rows,
        "live_filling_verified": False,
    }


def validate_cdp_endpoint(endpoint):
    from .browser_selection import validate_endpoint
    validate_endpoint(endpoint)


def inspect_cdp(config, selectors_path, endpoint="http://127.0.0.1:9527"):
    validate_cdp_endpoint(endpoint)
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(endpoint, timeout=int(config.browser.connect_timeout_seconds*1000))
        except Exception:
            from .browser_selection import BrowserError
            raise BrowserError("connection_failed") from None
        pages = [
            page for context in browser.contexts for page in context.pages
            if is_chaoxing(urlsplit(page.url).hostname or "")
            and urlsplit(page.url).path == "/mooc-ans/mooc2/work/dowork"
        ]
        if len(pages) != 1:
            return {
                "mode": "read_only", "ready": False,
                "reason": "target_page_not_unique", "matched_pages": len(pages),
                "live_filling_verified": False,
            }
        # No browser.close(), context.close(), state snapshot, or cookie access here.
        return inspect_page(pages[0], config, selectors_path)
