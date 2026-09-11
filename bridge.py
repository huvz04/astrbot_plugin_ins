"""Validation for data received from the optional browser bridge."""
import math
from urllib.parse import urlparse

from .core import username

SOURCES = {"posts", "reels", "stories", "highlights"}
MEDIA_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net", ".cdninstagram.net")


def _url(value, media=False):
    value = str(value or "")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise ValueError("桥接数据包含无效 URL")
    if media:
        if not (host.endswith(MEDIA_HOST_SUFFIXES) or host in {
                "instagram.com", "www.instagram.com"}):
            raise ValueError("桥接媒体 URL 来源不受支持")
    elif host not in {"instagram.com", "www.instagram.com"}:
        raise ValueError("桥接内容链接必须来自 Instagram")
    return value


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("桥接请求格式不正确")
    account = username(str(payload.get("account", "")))
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, dict) or not raw_sources:
        raise ValueError("桥接请求缺少内容")
    if set(raw_sources) - SOURCES:
        raise ValueError("桥接请求包含未知内容类型")
    result = {}
    total = 0
    for source, raw_items in raw_sources.items():
        if not isinstance(raw_items, list) or len(raw_items) > 100:
            raise ValueError("桥接内容数量超出限制")
        items = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise ValueError("桥接内容格式不正确")
            media_id = str(raw.get("id", ""))
            if not media_id.isdigit() or len(media_id) > 32:
                raise ValueError("桥接内容 ID 不正确")
            media = raw.get("media") or []
            if not isinstance(media, list) or len(media) > 20:
                raise ValueError("桥接媒体数量超出限制")
            normalized_media = []
            for entry in media:
                if not isinstance(entry, dict):
                    raise ValueError("桥接媒体格式不正确")
                normalized_media.append({
                    "video": bool(entry.get("video")),
                    "url": _url(entry.get("url"), media=True),
                })
            timestamp = float(raw.get("time", 0))
            if not math.isfinite(timestamp) or timestamp <= 0:
                raise ValueError("桥接内容时间不正确")
            shortcode = str(raw.get("shortcode", ""))[:64]
            link = raw.get("url") or (
                f"https://www.instagram.com/p/{shortcode}/" if shortcode else
                f"https://www.instagram.com/{account}/")
            items.append({
                "key": f"{'story' if source in ('stories', 'highlights') else 'post'}:{media_id}",
                "time": timestamp,
                "label": str(raw.get("label") or source)[:120],
                "caption": str(raw.get("caption") or "")[:5000],
                "url": _url(link),
                "media": normalized_media,
            })
            total += 1
        result[source] = items
    if total > 200:
        raise ValueError("桥接内容总数超出限制")
    return account, result
