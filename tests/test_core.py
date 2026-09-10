import tempfile
import unittest
from pathlib import Path

from core import Store, username


def item(key, timestamp=1, url='https://example.com/media'):
    return dict(key=key, time=timestamp, label='post', caption='', url='post',
                media=[dict(url=url, video=False)])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.store.add('group-a', 'test')
        self.sub = self.store.subscriptions()[0][0]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_baseline_new_and_restart(self):
        self.store.ingest(self.sub, 'posts', [item('old')])
        self.assertEqual(self.store.pending(self.sub), [])
        self.store.ingest(self.sub, 'posts', [item('old'), item('new', 2)])
        self.store.advance(self.sub, 'new', 1, False)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual([(r[0], r[2]) for r in self.store.pending(self.sub)], [('new', 1)])
        self.store.advance(self.sub, 'new', 2, True)
        self.store.ingest(self.sub, 'posts', [item('new', 2)])
        self.assertEqual(self.store.pending(self.sub), [])

    def test_destinations_have_independent_baselines_and_progress(self):
        self.store.ingest(self.sub, 'posts', [])
        self.store.ingest(self.sub, 'posts', [item('new')])
        self.store.add('group-b', 'test')
        other = self.store.subscriptions('group-b')[0][0]
        self.store.ingest(other, 'posts', [item('new')])
        self.assertEqual(len(self.store.pending(self.sub)), 1)
        self.assertEqual(self.store.pending(other), [])

    def test_cross_source_duplicates_and_late_baseline_preserve_pending(self):
        self.store.ingest(self.sub, 'posts', [])
        self.store.ingest(self.sub, 'posts', [item('shared')])
        self.store.ingest(self.sub, 'reels', [item('shared')])
        self.assertEqual(len(self.store.pending(self.sub)), 1)
        self.store.advance(self.sub, 'shared', 2, True)
        self.store.ingest(self.sub, 'reels', [item('shared')])
        self.assertEqual(self.store.pending(self.sub), [])

    def test_refresh_signed_url_keeps_progress(self):
        self.store.ingest(self.sub, 'posts', [])
        self.store.ingest(self.sub, 'posts', [item('new')])
        self.store.advance(self.sub, 'new', 1, False)
        self.store.ingest(self.sub, 'posts', [item('new', url='fresh')])
        row = self.store.pending(self.sub)[0]
        self.assertEqual(row[1]['media'][0]['url'], 'fresh')
        self.assertEqual(row[2], 1)

    def test_remove_cascades_only_one_destination(self):
        self.store.ingest(self.sub, 'posts', [item('old')])
        self.store.add('group-b', 'test')
        self.store.remove('group-a', 'test')
        self.assertEqual(len(self.store.subscriptions()), 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)

    def test_order_and_limit(self):
        self.store.ingest(self.sub, 'posts', [])
        self.store.ingest(self.sub, 'posts', [item('new', 3), item('old', 2)])
        self.assertEqual(self.store.pending(self.sub, 1)[0][0], 'old')

    def test_usernames(self):
        self.assertEqual(username('https://www.instagram.com/Test.User/?igsh=123'), 'test.user')
        self.assertEqual(username('@TEST'), 'test')
        for value in ('../path', 'https://evil.com/user', 'https://instagram.com/p/abc', ''):
            with self.assertRaises(ValueError):
                username(value)

    def test_failed_batch_does_not_starve_newer_updates(self):
        self.store.ingest(self.sub, 'posts', [])
        self.store.ingest(self.sub, 'posts', [item(str(i), i) for i in range(11)])
        for key, _, _ in self.store.pending(self.sub, 10):
            self.store.defer(self.sub, key)
        self.assertEqual([r[0] for r in self.store.pending(self.sub)], ['10'])
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual([r[0] for r in self.store.pending(self.sub)], ['10'])
        self.assertEqual(len(self.store.pending(self.sub, 20, include_deferred=True)), 11)


if __name__ == '__main__':
    unittest.main()
