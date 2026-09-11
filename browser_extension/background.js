const ALARM = "astrbot-ins-scan";
const DEFAULT_INTERVAL_MINUTES = 15;
const API_PATH = '/api/v1/plugins/extensions/astrbot_plugin_ins/bridge/';

function cleanBaseUrl(value) {
  const url = new URL(String(value || "").trim());
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('AstrBot 地址必须是 HTTP 或 HTTPS');
  return url.origin + url.pathname.replace(/\/+$/, '');
}

async function settings() {
  const value = await chrome.storage.local.get(['baseUrl', 'apiKey', 'token']);
  if (!value.baseUrl || !value.apiKey || !value.token) {
    throw new Error('请先在扩展设置中填写 AstrBot 地址、API Key 和桥接密钥');
  }
  return {baseUrl: cleanBaseUrl(value.baseUrl), apiKey: value.apiKey.trim(), token: value.token.trim()};
}

async function api(path, init = {}) {
  const cfg = await settings();
  const response = await fetch(cfg.baseUrl + API_PATH + path, {
    ...init,
    headers: {
      'X-API-Key': cfg.apiKey,
      'X-AstrBot-Ins-Token': cfg.token,
      ...(init.body ? {'Content-Type': 'application/json'} : {}),
      ...(init.headers || {}),
    },
  });
  let body;
  try { body = await response.json(); } catch (_) { body = {}; }
  if (!response.ok || body.status === 'error') {
    const suffix = response.status === 404 ? '；请确认 AstrBot ≥ 4.26 且插件已重载' : '';
    throw new Error((body.message || body.error || `AstrBot 返回 HTTP ${response.status}`) + suffix);
  }
  return body.status === 'ok' && body.data !== undefined ? body.data : body;
}

function waitForLoad(tabId, timeoutMs = 45000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => finish(new Error('Instagram 页面加载超时')), timeoutMs);
    function listener(changedId, info) {
      if (changedId === tabId && info.status === 'complete') finish();
    }
    function finish(error) {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      error ? reject(error) : resolve();
    }
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then(tab => {
      if (tab.status === 'complete') finish();
    }).catch(finish);
  });
}

async function sendScan(tabId, payload) {
  const attempt = async () => {
    const reply = await chrome.tabs.sendMessage(tabId, {type: 'ASTRBOT_INS_SCAN', ...payload});
    if (!reply || !reply.ok) throw new Error(reply?.error || 'Instagram 页面桥接未响应');
    return reply.data;
  };
  try { return await attempt(); } catch (_) {
    await new Promise(resolve => setTimeout(resolve, 1500));
    return attempt();
  }
}

async function setStatus(patch) {
  const previous = (await chrome.storage.local.get('status')).status || {};
  await chrome.storage.local.set({status: {...previous, ...patch}});
}

async function performScan(manual = false) {
  const startedAt = Date.now();
  await setStatus({running: true, startedAt, error: ''});
  let tab;
  try {
    const config = await api('config');
    const intervalMinutes = Math.max(5, Math.ceil((config.interval_seconds || 900) / 60));
    await chrome.alarms.create(ALARM, {delayInMinutes: intervalMinutes, periodInMinutes: intervalMinutes});
    if (!config.enabled && !manual) {
      await setStatus({running: false, finishedAt: Date.now(), message: 'AstrBot 已暂停自动检查'});
      return;
    }
    if (!config.accounts?.length) {
      await setStatus({running: false, finishedAt: Date.now(), message: 'AstrBot 中暂无已启用订阅'});
      return;
    }
    tab = await chrome.tabs.create({url: 'https://www.instagram.com/', active: manual});
    let scanned = 0;
    const failures = [];
    const summaries = [];
    for (const account of config.accounts) {
      try {
        await chrome.tabs.update(tab.id, {
          url: `https://www.instagram.com/${encodeURIComponent(account)}/`, active: manual,
        });
        await waitForLoad(tab.id);
        await new Promise(resolve => setTimeout(resolve, 2500));
        const data = await sendScan(tab.id, {
          account,
          sources: config.sources || [],
          scanLimit: Math.max(1, Math.min(30, config.scan_limit || 30)),
        });
        await api('ingest', {method: 'POST', body: JSON.stringify(data)});
        scanned += 1;
        const counts = Object.entries(data.sources || {})
          .map(([source, items]) => `${source} ${items.length}`).join('、');
        const diagnostic = data.diagnostics?.feedPages
          ? `（Instagram 页面接口 ${data.diagnostics.feedItems} 条/${data.diagnostics.feedPages} 页）`
          : data.diagnostics?.links !== undefined
            ? `（账号接口失败，页面链接回退 ${data.diagnostics.links} 个：${data.diagnostics.feedError}）`
            : '';
        summaries.push(`@${account}：${counts || '没有可确认的内容类型'}${diagnostic}`);
        await setStatus({message: `已扫描 ${scanned}/${config.accounts.length}：@${account}`});
      } catch (error) {
        failures.push(`@${account}：${String(error.message || error)}`);
      }
    }
    await setStatus({running: false, finishedAt: Date.now(), error: failures.join('\n'),
                     message: [`扫描完成，成功 ${scanned}/${config.accounts.length} 个账号`,
                               ...summaries].join('\n')});
  } catch (error) {
    await setStatus({running: false, finishedAt: Date.now(), error: String(error.message || error)});
    throw error;
  } finally {
    if (tab?.id) chrome.tabs.remove(tab.id).catch(() => {});
  }
}

let scanInFlight = null;
function runScan(manual = false) {
  if (!scanInFlight) {
    scanInFlight = performScan(manual).finally(() => { scanInFlight = null; });
  }
  return scanInFlight;
}

chrome.runtime.onInstalled.addListener(async () => {
  await chrome.alarms.create(ALARM, {delayInMinutes: 1, periodInMinutes: DEFAULT_INTERVAL_MINUTES});
  chrome.runtime.openOptionsPage();
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(ALARM, {delayInMinutes: 1, periodInMinutes: DEFAULT_INTERVAL_MINUTES});
});
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === ALARM) runScan().catch(() => {});
});
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'ASTRBOT_INS_RUN_NOW') {
    runScan(true).then(() => sendResponse({ok: true})).catch(error =>
      sendResponse({ok: false, error: String(error.message || error)}));
    return true;
  }
  if (message?.type === 'ASTRBOT_INS_RESCHEDULE') {
    chrome.alarms.create(ALARM, {delayInMinutes: 0.5, periodInMinutes: DEFAULT_INTERVAL_MINUTES});
  }
});
