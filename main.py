"""AstrBot commands, scheduling and media delivery."""
import asyncio
import contextlib
import hashlib
import random
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain, Video
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .core import Store, username
from .instagram import Instagram, error_message

HELP = '''Instagram 订阅推送（修改和手动检查需 AstrBot 管理员权限）
/ins add 用户名 — 订阅到当前群或私聊，首次成功检查只建立基线
/ins set 账号甲,账号乙 — 替换当前群或私聊的账号列表（英文逗号分隔，不加空格）
/ins remove 用户名 — 取消当前会话的订阅
/ins list — 查看当前会话订阅和运行状态
/ins check — 立即检查当前会话订阅（遵守失败退避）
默认获取帖子、Reels、Story、精选；登录信息在插件配置中设置。'''


class InsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.folder = Path(get_astrbot_plugin_data_path()) / 'astrbot_plugin_ins'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.cache = self.folder / 'media'
        self.cache.mkdir(exist_ok=True)
        self.store = Store(self.folder / 'state.sqlite3')
        self.instagram = Instagram(config)
        self.lock = asyncio.Lock()
        self.task = None
        self.status = {}
        self.retry = {}
        self.failures = {}

    async def initialize(self):
        self.task = asyncio.create_task(self.scheduler())

    def selected_subscriptions(self, origin=None):
        if not self.config.get('dashboard_control', False):
            return self.store.subscriptions(origin)
        desired = set()
        for row in self.config.get('subscriptions', []):
            if not row.get('enabled', True):
                continue
            accounts = str(row.get('accounts', '')).strip()
            if not accounts:
                continue
            platform = str(row.get('platform_id', '')).strip()
            target = str(row.get('target_id', '')).strip()
            kind = row.get('target_type', '群聊')
            if not platform or not target or ':' in platform or ':' in target or kind not in ('群聊', '私聊'):
                raise ValueError('后台订阅的机器人连接 ID、目标 ID 或目标类型不正确')
            session = f'{platform}:{"GroupMessage" if kind == "群聊" else "FriendMessage"}:{target}'
            for part in accounts.replace('，', ',').split(','):
                desired.add((session, username(part)))
        # Validate the entire configuration before changing state or sending anything.
        for session, account in desired:
            self.store.add(session, account)
        return [row for row in self.store.subscriptions(origin) if (row[1], row[2]) in desired]

    async def blocking(self, function, *args):
        # Cancellation must not release the lock while an Instaloader worker still runs.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await task
            raise

    async def scheduler(self):
        await asyncio.sleep(5)
        while True:
            try:
                if self.config.get('enabled', True):
                    await self.check()
            except Exception as exc:
                logger.warning('Ins 调度异常：%s', type(exc).__name__)
            interval = max(300, int(self.config.get('interval_seconds', 900)))
            await asyncio.sleep(interval + random.uniform(0, interval * 0.1))

    async def check(self, origin=None):
        async with self.lock:
            subscriptions = self.selected_subscriptions(origin)
            accounts = sorted({row[2] for row in subscriptions})
            for account in accounts:
                fetching = time.time() >= self.retry.get(account, 0)
                results, errors = {}, {}
                if fetching:
                    try:
                        results, errors = await self.blocking(self.instagram.fetch, account)
                    except Exception as exc:
                        results, errors = {}, {'account': error_message(exc)}
                transient = any(value != '需要配置 Instagram 登录会话。'
                                for value in errors.values())
                if transient:
                    self.failures[account] = self.failures.get(account, 0) + 1
                    delay = min(21600, max(300, int(self.config.get('interval_seconds', 900)))
                                * 2 ** min(self.failures[account], 5))
                    self.retry[account] = time.time() + delay
                elif fetching:
                    self.failures[account] = 0
                    self.retry.pop(account, None)
                notices = [f'{key}: {value}' for key, value in errors.items()]
                if not fetching:
                    notices.append('抓取退避中，本轮只处理已保存的待发内容')
                for sub, target, name in subscriptions:
                    if name != account:
                        continue
                    delivered = 0
                    target_notices = list(notices)
                    for source, items in results.items():
                        self.store.ingest(sub, source, items)
                    for key, item, position in self.store.pending(
                            sub, max(1, min(100, int(self.config.get('push_limit', 10)))),
                            include_deferred=not self.config.get('send_media', True)):
                        try:
                            await self.deliver(sub, target, name, key, item, position)
                            delivered += 1
                        except Exception as exc:
                            self.store.defer(sub, key)
                            target_notices.append(f'发送失败（{type(exc).__name__}），保留队列待重试')
                            continue
                    timestamp = time.strftime('%m-%d %H:%M')
                    self.status[(target, account)] = f'{timestamp}，推送 {delivered} 条'
                    if target_notices:
                        self.status[(target, account)] += '\n' + '\n'.join(target_notices)
                # A small gap between accounts, in addition to Instaloader rate control.
                if account != accounts[-1]:
                    await asyncio.sleep(3)
            self.clean_cache()

    async def send(self, target, component):
        result = await self.context.send_message(target, MessageChain([component]))
        if result is False:
            raise RuntimeError('adapter rejected message')

    async def deliver(self, sub, target, account, key, item, position):
        text = f"Instagram @{account} · {item['label']}\n{item['caption']}\n{item['url']}"
        # Separate messages suit adapters which cannot mix video, images and text.
        media = item['media'] if self.config.get('send_media', True) else []
        total = 1 + len(media)
        for index in range(position, total):
            if index == 0:
                component = Plain(text)
            else:
                medium = media[index - 1]
                digest = hashlib.sha256(f'{account}:{key}:{index}'.encode()).hexdigest()
                path = self.cache / (digest + ('.mp4' if medium['video'] else '.jpg'))
                local = await self.blocking(self.instagram.download, medium, path)
                component = Video.fromFileSystem(local) if medium['video'] else Image.fromFileSystem(local)
            await self.send(target, component)
            self.store.advance(sub, key, index + 1, index + 1 == total)
            await asyncio.sleep(0.5)
        # Configuration may have switched from media to link-only during a retry.
        if position >= total:
            self.store.advance(sub, key, position, True)

    def clean_cache(self):
        cutoff = time.time() - 86400
        for path in self.cache.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                with contextlib.suppress(OSError):
                    path.unlink()

    @filter.command_group('ins')
    def ins(self):
        pass

    @ins.command('help')
    async def help_command(self, event: AstrMessageEvent):
        event.stop_event()
        yield event.plain_result(HELP)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ins.command('add')
    async def add_command(self, event: AstrMessageEvent, account: str):
        event.stop_event()
        if self.config.get('dashboard_control', False):
            yield event.plain_result('当前由管理后台维护订阅，请在插件配置中修改。')
            return
        try:
            account = username(account)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        async with self.lock:
            added = self.store.add(event.unified_msg_origin, account)
        yield event.plain_result(
            f'已订阅 @{account} 到当前会话。首次成功检查建立基线，之后推送新增内容。'
            '\n可用 /ins check 立即初始化，/ins list 查看状态。' if added else '当前会话已订阅该账号。')

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ins.command('set')
    async def set_command(self, event: AstrMessageEvent, accounts: str):
        event.stop_event()
        if self.config.get('dashboard_control', False):
            yield event.plain_result('当前由管理后台维护订阅，请在插件配置中修改。')
            return
        try:
            async with self.lock:
                selected = self.store.set_accounts(event.unified_msg_origin, accounts)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result('当前会话的订阅已设为：' + '、'.join('@' + name for name in selected)
                                 + '。其他群和私聊不受影响。新增账号首次检查只建立基线。')

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ins.command('remove')
    async def remove_command(self, event: AstrMessageEvent, account: str):
        event.stop_event()
        if self.config.get('dashboard_control', False):
            yield event.plain_result('当前由管理后台维护订阅，请在插件配置中修改。')
            return
        try:
            account = username(account)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        async with self.lock:
            removed = self.store.remove(event.unified_msg_origin, account)
        yield event.plain_result('已取消订阅。' if removed else '当前会话未订阅该账号。')

    def listing(self, origin):
        rows = self.selected_subscriptions(origin)
        if not rows:
            return '当前会话暂无订阅。使用 /ins add 用户名 添加。'
        lines = []
        for _, _, account in rows:
            lines.append(f'@{account}：{self.status.get((origin, account), "等待检查")}')
            retry = self.retry.get(account, 0) - time.time()
            if retry > 0:
                lines.append(f'退避剩余 {int(retry / 60) + 1} 分钟')
        return '\n'.join(lines)

    @ins.command('list')
    async def list_command(self, event: AstrMessageEvent):
        event.stop_event()
        yield event.plain_result(self.listing(event.unified_msg_origin))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ins.command('check')
    async def check_command(self, event: AstrMessageEvent):
        event.stop_event()
        if self.lock.locked():
            yield event.plain_result('已有检查正在进行，请稍后用 /ins list 查看结果。')
            return
        yield event.plain_result('正在检查 Instagram 更新，首次获取可能需要一些时间。')
        await self.check(event.unified_msg_origin)
        yield event.plain_result(self.listing(event.unified_msg_origin))

    async def terminate(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        async with self.lock:
            await self.blocking(self.instagram.close)
            self.store.close()
