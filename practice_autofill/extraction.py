import time
from urllib.parse import urlsplit
from playwright.sync_api import Error as PlaywrightError
from .adapters.base import AdapterNotConfigured
from .adapters.generic import GenericAdapter
from .adapters.chaoxing import ChaoxingAdapter
from .models import Skip

def is_chaoxing(host):
    return host=="chaoxing.com" or host.endswith(".chaoxing.com")

def frame_chain(frame):
    chain=[]
    while frame.parent_frame is not None:
        parent=frame.parent_frame
        chain.append(parent.child_frames.index(frame))
        frame=parent
    return tuple(reversed(chain))

def access_blocked(page, selectors):
    for frame in list(page.frames):
        try:
            blockers=frame.locator(selectors.blockers)
            if any(blockers.nth(i).is_visible() for i in range(blockers.count())):
                return True
        except PlaywrightError:
            # An unreadable/navigating frame is not proof the page is clear.
            return True
    return False

def scan(page,config,chaoxing_selectors=None):
    generic=GenericAdapter(config.selectors)
    top_chaoxing=is_chaoxing(urlsplit(page.url).hostname or "")
    adapter=ChaoxingAdapter(chaoxing_selectors) if top_chaoxing else generic
    start=time.monotonic()
    stable_at=start
    previous=None
    latest=([],[])
    while time.monotonic()-start < config.run.load_timeout_seconds:
        targets,skips=[],[]
        try:
            # Site-specific blockers are used for all frames when a site adapter is active.
            if access_blocked(page,adapter.selectors):
                return [],[Skip("manual_access_required")]
            for frame in list(page.frames):
                chosen=adapter
                if not top_chaoxing and is_chaoxing(urlsplit(frame.url).hostname or ""):
                    chosen=ChaoxingAdapter(chaoxing_selectors)
                    if access_blocked(page,chosen.selectors):
                        return [],[Skip("manual_access_required")]
                ts,ss=chosen.extract(frame,frame_chain(frame))
                targets.extend(ts);skips.extend(ss)
        except AdapterNotConfigured:
            raise
        except (PlaywrightError,ValueError):
            page.wait_for_timeout(150)
            stable_at=time.monotonic()
            continue
        snapshot=tuple((t.frame_chain,t.epoch,t.identity,t.question.fingerprint,t.signature) for t in targets)
        if snapshot!=previous:
            previous=snapshot;stable_at=time.monotonic()
        latest=(targets,skips)
        if targets and time.monotonic()-stable_at >= config.run.stable_seconds:
            return latest
        page.wait_for_timeout(100)
    targets,skips=latest
    # An unstable snapshot must not be returned for filling.
    if not targets or time.monotonic()-stable_at < config.run.stable_seconds:
        return [],skips+[Skip("questions_not_ready")]
    return latest
