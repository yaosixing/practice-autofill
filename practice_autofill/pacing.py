"""Deterministic page-operation pacing; independent of model request throttling."""
PRESETS = {'slow': (1.5, .35), 'normal': (0.0, 0.0), 'fast': (0.0, 0.0)}
LABELS = {'slow':'慢', 'normal':'正常', 'fast':'快', 'custom':'自定义'}


def effective_delays(config):
    if config.mode == 'custom':
        return config.question_delay_seconds, config.control_delay_seconds
    return PRESETS[config.mode]


class FillPacer:
    def __init__(self,config):
        self.config=config
        self.question_started=False
        self.control_started=False

    @staticmethod
    def wait(target,seconds):
        if seconds>0:
            # Keep Playwright's event loop alive; resolve the controls again after waiting.
            target.frame.page.wait_for_timeout(seconds*1000)

    def before_question(self,target):
        if self.question_started:
            self.wait(target,effective_delays(self.config)[0])
        self.question_started=True
        self.control_started=False

    def before_control(self,target):
        if self.control_started:
            self.wait(target,effective_delays(self.config)[1])
        self.control_started=True
