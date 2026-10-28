from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()

@dataclass(frozen=True)
class Option:
    id: str
    text: str

@dataclass(frozen=True)
class Question:
    question_id: str
    type: str
    stem: str
    options: tuple[Option, ...]
    blank_count: int
    has_images: bool = False

    def payload(self) -> dict:
        return {"type": self.type, "stem": self.stem,
                "options": [{"id": o.id, "text": o.text} for o in self.options],
                "blank_count": self.blank_count}

    @property
    def fingerprint(self) -> str:
        return digest(self.payload())

@dataclass(frozen=True)
class Answer:
    type: str
    answers: tuple[str, ...]

    def json(self) -> str:
        return json.dumps({"type": self.type, "answers": list(self.answers)}, ensure_ascii=False)

@dataclass
class Target:
    question: Question
    frame: Any = field(repr=False)
    epoch: float
    frame_chain: tuple[int, ...]
    identity: str
    signature: str
    adapter: Any = field(repr=False)

@dataclass(frozen=True)
class Skip:
    code: str
    anonymous_id: str = ""
