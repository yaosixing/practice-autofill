import unittest
from support import artifact_dir
from practice_autofill.logging_utils import LOGGER,configure_logging,event

class LoggingLifecycleTests(unittest.TestCase):
    def test_reconfiguration_closes_previous_file_handle(self):
        directory=artifact_dir()
        configure_logging(directory/'first.jsonl')
        old=LOGGER.handlers[0]
        configure_logging(directory/'second.jsonl')
        self.assertIsNone(old.stream)
        event('test_event')
        self.assertIn('test_event',(directory/'second.jsonl').read_text(encoding='utf-8'))
        for handler in list(LOGGER.handlers):
            LOGGER.removeHandler(handler);handler.close()
