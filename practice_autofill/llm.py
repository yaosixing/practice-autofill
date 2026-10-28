import json
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from .prompts import SYSTEM_PROMPT
from .validation import AnswerError, validate_answer
from .logging_utils import event, safe_metadata, safe_code


class ResponseError(AnswerError):
    def __init__(self, code, diagnostics):
        super().__init__(code)
        self.diagnostics=safe_metadata(diagnostics)


def transport_diagnostics(transport,error=None):
    # Diagnostics are optional: existing/custom/mock transports may expose no mapping.
    value=getattr(error,'diagnostics',None)
    if not isinstance(value,dict):
        value=getattr(transport,'last_diagnostic',{})
    return safe_metadata(value)


def diagnostic_text(code, details):
    code=safe_code(code)
    details=safe_metadata(details)
    labels={'context_limit':'接口报告上下文/请求大小超限',
            'incomplete_response':'响应未正常结束','invalid_batch_json':'批量 JSON 无效',
            'timeout':'请求超时','connection':'连接失败','rate_limited':'接口限流',
            'api_temporary':'接口暂时异常','empty_response':'没有响应选项'}
    parts=[f'原因={code}（{labels.get(code,"协议校验或接口异常")}）'] if code else []
    if 'finish_reason' in details:
        parts.append('finish_reason='+details['finish_reason'])
        if details['finish_reason']=='length':
            parts.append('接口报告达到输出上限或会话长度限制；请结合用量判断')
        elif details['finish_reason']=='insufficient_system_resource':
            parts.append('接口报告推理服务资源不足，中断不等于题目过长')
        elif details['finish_reason']=='aborted':
            parts.append('接口报告生成被中断，不等于输出预算不足')
    if 'completion_tokens' in details:
        parts.append(f'输出 tokens={details["completion_tokens"]}/{details.get("max_tokens","未知")}')
    if 'reasoning_tokens' in details:
        parts.append(f'其中推理 tokens={details["reasoning_tokens"]}')
    if 'elapsed_ms' in details:
        parts.append(f'耗时={details["elapsed_ms"]}ms')
    if 'request_id' in details:
        parts.append('请求='+details['request_id'][:12])
    return '；'.join(parts)

class ModelFailure(RuntimeError):
    pass

class FatalModelError(ModelFailure):
    pass

class Retryable(ModelFailure):
    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.retry_after=retry_after

def retry_after_seconds(value):
    if not value:
        return None
    try:
        return max(0,float(value))
    except ValueError:
        try:
            return max(0,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
        except (ValueError,TypeError,OverflowError):
            return None

class OpenAITransport:
    def __init__(self, config, api_key):
        from openai import OpenAI
        self.config=config
        self.last_diagnostic={}
        self.client=OpenAI(api_key=api_key,base_url=config.base_url,
                           timeout=config.timeout_seconds,max_retries=0)

    def __call__(self, payload):
        from openai import APITimeoutError, APIConnectionError, APIStatusError
        from .batching import BATCH_SYSTEM_PROMPT
        prompt=BATCH_SYSTEM_PROMPT if "questions" in payload else SYSTEM_PROMPT
        serialized=json.dumps(payload,ensure_ascii=False)
        started=time.monotonic()
        details={'request_id':uuid.uuid4().hex,'request_mode':'batch' if 'questions' in payload else 'single',
                 'question_count':len(payload['questions']) if 'questions' in payload else 1,
                 'payload_chars':len(serialized),'max_tokens':self.config.max_tokens}
        self.last_diagnostic=details
        event('model_request',**details)
        def record(code='',**extra):
            details.update(safe_metadata(extra))
            details['elapsed_ms']=int((time.monotonic()-started)*1000)
            level='error' if code in {'authentication_or_access','api_configuration'} else ('warning' if code else 'info')
            event('model_response',error=code,level=level,outcome='failed' if code else 'ok',**details)
        try:
            response=self.client.chat.completions.create(
                model=self.config.model,
                messages=[{"role":"system","content":prompt},
                          {"role":"user","content":serialized}],
                response_format={"type":"json_object"},
                max_tokens=self.config.max_tokens,
                stream=False)
        except APITimeoutError:
            record('timeout')
            raise Retryable("timeout") from None
        except APIConnectionError:
            record('connection')
            raise Retryable("connection") from None
        except APIStatusError as exc:
            status=exc.status_code
            details['http_status']=status
            if status in (401,403):
                record('authentication_or_access')
                raise FatalModelError("authentication_or_access") from None
            if status in (408,429) or status >= 500:
                record('rate_limited' if status==429 else 'api_temporary')
                delay=retry_after_seconds(exc.response.headers.get("retry-after"))
                raise Retryable("rate_limited" if status==429 else "api_temporary",delay) from None
            body=exc.body if isinstance(exc.body,dict) else {}
            error=body.get("error",body)
            code=error.get("code","") if isinstance(error,dict) else ""
            if "questions" in payload and (status==413 or (status==400 and code in {
                    "context_length_exceeded","context_window_exceeded","max_context_length_exceeded"})):
                record('context_limit')
                raise ResponseError('context_limit',details) from None
            record('api_configuration')
            raise FatalModelError("api_configuration") from None
        usage=response.usage
        if usage:
            for name in ('prompt_tokens','completion_tokens','total_tokens'):
                value=getattr(usage,name,None)
                if type(value) is int and value>=0: details[name]=value
            breakdown=getattr(usage,'completion_tokens_details',None)
            value=getattr(breakdown,'reasoning_tokens',None)
            if type(value) is int and value>=0: details['reasoning_tokens']=value
        if not response.choices:
            record('empty_response')
            raise ResponseError('empty_response',details)
        choice=response.choices[0]
        reason=choice.finish_reason
        details['finish_reason']=reason if reason in {'stop','length','content_filter','tool_calls','function_call',
            'insufficient_system_resource','aborted'} else ('missing' if reason is None else 'other')
        content=choice.message.content
        if isinstance(content,str): details['content_chars']=len(content)
        if choice.finish_reason != "stop":
            record('incomplete_response')
            raise ResponseError('incomplete_response',details)
        record()
        return content

    def close(self):
        self.client.close()

class MockTransport:
    """Fixture lookup is exclusively by content fingerprint; never infer unknown answers."""
    def __init__(self, path: Path):
        self.answers=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(self.answers,dict):
            raise ValueError("invalid_mock_file")
        self.calls=0

    def __call__(self,payload):
        from .models import digest
        self.calls+=1
        if "questions" in payload:
            return json.dumps({"answers":[dict(id=item["id"],**self.answers.get(
                digest({k:v for k,v in item.items() if k!="id"}),{"type":"unknown","answers":[]}))
                for item in payload["questions"]]},ensure_ascii=False)
        return json.dumps(self.answers.get(digest(payload),{"type":"unknown","answers":[]}),
                          ensure_ascii=False)

class Solver:
    def __init__(self,config,transport,*,sleep=time.sleep,clock=time.monotonic,say=lambda _:None):
        self.config=config
        self.transport=transport
        self.sleep=sleep
        self.clock=clock
        self.last_request=None
        self.say=say
        self.request_count=0

    def solve(self,question):
        for attempt in range(self.config.max_retries+1):
            if self.last_request is not None:
                wait=self.config.min_interval_seconds-(self.clock()-self.last_request)
                if wait > 0:
                    self.sleep(wait)
            self.last_request=self.clock()
            self.request_count+=1
            self.say(f'单题请求：尝试 {attempt+1}/{self.config.max_retries+1}')
            try:
                answer=validate_answer(self.transport(question.payload()),question)
                event('single_validated',attempt=attempt+1,unknown_count=int(answer.type=='unknown'),
                      **transport_diagnostics(self.transport))
                return answer
            except (Retryable,AnswerError) as exc:
                details=transport_diagnostics(self.transport,exc)
                self.say('单题失败：'+diagnostic_text(str(exc),details))
                event('single_failed',error=str(exc),level='warning',attempt=attempt+1,**details)
                if attempt >= self.config.max_retries:
                    self.say('单题重试耗尽，转人工。')
                    raise ModelFailure("answer_unavailable") from None
                delay=exc.retry_after if isinstance(exc,Retryable) else None
                delay=delay if delay is not None else min(2**attempt,8)
                self.say(f'单题重试等待 {delay:g}s。')
                event('single_retry',error=str(exc),level='warning',attempt=attempt+2,
                      retry_delay_ms=int(delay*1000),**details)
                self.sleep(delay)
        raise ModelFailure("answer_unavailable")
