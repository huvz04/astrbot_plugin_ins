import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const listeners = [];
const responses = [];
let feedModuleFails = false;
const postNode = {
  pk: '1001', code: 'POST1', taken_at: 1700000000, product_type: 'feed',
  user: {pk: '2001', username: 'example'},
  image_versions2: {candidates: [{url: 'https://scontent.cdninstagram.com/post.jpg'}]},
  caption: {text: 'caption'},
};
const reelNode = {
  pk: '1002', code: 'REEL1', taken_at: 1700000002, product_type: 'clips',
  user: {pk: '2001', username: 'example'},
  video_versions: [{url: 'https://scontent.cdninstagram.com/reel.mp4'}],
};
const storyItem = id => ({
  pk: id, taken_at: 1700000001,
  image_versions2: {candidates: [{url: `https://scontent.cdninstagram.com/${id}.jpg`}]},
});

const context = {
  console,
  setTimeout: callback => { callback(); return 1; },
  URL,
  location: {origin: 'https://www.instagram.com'},
  sessionStorage: {getItem() { return null; }},
  fetch: async () => { throw new Error('页面模块成功时不应调用原始 fetch'); },
  document: {
    cookie: 'csrftoken=test-token',
    documentElement: {scrollHeight: 1000},
    querySelectorAll(selector) {
      if (selector.includes('/stories/highlights/')) return [];
      return [{href: 'https://www.instagram.com/p/POST1/'}];
    },
  },
};
context.window = {
  addEventListener(type, listener) { if (type === 'message') listeners.push(listener); },
  postMessage(message) { responses.push(message); },
  scrollTo() {},
  require(name) {
    if (name === 'CometRelay') return {fetchQuery: () => ({
      toPromise: async () => ({xdt_shortcode_media: {__fragments: {
        PolarisPostActionLoadPostQueryInlineFragment: postNode,
      }}}),
    })};
    if (name === 'PolarisRelayEnvironment') return {};
    if (name === 'PolarisPostActionLoadPostQuery') return {POST_QUERY: {}};
    if (name === 'PolarisConfig') return {getIGAppID: () => 'module-app-id'};
    if (name === 'PolarisWWWClaim') return {getWWWClaim: () => 'module-claim'};
    if (name === 'PolarisInstapi') return {apiGet: async (path, options) => {
      if (path.includes('/feed/user/')) {
        assert.equal(options.query.count, '12');
        if (feedModuleFails) throw new Error('module rejected');
        return {data: {items: [postNode, reelNode], more_available: false}};
      }
      if (path.includes('/highlights/')) {
        return {data: {tray: [{id: 'highlight:3001', title: '精选集'}]}};
      }
      return {data: {
        reels: {'2001': {}, 'highlight:3001': {title: '精选集'}},
        reels_media: [
          {id: '2001', items: [storyItem('4001')]},
          {id: '3001', reel_type: 'highlight_reel', items: [storyItem('4002')]},
        ],
      }};
    }};
    return null;
  },
};
vm.runInNewContext(fs.readFileSync('browser_extension/page.js', 'utf8'), context);
assert.equal(listeners.length, 1);
await listeners[0]({
  source: context.window,
  data: {type: 'ASTRBOT_INS_PAGE_REQUEST', id: 'request-1', payload: {
    account: 'example', sources: ['posts', 'reels', 'stories', 'highlights'], scanLimit: 1,
  }},
});

const response = responses.find(item => item.type === 'ASTRBOT_INS_PAGE_RESPONSE');
assert.equal(response.ok, true);
assert.equal(response.data.sources.posts[0].id, '1001');
assert.equal(response.data.sources.reels[0].id, '1002');
assert.equal(response.data.sources.stories[0].id, '4001');
assert.equal(response.data.sources.highlights[0].id, '4002');
assert.equal(response.data.diagnostics.posts.parsed, 1);
assert.equal(response.data.diagnostics.reels.parsed, 1);
assert.equal(response.data.diagnostics.feedItems, 2);
assert.equal(response.data.diagnostics.feedMethod, 'instagram-module');
assert.equal(response.data.diagnostics.highlightTray, 1);

feedModuleFails = true;
context.fetch = async (url, init) => {
  assert.match(url, /^https:\/\/www\.instagram\.com\/api\/v1\/feed\/user\/example\/username\//);
  assert.equal(init.headers['x-ig-app-id'], 'module-app-id');
  assert.equal(init.headers['x-ig-www-claim'], 'module-claim');
  assert.equal(init.headers['x-csrftoken'], 'test-token');
  return {ok: true, status: 200, text: async () => '<!doctype html><title>Instagram</title>'};
};
await listeners[0]({
  source: context.window,
  data: {type: 'ASTRBOT_INS_PAGE_REQUEST', id: 'request-2', payload: {
    account: 'example', sources: ['posts'], scanLimit: 1,
  }},
});
const fallback = responses.find(item => item.id === 'request-2');
assert.equal(fallback.ok, true);
assert.equal(fallback.data.sources.posts[0].id, '1001');
assert.equal(fallback.data.diagnostics.posts.method, 'page-links');
assert.match(fallback.data.diagnostics.feedError, /页面模块：module rejected/);
assert.match(fallback.data.diagnostics.feedError, /原始请求返回 HTML 页面（HTTP 200）/);
