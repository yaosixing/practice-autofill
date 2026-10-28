from support import preserved_test_dir
import unittest
from pathlib import Path
from practice_autofill.models import Question, Option
from practice_autofill.storage import Store, cache_key
from practice_autofill.validation import validate_answer

class StorageTests(unittest.TestCase):
    def test_cache_versions_and_progress(self):
        q = Question("one","single","Choose", (Option("A","yes"),),0)
        with preserved_test_dir() as d:
            key=cache_key(q,"https://api.deepseek.com","model")
            answer=validate_answer('{"type":"single","answers":["A"]}',q)
            path=Path(d)/"cache.sqlite"
            with Store(path) as s:
                s.put(key,q,answer)
                s.progress(q,"verified")
                self.assertEqual(s.get(key,q),answer)
                self.assertIsNone(s.get(cache_key(q,"https://api.deepseek.com","other"),q))
                self.assertNotEqual(key,cache_key(q,"https://api.deepseek.com","model",prompt_version="changed"))
                self.assertNotEqual(key,cache_key(q,"https://api.deepseek.com","model",protocol_version="changed"))
            with Store(path) as s:
                self.assertEqual(s.get(key,q),answer)
                self.assertEqual(s.status(q),"verified")
                s.db.execute("UPDATE answers SET answer='broken'")
                s.db.commit()
                self.assertIsNone(s.get(key,q))
