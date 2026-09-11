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
      fetch_comment_count: 40,
      has_threaded_comments: true,
      parent_comment_count: 24,
      shortcode,
    }).toPromise();
    const data = response?.data || response;
    const media = first(data?.xdt_shortcode_media, data?.shortcode_media,
      data?.data?.xdt_shortcode_media, data?.data?.shortcode_media);
    return first(
      media?.__fragments?.PolarisPostActionLoadPostQueryInlineFragment,
      media?.__fragments?.PolarisPostActionLoadPostQueryInlineFragmentWithoutRelatedProfiles,
      media,
    );
  }

  async function apiGet(instapi, path, query) {
    const response = await instapi.apiGet(path, {query});
    return unwrap(response);
  }

  function instagramHeaders() {
    let moduleAppId = '';
    let moduleClaim = '';
    try { moduleAppId = window.require?.('PolarisConfig')?.getIGAppID?.() || ''; } catch (_) {}
    try { moduleClaim = window.require?.('PolarisWWWClaim')?.getWWWClaim?.() || ''; } catch (_) {}
    const appId = moduleAppId || sessionStorage.getItem('__ig_app_id') || '936619743392459';
    const wwwClaim = moduleClaim || sessionStorage.getItem('__ig_www_claim') ||
      sessionStorage.getItem('www-claim-v2') || '0';
    const csrf = String(document.cookie || '').match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || '';
    return {
      'x-ig-app-id': appId, 'x-ig-www-claim': wwwClaim,
      'x-requested-with': 'XMLHttpRequest', 'x-asbd-id': '129477',
      ...(csrf ? {'x-csrftoken': csrf} : {}),
    };
  }

  async function rawAccountFeed(account, query) {
    const url = new URL(
      `https://www.instagram.com/api/v1/feed/user/${encodeURIComponent(account)}/username/`);
    for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
    const response = await fetch(url.href, {
      headers: instagramHeaders(), credentials: 'include', redirect: 'follow',
    });
    let text = '';
    try { text = await response.text(); } catch (_) {}
    const cleaned = text.replace(/^\s*for\s*\(\s*;\s*;\s*\)\s*;?\s*/, '');
    let body;
    try { body = JSON.parse(cleaned); } catch (_) {
      const kind = /^\s*</.test(text) ? 'HTML 页面' : '非 JSON 内容';
      throw new Error(`原始请求返回 ${kind}（HTTP ${response.status}）`);
    }
    if (!response.ok) {
      throw new Error(String(first(body?.message, body?.error, `HTTP ${response.status}`)));
    }
    return body;
  }

  async function accountFeedPage(instapi, account, query) {
    const errors = [];
    const path = `/api/v1/feed/user/${encodeURIComponent(account)}/username/`;
    if (instapi) {
      try {
        const body = await apiGet(instapi, path, query);
        if (Array.isArray(body?.items)) return {body, method: 'instagram-module'};
        errors.push('页面模块返回结构无法解析');
      } catch (error) {
        errors.push(`页面模块：${String(error.message || error)}`);
      }
    }
    try {
      const body = await rawAccountFeed(account, query);
      if (Array.isArray(body?.items)) return {body, method: 'raw-fetch'};
      errors.push('原始请求返回结构无法解析');
    } catch (error) {
      errors.push(String(error.message || error));
    }
    throw new Error(errors.join('；'));
  }

  async function fetchAccountFeed(instapi, account, requested, limit) {
    const found = {posts: [], reels: []};
    const candidates = {posts: 0, reels: 0};
    const seen = new Set();
    let maxId = '';
    let pages = 0;
    let feedItems = 0;
    let ownerId = '';
    const methods = new Set();
    for (; pages < 5; pages++) {
      const query = {count: '12'};
      if (maxId) query.max_id = maxId;
      const page = await accountFeedPage(instapi, account, query);
      const body = page.body;
      methods.add(page.method);
      if (!Array.isArray(body?.items)) throw new Error('Instagram 账号媒体接口返回结构无法解析');
      feedItems += body.items.length;
      for (const node of body.items) {
        ownerId ||= String(first(node?.user?.pk, node?.user?.id,
          node?.owner?.pk, node?.owner?.id, ''));
        const source = node?.product_type === 'clips' ? 'reels' : 'posts';
        if (!requested.has(source) || found[source].length >= limit) continue;
        candidates[source] += 1;
        const ownerName = String(first(node?.user?.username, node?.owner?.username, account)).toLowerCase();
        const item = normalizePost(node, source === 'reels' ? 'reel' : 'p', account);
        if (!item || ownerName !== account || seen.has(`${source}:${item.id}`)) continue;
        seen.add(`${source}:${item.id}`);
        found[source].push(item);
      }
      const complete = ['posts', 'reels'].every(source =>
        !requested.has(source) || found[source].length >= limit);
      maxId = String(first(body.next_max_id, ''));
      if (complete || !body.more_available || !maxId || !body.items.length) {
        pages += 1;
        break;
      }
    }
    for (const source of ['posts', 'reels']) {
      if (requested.has(source) && candidates[source] && !found[source].length) {
        throw new Error(`${source} 账号接口返回 ${candidates[source]} 条，但内容结构无法解析`);
      }
    }
    return {found, candidates, pages, feedItems, ownerId, method: [...methods].join('+')};
  }

  async function fetchLinkedPosts(account, requested, limit) {
    const relay = await requireModule('CometRelay');
    const environment = await requireModule('PolarisRelayEnvironment');
    const query = await requireModule('PolarisPostActionLoadPostQuery');
    const counts = {posts: 0, reels: 0};
    const attempts = {posts: 0, reels: 0};
    const successes = {posts: 0, reels: 0};
    const normalized = {posts: 0, reels: 0};
    const found = {posts: [], reels: []};
    let ownerId = '';
    const links = await loadShortcodeLinks(limit);
    for (const link of links) {
      const source = link.kind === 'reel' ? 'reels' : 'posts';
      if (!requested.has(source) || counts[source] >= limit) continue;
      attempts[source] += 1;
      try {
        const node = await fetchPost(relay, environment, query, link.shortcode);
        if (!node) continue;
        successes[source] += 1;
        ownerId ||= String(first(node?.owner?.id, node?.owner?.pk, ''));
        const item = normalizePost(node, link.kind, account);
        if (item) normalized[source] += 1;
        const ownerName = String(first(node?.owner?.username, account)).toLowerCase();
        if (item && ownerName === account) { found[source].push(item); counts[source] += 1; }
      } catch (_) {}
    }
    for (const source of ['posts', 'reels']) {
      if (attempts[source] && !normalized[source]) {
        throw new Error(`${source} 发现 ${attempts[source]} 个链接，但 Instagram 返回结构无法解析`);
      }
    }
    return {found, ownerId, links: links.length, attempts, successes, normalized};
  }

  async function scan(payload) {
    const account = String(payload.account || '').toLowerCase();
    const requested = new Set(payload.sources || []);
    const limit = Math.max(1, Math.min(30, Number(payload.scanLimit) || 30));
    const result = {account, sources: {}, diagnostics: {}};
    const highlightIds = [];
    const highlightTitles = new Map();
    if (requested.has('highlights')) {
      for (const anchor of document.querySelectorAll('a[href*="/stories/highlights/"]')) {
        const match = anchor.href.match(/\/stories\/highlights\/(\d+)/);
        if (match && !highlightIds.includes(match[1])) highlightIds.push(match[1]);
        if (highlightIds.length >= limit) break;
      }
    }

    const needsPosts = requested.has('posts') || requested.has('reels');
    let ownerId = '';
    let instapi = null;
    if (needsPosts) {
      try {
        try { instapi = await requireModule('PolarisInstapi', 5000); } catch (_) {}
        const feed = await fetchAccountFeed(instapi, account, requested, limit);
        ownerId = feed.ownerId;
        result.diagnostics.feedPages = feed.pages;
        result.diagnostics.feedItems = feed.feedItems;
        result.diagnostics.feedMethod = feed.method;
        for (const source of ['posts', 'reels']) {
          if (requested.has(source)) result.sources[source] = feed.found[source];
          result.diagnostics[source] = {
            attempts: feed.candidates[source], parsed: feed.found[source].length,
            method: 'account-feed',
          };
        }
      } catch (feedError) {
        result.diagnostics.feedError = String(feedError.message || feedError);
        const linked = await fetchLinkedPosts(account, requested, limit);
        ownerId = linked.ownerId;
        result.diagnostics.links = linked.links;
        for (const source of ['posts', 'reels']) {
          result.diagnostics[source] = {
            attempts: linked.attempts[source], responses: linked.successes[source],
            parsed: linked.normalized[source], method: 'page-links',
          };
          if (requested.has(source) && linked.attempts[source] && linked.normalized[source]) {
            result.sources[source] = linked.found[source];
          }
        }
      }
    }

    if (requested.has('stories') || requested.has('highlights')) {
      instapi ||= await requireModule('PolarisInstapi');
      if (!ownerId) {
        try {
          const profile = await apiGet(instapi, '/api/v1/users/web_profile_info/', {username: account});
          const user = first(profile?.user, profile?.data?.user);
          ownerId = String(first(user?.id, user?.pk, ''));
        } catch (_) {}
      }
      let highlightTrayConfirmed = false;
      if (requested.has('highlights') && /^\d+$/.test(ownerId)) {
        try {
          const trayResponse = await apiGet(
            instapi, `/api/v1/highlights/${ownerId}/highlights_tray/`, {});
          const tray = first(trayResponse?.tray, trayResponse?.data?.tray);
          if (!Array.isArray(tray)) throw new Error('精选列表结构无法解析');
          highlightTrayConfirmed = true;
          result.diagnostics.highlightTray = tray.length;
          for (const reel of tray) {
            const id = String(first(reel?.id, reel?.reel_id, reel?.pk, '')).replace(/^highlight:/, '');
            if (!/^\d+$/.test(id) || highlightIds.includes(id)) continue;
            highlightIds.push(id);
            highlightTitles.set(id, String(first(reel?.title, reel?.highlight_title, '精选')));
            if (highlightIds.length >= limit) break;
          }
        } catch (error) {
          result.diagnostics.highlightTrayError = String(error.message || error);
        }
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
          const reelsMedia = first(feed?.reels_media, feed?.data?.reels_media);
          if (requested.has('stories') && /^\d+$/.test(ownerId)) result.sources.stories = [];
          if (requested.has('highlights') && (highlightIds.length || highlightTrayConfirmed)) {
            result.sources.highlights = [];
          }
          const entries = Array.isArray(reelsMedia)
            ? reelsMedia.map((reel, index) => [String(first(reel?.id, reel?.reel_id, reelIds[index], '')), reel])
            : Object.entries(reels || {});
          result.diagnostics.storyReels = entries.length;
          for (const [key, reelMedia] of entries) {
            const requestedId = reelIds.find(id => id === key || id.endsWith(`:${key}`)) || '';
            const reel = first(reels?.[key], reels?.[requestedId], reelMedia);
            const isHighlight = requestedId.startsWith('highlight:') || key.startsWith('highlight:') ||
              String(reel?.id || '').startsWith('highlight:') ||
              String(reel?.reel_type || '').includes('highlight');
            const source = isHighlight ? 'highlights' : 'stories';
            if (!requested.has(source)) continue;
            const highlightId = String(first(reel?.id, key, requestedId, '')).replace(/^highlight:/, '');
            const title = isHighlight ? String(first(
              reel?.title, reel?.highlight_title, highlightTitles.get(highlightId), '精选')) : '';
            result.sources[source].push(...(first(reelMedia?.items, reel?.items, [])).map(item =>
              normalizeStory(item, account, title)).filter(Boolean));
          }
        } catch (_) {
          // Successful post streams can still be ingested when Story endpoints are unavailable.
        }
      } else if (requested.has('highlights') && highlightTrayConfirmed) {
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
