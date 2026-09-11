import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const listeners = [];
const responses = [];
const postNode = {
  id: '1001', shortcode: 'POST1', taken_at_timestamp: 1700000000,
  owner: {id: '2001', username: 'example'},
  display_url: 'https://scontent.cdninstagram.com/post.jpg',
  edge_media_to_caption: {edges: [{node: {text: 'caption'}}]},
};
const storyItem = id => ({
  pk: id, taken_at: 1700000001,
  image_versions2: {candidates: [{url: `https://scontent.cdninstagram.com/${id}.jpg`}]},
});

const context = {
  console,
  setTimeout: callback => { callback(); return 1; },
  location: {origin: 'https://www.instagram.com'},
  document: {
    documentElement: {scrollHeight: 1000},
    querySelectorAll(selector) {
      if (selector.includes('/stories/highlights/')) {
        return [{href: 'https://www.instagram.com/stories/highlights/3001/'}];
      }
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
    if (name === 'PolarisInstapi') return {apiGet: async () => ({data: {
      reels: {'2001': {}, 'highlight:3001': {title: '精选集'}},
      reels_media: [
        {id: '2001', items: [storyItem('4001')]},
        {id: '3001', reel_type: 'highlight_reel', items: [storyItem('4002')]},
      ],
    }})};
    return null;
  },
};
vm.runInNewContext(fs.readFileSync('browser_extension/page.js', 'utf8'), context);
assert.equal(listeners.length, 1);
await listeners[0]({
  source: context.window,
  data: {type: 'ASTRBOT_INS_PAGE_REQUEST', id: 'request-1', payload: {
    account: 'example', sources: ['posts', 'stories', 'highlights'], scanLimit: 1,
  }},
});

const response = responses.find(item => item.type === 'ASTRBOT_INS_PAGE_RESPONSE');
assert.equal(response.ok, true);
assert.equal(response.data.sources.posts[0].id, '1001');
assert.equal(response.data.sources.stories[0].id, '4001');
assert.equal(response.data.sources.highlights[0].id, '4002');
assert.equal(response.data.diagnostics.posts.parsed, 1);
