from dataclasses import asdict
from pathlib import Path
from .base import Resolved
from ..models import Question, Option, Target, Skip, digest

EXTRACT_JS=(Path(__file__).with_name("extract.js")).read_text(encoding="utf-8")

class GenericAdapter:
    def __init__(self, selectors):
        self.selectors=selectors

    def matches(self,hostname):
        return True

    def _snapshot(self,container):
        return container.evaluate(EXTRACT_JS,asdict(self.selectors))

    def extract(self,frame,frame_chain=()):
        targets,skips=[],[]
        epoch=frame.evaluate("performance.timeOrigin")
        containers=frame.locator(self.selectors.container)
        for index in range(containers.count()):
            container=containers.nth(index)
            raw=self._snapshot(container)
            if "reason" in raw:
                if raw["reason"] != "invisible_container":
                    skips.append(Skip(raw["reason"]))
                continue
            kinds=raw["kinds"]
            for i,kind in enumerate(kinds):
                if kind=="frame_blank":
                    element=container.locator(self.selectors.control).nth(i).element_handle()
                    child=element.content_frame() if element else None
                    body=child.locator("body[contenteditable=true]") if child else None
                    if body is None or body.count()!=1 or not body.is_visible():
                        raw["reason"]="unsupported_iframe_editor"
                        break
                    raw["descriptors"][i]["epoch"]=child.evaluate("performance.timeOrigin")
            if "reason" in raw:
                skips.append(Skip(raw["reason"]))
                continue
            q=Question(raw["identity"],raw["type"],raw["stem"],
                       tuple(Option(o["id"],o["text"]) for o in raw["options"]),raw["blank_count"])
            if not q.question_id:
                q=Question(q.fingerprint[:16],q.type,q.stem,q.options,q.blank_count)
            targets.append(Target(q,frame,epoch,frame_chain,raw["identity"],
                                  digest(raw["descriptors"]),self))
        return targets,skips

    def resolve(self,target):
        if target.frame.is_detached() or target.frame.evaluate("performance.timeOrigin") != target.epoch:
            raise ValueError("document_changed")
        current,_=self.extract(target.frame,target.frame_chain)
        matches=[t for t in current if t.identity==target.identity and
                 t.question.fingerprint==target.question.fingerprint and t.signature==target.signature]
        # Duplicate identities are rejected even when only one duplicate kept its original text.
        if target.identity and sum(t.identity==target.identity for t in current)!=1:
            raise ValueError("ambiguous_identity")
        if len(matches)!=1:
            raise ValueError("question_changed_or_ambiguous")
        containers=target.frame.locator(self.selectors.container)
        candidates=[]
        for index in range(containers.count()):
            container=containers.nth(index)
            raw=self._snapshot(container)
            if "reason" in raw:
                continue
            if raw["identity"] != target.identity:
                continue
            q=Question("",raw["type"],raw["stem"],tuple(Option(o["id"],o["text"]) for o in raw["options"]),raw["blank_count"])
            if q.fingerprint==target.question.fingerprint:
                candidates.append((container,raw["kinds"]))
        if len(candidates)!=1:
            raise ValueError("ambiguous_container")
        container,kinds=candidates[0]
        controls=[]
        for i,kind in enumerate(kinds):
            control=container.locator(self.selectors.control).nth(i)
            if kind=="frame_blank":
                child=control.element_handle().content_frame()
                control=child.locator("body[contenteditable=true]")
            handle=control.element_handle()
            if handle is None:
                raise ValueError('control_detached')
            controls.append(handle)
        return Resolved(container,controls,kinds)
