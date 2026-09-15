"""Сбор новостей из RSS + извлечение текста статьи."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass

import feedparser
import httpx
import trafilatura

from urllib.parse import parse_qs, quote_plus, urlparse

from config import FEEDS, SEARCH_FEED
import storage

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (compatible; ChiefsBot/1.0)"
MAX_AGE_SEC = 36 * 3600  # новости старше не берём


@dataclass
class Article:
    title: str
    url: str
    source: str
    summary: str = ""
    text: str = ""
    image_url: str | None = None


async def _get(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url, follow_redirects=True, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.warning("GET %s failed: %s", url, e)
        return None


def _real_url(url: str) -> str:
    """Bing отдаёт ссылки через apiclick.aspx?url=... — достаём настоящий адрес."""
    if "bing.com/news/apiclick" in url:
        target = parse_qs(urlparse(url).query).get("url")
        if target:
            return target[0]
    return url


def _og_image(html: str) -> str | None:
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.I)
    return m.group(1) if m else None


async def enrich(client: httpx.AsyncClient, a: Article) -> Article:
    """Скачивает статью, вытаскивает текст и картинку."""
    html = await _get(client, a.url)
    if not html:
        return a
    text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    a.text = text[:6000]
    a.image_url = _og_image(html)
    return a


async def fetch_new(limit: int) -> list[Article]:
    """Возвращает до `limit` новых (ещё не виденных) статей, обогащённых текстом."""
    found: list[Article] = []
    seen_urls: set[str] = set()
    first_run = storage.seen_count() == 0

    async with httpx.AsyncClient(headers={"User-Agent": UA}) as client:
        for source, feed_url in FEEDS:
            raw = await _get(client, feed_url)
            if not raw:
                continue
            parsed = feedparser.parse(raw)
            for e in parsed.entries[:30]:
                url = _real_url(getattr(e, "link", None) or "")
                title = getattr(e, "title", "").strip()
                if not url or not title or url in seen_urls or storage.is_seen(url):
                    continue
                published = getattr(e, "published_parsed", None)
                if published and time.time() - time.mktime(published) > MAX_AGE_SEC:
                    storage.mark_seen(url)
                    continue
                seen_urls.add(url)
                summary = re.sub(r"<[^>]+>", " ", getattr(e, "summary", "") or "").strip()
                found.append(Article(title=title, url=url, source=source, summary=summary[:1000]))

        # Берём самые свежие в порядке приоритета источников
        picked = found[:limit]
        picked = await asyncio.gather(*(enrich(client, a) for a in picked))

    # Обработанные помечаем как виденные. При первом запуске — всё, чтобы не
    # вывалить весь бэклог; дальше остаток дойдёт на следующих проверках.
    for a in (found if first_run else picked):
        storage.mark_seen(a.url)
    return list(picked)


async def search(topic: str, limit: int = 3) -> list[Article]:
    """Ищет свежие статьи по теме через Bing News RSS (для /find). Не трогает таблицу seen."""
    q = quote_plus(f"{topic} Chiefs" if "chiefs" not in topic.lower() else topic)
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as client:
        raw = await _get(client, SEARCH_FEED.format(q=q))
        if not raw:
            return []
        found: list[Article] = []
        for e in feedparser.parse(raw).entries[:limit]:
            url, title = _real_url(getattr(e, "link", None) or ""), getattr(e, "title", "").strip()
            if not url or not title:
                continue
            summary = re.sub(r"<[^>]+>", " ", getattr(e, "summary", "") or "").strip()
            found.append(Article(title=title, url=url, source="Bing News", summary=summary[:1000]))
        return list(await asyncio.gather(*(enrich(client, a) for a in found)))
