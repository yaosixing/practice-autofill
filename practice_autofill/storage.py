import sqlite3
from pathlib import Path
from .models import digest, Question, Answer
from .prompts import PROMPT_VERSION, PROTOCOL_VERSION
from .validation import validate_answer, AnswerError

def cache_key(q, endpoint, model, prompt_version=PROMPT_VERSION, protocol_version=PROTOCOL_VERSION):
    return digest([q.fingerprint, endpoint.rstrip("/"), model, prompt_version, protocol_version])

class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, question TEXT, answer TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS progress (fingerprint TEXT PRIMARY KEY, status TEXT)")
        self.db.commit()

    def get(self, key: str, q: Question):
        row = self.db.execute("SELECT answer FROM answers WHERE key=?", (key,)).fetchone()
        if row:
            try:
                return validate_answer(row[0], q)
            except AnswerError:
                pass
        return None

    def put(self, key: str, q: Question, answer: Answer):
        import json
        checked = validate_answer(answer.json(), q)
        if checked.type == "unknown":
            return
        self.db.execute("INSERT OR REPLACE INTO answers VALUES (?,?,?)",
                        (key, json.dumps(q.payload(), ensure_ascii=False), checked.json()))
        self.db.commit()

    def progress(self, q: Question, status: str):
        self.db.execute("INSERT OR REPLACE INTO progress VALUES (?,?)", (q.fingerprint, status))
        self.db.commit()

    def status(self, q: Question):
        row=self.db.execute("SELECT status FROM progress WHERE fingerprint=?", (q.fingerprint,)).fetchone()
        return row[0] if row else None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()
