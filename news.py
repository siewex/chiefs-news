"""Сбор новостей из RSS + извлечение текста статьи."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass

import feedparser
import httpx
import trafilatura

from config import FEEDS
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
    # Google News отдаёт редирект-страницу; финальный url узнаём через follow_redirects выше,
    # но текст всё равно пробуем извлечь.
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
                url = getattr(e, "link", None)
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
