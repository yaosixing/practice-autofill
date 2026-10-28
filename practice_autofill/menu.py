"""Session-only terminal menus; no configuration files or secrets are written."""
from copy import deepcopy
from dataclasses import replace
import getpass
import os
import sys
import warnings

from .config import Config, load_config, validate_config


class Navigation(Exception):
    def __init__(self, destination):
        self.destination = destination


def safe_text(value):
    return "".join(c if ord(c) >= 32 and not 127 <= ord(c) <= 159 else " " for c in str(value))


def hidden_input(prompt):
    # Refuse a plaintext fallback on a terminal that cannot disable echo.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(prompt)


def load_menu_config(path=None, *, ask=input, say=print):
    while True:
        try:
            return load_config(path)
        except (OSError, ValueError, TypeError):
            say("[配置不可用] 文件不存在、格式错误或参数无效；不会覆盖原文件。")
            say("[r] 重试  [d] 仅本次使用默认配置  [q] 退出")
            try:
                answer = ask("配置 > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return None
            if answer in {"q", "0", "b", "m"}:
                return None
            if answer == "d":
                say("[已选择] 本次使用默认配置，原文件未修改。")
                return Config()
            if answer != "r":
                say("[输入无效] 请输入 r、d 或 q。")


class TerminalMenu:
    FIELDS = (
        ("base_url", "API 地址", str),
        ("model", "模型名称", str),
        ("timeout_seconds", "超时秒数", float),
        ("max_retries", "重试次数（0–5）", int),
        ("min_interval_seconds", "调用间隔秒数", float),
        ("max_tokens", "最大输出 tokens", int),
    )

    def __init__(self, config, runner, *, ask=input, secret=hidden_input, say=print, color=None):
        self.config = config
        self.initial_llm = deepcopy(config.llm)
        self.runner = runner
        self.ask, self.secret, self.say = ask, secret, say
        self.color = (sys.stdout.isatty() and "NO_COLOR" not in os.environ) if color is None else color

    def header(self, title):
        self.say("")
        self.say("=" * 56)
        self.say(("\033[36m" if self.color else "") + title + ("\033[0m" if self.color else ""))
        self.say("=" * 56)

    def read(self, prompt, *, sensitive=False):
        try:
            value = (self.secret if sensitive else self.ask)(prompt).strip()
        except EOFError:
            raise Navigation("q") from None
        if value.lower() in {"b", "m", "q"}:
            raise Navigation(value.lower())
        return value

    def result(self, status, message):
        self.header("操作结果")
        self.say(f"[{status}] {safe_text(message)}")
        self.say("[b] 返回上一级  [m] 主菜单  [q] 退出")
        while True:
            try:
                self.read("结果 > ")
            except Navigation as nav:
                if nav.destination == "b":
                    return
                raise
            self.say("[输入无效] 请输入 b、m 或 q。")

    def run(self):
        while True:
            try:
                self.header("练习网页助手 · 主菜单")
                self.say("浏览器："+self.config.browser.mode+" / "+self.config.browser.kind+"（默认优先 Chrome 9527） | 提交：用户完成")
                self.say("API Key：" + ("[已设置，隐藏]" if os.environ.get("DEEPSEEK_API_KEY") else "[未设置]"))
                self.say("模型：" + safe_text(self.config.llm.model))
                self.say("配置保存：仅当前进程；退出后菜单修改失效")
                self.say("")
                self.say("[1] 自动完成当前页（不提交）")
                self.say("[2] 只读检查 Chrome 页面")
                self.say("[3 / a] 填写／修改 API 配置")
                self.say("[4 / c] 清除设置（仅当前进程）")
                self.say("[5] 更多操作")
                self.say("[6] 答题模式：" + self.config.answering.mode + "（批量 0 表示整页）")
                self.say("[7] 浏览器检测／设置")
                from .pacing import LABELS,effective_delays
                question,control=effective_delays(self.config.pacing)
                self.say(f"[8] 网页选填节奏：{LABELS[self.config.pacing.mode]}（题间 {question:g}s / 控件 {control:g}s）")
                self.say("[0 / q] 退出")
                choice = self.read("主菜单 > ").lower()
                if choice == "0":
                    break
                if choice == "1":
                    self.operation(["--run", "--auto-fill"], needs_key=True)
                elif choice == "2":
                    self.operation(["--inspect-cdp"])
                elif choice in {"3", "a"}:
                    self.api_page()
                elif choice in {"4", "c"}:
                    self.clear_page()
                elif choice == "5":
                    self.more_page()
                elif choice == "6":
                    self.answering_page()
                elif choice == "7":
                    self.browser_page()
                elif choice == "8":
                    self.pacing_page()
                else:
                    self.say("[输入无效] 请选择 0–8，或 a、c、q。")
            except Navigation as nav:
                if nav.destination == "q":
                    break
            except KeyboardInterrupt:
                self.say("\n[已取消] 当前操作已取消，返回主菜单；已发生的网页写入不会自动撤销。")
        self.say("[已退出] 菜单未向文件保存 API 配置或 Key。")
        return 0

    def api_page(self):
        draft = deepcopy(self.config.llm)
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        while True:
            self.header("填写／修改 API 配置")
            self.say("正在修改当前配置；保存前所有变更均为草稿。" if key else "API Key 未设置：首次填写；保存前所有变更均为草稿。")
            for number, (name, label, _) in enumerate(self.FIELDS, 1):
                self.say(f"[{number}] {label}：{safe_text(getattr(draft, name))}")
            self.say("[7] API Key：" + ("[已设置，隐藏]" if key else "[未设置]"))
            self.say("[s] 保存到当前进程  [b] 放弃并返回  [m] 主菜单  [q] 退出")
            choice = self.read("API 配置 > ").lower()
            if choice == "s":
                validate_config(replace(self.config, llm=draft))
                confirm = self.read("仅保存到当前进程，输入 yes 确认，直接回车取消 > ")
                if confirm.lower() != "yes":
                    self.say("[已取消] 未保存，仍可继续编辑或按 b 放弃。")
                    continue
                self.config.llm = deepcopy(draft)
                if key:
                    os.environ["DEEPSEEK_API_KEY"] = key
                else:
                    os.environ.pop("DEEPSEEK_API_KEY", None)
                self.result("已保存", "已应用当前进程；没有写入文件，也未测试 API 连通性。")
                continue
            if not choice.isdecimal() or not 1 <= int(choice) <= 7:
                self.say("[输入无效] 请选择 1–7 或 s、b、m、q。")
                continue
            try:
                if choice == "7":
                    value = self.read("API Key（隐藏输入；回车保留，b 返回，m 主菜单，q 退出）> ", sensitive=True)
                    if value:
                        if any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
                            raise ValueError("invalid_key")
                        key = value
                else:
                    name, label, convert = self.FIELDS[int(choice)-1]
                    value = self.read(f"{label}（回车保留；b 返回，m 主菜单，q 退出）> ")
                    if value:
                        candidate = replace(draft, **{name: convert(value)})
                        validate_config(replace(self.config, llm=candidate))
                        draft = candidate
            except Navigation as nav:
                if nav.destination != "b":
                    raise
            except (ValueError, TypeError, OverflowError, getpass.GetPassWarning):
                self.say("[参数无效] 未修改该字段。地址须为远程 HTTPS 或本地 HTTP，不能含凭据或查询参数；模型不能为空；超时/间隔须为有限正数，重试为 0–5，tokens 至少 64。隐藏输入不可用时请换本地终端。")

    def clear_page(self):
        self.header("清除设置 · 二次确认")
        self.say("清除范围：本菜单修改的 API 参数恢复到启动值；移除当前 Python 进程中的 API Key。")
        self.say("不修改 TOML 文件、父终端环境变量、登录状态、答案缓存、进度、日志或脚本。")
        self.say("[默认] 取消  [b] 返回  [m] 主菜单  [q] 退出")
        confirm = self.read("输入 CLEAR 确认清除，直接回车取消 > ")
        if confirm != "CLEAR":
            self.say("[已取消] 未清除任何设置。")
            return
        self.config.llm = deepcopy(self.initial_llm)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        self.result("已清除", "API 参数已恢复启动值，当前 Python 进程 Key 已移除；文件和业务数据未改动。")

    def operation(self, arguments, *, needs_key=False):
        if needs_key and not os.environ.get("DEEPSEEK_API_KEY"):
            self.result("未就绪", "API Key 未设置，请返回主菜单选择 3 / a 配置。")
            return
        self.header("执行中")
        self.say("正在执行所选操作。Ctrl+C 可中断并返回菜单；工具不执行提交。")
        try:
            config = deepcopy(self.config)
            if any(flag in arguments for flag in ("--inspect-cdp","--trial-cdp","--run-cdp")):
                # These menu operations explicitly mean attachment, independently of launch preferences.
                config.browser.mode = "attach"
                config.browser.executable_path = ""
            if "--trial-cdp" in arguments:
                config.run.auto_fill = False
            status = self.runner(config, arguments)
        except KeyboardInterrupt:
            self.result("已取消", "操作已中断；已发生的网页写入可能保留，请检查页面。")
            return
        except Exception:
            self.result("失败", "执行失败，请检查配置、Chrome 9527 连接与页面；未输出原始敏感异常。")
            return
        if status in (None, 0):
            self.result("操作结束", "请检查上方统计或页面回读结果；此状态不代表所有答案正确或已提交。")
        elif status == 130:
            self.result("已取消", "操作已中断，可返回后重新运行；页面已填写内容可能保留。")
        else:
            self.result("未完成", "请根据上方提示检查配置、权限或页面。修正后可重试。")

    def more_page(self):
        while True:
            self.header("更多操作")
            self.say("[1] 单题验证（预览后确认回填）")
            self.say("[2] 启动本地测试页面服务（Ctrl+C 停止）")
            self.say("[3] 打开独立浏览器进行本地模拟测试")
            self.say("[4] 输入 URL，在独立浏览器自动回填（不提交）")
            self.say("[b] 上一级  [m] 主菜单  [q] 退出")
            try:
                choice = self.read("更多操作 > ")
                if choice == "1":
                    while True:
                        try:
                            value = self.read("可识别题目序号（正整数；b 返回，m 主菜单，q 退出）> ")
                            if not value.isdecimal() or int(value) < 1:
                                self.say("[输入无效] 请输入正整数。")
                                continue
                            self.operation(["--trial-cdp", "--question", str(int(value))], needs_key=True)
                            break
                        except Navigation as nav:
                            if nav.destination != "b":
                                raise
                            break
                elif choice == "2":
                    self.operation(["--serve"])
                elif choice == "3":
                    self.operation(["--mock", "--browser-mode", "launch", "--cdp-url", ""])
                elif choice == "4":
                    self.operation(["--auto-fill", "--browser-mode", "launch", "--cdp-url", ""], needs_key=True)
                else:
                    self.say("[输入无效] 请选择 1–4 或 b、m、q。")
            except Navigation as nav:
                if nav.destination == "b":
                    return
                raise

    def answering_page(self):
        self.settings_page("答题模式", "answering", (
            ("mode","模式（fast/single）",str),
            ("batch_size","每批题数（0 为整页）",int),
            ("max_batch_chars","批量字符预算（至少 256）",int),
            ("fallback_to_single","允许逐题回退（true/false）",self.boolean),
        ))

    @staticmethod
    def boolean(value):
        if value.lower() not in {"true","false"}:
            raise ValueError("invalid_boolean")
        return value.lower()=="true"

    def settings_page(self,title,section,items):
        draft=deepcopy(getattr(self.config,section))
        while True:
            self.header(title)
            self.say("只保存到当前进程；超时、重试、间隔和输出上限在 API 配置页设置。")
            for i,(name,label,_) in enumerate(items,1):
                self.say(f"[{i}] {label}：{safe_text(getattr(draft,name))}")
            self.say("[s] 保存  [b] 放弃返回  [m] 主菜单  [q] 退出；输入 - 清空字符串字段")
            try:
                choice=self.read(title+" > ").lower()
                if choice=="s":
                    validate_config(replace(self.config,**{section:draft}))
                    setattr(self.config,section,deepcopy(draft))
                    self.result("已保存","已应用当前进程；退出后恢复启动配置。")
                    continue
                if not choice.isdecimal() or not 1<=int(choice)<=len(items):
                    self.say("[输入无效] 请选择字段编号或 s、b、m、q。")
                    continue
                name,label,convert=items[int(choice)-1]
                try:
                    value=self.read(label+"（回车保留；b 返回）> ")
                    if value:
                        value="" if value=="-" and convert is str else convert(value)
                        candidate=replace(draft,**{name:value})
                        validate_config(replace(self.config,**{section:candidate}))
                        draft=candidate
                except Navigation as nav:
                    if nav.destination!="b": raise
            except Navigation as nav:
                if nav.destination=="b": return
                raise
            except (ValueError,TypeError,OverflowError):
                self.say("[参数无效] 未修改该字段，请核对提示中的类型、范围及允许值。")

    def browser_page(self):
        self.header("浏览器检测／设置")
        from .cli import browser_report
        report=browser_report(self.config)
        self.say("CDP 状态："+report["cdp_status"]+"；检测不读取页面、Cookie 或登录状态。")
        for index,item in enumerate(report["browsers"],1):
            self.say(f"检测 {index}: {item['kind']} | {safe_text(item['executable_path'])}")
            self.say("  [文件存在；未验证启动] 依据："+safe_text(item["evidence"]))
        if not report["browsers"]:
            self.say("未检测到浏览器；可手动填写路径，或通过 Start.cmd --install-browser 确认下载。")
        self.settings_page("浏览器设置","browser",(
            ("mode","模式（auto/attach/launch）",str),
            ("kind","类型（auto/chrome/edge/chromium）",str),
            ("executable_path","程序路径（launch 使用；- 清空）",str),
            ("cdp_url","本机 CDP 地址（attach 使用；- 清空）",str),
            ("connect_timeout_seconds","连接超时秒数",float),
        ))

    @staticmethod
    def speed(value):
        aliases={"慢":"slow","正常":"normal","快":"fast","自定义":"custom",
                 "1":"slow","2":"normal","3":"fast","4":"custom"}
        return aliases.get(value.lower(),value.lower())

    def pacing_page(self):
        self.header("网页选填节奏")
        self.say("慢：题间 1.5s / 控件 0.35s；正常：原速，无额外等待；快：减少重复整题读取，保留清空及最终验证。")
        self.say("仅自定义模式使用下面两个自定义参数；范围 0–30 秒。")
        self.say("控件处理间隔包含清空检查和填写；等待后重新定位。不改变 API 调用间隔或跳过验证。")
        self.settings_page("选填节奏设置","pacing",(
            ("mode","模式（1 慢 / 2 正常 / 3 快 / 4 自定义，亦可中文或英文）",self.speed),
            ("question_delay_seconds","自定义题间等待秒数",float),
            ("control_delay_seconds","自定义控件处理间隔秒数",float),
        ))
