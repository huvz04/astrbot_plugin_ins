"""Fault-injection tests of the real plugin using a minimal AstrBot test double."""
import asyncio
import importlib.util
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


def load_plugin():
    modules = {name: types.ModuleType(name) for name in (
        'astrbot', 'astrbot.api', 'astrbot.api.event', 'astrbot.api.message_components',
        'astrbot.api.star', 'astrbot.core', 'astrbot.core.utils',
        'astrbot.core.utils.astrbot_path', 'ins_test_plugin')}
    modules['ins_test_plugin'].__path__ = [str(Path(__file__).resolve().parents[1])]

    def group(name):
        def decorate(fn):
            fn.command = lambda name: lambda handler: handler
            return fn
        return decorate

    class Star:
        def __init__(self, context):
            self.context = context

    class Component:
        def __init__(self, value):
            self.value = value

        @classmethod
        def fromFileSystem(cls, path):
            return cls(path)

    modules['astrbot.api'].AstrBotConfig = dict
    modules['astrbot.api'].logger = logging.getLogger('ins-test')
    modules['astrbot.api.event'].AstrMessageEvent = object
    modules['astrbot.api.event'].MessageChain = list
    modules['astrbot.api.event'].filter = types.SimpleNamespace(
        command_group=group, permission_type=lambda permission: lambda fn: fn,
        PermissionType=types.SimpleNamespace(ADMIN=1))
    for name in ('Plain', 'Image', 'Video'):
        setattr(modules['astrbot.api.message_components'], name, Component)
    modules['astrbot.api.star'].Star = Star
    modules['astrbot.api.star'].Context = object
    modules['astrbot.core.utils.astrbot_path'].get_astrbot_plugin_data_path = lambda: '.'
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(
            'ins_test_plugin.main', Path(__file__).resolve().parents[1] / 'main.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


plugin = load_plugin()


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = types.SimpleNamespace(send_message=AsyncMock(return_value=True))
        with patch.object(plugin, 'get_astrbot_plugin_data_path', return_value=self.temp.name):
            self.bot = plugin.InsPlugin(self.context, {'send_media': True})
        self.bot.store.add('group', 'account')
        self.sub = self.bot.store.subscriptions()[0][0]
        self.item = dict(key='post:1', time=1, label='post', caption='caption', url='link',
                         media=[dict(video=False, url='cdn'), dict(video=True, url='cdn2')])
        self.bot.store.ingest(self.sub, 'posts', [])
        self.bot.store.ingest(self.sub, 'posts', [self.item])
        self.bot.instagram.download = Mock(return_value='downloaded.jpg')

    async def asyncTearDown(self):
        await self.bot.terminate()
        self.temp.cleanup()

    async def test_resume_after_second_media_failure(self):
        self.context.send_message.side_effect = [True, True, False]
        with patch.object(plugin.asyncio, 'sleep', new=AsyncMock()):
            with self.assertRaises(RuntimeError):
                await self.bot.deliver(self.sub, 'group', 'account', 'post:1', self.item, 0)
            row = self.bot.store.pending(self.sub)[0]
            self.assertEqual(row[2], 2)
            self.context.send_message.reset_mock(side_effect=True)
            self.context.send_message.return_value = True
            await self.bot.deliver(self.sub, 'group', 'account', *row)
        self.assertEqual(self.context.send_message.await_count, 1)
        self.assertEqual(self.bot.store.pending(self.sub), [])

    async def test_one_target_failure_does_not_block_another(self):
        self.bot.store.add('other', 'account')
        other = self.bot.store.subscriptions('other')[0][0]
        self.bot.store.ingest(other, 'posts', [])
        self.bot.instagram.fetch = Mock(return_value=({'posts': [self.item]}, {}))
        self.bot.config['send_media'] = False

        async def send(target, message):
            return target != 'group'

        self.context.send_message.side_effect = send
        with patch.object(plugin.asyncio, 'sleep', new=AsyncMock()):
            await self.bot.check()
        self.assertEqual(len(self.bot.store.pending(self.sub, include_deferred=True)), 1)
        self.assertEqual(self.bot.store.pending(other), [])
        self.bot.instagram.fetch.assert_called_once_with('account')

    async def test_failed_source_does_not_initialize_baseline(self):
        self.bot.instagram.fetch = Mock(return_value=({}, {'stories': 'failed'}))
        self.bot.config['send_media'] = False
        with patch.object(plugin.asyncio, 'sleep', new=AsyncMock()):
            await self.bot.check()
        self.assertIsNone(self.bot.store.db.execute(
            "SELECT 1 FROM streams WHERE source='stories'").fetchone())
        self.assertGreater(self.bot.retry['account'], 0)

    async def test_cancellation_waits_for_worker_to_finish(self):
        import threading
        started, release = threading.Event(), threading.Event()

        def worker():
            started.set()
            release.wait(timeout=3)

        task = asyncio.create_task(self.bot.blocking(worker))
        await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_fetch_backoff_does_not_pause_saved_deliveries(self):
        import time
        self.bot.retry['account'] = time.time() + 3600
        self.bot.instagram.fetch = Mock()
        self.bot.config['send_media'] = False
        self.bot.store.defer(self.sub, 'post:1')
        with patch.object(plugin.asyncio, 'sleep', new=AsyncMock()):
            await self.bot.check()
        self.bot.instagram.fetch.assert_not_called()
        self.context.send_message.assert_awaited_once()
        self.assertEqual(self.bot.store.pending(self.sub, include_deferred=True), [])
        self.assertGreater(self.bot.retry['account'], time.time())
