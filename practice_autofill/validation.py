import json
from .models import Answer, Question

class AnswerError(ValueError):
    """Only fixed error codes; never include the raw model response."""

def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise AnswerError("duplicate_json_key")
        obj[key] = value
    return obj

def validate_answer(raw: str, question: Question) -> Answer:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 65536:
        raise AnswerError("empty_or_oversized_answer")
    try:
        obj = json.loads(raw, object_pairs_hook=_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(AnswerError("non_json_constant")))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise AnswerError("invalid_json") from None
    if not isinstance(obj, dict) or set(obj) != {"type", "answers"}:
        raise AnswerError("invalid_fields")
    kind, answers = obj["type"], obj["answers"]
    if not isinstance(kind, str) or not isinstance(answers, list):
        raise AnswerError("invalid_types")
    if any(not isinstance(a, str) or not a.strip() for a in answers):
        raise AnswerError("invalid_answer_item")
    if kind == "unknown":
        if answers:
            raise AnswerError("unknown_with_answers")
        return Answer(kind, ())
    if kind not in {"single", "multiple", "blank"} or kind != question.type:
        raise AnswerError("question_type_mismatch")
    if kind == "blank":
        if len(answers) != question.blank_count:
            raise AnswerError("blank_count_mismatch")
    else:
        legal = {o.id for o in question.options}
        if not answers or any(a not in legal for a in answers):
            raise AnswerError("illegal_option")
        if len(set(answers)) != len(answers):
            raise AnswerError("duplicate_option")
        if kind == "single" and len(answers) != 1:
            raise AnswerError("single_count_mismatch")
    return Answer(kind, tuple(answers))
