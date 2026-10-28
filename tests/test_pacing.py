import unittest
from unittest.mock import Mock
from practice_autofill.config import Config,validate_config
from practice_autofill.pacing import FillPacer,effective_delays

class PacingTests(unittest.TestCase):
    def test_presets_and_compatible_default(self):
        c=Config()
        self.assertEqual(c.pacing.mode,'normal')
        for mode,expected in [('slow',(1.5,.35)),('normal',(0,0)),('fast',(0,0))]:
            c.pacing.mode=mode
            self.assertEqual(effective_delays(c.pacing),expected)

    def test_custom_validation_rejects_nonfinite_negative_and_boolean(self):
        for value in [-1,float('nan'),float('inf'),True,31,'1']:
            c=Config();c.pacing.control_delay_seconds=value
            with self.subTest(value=value),self.assertRaises(ValueError): validate_config(c)

    def test_first_question_and_control_do_not_wait(self):
        c=Config();c.pacing.mode='custom';pacer=FillPacer(c.pacing)
        target=Mock();wait=target.frame.page.wait_for_timeout
        pacer.before_question(target);pacer.before_control(target)
        wait.assert_not_called()
        pacer.before_control(target)
        wait.assert_called_once_with(100)
        wait.reset_mock();pacer.before_question(target)
        wait.assert_called_once_with(500)
        wait.reset_mock();pacer.before_control(target)
        wait.assert_not_called()

    def test_fast_never_accesses_browser_wait(self):
        pacer=FillPacer(Config().pacing);target=Mock()
        for _ in range(3):
            pacer.before_question(target);pacer.before_control(target)
        target.frame.page.wait_for_timeout.assert_not_called()

    def test_cli_custom_delay_uses_selected_preset_as_base(self):
        from practice_autofill.cli import main
        from unittest.mock import patch
        c=Config()
        with patch('practice_autofill.cli.browser_report',return_value={}),patch('builtins.print'):
            self.assertEqual(main(['--detect-browsers','--fill-speed','slow','--control-delay','.2'],config_override=c),0)
        self.assertEqual(c.pacing.mode,'custom')
        self.assertEqual(effective_delays(c.pacing),(1.5,.2))

    def test_custom_toml_and_cli_invalid_delay(self):
        from support import artifact_dir
        from practice_autofill.config import load_config
        from practice_autofill.cli import main
        from unittest.mock import patch
        path=artifact_dir()/'pacing.toml'
        path.write_text('[pacing]\nmode="custom"\nquestion_delay_seconds=0.7\ncontrol_delay_seconds=0.2\n',encoding='utf-8')
        self.assertEqual(effective_delays(load_config(path).pacing),(.7,.2))
        with patch('builtins.print'):
            self.assertEqual(main(['--menu','--control-delay','nan']),2)
