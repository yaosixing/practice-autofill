from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

def artifact_dir():
    path=Path(__file__).resolve().parent.parent/"runtime"/"test-artifacts"/uuid4().hex
    path.mkdir(parents=True,exist_ok=True)
    return path

@contextmanager
def preserved_test_dir():
    # Preserve artifacts for human review; never auto-delete task output.
    yield artifact_dir()
