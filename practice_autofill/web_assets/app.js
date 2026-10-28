'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const pageNames = {workspace:'练习工作台',api:'API 配置',browser:'浏览器',settings:'答题与节奏',guide:'使用指南'};
const stageNames = {idle:'尚未开始',connecting:'准备浏览器',scanning:'正在识别',solving:'获取答案',filling:'回填核验',finished:'本次已结束',failed:'需要检查',cancelled:'已停止'};
const paceNames = {slow:'慢',normal:'正常',fast:'快',custom:'自定义'};
const actionHelp = {
  run:'识别已加载题目，获取答案，按页面顺序清空、回填并核验。',
  inspect:'只连接已有 CDP 浏览器，检查超星适配与页面结构。不会请求模型或修改答案。',
  trial:'只连接已有 CDP 浏览器。按识别顺序选择一道题，显示答案并等待你的明确确认。',
  demo:'启动专用新实例，打开项目本地测试页，使用模拟响应验证全流程，不调用外部 API。'
};
let state = null, csrf = '', hydrated = false, lastEvent = 0, activePrompt = null;
let toastTimer, connected = false, posting = false;
const emptyLog = $('#activity-log').firstElementChild.cloneNode(true);

function notify(message, error=false) {
  const box = $('#toast'); box.textContent = message; box.hidden = false;
  box.classList.toggle('error', error); clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {box.hidden = true;}, error ? 8500 : 4500);
}
function navigate(name, focus=true) {
  if (!pageNames[name]) name = 'workspace';
  $$('.page').forEach(page => {page.hidden = page.id !== 'page-' + name; page.classList.toggle('active',!page.hidden);});
  $$('.nav-item').forEach(item => {
    const active = item.dataset.page === name;
    item.classList.toggle('active',active);
    if (active) item.setAttribute('aria-current','page'); else item.removeAttribute('aria-current');
  });
  $('#page-name').textContent = pageNames[name];
  if (location.hash !== '#' + name) history.pushState(null,'','#' + name);
  if (focus) {$('#main').focus({preventScroll:true}); window.scrollTo({top:0});}
}
$$('[data-page],[data-go]').forEach(button => button.addEventListener('click',() => navigate(button.dataset.page || button.dataset.go)));
window.addEventListener('hashchange',() => navigate(location.hash.slice(1)));
navigate(location.hash.slice(1) || 'workspace',false);

async function api(path, data) {
  const response = await fetch('/api/' + path, {method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':csrf},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作失败，请重试。');
  return result;
}
function readValue(input) {
  if (input.type === 'checkbox') return input.checked;
  if (input.type === 'number') return input.valueAsNumber;
  return input.value.trim();
}
function fillFields(form, values, nested=false) {
  for (const input of [...form.elements]) {
    if (!input.name) continue;
    const [section,field] = input.name.split('.');
    const value = nested ? values[section]?.[field] : values[input.name];
    if (value === undefined) continue;
    if (input.type === 'checkbox') input.checked = value;
    else if (input.type === 'radio') input.checked = input.value === value;
    else input.value = value;
  }
}
function collect(form, nested=false) {
  const output = {};
  for (const input of [...form.elements]) {
    if (!input.name || input.disabled || input.type === 'radio' && !input.checked) continue;
    if (nested) {
      const [section,field] = input.name.split('.');
      (output[section] ||= {})[field] = readValue(input);
    } else output[input.name] = readValue(input);
  }
  return output;
}
function hydrate() {
  fillFields($('#api-form'),state.config.llm);
  fillFields($('#browser-form'),state.config.browser);
  fillFields($('#settings-form'),state.config,true);
  updatePaceFields();
  hydrated = true;
}
function updatePaceFields() {
  const custom = $('#settings-form input[name="pacing.mode"]:checked')?.value === 'custom';
  $('.custom-delays').classList.toggle('inactive',!custom);
  $$('.custom-delays input').forEach(input => {input.disabled = !custom || Boolean(state?.running);});
}
$$('input[name="pacing.mode"]').forEach(input => input.addEventListener('change',updatePaceFields));

function renderPrompt(prompt) {
  if (prompt?.id === activePrompt?.id) return;
  activePrompt = prompt;
  $('#prompt-form').hidden = !prompt;
  $('#interaction-empty').hidden = Boolean(prompt);
  $('#interaction-panel').classList.toggle('awaiting',Boolean(prompt));
  $('#prompt-state').textContent = prompt ? '需要你的回应' : (state.running ? '正在执行' : '暂无待回应提示');
  if (!prompt) return;
  $('#prompt-text').textContent = prompt.text;
  $('#prompt-value').value = '';
  $('#prompt-value').type = prompt.sensitive ? 'password' : 'text';
  $('#prompt-value').placeholder = prompt.sensitive ? '输入完整页面地址，不会回显或保存' : '按上方提示输入';
  const fill = prompt.text.includes('输入 y 回填');
  const rescan = prompt.text.includes('重新扫描');
  $('#prompt-enter').hidden = !(prompt.text.includes('Enter') || rescan);
  $('#prompt-enter').textContent = rescan ? '结束本次处理' : '登录完成，继续';
  $('#prompt-skip').hidden = !fill;
  $('#prompt-rescan').hidden = !rescan;
}
function renderLog(events) {
  const log = $('#activity-log');
  if (!events.length) {
    if (!log.querySelector('.log-empty')) log.replaceChildren(emptyLog.cloneNode(true));
    return;
  }
  const fresh = events.filter(event => event.id > lastEvent);
  if (!fresh.length) return;
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 50;
  if (log.querySelector('.log-empty')) log.replaceChildren();
  for (const event of fresh) {
    const line = document.createElement('div'); line.className = 'log-line';
    if (event.level === 'warning') line.classList.add('warning');
    const timestamp = event.time ? '[' + new Date(event.time).toLocaleTimeString('zh-CN',{hour12:false}) + '] ' : '';
    line.textContent = timestamp + event.text; log.append(line); lastEvent = event.id;
  }
  while (log.children.length > 400) log.firstElementChild.remove();
  if (atBottom) log.scrollTop = log.scrollHeight;
}
function render() {
  const config = state.config;
  const busy = state.running;
  document.body.classList.toggle('task-running',busy);
  $('#key-status').textContent = state.key_set ? 'API Key 已配置' : 'API 未配置';
  $('#api-badge').textContent = state.key_set ? 'Key 已设置 · 隐藏' : '尚未设置 Key';
  $('#key-field-label').textContent = state.key_set ? '已设置，留空保留' : '隐藏输入';
  $('#summary-answer').textContent = (config.answering.mode === 'fast' ? '快速批量' : '逐题请求') + ' ↗';
  $('#summary-pace').textContent = paceNames[config.pacing.mode] + ' ↗';
  $('#summary-browser').textContent = ({auto:'自动选择',attach:'连接已有',launch:'新实例'}[config.browser.mode]) + ' ↗';
  $('#start-task').disabled = busy || !connected || posting;
  $('#stop-task').disabled = !busy || state.cancelling;
  $('#stop-task').textContent = state.cancelling ? '正在安全停止…' : '停止任务';
  $('#run-badge').textContent = state.cancelling ? '正在停止' : (busy ? '执行中' : '准备就绪');
  $('#job-status').textContent = state.cancelling ? '正在安全停止' : (state.prompt ? '等待你的回应' : stageNames[state.stage]);
  ['api-form','browser-form','settings-form'].forEach(id => {
    [...$('#' + id).elements].forEach(input => {if (input.id !== 'detect-browsers') input.disabled = busy;});
  });
  $('#clear-settings').disabled = busy;
  for (const id of ['task-action','trial-question','target-url','selectors-path']) $('#' + id).disabled = busy;
  updatePaceFields();
  const hasRun = state.stage !== 'idle';
  $('#count-total').textContent = hasRun ? state.counts.total : '—';
  $('#count-verified').textContent = hasRun ? state.counts.verified : '—';
  $('#count-manual').textContent = hasRun ? state.counts.manual + state.counts.failed : '—';
  $('#progress-bar').style.width = (state.counts.total ? Math.min(100,state.counts.verified/state.counts.total*100) : 0) + '%';
  $('#progress-note').textContent = hasRun ? '核验数含重新扫描；跳过 ' + state.counts.skipped + ' 题。最终提交由你完成。' : '开始后显示实际处理结果。';
  const stage = ['solving'].includes(state.stage) ? 1 : ['filling','finished'].includes(state.stage) ? 2 : 0;
  ['prepare','solve','fill'].forEach((name,index) => {const step = $('#step-' + name);step.classList.toggle('current',index === stage);step.classList.toggle('done',index < stage);});
  renderPrompt(state.prompt); renderLog(state.events);
}
async function refresh() {
  const response = await fetch('/api/state',{cache:'no-store'});
  if (!response.ok) throw new Error('控制台连接失效，请刷新页面。');
  state = await response.json(); csrf = state.csrf; connected = true;
  $('#connection-label').textContent = '本机已连接'; $('#connection-dot').classList.remove('offline');
  if (!hydrated) hydrate(); render();
}
async function heartbeat() {
  try {await refresh();} catch (_) {
    connected = false; $('#connection-label').textContent = '连接已断开'; $('#connection-dot').classList.add('offline');
    $('#start-task').disabled = true;
  } finally {setTimeout(heartbeat,1000);}
}
async function saveConfig(payload, form) {
  const submit = form.querySelector('[type="submit"]'); submit.disabled = true;
  try {await api('config',payload); await refresh(); notify('已应用到本次控制台；没有写入配置文件。');}
  catch (error) {notify(error.message,true);}
  finally {submit.disabled = Boolean(state?.running);}
}
$('#api-form').addEventListener('submit',async event => {
  event.preventDefault(); const payload = {llm:collect(event.currentTarget)};
  const key = $('#api-key').value;
  if (key) payload.key = key;
  await saveConfig(payload,event.currentTarget); $('#api-key').value = '';
});
$('#browser-form').addEventListener('submit',async event => {event.preventDefault(); await saveConfig({browser:collect(event.currentTarget)},event.currentTarget);});
$('#settings-form').addEventListener('submit',async event => {event.preventDefault(); await saveConfig(collect(event.currentTarget,true),event.currentTarget);});

function selectAction() {
  const action = $('#task-action').value;
  $('#action-help').textContent = actionHelp[action];
  $('#trial-field').hidden = action !== 'trial';
  $('#url-field').hidden = action !== 'run';
  $('#external-note').textContent = action === 'demo' ? '本地演练不使用密钥，不发送外部模型请求。只处理项目内的测试页面。' : action === 'inspect' ? '只读检查不调用模型，也不修改网页答案。此入口使用现有超星检查适配器。' : '真实模型会接收题干、选项与空位数量。默认允许外传，可在“答题与节奏”恢复每次确认。';
}
$('#task-action').addEventListener('change',selectAction);
$('#choose-demo').addEventListener('click',() => {$('#task-action').value = 'demo';selectAction();$('#start-task').focus();});
$('#toggle-url').addEventListener('click',() => {const show = $('#target-url').type === 'password';$('#target-url').type = show ? 'text' : 'password';$('#toggle-url').textContent = show ? '隐藏' : '显示';$('#toggle-url').setAttribute('aria-label',show ? '隐藏页面地址' : '显示页面地址');});
$('#task-form').addEventListener('submit',async event => {
  event.preventDefault(); if (posting || !state || state.running) return;
  posting = true; render();
  const action = $('#task-action').value;
  const payload = {action, question:$('#trial-question').valueAsNumber,
    selectors_path:$('#selectors-path').value.trim(), url:action === 'demo' ? state.demo_url : (action === 'run' ? $('#target-url').value.trim() : '')};
  try {await api('start',payload);$('#target-url').value = '';await refresh();notify('任务已启动，请查看运行记录和交互区。');}
  catch (error) {notify(error.message,true);}
  finally {posting = false;render();}
});
$('#stop-task').addEventListener('click',async () => {
  try {await api('stop',{});await refresh();notify('已请求安全停止；正在等待的 API 请求可能需要等到返回或超时。');}
  catch (error) {notify(error.message,true);}
});
async function reply(value) {
  if (!activePrompt) return;
  const id = activePrompt.id;
  $('#prompt-form button[type="submit"]').disabled = true;
  try {await api('reply',{id,value});$('#prompt-value').value = '';await refresh();}
  catch (error) {notify(error.message,true);}
  finally {$('#prompt-form button[type="submit"]').disabled = false;}
}
$('#prompt-form').addEventListener('submit',event => {event.preventDefault();reply($('#prompt-value').value);});
$('#prompt-enter').addEventListener('click',() => reply(''));
$('#prompt-skip').addEventListener('click',() => reply('n'));
$('#prompt-rescan').addEventListener('click',() => reply('r'));
$('#clear-settings').addEventListener('click',() => {$('#clear-confirmation').value = '';$('#confirm-clear').disabled = true;$('#clear-dialog').showModal();});
$('#clear-confirmation').addEventListener('input',() => {$('#confirm-clear').disabled = $('#clear-confirmation').value !== 'CLEAR';});
$('#confirm-clear').addEventListener('click',async () => {
  try {await api('clear',{confirmation:$('#clear-confirmation').value});$('#clear-dialog').close();await refresh();fillFields($('#api-form'),state.config.llm);$('#api-key').value = '';notify('API 参数已恢复启动值，当前工具 Key 已移除；文件和业务数据未改动。');}
  catch (error) {notify(error.message,true);}
});
$('#detect-browsers').addEventListener('click',async () => {
  const button = $('#detect-browsers'); button.disabled = true;button.textContent = '正在检测…';
  try {
    const {report} = await api('detect',{}); const box = $('#detection-result');box.replaceChildren();
    const status = document.createElement('p');status.className = 'detection-status';
    const names = {available:'可以连接',unavailable:'暂不可连接',non_cdp_service:'端口不是 CDP 服务',connection_timeout:'连接超时'};
    status.textContent = 'CDP：' + (names[report.cdp_status] || report.cdp_status) + (report.product ? ' · ' + report.product : '');box.append(status);
    for (const browser of report.browsers) {
      const item = document.createElement('div');item.className = 'browser-result';
      const detail = document.createElement('div');const title = document.createElement('strong');
      title.textContent = {chrome:'Google Chrome',edge:'Microsoft Edge',chromium:'Chromium'}[browser.kind];
      const path = document.createElement('small');path.textContent = browser.executable_path;
      const note = document.createElement('small');note.textContent = '路径存在 · 尚未启动验证 · 依据：' + browser.evidence;
      detail.append(title,path,note);item.append(detail);
      const use = document.createElement('button');use.type = 'button';use.className = 'button secondary small';use.textContent = '使用此路径';use.disabled = Boolean(state.running);
      use.addEventListener('click',() => {const form = $('#browser-form');form.elements.mode.value = 'launch';form.elements.kind.value = browser.kind;form.elements.executable_path.value = browser.executable_path;form.elements.cdp_url.value = '';notify('已填入启动草稿，请点击“应用浏览器设置”生效。');});
      item.append(use);box.append(item);
    }
    if (!report.browsers.length) {const empty = document.createElement('p');empty.textContent = '未发现可执行文件。可手动指定路径，或从终端确认安装项目 Chromium。';box.append(empty);}
  } catch (error) {notify(error.message,true);}
  finally {button.disabled = false;button.textContent = '检测本机浏览器 ↗';}
});
selectAction();heartbeat();
