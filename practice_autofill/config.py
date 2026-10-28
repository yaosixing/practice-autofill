from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit
import tomllib
import math

ROOT = Path(__file__).resolve().parent.parent

@dataclass
class LLMConfig:
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    timeout_seconds: float = 60
    max_retries: int = 2
    min_interval_seconds: float = 2
    max_tokens: int = 2048

@dataclass
class RunConfig:
    auto_fill: bool = False
    confirm_external_data: bool = False
    clear_before_fill: bool = True
    load_timeout_seconds: float = 20
    stable_seconds: float = 1.5
    runtime_dir: str = "runtime"
    executable_path: str = ""
    channel: str = ""

@dataclass
class PacingConfig:
    mode: str = "normal"
    question_delay_seconds: float = .5
    control_delay_seconds: float = .1

@dataclass
class BrowserConfig:
    mode: str = "auto"
    kind: str = "auto"
    executable_path: str = ""
    cdp_url: str = ""
    connect_timeout_seconds: float = 10

@dataclass
class AnsweringConfig:
    mode: str = "fast"
    batch_size: int = 0
    max_batch_chars: int = 24000
    fallback_to_single: bool = True

@dataclass
class Selectors:
    container: str = "fieldset, [data-question]"
    stem: str = "legend, [data-stem]"
    control: str = ('input[type=radio], input[type=checkbox], [role=radio], [role=checkbox], '
                    'input:not([type]), input[type=text], textarea, [contenteditable=true], iframe[data-blank]')
    # Explicit site-specific blockers can be added; generic detection is deliberately limited.
    blockers: str = ('input[type=password], .g-recaptcha, iframe[src*="recaptcha"], '
                     'iframe[src*="hcaptcha"], [data-automation-blocked]')

@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    run: RunConfig = field(default_factory=RunConfig)
    selectors: Selectors = field(default_factory=Selectors)
    answering: AnsweringConfig = field(default_factory=AnsweringConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    pacing: PacingConfig = field(default_factory=PacingConfig)

    @property
    def runtime(self) -> Path:
        value=Path(self.run.runtime_dir)
        return value if value.is_absolute() else ROOT/value

def _section(cls, data):
    if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
        raise ValueError("invalid_config_fields")
    return cls(**data)

def load_config(path=None):
    data=tomllib.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    if set(data) - {"llm", "run", "selectors", "answering", "browser", "pacing"}:
        raise ValueError("invalid_config_sections")
    c=Config(_section(LLMConfig,data.get("llm",{})),_section(RunConfig,data.get("run",{})),
             _section(Selectors,data.get("selectors",{})),
             _section(AnsweringConfig,data.get("answering",{})),
             _section(BrowserConfig,data.get("browser",{})),
             _section(PacingConfig,data.get("pacing",{})))
    return validate_config(c)

def validate_config(c):
    if not isinstance(c.llm.base_url,str):
        raise ValueError("invalid_api_endpoint")
    u=urlsplit(c.llm.base_url)
    u.port  # Validate malformed and out-of-range ports before applying settings.
    if u.scheme not in {"http","https"} or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("invalid_api_endpoint")
    if u.scheme=="http" and u.hostname not in {"localhost","127.0.0.1","::1"}:
        raise ValueError("https_required_for_remote_endpoint")
    if not isinstance(c.llm.model,str) or not c.llm.model.strip():
        raise ValueError("invalid_model")
    for x in (c.llm.timeout_seconds,c.llm.min_interval_seconds,c.run.load_timeout_seconds,c.run.stable_seconds):
        if type(x) not in (int,float) or not math.isfinite(x) or x <= 0:
            raise ValueError("invalid_positive_interval")
    if type(c.llm.max_retries) is not int or not 0 <= c.llm.max_retries <= 5:
        raise ValueError("invalid_retry_count")
    if type(c.llm.max_tokens) is not int or c.llm.max_tokens < 64:
        raise ValueError("invalid_token_limit")
    if type(c.run.auto_fill) is not bool:
        raise ValueError("invalid_auto_fill")
    if type(c.run.clear_before_fill) is not bool:
        raise ValueError("invalid_clear_before_fill")
    if type(c.run.confirm_external_data) is not bool:
        raise ValueError("invalid_confirm_external_data")
    if any(not isinstance(getattr(c.selectors,f.name),str) or not getattr(c.selectors,f.name).strip()
           for f in fields(Selectors)):
        raise ValueError("empty_selector")
    if c.answering.mode not in {"fast","single"}:
        raise ValueError("invalid_answer_mode")
    if type(c.answering.batch_size) is not int or c.answering.batch_size < 0:
        raise ValueError("invalid_batch_size")
    if type(c.answering.max_batch_chars) is not int or c.answering.max_batch_chars < 256:
        raise ValueError("invalid_batch_budget")
    if type(c.answering.fallback_to_single) is not bool:
        raise ValueError("invalid_batch_fallback")
    if c.browser.mode not in {"auto","attach","launch"} or c.browser.kind not in {"auto","chrome","edge","chromium"}:
        raise ValueError("invalid_browser_kind_or_mode")
    value=c.browser.connect_timeout_seconds
    if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
        raise ValueError("invalid_browser_timeout")
    if not isinstance(c.browser.executable_path,str) or not isinstance(c.browser.cdp_url,str):
        raise ValueError("invalid_browser_fields")
    if c.browser.cdp_url:
        from .browser_selection import validate_endpoint
        validate_endpoint(c.browser.cdp_url)
    if ((c.browser.mode=="launch" and c.browser.cdp_url) or
            (c.browser.mode=="attach" and c.browser.executable_path) or
            (c.browser.cdp_url and c.browser.executable_path)):
        raise ValueError("conflicting_browser_settings")
    if not isinstance(c.pacing.mode,str) or c.pacing.mode not in {"slow","normal","fast","custom"}:
        raise ValueError("invalid_fill_speed")
    for value in (c.pacing.question_delay_seconds,c.pacing.control_delay_seconds):
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=30:
            raise ValueError("invalid_fill_delay")
    return c
