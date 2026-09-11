(() => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function requireModule(name, timeoutMs = 15000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      try {
        const module = window.require?.(name);
        if (module) return module;
      } catch (_) {}
      await sleep(250);
    }
    throw new Error(`Instagram 模块 ${name} 当前不可用，请确认已经登录并刷新页面`);
  }

  function first(...values) { return values.find(value => value !== undefined && value !== null); }
  function candidateImage(node) {
    const candidates = node?.image_versions2?.candidates || [];
    return first(candidates[0]?.url, node?.display_url, node?.display_src, node?.thumbnail_src);
  }
  function mediaParts(node) {
    const children = node?.edge_sidecar_to_children?.edges?.map(edge => edge.node) ||
      node?.carousel_media || [node];
    return children.map(child => {
      const videoUrl = first(child?.video_url, child?.video_versions?.[0]?.url);
      const url = videoUrl || candidateImage(child);
      return url ? {video: Boolean(videoUrl || child?.is_video), url} : null;
    }).filter(Boolean);
  }
  function caption(node) {
    return String(first(node?.edge_media_to_caption?.edges?.[0]?.node?.text,
      node?.caption?.text, node?.caption, '') || '');
  }

  function normalizePost(node, kind, account) {
    const id = String(first(node?.id, node?.pk, ''));
    const shortcode = String(first(node?.shortcode, node?.code, ''));
    const time = Number(first(node?.taken_at_timestamp, node?.taken_at, 0));
    if (!/^\d+$/.test(id) || !time || !mediaParts(node).length) return null;
    const reel = kind === 'reel' || node?.product_type === 'clips';
    return {
      id, time, shortcode,
      label: reel ? 'Reel' : '帖子',
      caption: caption(node),
      url: `https://www.instagram.com/${reel ? 'reel' : 'p'}/${shortcode}/`,
      media: mediaParts(node),
    };
  }

  function normalizeStory(item, account, highlightTitle = '') {
    const id = String(first(item?.pk, item?.id, ''));
    const time = Number(first(item?.taken_at, item?.taken_at_timestamp, 0));
    if (!/^\d+$/.test(id) || !time || !mediaParts(item).length) return null;
    return {
      id, time, shortcode: String(first(item?.code, '')),
      label: highlightTitle ? `精选：${highlightTitle}` : 'Story',
      caption: caption(item),
      url: `https://www.instagram.com/stories/${account}/${id}/`,
      media: mediaParts(item),
    };
  }

  function shortcodeLinks(limit) {
    const seen = new Set();
    const links = [];
    for (const anchor of document.querySelectorAll('a[href*="/p/"],a[href*="/reel/"]')) {
      const match = anchor.href.match(/instagram\.com\/(p|reel)\/([^/?#]+)/);
      if (!match) continue;
      const key = `${match[1]}:${match[2]}`;
      if (!seen.has(key)) links.push({kind: match[1], shortcode: match[2]});
      seen.add(key);
      if (links.length >= limit * 2) break;
    }
    return links;
  }

  async function loadShortcodeLinks(limit) {
    const collected = new Map();
    const collect = () => shortcodeLinks(limit).forEach(link =>
      collected.set(`${link.kind}:${link.shortcode}`, link));
    collect();
    let unchanged = 0;
    for (let round = 0; round < 8 && collected.size < limit * 2 && unchanged < 2; round++) {
      const before = collected.size;
      window.scrollTo(0, document.documentElement.scrollHeight);
      await sleep(800);
      collect();
      unchanged = collected.size === before ? unchanged + 1 : 0;
    }
    return [...collected.values()];
  }

  function unwrap(value) {
    let current = value;
    for (let i = 0; i < 4; i++) {
      const next = first(current?.data, current?.payload);
      if (!next || next === current) break;
      current = next;
    }
    return current;
  }

  async function fetchPost(relay, environment, query, shortcode) {
    const response = await relay.fetchQuery(environment, query.POST_QUERY, {
      child_comment_count: 3,
      fetch_comment_count: 0,
      has_threaded_comments: true,
      parent_comment_count: 0,
      shortcode,
    }).toPromise();
    const data = response?.data || response;
    return first(data?.xdt_shortcode_media, data?.shortcode_media,
      data?.data?.xdt_shortcode_media, data?.data?.shortcode_media);
  }

  async function apiGet(instapi, path, query) {
    const response = await instapi.apiGet(path, {query});
    return unwrap(response);
  }

  async function scan(payload) {
    const account = String(payload.account || '').toLowerCase();
    const requested = new Set(payload.sources || []);
    const limit = Math.max(1, Math.min(30, Number(payload.scanLimit) || 30));
    const result = {account, sources: {}};
    const highlightIds = [];
    if (requested.has('highlights')) {
      for (const anchor of document.querySelectorAll('a[href*="/stories/highlights/"]')) {
        const match = anchor.href.match(/\/stories\/highlights\/(\d+)/);
        if (match && !highlightIds.includes(match[1])) highlightIds.push(match[1]);
        if (highlightIds.length >= limit) break;
      }
    }

    const needsPosts = requested.has('posts') || requested.has('reels');
    let ownerId = '';
    if (needsPosts) {
      const relay = await requireModule('CometRelay');
      const environment = await requireModule('PolarisRelayEnvironment');
      const query = await requireModule('PolarisPostActionLoadPostQuery');
      const counts = {posts: 0, reels: 0};
      const attempts = {posts: 0, reels: 0};
      const successes = {posts: 0, reels: 0};
      const found = {posts: [], reels: []};
      for (const link of await loadShortcodeLinks(limit)) {
        const source = link.kind === 'reel' ? 'reels' : 'posts';
        if (!requested.has(source) || counts[source] >= limit) continue;
        attempts[source] += 1;
        try {
          const node = await fetchPost(relay, environment, query, link.shortcode);
          if (!node) continue;
          successes[source] += 1;
          ownerId ||= String(first(node?.owner?.id, node?.owner?.pk, ''));
          const item = normalizePost(node, link.kind, account);
          const ownerName = String(first(node?.owner?.username, account)).toLowerCase();
          if (item && ownerName === account) { found[source].push(item); counts[source] += 1; }
        } catch (_) {}
      }
      for (const source of ['posts', 'reels']) {
        if (requested.has(source) && (!attempts[source] || successes[source])) {
          result.sources[source] = found[source];
        }
      }
    }

    if (requested.has('stories') || requested.has('highlights')) {
      const instapi = await requireModule('PolarisInstapi');
      if (!ownerId) {
        try {
          const profile = await apiGet(instapi, '/api/v1/users/web_profile_info/', {username: account});
          const user = first(profile?.user, profile?.data?.user);
          ownerId = String(first(user?.id, user?.pk, ''));
        } catch (_) {}
      }
      const reelIds = [];
      if (requested.has('stories') && /^\d+$/.test(ownerId)) reelIds.push(ownerId);
      if (requested.has('highlights')) reelIds.push(...highlightIds.map(id => `highlight:${id}`));
      if (reelIds.length) {
        try {
          const feed = await apiGet(instapi, '/api/v1/feed/reels_media/', {
            media_id: '', reel_ids: reelIds.join(','),
          });
          const reels = first(feed?.reels, feed?.data?.reels, {});
          if (requested.has('stories') && /^\d+$/.test(ownerId)) result.sources.stories = [];
          if (requested.has('highlights')) result.sources.highlights = [];
          for (const [key, reel] of Object.entries(reels || {})) {
            const isHighlight = key.startsWith('highlight:') || String(reel?.id || '').startsWith('highlight:');
            const source = isHighlight ? 'highlights' : 'stories';
            if (!requested.has(source)) continue;
            const title = isHighlight ? String(first(reel?.title, reel?.highlight_title, '精选')) : '';
            result.sources[source].push(...(reel?.items || []).map(item =>
              normalizeStory(item, account, title)).filter(Boolean));
          }
        } catch (_) {
          // Successful post streams can still be ingested when Story endpoints are unavailable.
        }
      } else if (requested.has('highlights') && !highlightIds.length) {
        result.sources.highlights = [];
      }
    }
    if (!Object.keys(result.sources).length) throw new Error('Instagram 没有返回任何可确认的内容类型');
    return result;
  }

  window.addEventListener('message', async event => {
    const message = event.data;
    if (event.source !== window || message?.type !== 'ASTRBOT_INS_PAGE_REQUEST' || !message.id) return;
    try {
      const data = await scan(message.payload || {});
      window.postMessage({type: 'ASTRBOT_INS_PAGE_RESPONSE', id: message.id, ok: true, data}, location.origin);
    } catch (error) {
      window.postMessage({type: 'ASTRBOT_INS_PAGE_RESPONSE', id: message.id, ok: false,
                          error: String(error.message || error)}, location.origin);
    }
  });
})();
