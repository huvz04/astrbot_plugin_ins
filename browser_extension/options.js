const baseUrl = document.querySelector('#baseUrl');
const apiKey = document.querySelector('#apiKey');
const token = document.querySelector('#token');
const notice = document.querySelector('#notice');
const API_PATH = '/api/v1/plugins/extensions/astrbot_plugin_ins/bridge/config';

function show(message, error = false) {
  notice.textContent = message;
  notice.className = error ? 'error' : 'success';
}
function normalize(value) {
  const url = new URL(value.trim());
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('地址必须以 http:// 或 https:// 开头');
  return url.origin + url.pathname.replace(/\/+$/, '');
}
async function refresh() {
  const saved = await chrome.storage.local.get(['baseUrl', 'apiKey', 'token', 'status']);
  baseUrl.value = saved.baseUrl || '';
  apiKey.value = saved.apiKey || '';
  token.value = saved.token || '';
  if (saved.status) {
    const time = saved.status.finishedAt ? new Date(saved.status.finishedAt).toLocaleString() : '';
    show([saved.status.message, saved.status.error, time].filter(Boolean).join('\n'), Boolean(saved.status.error));
  }
}
document.querySelector('#save').addEventListener('click', async () => {
  try {
    const value = normalize(baseUrl.value);
    const astrbotKey = apiKey.value.trim();
    const key = token.value.trim();
    if (!astrbotKey) throw new Error('请填写具有 plugin 权限的 AstrBot API Key');
    if (!key) throw new Error('请填写桥接密钥');
    const origin = new URL(value).origin + '/*';
    const granted = await chrome.permissions.request({origins: [origin]});
    if (!granted) throw new Error('需要允许扩展访问这个 AstrBot 地址');
    await chrome.storage.local.set({baseUrl: value, apiKey: astrbotKey, token: key});
    const response = await fetch(value + API_PATH, {
      headers: {'X-API-Key': astrbotKey, 'X-AstrBot-Ins-Token': key},
    });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok || body.status === 'error') {
      const suffix = response.status === 404 ? '；请确认 AstrBot ≥ 4.26 且插件已重载' : '';
      throw new Error((body.message || body.error || `连接失败（HTTP ${response.status}）`) + suffix);
    }
    if (body.status === 'ok' && body.data !== undefined) body = body.data;
    chrome.runtime.sendMessage({type: 'ASTRBOT_INS_RESCHEDULE'});
    show(`连接成功，AstrBot 返回 ${body.accounts?.length || 0} 个订阅账号。`);
  } catch (error) { show(String(error.message || error), true); }
});
document.querySelector('#run').addEventListener('click', async () => {
  show('正在扫描，请保持 Chrome 和 Instagram 页面可用……');
  const response = await chrome.runtime.sendMessage({type: 'ASTRBOT_INS_RUN_NOW'});
  if (response?.ok) { await refresh(); } else { show(response?.error || '扫描失败', true); }
});
refresh();
