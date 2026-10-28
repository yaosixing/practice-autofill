# 一键启动、快速求解与 Chromium 浏览器

## 入口和兼容性

Windows 双击根目录 `Start.cmd`，或在 PowerShell 运行 `./Start.cmd`。入口按自身位置定位项目，优先使用 `.venv/Scripts/python.exe`；再尝试 `py -3` 和 `python`，要求 Python 3.11+。失败保留终端并返回非零退出码。`bootstrap.py` 只用标准库。

- `Start.cmd --check`：只检查 Python、依赖和配置，绝不安装；缺失虚拟环境、依赖、配置分别返回 3、4、5。
- `Start.cmd --menu --config config.example.toml`：检查并只读载入指定配置。未指定配置时仍沿用项目默认值，不自动读取或覆盖其他 TOML。
- `Start.cmd --install-browser`：显示目标并确认后下载 Chromium，再进入菜单。已有可用 CDP 或系统 Chrome/Edge 时不需要下载。
- 创建/修复虚拟环境、按 requirements.lock.txt 安装依赖、下载浏览器均需输入 y，回车取消。解释器、pip 缓存、下载、临时目录位于项目 `.venv/` 或 `runtime/`；不安装全局软件、不改系统 PATH。
- API Key 未设置仍可进入菜单。3 / a 隐藏输入；仅当前进程有效。父终端环境变量仍可使用，不要把真实 Key 写入 TOML。
- 原有 Python 启动命令继续有效。旧 `--run-cdp`、`--trial-cdp`、`--inspect-cdp` 始终只连接已有实例。

```powershell
# 正式流程：默认优先 9527，否则发现并启动专用实例
./.venv/Scripts/python.exe -m practice_autofill --run --auto-fill
# 旧连接方式，默认也使用快速求解
./.venv/Scripts/python.exe -m practice_autofill --run-cdp --auto-fill
```

## 快速模式

主菜单 6 修改当前进程设置。持久化非敏感参数请手动编辑 TOML，并通过 --config 加载：

```toml
[answering]
mode = "fast"
batch_size = 0
max_batch_chars = 24000
fallback_to_single = true

[llm]
timeout_seconds = 60
max_retries = 2
min_interval_seconds = 2
max_tokens = 2048
```

`batch_size=0` 在字符预算内整页一次请求；正整数限制每批题数。字符预算包含序列化题目数据，不是精确 token 估计，不保证服务端上下文容量。超时、重试、调用间隔和输出上限复用原 llm 字段；较多填空可能需要增大 max_tokens 或减小 batch_size。

```powershell
# 每批最多 8 题
./.venv/Scripts/python.exe -m practice_autofill --run --auto-fill --batch-size 8
# 每题单独请求，自动填写的行为不变
./.venv/Scripts/python.exe -m practice_autofill --run --auto-fill --answer-mode single
```

“single”表示逐题请求，不表示强制逐题确认。单题验证入口保留原协议和人工确认。一般独立 URL 模式仍由 run.auto_fill 或 --auto-fill 决定是否确认。

流程：题目去重 → 复用校验过的批量或旧逐题缓存 → 请求未命中项 → 每个有效答案立即保存 → 求解结束后按原页面顺序清空、填写、回读。批量结果按匿名 id 对应，不依赖响应顺序。数据库结构不变，批量使用独立 prompt/protocol 版本。逐题路径保留原缓存键；不把批量缓存冒充旧协议缓存。

请求只包含解题数据：

```json
{"questions":[{"id":"q0001","type":"single","stem":"2 + 2？","options":[{"id":"A","text":"3"},{"id":"B","text":"4"}],"blank_count":0}]}
```

响应必须是：

```json
{"answers":[{"id":"q0001","type":"single","answers":["B"]}]}
```

完整批量系统提示词位于 `practice_autofill/batching.py` 的 BATCH_SYSTEM_PROMPT；原单题提示词仍在 prompts.py。两者均要求自行求解、只输出 JSON、不得执行题目夹带指令、无法确定返回 unknown，不展示或保存推理字段。请求不含网页题号、URL、DOM、定位、账号或会话数据。

恢复边界：

- 有效项立即保留，缺失/非法/重复 ID 仅恢复对应失败项；未知 ID 忽略并报告固定分类，不填其他题。
- JSON 重复键、额外外层字段、错误结构、非法选项、题型或空位数量均拒绝。
- 格式恢复耗尽、截断、413 或明确的上下文错误代码使失败组严格拆小；最后按配置回退逐题。单题超字符预算时仅允许完整逐题请求或转人工，不截断题干。
- 网络错误、408、429、5xx 有限重试，遵守 Retry-After；网络耗尽不递归拆成大量单题请求。401、403、其他明确配置错误停止。unknown 直接转人工。
- 求解失败的准备项不会在填写阶段触发额外隐式请求。导航、题目变化、清空失败或回读失败停止本次填写，可能已有部分写入，请人工检查。
- 数据外传默认允许的设置不变；可用 run.confirm_external_data=true 恢复每次运行确认。

## 浏览器检测与设置

```powershell
./.venv/Scripts/python.exe -m practice_autofill --detect-browsers
```

主菜单 7 先显示文件路径、检测依据及 CDP 状态，再编辑配置。只读检测不启动浏览器，不读登录凭据；显示“文件存在”不等于“实际启动已验证”。检查 Windows App Paths、常见安装位置、项目 Playwright Chromium，按规范化路径去重。

```toml
[browser]
mode = "auto"              # auto / attach / launch
kind = "auto"              # auto / chrome / edge / chromium
executable_path = ""
cdp_url = ""
connect_timeout_seconds = 10
```

优先级：CLI 显式值 > TOML/菜单显式值 > 可用默认 Chrome 9527 > 检测 Chrome、Edge、Chromium（项目 Playwright 最后）。指定 kind 或 executable_path 的 auto 模式选择对应新实例，不优先附加无法确认类型的 9527。指定 cdp_url 或 attach 模式只连接指定本机服务，失败不会悄悄启动其他浏览器。

```powershell
# 新建 Edge 专用实例，随后隐藏输入 URL、手动登录
./.venv/Scripts/python.exe -m practice_autofill --run --auto-fill --browser-mode launch --browser-kind edge
# 指定实际路径（PowerShell 引号保留空格）
./.venv/Scripts/python.exe -m practice_autofill --run --auto-fill --browser-mode launch --browser-kind chrome --browser-path 'C:/Program Files/Google/Chrome/Application/chrome.exe'
# 只附加已运行的 Chromium CDP
./.venv/Scripts/python.exe -m practice_autofill --run --auto-fill --browser-mode attach --cdp-port 9527
# 地址和端口选项二选一；第一版仅允许本机 HTTP CDP
./.venv/Scripts/python.exe -m practice_autofill --run --auto-fill --browser-mode attach --cdp-url http://127.0.0.1:9222 --browser-timeout 15
```

attach 不使用 executable_path；launch 不使用 cdp_url，配置冲突会提示修正。菜单输入 `-` 清空旧字符串字段后再切换模式。attach 中 kind 不证明远程浏览器的具体品牌；CDP 只能确认 Chromium 产品协议。

旧 run.executable_path/run.channel 继续读取。新 browser 显式设置优先，并提示旧字段不生效。支持的旧 channel 为 chrome、msedge、chromium。launch 使用专用 `runtime/browser-profiles/<kind>-<path-hash>/`，不同程序隔离；原 runtime/browser-profile 与 storage-state.json 保留、不移动、不清理。低层旧 launch_browser 接口仍支持旧资料目录。新实例可能需要首次手动登录。

附加连接只断开工具连接，不关闭用户浏览器，不读/保存其 Cookie 或 storage state。新实例由工具创建，退出时保存自己的状态并关闭。正常 launch 不开放额外 CDP 端口，不使用个人默认配置目录。

## 本地验证和排查

```powershell
# 终端一：仅绑定 127.0.0.1 的测试服务
./.venv/Scripts/python.exe -m practice_autofill --serve
# 终端二：输入 http://127.0.0.1:8765/practice.html，无 Key、无外部模型请求
./.venv/Scripts/python.exe -m practice_autofill --mock --auto-fill --answer-mode fast --browser-mode launch --browser-kind chromium
# 项目回归（先按原 README 设置项目浏览器与临时目录环境变量）
./.venv/Scripts/python.exe -B -m unittest discover -s tests -v
```

- Python 未找到：安装/修复 Python 3.11+ 后重开终端，或修复项目虚拟环境。启动器不自动安装系统 Python。
- 模块缺失：运行 Start.cmd，确认后按锁文件安装；拒绝不会执行安装。
- 配置异常：检查指定文件及字段，--check 返回 5；原 Python 菜单仍支持重试或显式选择默认值。
- 未检测到浏览器：设置实际路径，或显式确认下载项目 Chromium。
- 端口能访问但不是 CDP：可能被其他 HTTP 服务占用，选择真实调试端口；不会结束占用进程。
- 连接拒绝/超时：确认浏览器在专用资料目录下开放对应调试端口，或选 launch。程序不绕过浏览器限制。
- 新实例启动失败：核对路径、目录权限、资料目录占用；不直接断言安装损坏。
- 浏览器可启动但没有题目：浏览器兼容不等于网站适配。继续使用现有站点适配器和脱敏 DOM 流程。
- 输出截断/多题人工：减少 batch_size 或增大 llm.max_tokens；题目内容缺失需人工处理。

支持范围仅为 Playwright Chromium 的 Chrome、Edge、Chromium。Firefox/WebKit 不能用 Chromium CDP；支持它们需要另写启动、资料/状态和连接路径，并单独验证，第一版未实现。

所有测试和检测产物保留在 runtime，不自动清理。真实 DeepSeek 和真实账号网页批量填写未用于开发验证；本地成功不代表任意站点或真实答案正确。

## 网页选填节奏

主菜单新增 **8 网页选填节奏**。进入后选择字段 1，可输入编号、中文或英文：1/慢/slow、2/正常/normal、3/快/fast、4/自定义/custom。输入 s 保存，m 返回主菜单，选择 1 正式操作。b 放弃草稿返回，q 退出。

| 模式 | 题间额外等待 | 题内控件处理间隔 |
| --- | --- | --- |
| 慢 slow | 1.5 秒 | 0.35 秒 |
| 正常 normal（默认，原速） | 0 秒 | 0 秒 |
| 快 fast（减少重复读取） | 0 秒 | 0 秒 |
| 自定义 custom | 使用自定义字段 | 使用自定义字段 |

控件处理包含清空检查与填写步骤，所以会在清空和重新选择之间应用间隔。第一道实际处理的题、每题第一个控件步骤不额外等待；跳过或 unknown 不触发题间等待。等待后重新定位并验证控件；等待期间发生导航时停止旧目标的填写。不增加逐字输入模拟，不跳过校验，不改 API 限流设置，不改变提交行为。上述数值是额外等待时间，实际耗时还包括定位、操作与验证。

菜单设置仍仅在当前进程生效；清除 API 设置不会清除此节奏。持久配置在 TOML 增加：

```toml
[pacing]
mode = "custom"
question_delay_seconds = 0.8
control_delay_seconds = 0.15
```

自定义值允许 0–30 秒的有限数字，拒绝负值、NaN、无穷大和布尔值。只有 custom 模式使用这两个字段，预设不会覆盖保存的自定义值。默认 normal 保持此前的原速流程；fast 在保持清空、控件重定位和最终回读验证的前提下，省去清空前无必要的整题读取及回填后的重复整题读取。实际提速取决于题目和页面，模型耗时不受影响。

```powershell
# 正常节奏
./Start.cmd --run --auto-fill --fill-speed normal
# 慢节奏
./Start.cmd --run --auto-fill --fill-speed slow
# 自定义：题间 0.8 秒，控件间 0.15 秒
./Start.cmd --run --auto-fill --fill-speed custom --question-delay 0.8 --control-delay 0.15
```

提供 --question-delay 或 --control-delay 会自动启用 custom；未指定的另一项以选中预设的有效值为基础（若已经是 custom，则保留当前自定义值）。例如 --fill-speed slow --control-delay 0.2 对应题间 1.5 秒、控件 0.2 秒。
