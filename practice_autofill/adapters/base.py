from dataclasses import dataclass
from typing import Protocol, Any, Callable

class AdapterNotConfigured(ValueError):
    pass

@dataclass
class Resolved:
    container: Any
    controls: list[Any]
    kinds: list[str]
    state_reader: Callable[[Any, str], bool] | None = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        from playwright.sync_api import Error
        for handle in self.controls:
            try:
                handle.dispose()
            except Error:
                pass

class Adapter(Protocol):
    def matches(self, hostname: str) -> bool: ...
    def extract(self, frame, frame_chain=()) -> tuple[list, list]: ...
    def resolve(self, target) -> Resolved: ...
