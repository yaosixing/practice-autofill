"""Project-local Windows launcher. Uses only the Python standard library."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def version_supported(version):
    return tuple(version[:2]) >= (3, 11)


def environment(root):
    env = os.environ.copy()
    runtime = root / 'runtime'
    for name, folder in [('TEMP','tmp'),('TMP','tmp'),('TMPDIR','tmp'),
                         ('PIP_CACHE_DIR','pip-cache'),('PLAYWRIGHT_BROWSERS_PATH','browsers')]:
        env[name] = str(runtime / folder)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def confirmed_run(command, description, *, ask=input, run=subprocess.run, **kwargs):
    print('[需要确认] ' + description)
    print('可能联网；安装、下载、缓存及临时文件仅在本项目中，不修改系统 PATH。')
    try:
        approved = ask('输入 y 执行，回车取消 > ').strip().lower() == 'y'
    except (EOFError, KeyboardInterrupt):
        approved = False
    if not approved:
        print('[已取消] 未执行安装或下载。')
        return False
    return run(command, **kwargs).returncode == 0


def choose_interface(*, ask=input):
    print('\n选择控制方式（不影响原有命令行参数）')
    print('[1] 打开网页控制台  [2] 终端菜单（默认）  [q] 退出')
    while True:
        try:
            choice = ask('启动方式 > ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice in {'', '2', 'n'}:
            return ['--menu']
        if choice in {'1', 'y'}:
            return ['--web']
        if choice in {'q', '0'}:
            return None
        print('[输入无效] 请输入 1、2 或 q；回车使用终端。')


def main(argv=None):
    parser = argparse.ArgumentParser(description='项目启动检查；其他参数传给原 CLI')
    parser.add_argument('--check', action='store_true', help='仅检查，不进入菜单')
    parser.add_argument('--install-browser', action='store_true', help='确认后下载项目 Chromium')
    args, forwarded = parser.parse_known_args(argv)
    if not version_supported(sys.version_info):
        print('[Python 不支持] 请安装 Python 3.11+，然后重新运行 Start.cmd。')
        return 2
    os.chdir(ROOT)
    env = environment(ROOT)
    for name in ('TEMP','PIP_CACHE_DIR','PLAYWRIGHT_BROWSERS_PATH'):
        Path(env[name]).mkdir(parents=True, exist_ok=True)
    vpython = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    probe = [str(vpython), '-B', '-c', 'import sys;sys.exit(sys.version_info<(3,11))']
    try:
        valid_venv = subprocess.run(probe, env=env, capture_output=True).returncode == 0
    except OSError:
        valid_venv = False
    if not valid_venv:
        if args.check:
            print('[虚拟环境缺失或损坏] 运行 Start.cmd 并确认创建/修复 .venv。')
            return 3
        if not confirmed_run([sys.executable, '-m', 'venv', str(ROOT / '.venv')],
                             '创建/修复虚拟环境：' + str(ROOT / '.venv'), env=env):
            return 3
    check = subprocess.run([str(vpython), '-B', '-c', 'import playwright.sync_api, openai, practice_autofill.cli'],
                           cwd=ROOT, env=env, capture_output=True)
    if check.returncode:
        print('[依赖检查失败] 缺少依赖或项目无法导入。')
        if args.check:
            return 4
        if not confirmed_run([str(vpython), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.lock.txt')],
                             '按 requirements.lock.txt 安装到：' + str(ROOT / '.venv'), cwd=ROOT, env=env):
            return 4
        check = subprocess.run([str(vpython), '-B', '-c', 'import playwright.sync_api, openai, practice_autofill.cli'],
                               cwd=ROOT, env=env, capture_output=True)
        if check.returncode:
            print('[项目导入失败] 依赖已检查，请用原 Python 启动方式排查模块或源码；未打印可能敏感的异常。')
            return 4
    # Validate using the selected interpreter; do not import third-party modules into this bootstrap process.
    config_path = None
    for index, value in enumerate(forwarded):
        if value == '--config' and index + 1 < len(forwarded):
            config_path = forwarded[index + 1]
        elif value.startswith('--config='):
            config_path = value.split('=', 1)[1]
    code = 'from practice_autofill.config import load_config; import sys; load_config(sys.argv[1] if len(sys.argv)>1 else None)'
    checked = subprocess.run([str(vpython), '-B', '-c', code] + ([config_path] if config_path else []),
                             cwd=ROOT, env=env, capture_output=True)
    if checked.returncode:
        print('[配置无效] 请检查 --config 文件是否存在、TOML 格式及字段范围；未改写文件。')
        return 5
    if args.install_browser and not confirmed_run([str(vpython), '-m', 'playwright', 'install', 'chromium'],
                         '下载 Chromium 到：' + env['PLAYWRIGHT_BROWSERS_PATH'], cwd=ROOT, env=env):
        return 6
    print('[检查通过] Python、项目依赖及配置就绪。')
    if not env.get('DEEPSEEK_API_KEY'):
        print('[提示] API Key 未设置，可在主菜单 3 / a 中隐藏输入；模拟模式不需要 Key。')
    if args.check:
        return 0
    if not forwarded:
        forwarded = choose_interface()
        if forwarded is None:
            return 0
    sys.stdout.flush()
    return subprocess.run([str(vpython), '-B', '-m', 'practice_autofill'] + forwarded,
                          cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (EOFError, KeyboardInterrupt):
        print('\n[已取消] 启动已停止。')
        raise SystemExit(130)
    except OSError:
        print('[启动环境错误] 文件不可访问或进程无法启动，请检查项目路径与 Python 安装。')
        raise SystemExit(7)
