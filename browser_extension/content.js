function askPage(payload, timeoutMs = 90000) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => finish(new Error('读取 Instagram 数据超时')), timeoutMs);
    function listener(event) {
      if (event.source !== window || event.data?.type !== 'ASTRBOT_INS_PAGE_RESPONSE' || event.data.id !== id) return;
      event.data.ok ? finish(null, event.data.data) : finish(new Error(event.data.error || '页面读取失败'));
    }
    function finish(error, value) {
      clearTimeout(timer);
      window.removeEventListener('message', listener);
      error ? reject(error) : resolve(value);
    }
    window.addEventListener('message', listener);
    window.postMessage({type: 'ASTRBOT_INS_PAGE_REQUEST', id, payload}, location.origin);
  });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== 'ASTRBOT_INS_SCAN') return;
  askPage({account: message.account, sources: message.sources, scanLimit: message.scanLimit})
    .then(data => sendResponse({ok: true, data}))
    .catch(error => sendResponse({ok: false, error: String(error.message || error)}));
  return true;
});
