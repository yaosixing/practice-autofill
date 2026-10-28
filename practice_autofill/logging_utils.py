import json
import logging
import re
import uuid
from datetime import datetime, timezone

LOGGER = logging.getLogger("practice_autofill")
LOGGER.addHandler(logging.NullHandler())
RUN_ID = uuid.uuid4().hex
NUMERIC_FIELDS = {'attempt','attempt_limit','depth','question_count','payload_chars','max_tokens',
                  'prompt_tokens','completion_tokens','reasoning_tokens','total_tokens','content_chars',
                  'elapsed_ms','http_status','accepted_count','pending_count','unknown_count',
                  'request_count','split_count','fallback_count','retry_delay_ms'}
CODE_FIELDS = {'request_id','batch_id','parent_batch_id','request_mode','finish_reason','reason','outcome'}


def safe_code(value):
    return value if isinstance(value,str) and re.fullmatch(r'[a-z0-9_]{0,64}',value) else 'redacted'


def safe_metadata(values):
    if not isinstance(values,dict):
        return {}
    result={}
    for key,value in values.items():
        if key in NUMERIC_FIELDS and type(value) is int and 0<=value<=10**15:
            result[key]=value
        elif key in CODE_FIELDS:
            result[key]=safe_code(value)
    return result

def configure_logging(path):
    global RUN_ID
    RUN_ID = uuid.uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    handler=logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    for previous in list(LOGGER.handlers):
        LOGGER.removeHandler(previous)
        previous.close()
    LOGGER.addHandler(handler)
    event('run_started')
    return RUN_ID

def event(code, fingerprint="", error="", *, level='info', **metadata):
    # No arbitrary strings, response bodies, question contents, endpoints or credentials.
    level=level if level in {'info','warning','error'} else 'info'
    value={'time':datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
           'run_id':RUN_ID,'level':level,'event':safe_code(code),
           'question':safe_code(fingerprint),'error':safe_code(error)}
    value.update(safe_metadata(metadata))
    LOGGER.log({'info':logging.INFO,'warning':logging.WARNING,'error':logging.ERROR}[level],
               json.dumps(value,ensure_ascii=False))
