"""AstrBot commands, scheduling and media delivery."""
import asyncio
import contextlib
import hashlib
import copy
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
        self.manual_task = None
        self.progress = {}

    async def initialize(self):
        self.task = asyncio.create_task(self.scheduler())

    def save_config(self):
        if hasattr(self.config, 'save_config'):
            self.config.save_config()

    @staticmethod
    def row_origin(row):
        if row.get('umo'):
            return row['umo']
        platform = str(row.get('platform_id', '')).strip()
        target = str(row.get('target_id', '')).strip()
        kind = row.get('target_type', '群聊')
        if not platform or not target or ':' in platform or ':' in target or kind not in ('群聊', '私聊'):
            raise ValueError('后台订阅的机器人连接 ID、目标 ID 或目标类型不正确')
        return f'{platform}:{"GroupMessage" if kind == "群聊" else "FriendMessage"}:{target}'

    @staticmethod
    def new_row(origin, accounts):
        parts = origin.split(':', 2)
        row = dict(__template_key='target', label=origin, enabled=True, accounts=','.join(sorted(accounts)))
        if len(parts) == 3 and parts[1] in ('GroupMessage', 'FriendMessage'):
            row.update(platform_id=parts[0], target_id=parts[2],
                       target_type='群聊' if parts[1] == 'GroupMessage' else '私聊')
        else:
            row['umo'] = origin
        return row

    def selected_subscriptions(self, origin=None):
        rows = copy.deepcopy(self.config.get('subscriptions', []))
        migrated = self.store.db.execute("SELECT 1 FROM settings WHERE key='shared_config'").fetchone()
        if not migrated:
            # Old command subscriptions are imported once; old dashboard mode retains its exact selection.
            if not self.config.get('dashboard_control', False):
                known = {(self.row_origin(r), username(a)) for r in rows
                         for a in str(r.get('accounts', '')).replace('，', ',').split(',') if a.strip()}
                for _, target, account in self.store.subscriptions():
                    if (target, account) not in known:
                        rows.append(self.new_row(target, [account]))
            self.config['subscriptions'] = rows
            self.save_config()
            with self.store.db:
                self.store.db.execute("INSERT INTO settings VALUES ('shared_config','1')")
        desired, retained = set(), set()
        for row in rows:
            accounts = str(row.get('accounts', '')).strip()
            if not accounts:
                continue
            target = self.row_origin(row)
            for part in accounts.replace('，', ',').split(','):
                pair = (target, username(part))
                retained.add(pair)
                if row.get('enabled', True):
                    desired.add(pair)
        # Configuration is the sole subscription source; SQLite only mirrors it for delivery progress.
        for target, account in retained:
            self.store.add(target, account)
        for _, target, account in self.store.subscriptions():
            if (target, account) not in retained:
                self.store.remove(target, account)
        return [r for r in self.store.subscriptions(origin) if (r[1], r[2]) in desired]

    def edit_subscriptions(self, origin, accounts, action):
        self.selected_subscriptions()
        incoming = {username(a) for a in accounts.replace('，', ',').split(',')}
        rows = copy.deepcopy(self.config.get('subscriptions', []))
        matched = [r for r in rows if self.row_origin(r) == origin]
        existing = {username(a) for r in matched for a in r.get('accounts', '').replace('，', ',').split(',') if a.strip()}
        selected = incoming if action == 'set' else (existing | incoming if action == 'add' else existing - incoming)
        rows = [r for r in rows if self.row_origin(r) != origin]
        if selected:
            row = matched[0] if matched else self.new_row(origin, selected)
            row.update(accounts=','.join(sorted(selected)), enabled=True)
            rows.append(row)
        previous = self.config.get('subscriptions', [])
        self.config['subscriptions'] = rows
        try:
            self.save_config()
        except Exception:
            self.config['subscriptions'] = previous
            raise
        self.selected_subscriptions()
        return sorted(selected)

    async def blocking(self, function, *args):
        # Cancellation must not release the lock while an Instaloader worker still runs.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            started = time.monotonic()
            while not task.done():
                finished, _ = await asyncio.wait({task}, timeout=15)
                if not finished:
                    logger.info('Ins 后台任务仍在执行：%s，耗时 %.0f 秒',
                                getattr(function, '__name__', 'request'), time.monotonic() - started)
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
            all_subscriptions = self.selected_subscriptions()
            requested = {r[2] for r in all_subscriptions if origin is None or r[1] == origin}
            subscriptions = [r for r in all_subscriptions if r[2] in requested]
            accounts = sorted({row[2] for row in subscriptions})
            for account in accounts:
                fetching = time.time() >= self.retry.get(account, 0)
                results, errors = {}, {}
                if fetching:
                    try:
                        started = time.monotonic()
                        def report(message, name=account):
                            self.progress[name] = (started, message)
                            logger.info('Ins @%s：%s', name, message)
                        self.instagram.report = report
                        report('开始检查')
                        results, errors = await self.blocking(self.instagram.fetch, account)
                        logger.info('Ins @%s 检查完成，耗时 %.1f 秒，获取 %s，错误 %s',
                                    account, time.monotonic() - started,
                                    {k: len(v) for k, v in results.items()}, errors)
                    except Exception as exc:
                        results, errors = {}, {'account': error_message(exc)}
                        logger.warning('Ins @%s 检查失败：%s', account, errors['account'])
                    finally:
                        self.progress.pop(account, None)
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
                    if (sub, target, name) not in self.selected_subscriptions():
                        continue
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
                    if fetching:
                        self.status[(target, account)] += f'，获取 {sum(len(v) for v in results.values())} 条'
                    if target_notices:
                        self.status[(target, account)] += '\n' + '\n'.join(target_notices)
                    logger.info('Ins @%s -> %s：%s', account, target, self.status[(target, account)])
                # A small gap between accounts, in addition to Instaloader rate control.
                if account != accounts[-1]:
                    await asyncio.sleep(3)
            self.clean_cache()

    async def send(self, target, component):
        result = await asyncio.wait_for(
            self.context.send_message(target, MessageChain([component])), timeout=60)
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
        try:
            account = username(account)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        async with self.lock:
            self.edit_subscriptions(event.unified_msg_origin, account, 'add')
            added = True
        yield event.plain_result(
            f'已订阅 @{account} 到当前会话。首次成功检查建立基线，之后推送新增内容。'
            '\n可用 /ins check 立即初始化，/ins list 查看状态。' if added else '当前会话已订阅该账号。')

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ins.command('set')
    async def set_command(self, event: AstrMessageEvent, accounts: str):
        event.stop_event()
        try:
            async with self.lock:
                selected = self.edit_subscriptions(event.unified_msg_origin, accounts, 'set')
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result('当前会话的订阅已设为：' + '、'.join('@' + name for name in selected)
                                 + '。其他群和私聊不受影响。新增账号首次检查只建立基线。')

    @filter.permission_type(filter.PermissionType.ADMIN)
    @ins.command('remove')
    async def remove_command(self, event: AstrMessageEvent, account: str):
        event.stop_event()
        try:
            account = username(account)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        async with self.lock:
            self.edit_subscriptions(event.unified_msg_origin, account, 'remove')
            removed = True
        yield event.plain_result('已取消订阅。' if removed else '当前会话未订阅该账号。')

    def listing(self, origin):
        rows = self.selected_subscriptions(origin)
        if not rows:
            return '当前会话暂无订阅。使用 /ins add 用户名 添加。'
        lines = []
        for _, _, account in rows:
            current = self.progress.get(account)
            state = (f'{current[1]}，已耗时 {time.monotonic() - current[0]:.0f} 秒'
                     if current else self.status.get((origin, account), '等待检查'))
            lines.append(f'@{account}：{state}')
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
        if self.manual_task and not self.manual_task.done():
            yield event.plain_result('已有手动检查任务，请用 /ins list 查看进度。')
            return
        self.manual_task = asyncio.create_task(self.manual_check(event.unified_msg_origin))
        yield event.plain_result('已启动后台检查；用 /ins list 查看当前阶段，管理后台日志可查看请求结果。')

    async def manual_check(self, origin):
        try:
            await self.check(origin)
            await self.send(origin, Plain(self.listing(origin)))
        except Exception as exc:
            logger.warning('Ins 手动检查失败：%s', error_message(exc))

    async def terminate(self):
        if self.manual_task:
            self.manual_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.manual_task
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        async with self.lock:
            await self.blocking(self.instagram.close)
            self.store.close()
