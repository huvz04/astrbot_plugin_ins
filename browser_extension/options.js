const baseUrl = document.querySelector('#baseUrl');
const token = document.querySelector('#token');
const notice = document.querySelector('#notice');

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
  const saved = await chrome.storage.local.get(['baseUrl', 'token', 'status']);
  baseUrl.value = saved.baseUrl || '';
  token.value = saved.token || '';
  if (saved.status) {
    const time = saved.status.finishedAt ? new Date(saved.status.finishedAt).toLocaleString() : '';
    show([saved.status.message, saved.status.error, time].filter(Boolean).join('\n'), Boolean(saved.status.error));
  }
}
document.querySelector('#save').addEventListener('click', async () => {
  try {
    const value = normalize(baseUrl.value);
    const key = token.value.trim();
    if (!key) throw new Error('请填写桥接密钥');
    const origin = new URL(value).origin + '/*';
    const granted = await chrome.permissions.request({origins: [origin]});
    if (!granted) throw new Error('需要允许扩展访问这个 AstrBot 地址');
    await chrome.storage.local.set({baseUrl: value, token: key});
    const response = await fetch(value + '/astrbot_plugin_ins/bridge/config', {
      headers: {'Authorization': 'Bearer ' + key},
    });
    let body = {};
    try { body = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(body.message || body.error || `连接失败（HTTP ${response.status}）`);
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
