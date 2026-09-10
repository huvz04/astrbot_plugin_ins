import unittest
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import instaloader
from instagram import Instagram


def post(mediaid=1):
    return NS(typename='GraphImage', is_video=False, url='https://cdn/image.jpg',
              mediaid=mediaid, date_utc=datetime(2026, 9, 10), caption='caption',
              shortcode='abc')


class InstagramTests(unittest.TestCase):
    def test_carousel_keeps_image_video_order(self):
        p = post()
        p.typename = 'GraphSidecar'
        p.get_sidecar_nodes = lambda: [NS(is_video=False, display_url='image'),
                                       NS(is_video=True, video_url='video')]
        result = Instagram.post(p)
        self.assertEqual(result['media'], [dict(video=False, url='image'),
                                          dict(video=True, url='video')])

    def test_partial_failure_retains_successful_stream_only(self):
        adapter = Instagram(dict(enable_highlights=False))
        adapter.loader = Mock()
        adapter.loader.context.is_logged_in = True
        adapter.loader.get_stories.side_effect = instaloader.LoginRequiredException('secret')
        profile = Mock(userid=42)
        profile.get_posts.return_value = iter([post()])
        profile.get_reels.return_value = iter([post()])
        with patch('instagram.instaloader.Profile.from_username', return_value=profile):
            results, errors = adapter.fetch('account')
        self.assertEqual(set(results), {'posts', 'reels'})
        self.assertEqual(set(errors), {'stories'})
        self.assertNotIn('secret', str(errors))

    def test_fatal_status_does_not_request_more_endpoints(self):
        adapter = Instagram({})
        adapter.loader = Mock()
        profile = Mock()
        profile.get_posts.side_effect = instaloader.AbortDownloadException('429 secret')
        with patch('instagram.instaloader.Profile.from_username', return_value=profile):
            results, errors = adapter.fetch('account')
        self.assertEqual(results, {})
        self.assertIn('posts', errors)
        profile.get_reels.assert_not_called()

    def test_anonymous_story_is_reported_not_successfully_empty(self):
        adapter = Instagram(dict(enable_posts=False, enable_reels=False))
        adapter.loader = Mock()
        adapter.loader.context.is_logged_in = False
        with patch('instagram.instaloader.Profile.from_username', return_value=Mock()):
            results, errors = adapter.fetch('account')
        self.assertEqual(results, {})
        self.assertEqual(set(errors), {'stories', 'highlights'})

    def test_story_and_highlight_share_identity(self):
        story = NS(mediaid=12, date_utc=datetime(2026, 9, 10), is_video=True,
                   video_url='video')
        a = Instagram.story(story, 'account')
        b = Instagram.story(story, 'account', NS(title='精选', unique_id=123))
        self.assertEqual(a['key'], b['key'])
        self.assertIn('/highlights/123/', b['url'])

    def test_real_instaloader_initializes_offline(self):
        adapter = Instagram({})
        try:
            loader = adapter.connect()
            self.assertFalse(loader.context.is_logged_in)
            self.assertTrue(callable(instaloader.Profile.get_reels))
        finally:
            adapter.close()
