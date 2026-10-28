"""Local Chromium discovery and explicit, inspectable browser selection."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

DEFAULT_CDP = 'http://127.0.0.1:9527'

class BrowserError(RuntimeError):
    pass

ERROR_MESSAGES = {
    'invalid_executable':'浏览器路径无效：请填写实际存在的 Chrome、Edge 或 Chromium 可执行文件。',
    'browser_not_found':'未发现可用 Chromium 浏览器。请在菜单设置现有浏览器路径，或运行 Start.cmd --install-browser 并确认下载。',
    'requested_browser_missing':'未找到指定类型的浏览器。请核对安装位置或显式填写 executable_path。',
    'non_cdp_service':'该端口可访问，但不是 Chromium CDP 服务，可能被其他程序占用。请选择实际调试端口；不会结束占用进程。',
    'connection_timeout':'连接本机 CDP 超时。请确认浏览器已启动并开放对应调试端口。',
    'unavailable':'无法连接本机 CDP。请先启动带远程调试的专用浏览器实例，或选择 launch 启动新实例。',
    'connection_failed':'CDP 连接失败或连接期间浏览器已退出。请检查调试地址、端口与浏览器状态。',
    'launch_failed':'新浏览器实例启动失败。请检查可执行文件、专用资料目录是否被占用及系统访问权限；未结束任何进程。',
    'unsupported_browser':'只支持 Chrome、Edge、Chromium；Firefox/WebKit 不能通过此 CDP 实现连接。',
    'conflicting_browser_settings':'浏览器配置冲突：attach 不使用可执行路径；launch 不使用 CDP 地址。请只配置对应模式所需的字段。',
}

def error_message(error):
    return ERROR_MESSAGES.get(str(error),'浏览器不可用，请检查浏览器配置。')

@dataclass(frozen=True)
class BrowserChoice:
    mode: str
    kind: str
    executable_path: str = ''
    endpoint: str = ''
    evidence: str = ''
    channel: str = ''


def validate_endpoint(endpoint):
    try:
        u=urlsplit(endpoint)
        port=u.port
        if (u.scheme!='http' or u.hostname not in {'localhost','127.0.0.1','::1'} or
                u.username or u.password or u.query or u.fragment or u.path not in {'','/'} or port==0):
            raise ValueError('local_cdp_endpoint_required')
    except (TypeError,ValueError):
        raise ValueError('local_cdp_endpoint_required') from None
    return endpoint

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def probe_cdp(endpoint,timeout=10):
    validate_endpoint(endpoint)
    try:
        opener=build_opener(ProxyHandler({}),NoRedirect())
        with opener.open(Request(endpoint.rstrip('/')+'/json/version'),timeout=timeout) as response:
            raw=response.read(65537)
        if len(raw)>65536: return 'non_cdp_service',{}
        info=json.loads(raw)
        if not isinstance(info,dict): return 'non_cdp_service',{}
        product=info.get('Browser','')
        ws=urlsplit(info.get('webSocketDebuggerUrl',''))
        ws.port
        if (not isinstance(product,str) or not any(s in product.lower() for s in ('chrome','chromium','edg')) or
                ws.scheme!='ws' or ws.hostname not in {'localhost','127.0.0.1','::1'} or ws.username or ws.password):
            return 'non_cdp_service',{}
        # Return only non-sensitive product metadata, never the websocket identifier.
        return 'available',{'Browser':product}
    except (TimeoutError,socket.timeout):
        return 'connection_timeout',{}
    except HTTPError:
        return 'non_cdp_service',{}
    except URLError as exc:
        return ('connection_timeout' if isinstance(exc.reason,(TimeoutError,socket.timeout)) else 'unavailable'),{}
    except (OSError,ValueError,TypeError):
        return 'non_cdp_service',{}


def unique_candidates(candidates):
    seen=set();result=[]
    for candidate in candidates:
        key=candidate.executable_path.replace('\\','/').casefold()
        if key not in seen:
            seen.add(key);result.append(candidate)
    order={'chrome':0,'edge':1,'chromium':2}
    return sorted(result,key=lambda c:(order[c.kind],c.evidence=='playwright'))


def discover_browsers(runtime):
    found=[]
    def add(kind,path,evidence):
        path=Path(path)
        if path.is_file(): found.append(BrowserChoice('launch',kind,str(path.resolve()),evidence=evidence))
    if os.name=='nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                for name,kind in [('chrome.exe','chrome'),('msedge.exe','edge'),('chromium.exe','chromium')]:
                    try:
                        keypath=r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths' + '\\' + name
                        with winreg.OpenKey(hive,keypath,0,winreg.KEY_READ|view) as key:
                            value,_=winreg.QueryValueEx(key,None)
                            if isinstance(value,str): add(kind,os.path.expandvars(value.strip('"')),'Windows App Paths')
                    except OSError: pass
        roots=[os.environ.get('PROGRAMFILES',r'C:\Program Files'),
               os.environ.get('PROGRAMFILES(X86)',r'C:\Program Files (x86)'),os.environ.get('LOCALAPPDATA','')]
        for root in filter(None,roots):
            for kind,tail in [('chrome','Google/Chrome/Application/chrome.exe'),
                              ('edge','Microsoft/Edge/Application/msedge.exe'),('chromium','Chromium/Application/chrome.exe')]:
                add(kind,Path(root)/tail,'common installation path')
    bundled=Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH',str(Path(runtime)/'browsers')))
    if bundled.is_dir():
        for pattern in ('chromium-*/chrome-win*/chrome.exe','chromium-*/chrome-linux*/chrome',
                        'chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium'):
            for path in bundled.glob(pattern): add('chromium',path,'playwright')
    return unique_candidates(found)


def choose_browser(config,*,force_launch=False,probe=probe_cdp,discover=discover_browsers,say=lambda _:None):
    b=config.browser
    mode='launch' if force_launch else b.mode
    path=b.executable_path
    new_explicit=b.kind!='auto' or bool(path) or bool(b.cdp_url) or b.mode!='auto'
    legacy=bool(config.run.executable_path or config.run.channel)
    if new_explicit and legacy:
        say('浏览器新配置优先，旧 run.executable_path/run.channel 本次不生效；旧资料目录未移动。')
    if not new_explicit and legacy:
        if config.run.executable_path: path=config.run.executable_path
        elif config.run.channel:
            mapping={'chrome':'chrome','msedge':'edge','chromium':'chromium'}
            if config.run.channel not in mapping: raise BrowserError('unsupported_browser')
            return BrowserChoice('launch',mapping[config.run.channel],channel=config.run.channel,evidence='legacy run.channel')
        mode='launch'
    if (mode=='attach' and path) or (mode=='launch' and b.cdp_url):
        raise BrowserError('conflicting_browser_settings')
    if path:
        if not Path(path).is_file(): raise BrowserError('invalid_executable')
        kind=b.kind if b.kind!='auto' else ('edge' if Path(path).name.lower()=='msedge.exe' else 'chromium')
        if Path(path).name.lower() not in {'chrome.exe','msedge.exe','chromium.exe','chrome','chromium'}:
            raise BrowserError('unsupported_browser')
        return BrowserChoice('launch',kind,str(Path(path).resolve()),evidence='explicit executable')
    if mode=='attach' or b.cdp_url:
        endpoint=b.cdp_url or DEFAULT_CDP
        status,_=probe(endpoint,b.connect_timeout_seconds)
        if status!='available': raise BrowserError(status)
        return BrowserChoice('attach',b.kind,endpoint=endpoint,evidence='explicit CDP')
    if mode=='auto' and b.kind=='auto':
        status,_=probe(DEFAULT_CDP,b.connect_timeout_seconds)
        if status=='available':
            return BrowserChoice('attach','auto',endpoint=DEFAULT_CDP,evidence='default Chrome 9527 CDP')
        say('默认 Chrome 9527 不可连接（'+status+'），回退到检测到的专用新实例。')
    candidates=discover(config.runtime)
    if b.kind!='auto': candidates=[c for c in candidates if c.kind==b.kind]
    if not candidates: raise BrowserError('requested_browser_missing' if b.kind!='auto' else 'browser_not_found')
    return candidates[0]
