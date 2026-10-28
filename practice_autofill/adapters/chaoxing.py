"""Choice-layout adapter based on read-only DOM and handler inspection.

Observed work/dowork ordinary choices and combined-material choice subquestions
are supported. Local fixture filling is tested separately from real-site
extraction; live filling is not implied.
"""
from dataclasses import asdict
from pathlib import Path
import re
import tomllib

from .base import AdapterNotConfigured
from .generic import GenericAdapter
from ..config import Selectors

EXTRACT_JS = Path(__file__).with_name("chaoxing.js").read_text(encoding="utf-8")
CHOICE_FIELDS = {
    "single_marker", "multiple_marker", "option_text",
    "single_selected_class", "multiple_selected_class",
    "single_handler", "multiple_handler",
}

READ_CHECKED_JS = """
(element, cfg) => {
    const markers=element.querySelectorAll(cfg.marker);
    if(markers.length!==1) return null;
    const marker=markers[0];
    if(marker.classList.contains(cfg.other)) return null;
    const selected=marker.classList.contains(cfg.selected);
    const aria=element.getAttribute('aria-checked');
    if(aria!==null && (!['true','false'].includes(aria) || (aria==='true')!==selected)) return null;
    return selected;
}
"""

class ChaoxingAdapter(GenericAdapter):
    def __init__(self, path):
        if not path:
            raise AdapterNotConfigured("chaoxing_selectors_required")
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("verified_locally") is not True:
            raise AdapterNotConfigured("chaoxing_selectors_not_verified")
        if set(data) != {"verified_locally", "selectors", "choices"}:
            raise AdapterNotConfigured("chaoxing_config_sections")
        selection = data.get("selectors", {})
        choices = data.get("choices", {})
        if (not isinstance(selection, dict) or
                set(selection) != {"container", "stem", "control", "blockers"} or
                not isinstance(choices, dict) or set(choices) != CHOICE_FIELDS):
            raise AdapterNotConfigured("chaoxing_selectors_incomplete")
        if any(not isinstance(v, str) or not v.strip() or "TODO" in v
               for v in (*selection.values(), *choices.values())):
            raise AdapterNotConfigured("chaoxing_selectors_incomplete")
        for name in ("single_selected_class", "multiple_selected_class"):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", choices[name]):
                raise AdapterNotConfigured("chaoxing_state_class_invalid")
        for name in ("single_handler", "multiple_handler"):
            if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", choices[name]):
                raise AdapterNotConfigured("chaoxing_handler_invalid")
        if choices["single_selected_class"] == choices["multiple_selected_class"]:
            raise AdapterNotConfigured("chaoxing_state_classes_ambiguous")
        super().__init__(Selectors(**selection))
        self.choices = choices

    def matches(self, hostname):
        return hostname == "chaoxing.com" or hostname.endswith(".chaoxing.com")

    def _snapshot(self, container):
        return container.evaluate(EXTRACT_JS, {
            "selectors": asdict(self.selectors), "choices": self.choices,
        })

    def resolve(self, target):
        resolved = super().resolve(target)
        resolved.state_reader = self.read_checked
        return resolved

    def read_checked(self, control, kind):
        if kind not in {"class_radio", "class_checkbox"}:
            raise ValueError("unsupported_choice_state")
        single = kind == "class_radio"
        prefix = "single" if single else "multiple"
        other = "multiple" if single else "single"
        value = control.evaluate(READ_CHECKED_JS, {
            "marker": self.choices[prefix + "_marker"],
            "selected": self.choices[prefix + "_selected_class"],
            "other": self.choices[other + "_selected_class"],
        })
        if type(value) is not bool:
            raise ValueError("ambiguous_checked_state")
        return value
