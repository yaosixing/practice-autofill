import os
import unittest
from unittest.mock import Mock, patch

from practice_autofill.config import Config
from practice_autofill.menu import TerminalMenu, load_menu_config


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("DEEPSEEK_API_KEY",None)
        self.addCleanup(self.env.stop)
        self.config=Config()
        self.messages=[]
        self.runner=Mock(return_value=0)

    def run_menu(self,answers,secrets=()):
        menu=TerminalMenu(self.config,self.runner,ask=Mock(side_effect=answers),
                          secret=Mock(side_effect=secrets),say=self.messages.append,color=False)
        self.assertEqual(menu.run(),0)
        return menu

    def test_first_key_save_is_masked_and_current_process_only(self):
        self.run_menu(["a","7","s","yes","m","q"],["TEST_PRIVATE_KEY"])
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"],"TEST_PRIVATE_KEY")
        self.assertNotIn("TEST_PRIVATE_KEY","\n".join(self.messages))
        self.assertTrue(any("当前进程" in line for line in self.messages))

    def test_modify_field_and_keep_other_values(self):
        self.run_menu(["a","2","custom-model","s","yes","m","q"])
        self.assertEqual(self.config.llm.model,"custom-model")
        self.assertEqual(self.config.llm.base_url,"https://api.deepseek.com")

    def test_cancel_draft_leaves_config_and_key_unchanged(self):
        os.environ["DEEPSEEK_API_KEY"]="ORIGINAL_PRIVATE_KEY"
        self.run_menu(["a","2","changed","7","b","q"],["b"])
        self.assertEqual(self.config.llm.model,"deepseek-flash")
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"],"ORIGINAL_PRIVATE_KEY")

    def test_invalid_menu_and_numeric_value_reprompt(self):
        self.run_menu(["wrong","a","3","nan","3","0","b","q"])
        self.assertEqual(self.config.llm.timeout_seconds,60)
        self.assertTrue(any("输入无效" in line for line in self.messages))
        self.assertTrue(any("参数无效" in line for line in self.messages))

    def test_cancel_save_defaults_to_no(self):
        self.run_menu(["a","2","changed","s","","b","q"])
        self.assertEqual(self.config.llm.model,"deepseek-flash")

    def test_clear_defaults_to_cancel(self):
        os.environ["DEEPSEEK_API_KEY"]="KEEP_PRIVATE_KEY"
        self.run_menu(["c","","q"])
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"],"KEEP_PRIVATE_KEY")

    def test_clear_resets_only_menu_api_parameters_and_key(self):
        os.environ["DEEPSEEK_API_KEY"]="REMOVE_PRIVATE_KEY"
        original_run=self.config.run
        self.run_menu(["a","2","changed","s","yes","m","c","CLEAR","m","q"])
        self.assertEqual(self.config.llm.model,"deepseek-flash")
        self.assertNotIn("DEEPSEEK_API_KEY",os.environ)
        self.assertIs(self.config.run,original_run)

    def test_operation_without_key_stays_in_menu(self):
        self.run_menu(["1","m","q"])
        self.runner.assert_not_called()
        self.assertTrue(any("未设置" in line for line in self.messages))

    def test_failure_and_cancel_return_to_menu(self):
        self.runner.side_effect=[RuntimeError("PRIVATE_EXCEPTION"),KeyboardInterrupt()]
        self.run_menu(["2","m","2","m","q"])
        self.assertEqual(self.runner.call_count,2)
        self.assertNotIn("PRIVATE_EXCEPTION","\n".join(self.messages))

    def test_formal_operation_keeps_explicit_auto_fill(self):
        os.environ["DEEPSEEK_API_KEY"]="TEST_PRIVATE_KEY"
        self.run_menu(["1","m","q"])
        self.assertEqual(self.runner.call_args.args[1],["--run","--auto-fill"])

    def test_more_menu_back_and_invalid_question(self):
        self.run_menu(["5","1","0","b","b","q"])
        self.runner.assert_not_called()

    def test_eof_exits(self):
        menu=TerminalMenu(self.config,self.runner,ask=Mock(side_effect=EOFError()),say=self.messages.append)
        self.assertEqual(menu.run(),0)

    def test_missing_config_can_retry_or_use_defaults_explicitly(self):
        with patch("practice_autofill.menu.load_config",side_effect=[OSError(),Config()]):
            config=load_menu_config("missing",ask=Mock(side_effect=["bad","r"]),say=self.messages.append)
        self.assertIsInstance(config,Config)
        with patch("practice_autofill.menu.load_config",side_effect=OSError()):
            config=load_menu_config("missing",ask=Mock(side_effect=["d"]),say=self.messages.append)
        self.assertIsInstance(config,Config)

    def test_hidden_input_refuses_echo_fallback(self):
        import getpass
        import warnings
        from practice_autofill.menu import hidden_input
        def insecure(prompt):
            warnings.warn("cannot_hide",getpass.GetPassWarning)
            return "MUST_NOT_BE_READ"
        with patch("practice_autofill.menu.getpass.getpass",side_effect=insecure):
            with self.assertRaises(getpass.GetPassWarning):
                hidden_input("Key > ")

    def test_ctrl_c_abandons_draft_and_returns_main(self):
        menu=TerminalMenu(self.config,self.runner,ask=Mock(side_effect=["a","2","changed",KeyboardInterrupt(),"q"]),
                          say=self.messages.append,color=False)
        self.assertEqual(menu.run(),0)
        self.assertEqual(self.config.llm.model,"deepseek-flash")
        self.assertTrue(any("已取消" in line for line in self.messages))

    def test_config_can_restore_original_non_default_model(self):
        self.config.llm.model="launch-model"
        self.run_menu(["a","2","temporary-model","s","yes","m","c","CLEAR","m","q"])
        self.assertEqual(self.config.llm.model,"launch-model")

    def test_page_selection_reprompts_and_allows_cancel(self):
        from types import SimpleNamespace
        from practice_autofill.cli import select_page, OperationCancelled
        pages=[Mock(),Mock()]
        for page in pages:
            page.is_closed.return_value=False
            page.title.return_value="title"
        context=SimpleNamespace(pages=pages)
        with patch("builtins.input",side_effect=["bad","3","2"]), patch("builtins.print"):
            self.assertIs(select_page(context,pages[0]),pages[1])
        with patch("builtins.input",return_value="b"),patch("builtins.print"):
            with self.assertRaises(OperationCancelled):
                select_page(context,pages[0])

    def test_url_invalid_retry_and_cancel(self):
        from practice_autofill.cli import prompt_url, OperationCancelled
        say=Mock()
        reader=Mock(side_effect=["bad","https://example.com/practice"])
        self.assertEqual(prompt_url(reader=reader,say=say),"https://example.com/practice")
        self.assertEqual(reader.call_count,2)
        with self.assertRaises(OperationCancelled):
            prompt_url(reader=Mock(return_value="m"),say=say)

    def test_malformed_api_address_is_validation_error(self):
        from dataclasses import replace
        from practice_autofill.config import validate_config
        for address in (123, "http://localhost:invalid", "http://localhost:99999"):
            with self.subTest(address=address):
                config=Config()
                config.llm=replace(config.llm,base_url=address)
                with self.assertRaises(ValueError):
                    validate_config(config)
    def test_answer_mode_menu_save_and_validation(self):
        self.run_menu(['6','2','-1','2','4','1','single','s','m','q'])
        self.assertEqual(self.config.answering.mode,'single')
        self.assertEqual(self.config.answering.batch_size,4)
        self.assertTrue(any('参数无效' in line for line in self.messages))
    def test_browser_menu_detects_then_saves_current_process_only(self):
        with patch('practice_autofill.cli.browser_report',return_value={'cdp_status':'unavailable','browsers':[]}):
            self.run_menu(['7','1','launch','2','edge','s','m','q'])
        self.assertEqual(self.config.browser.mode,'launch')
        self.assertEqual(self.config.browser.kind,'edge')

    def test_browser_draft_cancel_and_clear_does_not_reset_answering(self):
        self.config.answering.batch_size=5
        with patch('practice_autofill.cli.browser_report',return_value={'cdp_status':'unavailable','browsers':[]}):
            self.run_menu(['7','2','chrome','b','c','CLEAR','m','q'])
        self.assertEqual(self.config.browser.kind,'auto')
        self.assertEqual(self.config.answering.batch_size,5)
    def test_more_independent_actions_force_launch_and_clear_attached_endpoint(self):
        os.environ['DEEPSEEK_API_KEY']='TEST_PRIVATE_KEY'
        self.config.browser.mode='attach'
        self.config.browser.cdp_url='http://localhost:9222'
        self.run_menu(['5','3','b','4','m','q'])
        commands=[call.args[1] for call in self.runner.call_args_list]
        self.assertEqual(commands[0],['--mock','--browser-mode','launch','--cdp-url',''])
        self.assertEqual(commands[1],['--auto-fill','--browser-mode','launch','--cdp-url',''])
        self.assertEqual(self.config.browser.mode,'attach')

    def test_read_only_action_uses_attach_without_mutating_launch_preferences(self):
        self.config.browser.mode='launch'
        self.config.browser.executable_path='E:/browser/chrome.exe'
        self.run_menu(['2','m','q'])
        copied=self.runner.call_args.args[0]
        self.assertEqual(copied.browser.mode,'attach')
        self.assertEqual(copied.browser.executable_path,'')
        self.assertEqual(self.config.browser.mode,'launch')
        self.assertEqual(self.config.browser.executable_path,'E:/browser/chrome.exe')
    def test_pacing_presets_save_and_cancel(self):
        from practice_autofill.pacing import effective_delays
        self.run_menu(['8','1','慢','s','m','8','1','快','b','q'])
        self.assertEqual(effective_delays(self.config.pacing),(1.5,.35))

    def test_custom_pacing_validation_and_api_clear_scope(self):
        self.run_menu(['8','1','4','2','nan','2','0.7','3','0','s','m','c','CLEAR','m','q'])
        self.assertEqual(self.config.pacing.mode,'custom')
        self.assertEqual(self.config.pacing.question_delay_seconds,.7)
        self.assertEqual(self.config.pacing.control_delay_seconds,0)
        self.assertTrue(any('参数无效' in line for line in self.messages))
