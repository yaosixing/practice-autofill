import argparse
import functools
import http.server
import json
import os
import sys
from playwright.sync_api import Error as PlaywrightError
from pathlib import Path

from .config import ROOT,load_config,validate_config
from .browser import launch_browser,open_browser,runtime_environment,validate_url
from .browser_selection import choose_browser,discover_browsers,probe_cdp,DEFAULT_CDP,BrowserError,error_message
from .extraction import scan
from .filling import fill_answer,read_matches,FillError
from .storage import Store,cache_key
from .llm import Solver,MockTransport,OpenAITransport,ModelFailure,FatalModelError
from .logging_utils import configure_logging,event
from .adapters.base import AdapterNotConfigured

def terminal_text(text):
    # Prevent question data from injecting terminal escape/control sequences.
    return "".join(c if c in "\n\t" or (ord(c)>=32 and not 127<=ord(c)<=159) else " " for c in str(text))

class Session:
    def __init__(self,config,store,*,mock=False,mock_file=None,ask=input,say=print,transport_factory=None):
        self.config=config;self.store=store;self.mock=mock
        self.mock_file=mock_file or ROOT/"fixtures/mock_answers.json"
        self.ask=ask;self.say=say
        self.consent=None
        self.solver=None
        self.transport_factory=transport_factory
        self.transport=None
        self.prepared=None
        from .pacing import FillPacer
        self.pacer=FillPacer(config.pacing)

    def _solver(self):
        if self.solver:
            return self.solver
        if not self.mock:
            if self.consent is None:
                self.say("将向以下 API 发送每题题干、选项和空位数量：")
                self.say(terminal_text(self.config.llm.base_url))
                self.say("不发送账号、URL、DOM 或会话数据。已校验题目与答案会缓存在本地 runtime/answers.sqlite。")
                if self.config.run.confirm_external_data:
                    self.consent=self.ask("确认本次运行允许发送这些数据？输入 yes：").strip().lower()=="yes"
                else:
                    self.say("按当前配置默认允许数据外传，不再询问；可设置 run.confirm_external_data=true 恢复确认。")
                    self.consent=True
            if not self.consent:
                return None
        if self.transport_factory:
            self.transport=self.transport_factory()
        elif self.mock:
            self.transport=MockTransport(self.mock_file)
        else:
            key=os.environ.get("DEEPSEEK_API_KEY")
            if not key:
                raise FatalModelError("missing_api_key")
            self.transport=OpenAITransport(self.config.llm,key)
        self.solver=Solver(self.config.llm,self.transport,say=self.say)
        return self.solver

    def _cache_key(self,q,*,batch=False):
        endpoint="mock://local" if self.mock else self.config.llm.base_url
        model="fixture-"+str(self.mock_file.resolve()) if self.mock else self.config.llm.model
        if self.mock:
            # Fixture edits invalidate previous simulated answers.
            import hashlib
            model += hashlib.sha256(self.mock_file.read_bytes()).hexdigest()
        if batch:
            from .batching import BATCH_PROMPT_VERSION,BATCH_PROTOCOL_VERSION
            return cache_key(q,endpoint,model,BATCH_PROMPT_VERSION,BATCH_PROTOCOL_VERSION)
        key=cache_key(q,endpoint,model)
        return key

    def prepare(self,targets):
        """Solve first; process() must not silently request failed prepared questions again."""
        self.prepared=None
        if self.config.answering.mode=="single":
            return True
        self.prepared={}
        pending={}
        for target in targets:
            q=target.question
            if q.fingerprint in self.prepared or q.fingerprint in pending:
                continue
            answer=self.store.get(self._cache_key(q,batch=True),q) or self.store.get(self._cache_key(q),q)
            if answer is not None:
                self.prepared[q.fingerprint]=answer
            else:
                pending[q.fingerprint]=q
        self.say(f"快速模式：缓存命中 {len(self.prepared)} 题；待请求 {len(pending)} 题。")
        if not pending:
            return True
        solver=self._solver()
        if solver is None:
            return False
        from .batching import BatchSolver
        def save(q,answer):
            self.prepared[q.fingerprint]=answer
            self.store.put(self._cache_key(q,batch=True),q,answer)
        batch=BatchSolver(self.config.llm,self.config.answering,self.transport,say=self.say)
        batch.single=solver  # Share rate-limiting state across rescans and single fallbacks.
        batch.solve(list(pending.values()),on_answer=save)
        for fingerprint,q in pending.items():
            if fingerprint not in self.prepared:
                self.prepared[fingerprint]=None
                self.store.progress(q,"manual")
        return True

    def process(self,target):
        q=target.question
        key=self._cache_key(q)
        if self.prepared is not None:
            answer=self.prepared.get(q.fingerprint)
            if answer is None:
                self.say("该题没有可靠的已准备答案，转人工；不重复发起模型调用。")
                self.store.progress(q,"manual")
                return "manual"
        else:
            answer=self.store.get(key,q)
        if answer is None:
            solver=self._solver()
            if solver is None:
                event("external_data_declined",q.fingerprint)
                return "declined"
            try:
                answer=solver.solve(q)
            except FatalModelError:
                raise
            except ModelFailure:
                self.store.progress(q,"manual")
                event("manual",q.fingerprint,"answer_unavailable")
                self.say("模型未返回可靠答案，该题转人工。")
                return "manual"
            self.store.put(key,q,answer)
        self.say("\n题目 "+q.fingerprint[:12]+" ["+q.type+"]")
        self.say(terminal_text(q.stem))
        for option in q.options:
            self.say(terminal_text(option.id+": "+option.text))
        self.say("答案："+terminal_text(answer.json()))
        if answer.type=="unknown":
            self.store.progress(q,"manual")
            return "manual"
        if not self.config.run.auto_fill:
            choice=self.ask("输入 y 回填，q 结束，其余跳过：").strip().lower()
            if choice=="q":
                return "quit"
            if choice!="y":
                self.store.progress(q,"skipped")
                return "skipped"
        try:
            self.pacer.before_question(target)
            fast=self.config.pacing.mode=="fast"
            # Clearing always writes; the initial read cannot change that decision.
            matches=False if fast and self.config.run.clear_before_fill else read_matches(target,answer)
            filled=False
            if self.config.run.clear_before_fill or not matches:
                from .pacing import effective_delays
                pacing_options={}
                if effective_delays(self.config.pacing)[1]>0:
                    pacing_options["before_control"]=lambda:self.pacer.before_control(target)
                fill_answer(target,answer,clear_first=self.config.run.clear_before_fill,**pacing_options)
                filled=True
            # fill_answer already validates the complete final state.
            if not (fast and filled) and not read_matches(target,answer):
                raise FillError("readback_mismatch_partial_fill_possible")
        except (FillError,PlaywrightError):
            self.store.progress(q,"failed")
            event("fill_failed",q.fingerprint,"manual_check_required")
            self.say("该题状态变化或验证失败，停止处理该题；可能已部分填写，请人工检查。")
            return "failed"
        self.store.progress(q,"verified")
        event("verified",q.fingerprint)
        self.say("已读取页面状态并验证；未执行提交。")
        return "verified"

    def close(self):
        if self.transport and hasattr(self.transport,"close"):
            self.transport.close()

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass

def serve(port):
    with http.server.ThreadingHTTPServer(("127.0.0.1",port),
            functools.partial(QuietHandler,directory=str(ROOT/"fixtures"))) as server:
        print(f"本地测试页面：http://127.0.0.1:{server.server_port}/practice.html")
        print("Ctrl+C 停止服务。")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

class OperationCancelled(Exception):
    pass


def prompt_url(*,reader=None,say=print):
    if reader is None:
        from .menu import hidden_input
        reader=hidden_input
    while True:
        raw=reader("输入完整答题 URL（不回显；b/m 返回，q 停止任务）：").strip()
        if raw.lower() in {"b","m","q"}:
            raise OperationCancelled()
        try:
            return validate_url(raw)
        except ValueError:
            say("URL 无效：请输入 http/https 完整地址，不得包含用户名或密码；未显示输入内容。")


def select_page(context,current):
    pages=[p for p in context.pages if not p.is_closed()]
    if not pages:
        raise ValueError("no_open_page")
    if len(pages)==1:
        return pages[0]
    print("存在多个标签页，请选择题目所在页（不显示 URL）：")
    for index,page in enumerate(pages,1):
        print(str(index)+": "+terminal_text(page.title()))
    while True:
        choice=input("标签页编号（b/m 返回，q 停止任务）：").strip()
        if choice.lower() in {"b","m","q"}:
            raise OperationCancelled()
        if choice.isdecimal() and 1<=int(choice)<=len(pages):
            return pages[int(choice)-1]
        print(f"输入无效：请输入 1–{len(pages)}，或 b、m、q。")

def apply_overrides(config,args):
    from .pacing import effective_delays
    if args.fill_speed is not None: config.pacing.mode=args.fill_speed
    if args.question_delay is not None or args.control_delay is not None:
        question,control=effective_delays(config.pacing)
        config.pacing.mode="custom"
        config.pacing.question_delay_seconds=question if args.question_delay is None else args.question_delay
        config.pacing.control_delay_seconds=control if args.control_delay is None else args.control_delay
    if args.answer_mode is not None: config.answering.mode=args.answer_mode
    if args.batch_size is not None: config.answering.batch_size=args.batch_size
    for name in ("mode","kind","executable_path","cdp_url","connect_timeout_seconds"):
        value=getattr(args,"browser_"+name,None)
        if value is not None: setattr(config.browser,name,value)
    if args.cdp_port is not None:
        if not 1<=args.cdp_port<=65535: raise ValueError("invalid_cdp_port")
        config.browser.cdp_url=f"http://127.0.0.1:{args.cdp_port}"
    return validate_config(config)


def cdp_endpoint(value,config):
    return (config.browser.cdp_url or DEFAULT_CDP) if value=="__default_cdp__" else value


def browser_report(config):
    from dataclasses import asdict
    candidates=discover_browsers(config.runtime)
    status,info=probe_cdp(config.browser.cdp_url or DEFAULT_CDP,config.browser.connect_timeout_seconds)
    return {"cdp_status":status,"product":info.get("Browser",""),
            "browsers":[dict(asdict(c),file_exists=True,launch_verified=False) for c in candidates],
            "supported":["chrome","edge","chromium"]}


def main(argv=None, *, config_override=None):
    parser=argparse.ArgumentParser(description="授权练习网页的答案预览与回填，不提交。")
    parser.add_argument("--config",type=Path)
    parser.add_argument("--menu",action="store_true",help="打开交互主菜单；API 配置仅保存当前进程")
    parser.add_argument("--mock",action="store_true")
    parser.add_argument("--mock-file",type=Path)
    parser.add_argument("--auto-fill",action="store_true")
    parser.add_argument("--answer-mode",choices=["fast","single"])
    parser.add_argument("--batch-size",type=int)
    parser.add_argument("--fill-speed",choices=["slow","normal","fast","custom"],help="网页选填节奏；默认 normal")
    parser.add_argument("--question-delay",type=float,help="题间等待秒数 0–30；提供后启用自定义节奏")
    parser.add_argument("--control-delay",type=float,help="题内控件处理间隔秒数 0–30；提供后启用自定义节奏")
    parser.add_argument("--chaoxing-selectors",type=Path)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument("--serve",action="store_true")
    modes.add_argument("--run",action="store_true",help="正式操作：统一选择已有 CDP 或专用新浏览器，不提交")
    modes.add_argument("--web",action="store_true",help="启动本机网页控制台，保留终端菜单启动方式")
    parser.add_argument("--no-open",action="store_true",help="启动网页服务但不自动打开浏览器")
    parser.add_argument("--web-port",type=int,default=0,help="网页控制台本机端口；0 自动分配")
    modes.add_argument("--detect-browsers",action="store_true",help="只读检测本机 Chromium 浏览器与 CDP")
    parser.add_argument("--browser-mode",dest="browser_mode",choices=["auto","attach","launch"])
    parser.add_argument("--browser-kind",dest="browser_kind",choices=["auto","chrome","edge","chromium"])
    parser.add_argument("--browser-path",dest="browser_executable_path")
    connection=parser.add_mutually_exclusive_group()
    connection.add_argument("--cdp-url",dest="browser_cdp_url")
    connection.add_argument("--cdp-port",type=int)
    parser.add_argument("--browser-timeout",dest="browser_connect_timeout_seconds",type=float)
    modes.add_argument("--inspect-cdp",nargs="?",const="__default_cdp__",
                       help="只读核验已打开的超星作业页；默认连接 Chrome 9527")
    modes.add_argument("--trial-cdp",nargs="?",const="__default_cdp__",
                       help="连接 Chrome，仅预览并经确认处理指定一题，不提交")
    modes.add_argument("--run-cdp",nargs="?",const="__default_cdp__",
                       help="连接 Chrome 处理当前页，配合 --auto-fill 无逐题确认，不提交")
    parser.add_argument("--question",type=int,default=1,help="单题验证的可识别题目序号，从 1 开始")
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args(argv)
    if args.menu or argv == [] or (argv is None and len(sys.argv) == 1):
        from .menu import TerminalMenu, load_menu_config
        config=config_override or load_menu_config(args.config)
        if config is not None:
            try:
                apply_overrides(config,args)
            except (ValueError,TypeError):
                print("配置参数无效，请检查答题模式、批量大小、选填节奏与浏览器参数。")
                return 2
        if config is None:
            return 0
        def runner(current, arguments):
            command=list(arguments)
            if args.chaoxing_selectors and "--serve" not in command:
                command.extend(["--chaoxing-selectors",str(args.chaoxing_selectors)])
            return main(command,config_override=current)
        return TerminalMenu(config,runner).run()
    try:
        if args.serve:
            serve(args.port)
            return
        config=config_override or load_config(args.config)
        apply_overrides(config,args)
        if args.web:
            from .web import serve_console
            config.run.auto_fill=True  # Same default as the terminal formal-operation entry.
            return serve_console(config,port=args.web_port,open_page=not args.no_open)
        if args.detect_browsers:
            print(json.dumps(browser_report(config),ensure_ascii=False,indent=2))
            return 0
        if (args.inspect_cdp or args.trial_cdp or args.run_cdp) and (config.browser.mode=="launch" or config.browser.executable_path):
            raise BrowserError("conflicting_browser_settings")
        if args.inspect_cdp:
            if args.auto_fill or args.mock or args.mock_file:
                parser.error("--inspect-cdp cannot be combined with fill/model options")
            from .inspection import inspect_cdp
            selectors=args.chaoxing_selectors or ROOT/"selectors/chaoxing.work.toml"
            endpoint=cdp_endpoint(args.inspect_cdp,config)
            report=inspect_cdp(config,selectors,endpoint)
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return 0 if report["ready"] else 1
        if args.trial_cdp or args.run_cdp:
            if args.mock or args.mock_file:
                parser.error("CDP filling requires real model mode")
            if args.trial_cdp and (args.auto_fill or config.run.auto_fill):
                parser.error("--trial-cdp requires manual confirmation; use --run-cdp --auto-fill for formal operation")
            if args.run_cdp:
                config.run.auto_fill=config.run.auto_fill or args.auto_fill
            if args.question < 1:
                parser.error("--question must be positive")
            if not os.environ.get("DEEPSEEK_API_KEY"):
                print("未设置 DEEPSEEK_API_KEY；请在启动本命令的本地终端设置，不要发送到聊天。")
                return 1
            from .trial import trial_cdp
            runtime_environment(config.runtime)
            run_id=configure_logging(config.runtime/"events.jsonl")
            print('诊断记录已启用；运行编号 '+run_id+'；记录位于本地 events.jsonl。')
            selectors=args.chaoxing_selectors or ROOT/"selectors/chaoxing.work.toml"
            with Store(config.runtime/"answers.sqlite") as store:
                session=Session(config,store)
                try:
                    if args.run_cdp:
                        from .cdp_run import run_cdp
                        report=run_cdp(config,selectors,session,cdp_endpoint(args.run_cdp,config))
                        print("本次处理统计（包含重新扫描）："+json.dumps(report,ensure_ascii=False))
                        print("已断开工具连接；未关闭 Chrome，未执行提交。")
                        return 1 if report.get("failed") else 0
                    result=trial_cdp(config,selectors,session,cdp_endpoint(args.trial_cdp,config),args.question)
                    print("单题验证结果："+result+"；不会继续处理其他题目。")
                    return 0 if result in {"verified","skipped","quit","declined","manual"} else 1
                finally:
                    session.close()
        config.run.auto_fill=config.run.auto_fill or args.auto_fill
        runtime_environment(config.runtime)
        run_id=configure_logging(config.runtime/"events.jsonl")
        print('诊断记录已启用；运行编号 '+run_id+'；记录位于本地 events.jsonl。')
        print("仅用于你有权访问且允许自动化和 AI 辅助的练习或自测页面。")
        print("浏览器登录资料、题目答案缓存存于本地 runtime/，请勿分享或提交版本库。")
        # Hidden input avoids retaining sensitive query parameters in terminal scrollback.
        force_launch=not args.run and config.browser.mode=="auto" and not config.browser.cdp_url
        choice=choose_browser(config,force_launch=force_launch,say=print)
        print("浏览器选择："+choice.mode+" / "+choice.kind+"；依据："+choice.evidence)
        url=prompt_url() if choice.mode=="launch" else None
        with Store(config.runtime/"answers.sqlite") as store,open_browser(config,choice) as (context,page):
            if choice.mode=="launch":
                try:
                    page.goto(url,wait_until="domcontentloaded",timeout=int(config.run.load_timeout_seconds*1000))
                except Exception:
                    print("导航未完成，请在浏览器手动检查登录、网络或访问限制。")
            url=None
            if input("请完成手动登录并打开题目页面；Enter 继续，b/m 返回，q 停止任务：").strip().lower() in {"b","m","q"}:
                raise OperationCancelled()
            session=Session(config,store,mock=args.mock,mock_file=args.mock_file)
            from collections import Counter
            totals=Counter()
            try:
                while True:
                    page=select_page(context,page)
                    targets,skips=scan(page,config,args.chaoxing_selectors or (ROOT/"selectors/chaoxing.work.toml" if args.run else None))
                    for skip in skips:
                        event("scan_skip",error=skip.code)
                    print(f"识别到 {len(targets)} 题；跳过/暂停原因："+", ".join(sorted({s.code for s in skips})))
                    if not session.prepare(targets):
                        return 0
                    seen=set()
                    for target in targets:
                        dedup=(id(target.frame),target.identity,target.question.fingerprint)
                        if dedup in seen:
                            continue
                        seen.add(dedup)
                        result=session.process(target)
                        totals[result]+=1
                        if result in {"quit","failed","declined"}:
                            print("本次处理统计："+json.dumps(dict(totals),ensure_ascii=False))
                            return 1 if result=="failed" else 0
                    if input("可人工翻页或处理访问限制。输入 r 重新扫描，其余退出：").strip().lower()!="r":
                        break
                print("本次处理统计："+json.dumps(dict(totals),ensure_ascii=False))
                print("未执行提交。已附加的浏览器保持打开；专用新实例正常关闭。")
            finally:
                session.close()
    except (OperationCancelled, EOFError):
        print("操作已取消，可返回菜单后重新选择；已发生的网页写入不会自动撤销。")
        return 130
    except KeyboardInterrupt:
        print("\n已中断；再次启动会复用登录和缓存，并重新核验页面。")
        return 130
    except FatalModelError as exc:
        event("model_stopped",error=str(exc))
        print("API Key、权限或接口配置异常，已停止模型调用。请检查环境变量及配置。")
        return 1
    except BrowserError as exc:
        print(error_message(exc))
        return 1
    except AdapterNotConfigured:
        print("超星适配器待配置：请根据脱敏 DOM 配置选择器并完成本地核验。")
        return 1
    except Exception as exc:
        # Do not print raw exception messages: SDK/Playwright may include URLs or secrets.
        event("stopped",error="configuration_or_environment")
        print("已停止：配置、浏览器或页面不可用。请按 README 排查；未输出原始敏感异常。")
        return 1
