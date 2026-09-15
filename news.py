"""Сбор новостей из RSS + извлечение текста статьи."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass

import feedparser
import httpx
import trafilatura
from html import unescape as html_unescape

from urllib.parse import parse_qs, quote_plus, urlparse

from config import BLUESKY_ACCOUNTS, FEEDS, KEYWORDS, REDDIT_FEEDS, SEARCH_FEED
import storage

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (compatible; ChiefsBot/1.0)"
REDDIT_UA = "linux:chiefs-tg-bot:1.0 (news digest bot)"  # Reddit требует осмысленный UA, иначе 429
MAX_AGE_SEC = 36 * 3600  # новости старше не берём


@dataclass
class Article:
    title: str
    url: str
    source: str
    summary: str = ""
    text: str = ""
    image_url: str | None = None
    kind: str = "article"   # article | tweet


async def _get(client: httpx.AsyncClient, url: str, headers: dict | None = None) -> str | None:
    try:
        r = await client.get(url, follow_redirects=True, timeout=20, headers=headers)
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

        # Чередуем источники (AP, Bing, ESPN, AP, Bing, ...), чтобы одна лента не забивала лимит
        by_source: dict[str, list[Article]] = {}
        for a in found:
            by_source.setdefault(a.source, []).append(a)
        picked: list[Article] = []
        while len(picked) < limit and any(by_source.values()):
            for queue in by_source.values():
                if queue and len(picked) < limit:
                    picked.append(queue.pop(0))
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


# ---------- быстрые источники: репосты твитов ----------

_TAG_RE = re.compile(r"^\s*\[([^\]]{2,40})\]\s*:?\s*(.+)$", re.S)


def _has_keyword(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in KEYWORDS)


_SKIP_TAGS = {"oc", "meme", "highlight", "discussion", "question", "serious", "fan art", "art", "shitpost", "poll"}


def _reddit_items(source: str, raw: str, need_keyword: bool) -> list[Article]:
    out = []
    for e in feedparser.parse(raw).entries[:40]:
        title = html_unescape(getattr(e, "title", "")).strip()
        m = _TAG_RE.match(title)
        if not m:
            continue  # обсуждения, мемы, вопросы — без пометки источника
        tag, headline = m.group(1).strip(), m.group(2).strip()
        if tag.lower() in _SKIP_TAGS:
            continue
        if need_keyword and not _has_keyword(title):
            continue
        permalink = getattr(e, "link", "")
        content = html_unescape(e.content[0].value) if getattr(e, "content", None) else ""
        # Внешняя ссылка поста: статья, картинка (скриншот твита), видео или галерея
        ext = None
        for href in re.findall(r'href="([^"]+)"', content):
            if "reddit.com/" in href or href.startswith("/u/"):
                continue
            ext = href
            break
        url, image, kind, text = permalink, None, "tweet", headline
        if ext:
            if re.search(r"i\.redd\.it/.*\.(png|jpe?g|webp)$", ext):
                image = ext
            elif "redd.it" in ext or "reddit.com" in ext:
                pass  # видео/галерея — оставляем ссылку на пост
            else:
                url, kind = ext, "article"  # обычная статья — скачаем текст
        body = re.sub(r"<[^>]+>", " ", content)
        body = re.sub(r"\s+", " ", body).replace("[link]", "").replace("[comments]", "").strip()
        out.append(Article(title=headline, url=url, source=f"{tag} via {source}",
                           summary=body[:800], text=text, image_url=image, kind=kind))
    return out


async def _bluesky_items(client: httpx.AsyncClient, handle: str) -> list[Article]:
    api = ("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
           f"?actor={handle}&limit=15&filter=posts_no_replies")
    try:
        r = await client.get(api, timeout=20)
        r.raise_for_status()
        feed = r.json().get("feed", [])
    except Exception as e:
        log.warning("bluesky %s failed: %s", handle, e)
        return []
    out = []
    for item in feed:
        if "reason" in item:  # репост
            continue
        post = item["post"]
        rec = post.get("record", {})
        text = (rec.get("text") or "").strip()
        if len(text) < 20:
            continue
        rkey = post["uri"].rsplit("/", 1)[-1]
        url = f"https://bsky.app/profile/{handle}/post/{rkey}"
        img = None
        embed = post.get("embed") or {}
        if embed.get("images"):
            img = embed["images"][0].get("fullsize")
        name = post.get("author", {}).get("displayName") or handle
        out.append(Article(title=text.splitlines()[0][:120], url=url, source=f"{name} (Bluesky)",
                           text=text, image_url=img, kind="tweet"))
    return out


async def fetch_fast(limit: int) -> list[Article]:
    """Свежие репосты твитов из Reddit и Bluesky. Без скачивания статей — текст уже в посте."""
    found: list[Article] = []
    first_run = storage.seen_count() == 0
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as client:
        for source, feed_url, need_kw in REDDIT_FEEDS:
            raw = await _get(client, feed_url, headers={"User-Agent": REDDIT_UA})
            if raw:
                found += _reddit_items(source, raw, need_kw)
        for handle in BLUESKY_ACCOUNTS:
            found += await _bluesky_items(client, handle)

    fresh = []
    seen_urls: set[str] = set()
    for a in found:
        if a.url in seen_urls or storage.is_seen(a.url):
            continue
        seen_urls.add(a.url)
        fresh.append(a)

    picked = fresh[:limit]
    # Посты со ссылкой на статью — докачиваем текст, как для обычных RSS
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as client:
        await asyncio.gather(*(enrich(client, a) for a in picked if a.kind == "article"))
    for a in (fresh if first_run else picked):
        storage.mark_seen(a.url)
    return picked
