"""Instaloader adapter. All methods run on a single background worker."""
import itertools
import json
import os
import re
import time
from datetime import timezone

import instaloader


class LoginConfigurationError(ValueError):
    pass


class BoundedRateController(instaloader.RateController):
    def __init__(self, context, owner):
        super().__init__(context)
        self.owner = owner

    def wait_before_query(self, query_type):
        self.owner.check_deadline()
        super().wait_before_query(query_type)

    def sleep(self, secs):
        if time.monotonic() + secs >= self.owner.deadline:
            raise TimeoutError('Instagram rate wait exceeds check budget')
        self.owner.report(f'限流等待 {secs:.0f} 秒')
        super().sleep(secs)


def parse_cookies(value):
    """Accept a browser Cookie header or a JSON name/value object."""
    value = value.strip()
    try:
        if value.startswith('{'):
            cookies = json.loads(value)
            if not isinstance(cookies, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in cookies.items()):
                raise ValueError()
        else:
            if value.lower().startswith('cookie:'):
                value = value.split(':', 1)[1].strip()
            cookies = {}
            for part in value.split(';'):
                if not part.strip():
                    continue
                key, content = part.strip().split('=', 1)
                cookies[key.strip()] = content.strip()
        if not cookies.get('sessionid') or not cookies.get('csrftoken'):
            raise ValueError()
        if any('\n' in v or '\r' in v for v in cookies.values()):
            raise ValueError()
        return cookies
    except (ValueError, TypeError):
        raise LoginConfigurationError(
            '登录 Cookie 格式不正确，需要包含 sessionid 和 csrftoken；'
            '请在插件配置中粘贴完整 Cookie 请求头或 JSON 键值对象。') from None


def error_message(exc):
    # Do not expose upstream exceptions: they may contain signed URLs or credentials.
    name = type(exc).__name__
    if isinstance(exc, LoginConfigurationError):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return '检查超时或限流等待超过预算，已停止本轮，稍后重试。'
    # Instaloader wraps fatal HTTP responses in AbortDownloadException. Read only
    # the status code; never expose the full exception because it can contain URLs.
    status = re.search(r'(?<!\d)(401|403|429)(?!\d)', str(exc))
    if status and status.group(1) == '401':
        return 'Instagram 返回 HTTP 401：登录状态无效或已过期，请更新 Cookie 后重载插件。'
    if status and status.group(1) == '403':
        return 'Instagram 返回 HTTP 403：当前登录状态、请求或网络出口被拒绝。'
    if status and status.group(1) == '429':
        return 'Instagram 返回 HTTP 429：资料接口被限流；即使首次请求也可能发生，请等待后重试。'
    if 'Login' in name or 'Unauthorized' in name:
        return '需要有效登录会话；请在插件配置中更新 Cookie（或会话文件）并重载插件。'
    if 'Private' in name:
        return '私密账号不可访问，请确认登录账号已获准关注。'
    if 'TooMany' in name:
        return 'Instagram 返回请求过多；将退避后重试。'
    if 'Abort' in name:
        return 'Instagram 拒绝了请求；将退避后重试，请查看同轮日志中的 HTTP 状态。'
    if 'NotExists' in name or 'NotFound' in name:
        return '账号或内容不存在，或当前登录账号无权访问。'
    return f'获取失败（{name}），请检查网络、登录会话及 Instagram 验证提示。'


class Instagram:
    def __init__(self, config):
        self.config = config
        self.loader = None
        self.deadline = float('inf')
        self.report = lambda message: None

    def check_deadline(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Instagram check budget exceeded')

    def connect(self):
        if self.loader is not None:
            return self.loader
        loader = instaloader.Instaloader(
            quiet=True, max_connection_attempts=1, request_timeout=30,
            rate_controller=lambda context: BoundedRateController(context, self),
            fatal_status_codes=[401, 403, 429])
        try:
            login = str(self.config.get('login_username', '')).strip()
            session = str(self.config.get('session_file', '')).strip()
            cookie = str(self.config.get('login_cookie', '')).strip()
            if cookie:
                if not login:
                    raise LoginConfigurationError('填写登录 Cookie 时也需要填写对应的登录用户名。')
                loader.load_session(login, parse_cookies(cookie))
            elif login:
                loader.load_session_from_file(login, session or None)
            elif session:
                raise ValueError('session_file requires login_username')
            # Instaloader has no public per-instance proxy API.
            proxy = str(self.config.get('proxy', '')).strip()
            if proxy:
                loader.context._session.proxies.update({'http': proxy, 'https': proxy})
        except Exception:
            loader.close()
            raise
        self.loader = loader
        return loader

    @staticmethod
    def post(post):
        if post.typename == 'GraphSidecar':
            media = [{'video': node.is_video,
                      'url': node.video_url if node.is_video else node.display_url}
                     for node in post.get_sidecar_nodes()]
        else:
            media = [{'video': post.is_video,
                      'url': post.video_url if post.is_video else post.url}]
        return {'key': f'post:{post.mediaid}', 'time': post.date_utc.replace(
                    tzinfo=timezone.utc).timestamp(),
                'label': '帖子 / Reels', 'caption': post.caption or '',
                'url': f'https://www.instagram.com/p/{post.shortcode}/', 'media': media}

    @staticmethod
    def story(item, account, highlight=None):
        return {'key': f'story:{item.mediaid}', 'time': item.date_utc.replace(
                    tzinfo=timezone.utc).timestamp(),
                'label': f'精选：{highlight.title}' if highlight else 'Story',
                'caption': '',
                'url': (f'https://www.instagram.com/stories/highlights/{highlight.unique_id}/'
                        if highlight else
                        f'https://www.instagram.com/stories/{account}/{item.mediaid}/'),
                'media': [{'video': item.is_video,
                           'url': item.video_url if item.is_video else item.url}]}

    def fetch(self, account):
        self.deadline = time.monotonic() + max(30, int(self.config.get('check_timeout', 120)))
        self.report('连接 Instagram、获取账号资料')
        loader = self.connect()
        profile = instaloader.Profile.from_username(loader.context, account)
        limit = max(1, min(500, int(self.config.get('scan_limit', 30))))
        results, errors = {}, {}
        sources = {
            'posts': lambda: (self.post(p) for p in itertools.islice(profile.get_posts(), limit)),
            'reels': lambda: (self.post(p) for p in itertools.islice(profile.get_reels(), limit)),
            'stories': lambda: (self.story(i, account)
                for s in loader.get_stories(userids=[profile.userid]) for i in s.get_items()),
            'highlights': lambda: (self.story(i, account, h)
                for h in itertools.islice(loader.get_highlights(profile), limit)
                for i in h.get_items()),
            'tagged': lambda: (self.post(p) for p in itertools.islice(profile.get_tagged_posts(), limit)),
        }
        for source, fetch in sources.items():
            if not self.config.get('enable_' + source, source != 'tagged'):
                continue
            if source in ('stories', 'highlights') and not loader.context.is_logged_in:
                errors[source] = '需要配置 Instagram 登录会话。'
                continue
            try:
                self.check_deadline()
                self.report(f'请求 {source}')
                results[source] = list(fetch())
                self.report(f'{source} 成功，获取 {len(results[source])} 条')
            except Exception as exc:
                errors[source] = error_message(exc)
                self.report(f'{source} 失败：{errors[source]}')
                if isinstance(exc, (instaloader.AbortDownloadException, TimeoutError)):
                    break  # Do not hammer other endpoints after 401/403/429.
        return results, errors

    def download(self, media, path):
        """No login cookies are sent to the media CDN."""
        import requests
        if path.exists():
            return str(path)
        maximum = max(1, min(1024, int(self.config.get('max_media_mb', 100)))) * 1024**2
        proxy = str(self.config.get('proxy', '')).strip()
        proxies = {'http': proxy, 'https': proxy} if proxy else None
        temporary = path.with_suffix('.part')
        try:
            with requests.get(media['url'], stream=True, timeout=(15, 30),
                              proxies=proxies) as response:
                response.raise_for_status()
                if int(response.headers.get('Content-Length', 0)) > maximum:
                    raise ValueError('media exceeds size limit')
                size = 0
                deadline = time.monotonic() + 120
                with temporary.open('wb') as output:
                    for chunk in response.iter_content(128 * 1024):
                        if time.monotonic() >= deadline:
                            raise TimeoutError('media download budget exceeded')
                        size += len(chunk)
                        if size > maximum:
                            raise ValueError('media exceeds size limit')
                        output.write(chunk)
                if not size:
                    raise ValueError('empty media')
            os.replace(temporary, path)
            return str(path)
        finally:
            temporary.unlink(missing_ok=True)

    def close(self):
        if self.loader:
            self.loader.close()
