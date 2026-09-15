"""Генерация постов через сторонний OpenAI-совместимый API (ai.starimg.ru)."""
import json
import logging
import re
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import BaseModel

import storage
from config import ADD_SOURCE_LINK, API_BASE_URL, API_KEY, MODEL, STYLE_PATH
from news import Article

log = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=API_KEY, base_url=API_BASE_URL)


def style() -> str:
    return Path(STYLE_PATH).read_text(encoding="utf-8")


async def ask(user: str, max_tokens: int = 4000) -> str:
    """Один запрос к модели, возвращает текст ответа."""
    resp = await client.chat.completions.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": style()},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


class Draft(BaseModel):
    relevant: bool          # стоит ли постить это в канал про Chiefs
    reason: str             # почему да/нет (одна строка, для админа)
    post_html: str          # готовый пост в Telegram HTML (пусто, если relevant=false)


def _parse_json(text: str) -> dict:
    # Модель может обернуть JSON в ```json ... ``` или добавить текст вокруг
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in response: {text[:200]}")
    return json.loads(m.group(0))


def _clean_html(post: str) -> str:
    post = post.strip()
    post = re.sub(r"^```(?:html)?\s*|\s*```$", "", post, flags=re.S)
    return post.strip()


def _latin_words(post: str) -> list[str]:
    """Латинские слова в видимом тексте поста (ссылки и теги не считаем)."""
    visible = re.sub(r"<a\s+href=\"[^\"]*\">Источник</a>", "", post)
    visible = re.sub(r"<[^>]+>", "", visible)
    return re.findall(r"[A-Za-z]{2,}", visible)


async def no_latin(post: str) -> str:
    """Если в посте осталась латиница — один раз просим переписать."""
    words = _latin_words(post)
    if not words:
        return post
    log.info("latin in post, fixing: %s", words[:8])
    user = (
        f"В этом посте есть латиница: {', '.join(dict.fromkeys(words))}. "
        "Перепиши его ПОЛНОСТЬЮ кириллицей по правилам стиля (имена — транскрипцией, команды — в «ёлочках», "
        "термины — русские), ничего больше не меняя. Ответь ТОЛЬКО текстом поста в Telegram HTML.\n\n" + post
    )
    fixed = _clean_html(await ask(user))
    return fixed if fixed else post


async def draft_from_article(a: Article) -> Draft:
    body = a.text or a.summary or "(текста нет, только заголовок)"
    if a.kind == "tweet":
        head = (f"Это короткая новость (твит/репост). Источник: {a.source}\nТекст: {a.title}\n"
                + (f"Комментарий/контекст: {a.summary}\n" if a.summary and a.summary != a.title else "")
                + f"Ссылка: {a.url}\n\n"
                "Если это новость — напиши КОРОТКИЙ пост (200–500 символов): заголовок + 1–2 абзаца, "
                "без домыслов сверх того, что есть в твите. ")
    else:
        head = (f"Источник: {a.source}\nЗаголовок: {a.title}\nСсылка: {a.url}\n\n"
                f"Текст статьи:\n{body}\n\n")
    covered = storage.recent_titles()
    covered_block = ("\n\nУже освещали за последние сутки (если это та же новость без новых фактов — relevant=false):\n- "
                     + "\n- ".join(covered)) if covered else ""
    user = (
        head +
        "Оцени, интересна ли эта новость подписчикам канала про Kansas City Chiefs "
        "(новости о других командах — только если напрямую касаются Чифс: матч, обмен, соперник в плей-офф). "
        "Реклама, ставки, кликбейт без сути, повторы старых новостей, фанатские эмоции без фактов — не интересны. "
        "Если интересна — напиши пост по правилам стиля."
        + (f' В конце поста добавь строку: <a href="{a.url}">Источник</a>' if ADD_SOURCE_LINK else "")
        + covered_block
        + "\n\nОтветь СТРОГО одним JSON-объектом без пояснений и без markdown:\n"
        '{"relevant": true|false, "reason": "одна строка почему", "post_html": "текст поста в Telegram HTML или пустая строка"}'
    )
    raw = await ask(user)
    d = Draft.model_validate(_parse_json(raw))
    d.post_html = _clean_html(d.post_html)
    if d.relevant and d.post_html:
        d.post_html = await no_latin(d.post_html)
    return d


async def rewrite(post_html: str, instruction: str, source_text: str | None = None) -> str:
    ctx = f"\n\nИсходный материал (для фактов):\n{source_text[:4000]}" if source_text else ""
    user = (
        f"Вот текущий пост:\n\n{post_html}\n\n"
        f"Переделай его по инструкции: «{instruction}». "
        "Сохрани стиль канала и факты. Ответь ТОЛЬКО текстом поста в Telegram HTML, без пояснений и без ```."
        + ctx
    )
    return await no_latin(_clean_html(await ask(user)))


async def write_on_topic(topic: str) -> str:
    user = (
        f"Напиши пост для канала на тему: «{topic}». "
        "Используй только общеизвестные факты, ничего не выдумывай — если данных не хватает, "
        "сделай пост мнением/рассуждением. Ответь ТОЛЬКО текстом поста в Telegram HTML, без пояснений и без ```."
    )
    return await no_latin(_clean_html(await ask(user)))


async def write_from_search(topic: str, articles: list[Article]) -> str:
    """Пишет пост по самой важной из найденных статей."""
    parts = []
    for i, a in enumerate(articles, 1):
        parts.append(f"### Статья {i}\nИсточник: {a.source}\nЗаголовок: {a.title}\nСсылка: {a.url}\n"
                     f"{(a.text or a.summary)[:3000]}")
    user = (
        f"Запрос админа: «{topic}». Ниже найденные свежие статьи.\n\n" + "\n\n".join(parts) +
        "\n\nВыбери самую важную/свежую по запросу и напиши один пост по правилам стиля. "
        "Не выдумывай факты, которых нет в статьях."
        + (' В конце поста добавь строку: <a href="ССЫЛКА">Источник</a> с реальной ссылкой на выбранную статью.' if ADD_SOURCE_LINK else "")
        + " Ответь ТОЛЬКО текстом поста в Telegram HTML, без пояснений и без ```."
    )
    return await no_latin(_clean_html(await ask(user, max_tokens=6000)))
