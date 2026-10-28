from playwright.sync_api import Error as PlaywrightError
from .validation import validate_answer

class FillError(RuntimeError):
    pass

def resolve(target):
    try:
        # Do not fill after a login/challenge overlay appeared during model calls or preview.
        from .extraction import access_blocked
        if access_blocked(target.frame.page,target.adapter.selectors):
            raise ValueError("access_blocked")
        return target.adapter.resolve(target)
    except (PlaywrightError,ValueError,AttributeError,IndexError):
        raise FillError("question_changed_or_unavailable") from None

def _checked(control,kind,state_reader=None):
    if state_reader is not None:
        value=state_reader(control,kind)
        if type(value) is not bool:
            raise FillError("ambiguous_checked_state")
        return value
    if kind.startswith("aria_"):
        value=control.get_attribute("aria-checked")
        if value not in ("true","false"):
            raise FillError("ambiguous_checked_state")
        return value=="true"
    return control.is_checked()

def _value(control):
    if control.evaluate("(e)=>e.matches('input,textarea')"):
        return control.input_value()
    # Chromium leaves <br> (or empty paragraph wrappers) after clearing a rich editor.
    # Treat only structurally empty editor placeholders as empty; preserve meaningful newlines.
    if control.evaluate("e => e.textContent === '' && [...e.querySelectorAll('*')].every(n => ['BR','DIV','P'].includes(n.tagName))"):
        return ""
    return control.inner_text().replace("\r\n","\n")

def read_matches(target,answer):
    answer=validate_answer(answer.json(),target.question)
    if answer.type=="unknown":
        return False
    try:
        with resolve(target) as resolved:
            if answer.type=="blank":
                return all(_value(c)==value.replace("\r\n","\n")
                           for c,value in zip(resolved.controls,answer.answers,strict=True))
            desired=set(answer.answers)
            return all(_checked(c,k,resolved.state_reader)==(option.id in desired)
                       for c,k,option in zip(resolved.controls,resolved.kinds,target.question.options,strict=True))
    except (PlaywrightError, ValueError):
        raise FillError("readback_unavailable") from None

def _clear_existing(target,before_control):
    count = target.question.blank_count if target.question.type == "blank" else len(target.question.options)
    for index in range(count):
        before_control()
        with resolve(target) as resolved:
            control, kind = resolved.controls[index], resolved.kinds[index]
            if target.question.type == "blank":
                if _value(control) != "":
                    control.fill("", timeout=3000)
            elif _checked(control, kind, resolved.state_reader):
                if kind == "checkbox":
                    control.set_checked(False, timeout=3000)
                elif kind == "radio":
                    # Browsers do not expose a radio-uncheck gesture. Clear this bound
                    # native input and notify form listeners; do not reset the whole form.
                    control.evaluate("""e => {
                        if (!e.isConnected || !e.matches('input[type=radio]')) throw new Error('stale_radio');
                        const setter=Object.getOwnPropertyDescriptor(e.ownerDocument.defaultView.HTMLInputElement.prototype,'checked').set;
                        setter.call(e,false);
                        e.dispatchEvent(new Event('input',{bubbles:true}));
                        e.dispatchEvent(new Event('change',{bubbles:true}));
                    }""")
                else:
                    # Custom controls must actually support deselection through their UI.
                    # Never edit CSS/ARIA or a hidden field to fabricate an empty state.
                    control.click(timeout=3000)
    with resolve(target) as resolved:
        if target.question.type == "blank":
            cleared = all(_value(control) == "" for control in resolved.controls)
        else:
            cleared = all(not _checked(control, kind, resolved.state_reader)
                          for control, kind in zip(resolved.controls, resolved.kinds, strict=True))
        if not cleared:
            raise FillError("clear_readback_failed_partial_clear_possible")


def fill_answer(target,answer,*,clear_first=False,before_control=None):
    answer=validate_answer(answer.json(),target.question)
    if answer.type=="unknown":
        raise FillError("unknown_answer")
    pause=before_control or (lambda:None)
    try:
        if clear_first:
            _clear_existing(target,pause)
        # Resolve and validate before every control operation, then read all final states.
        if answer.type=="blank":
            for i,value in enumerate(answer.answers):
                pause()
                with resolve(target) as resolved:
                    resolved.controls[i].fill(value,timeout=3000)
        elif answer.type=="single":
            index=next(i for i,o in enumerate(target.question.options) if o.id==answer.answers[0])
            pause()
            with resolve(target) as resolved:
                control,kind=resolved.controls[index],resolved.kinds[index]
                if kind=="radio":
                    control.set_checked(True,timeout=3000)
                elif not _checked(control,kind,resolved.state_reader):
                    control.click(timeout=3000)
        else:
            desired=set(answer.answers)
            for i,option in enumerate(target.question.options):
                pause()
                with resolve(target) as resolved:
                    control,kind=resolved.controls[i],resolved.kinds[i]
                    checked=option.id in desired
                    if kind=="checkbox":
                        control.set_checked(checked,timeout=3000)
                    elif _checked(control,kind,resolved.state_reader)!=checked:
                        control.click(timeout=3000)
        if not read_matches(target,answer):
            raise FillError("readback_mismatch_partial_fill_possible")
    except (PlaywrightError, ValueError):
        raise FillError("control_failed_partial_fill_possible") from None
