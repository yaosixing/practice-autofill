import unittest
from pathlib import Path
from unittest.mock import Mock
import bootstrap

class BootstrapTests(unittest.TestCase):
    def test_refused_install_has_no_side_effect(self):
        run=Mock()
        self.assertFalse(bootstrap.confirmed_run(['python','-m','pip','install'], '安装依赖', ask=lambda _: '', run=run))
        run.assert_not_called()

    def test_confirmed_install_executes_argv(self):
        run=Mock(return_value=Mock(returncode=0))
        self.assertTrue(bootstrap.confirmed_run(['python with spaces','-m','pip'], '安装依赖', ask=lambda _: 'y', run=run))
        self.assertEqual(run.call_args.args[0][0], 'python with spaces')

    def test_validate_minimum_python(self):
        self.assertFalse(bootstrap.version_supported((3,10)))
        self.assertTrue(bootstrap.version_supported((3,11)))

    def test_runtime_paths_project_local(self):
        env=bootstrap.environment(Path('E:/project with spaces'))
        for name in ('TEMP','TMP','TMPDIR','PIP_CACHE_DIR','PLAYWRIGHT_BROWSERS_PATH'):
            self.assertIn('project with spaces',env[name])


class BootstrapFailureTests(unittest.TestCase):
    def run_checked(self,results):
        from support import artifact_dir
        from unittest.mock import patch
        with patch.object(bootstrap,'ROOT',artifact_dir()),patch.object(bootstrap.subprocess,'run',side_effect=results) as run:
            code=bootstrap.main(['--check'])
            commands=[call.args[0] for call in run.call_args_list]
        self.assertFalse(any('pip' in command or 'venv' in command for command in commands))
        return code,commands

    def test_missing_venv_check_does_not_install(self):
        code,commands=self.run_checked([OSError()])
        self.assertEqual(code,3)
        self.assertEqual(len(commands),1)

    def test_missing_dependencies_check_does_not_install(self):
        code,commands=self.run_checked([Mock(returncode=0),Mock(returncode=1)])
        self.assertEqual(code,4)
        self.assertEqual(len(commands),2)

    def test_bad_config_reports_distinct_exit_code(self):
        code,commands=self.run_checked([Mock(returncode=0),Mock(returncode=0),Mock(returncode=1)])
        self.assertEqual(code,5)
        self.assertEqual(len(commands),3)

if __name__=='__main__': unittest.main()
