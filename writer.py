"""Генерация постов через сторонний OpenAI-совместимый API (ai.starimg.ru)."""
import json
import logging
import re
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import BaseModel

import storage
from config import ADD_SOURCE_LINK, API_BASE_URL, API_KEY, FILTER_MODEL, MODEL, STYLE_PATH
from news import Article

log = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=API_KEY, base_url=API_BASE_URL)
# Текущие модели; можно переключать на лету командой /model (до перезапуска)
current = {"model": MODEL, "filter": FILTER_MODEL}


def style() -> str:
    return Path(STYLE_PATH).read_text(encoding="utf-8")


async def ask(user: str, max_tokens: int = 4000, model: str | None = None, system: str | None = None) -> str:
    """Один запрос к модели, возвращает текст ответа. По умолчанию — со стилем канала."""
    model = model or current["model"]
    resp = await client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": style() if system is None else system},
            {"role": "user", "content": user},
        ],
    )
    usage = getattr(resp, "usage", None)
    if usage:
        log.info("%s: %s in / %s out", model, usage.prompt_tokens, usage.completion_tokens)
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


FILTER_SYSTEM = (
    "Ты отбираешь новости для русскоязычного Telegram-канала о клубе НФЛ «Канзас-Сити Чифс». "
    "Отвечай только JSON."
)


async def is_relevant(a: Article) -> tuple[bool, str]:
    """Этап 1 (дешёвая модель): стоит ли вообще писать пост по этой новости."""
    snippet = (a.text or a.summary or "")[:1000]
    covered = storage.recent_titles()
    covered_block = ("\nУже освещали за последние сутки:\n- " + "\n- ".join(covered)) if covered else ""
    user = (
        f"Источник: {a.source}\nЗаголовок: {a.title}\n"
        + (f"Текст: {snippet}\n" if snippet and snippet != a.title else "")
        + covered_block +
        "\n\nИнтересна ли новость подписчикам канала про «Чифс»? Да — если это новость о команде, игроках, "
        "тренерах, матчах, травмах, обменах, контрактах, драфте или о соперниках, когда это напрямую касается «Чифс». "
        "Нет — реклама, ставки и прогнозы, кликбейт без сути, фанатские эмоции без фактов, мемы, "
        "новости про другие команды без связи с «Чифс», а также то, что уже освещали (если нет новых фактов).\n"
        'Ответь СТРОГО JSON: {"relevant": true|false, "reason": "одна короткая строка"}'
    )
    raw = await ask(user, max_tokens=200, model=current["filter"], system=FILTER_SYSTEM)
    try:
        data = _parse_json(raw)
        return bool(data.get("relevant")), str(data.get("reason", ""))[:200]
    except Exception:
        log.warning("filter returned garbage, passing through: %s", raw[:120])
        return True, "фильтр не распарсился"


async def draft_from_article(a: Article) -> Draft:
    relevant, reason = await is_relevant(a)
    if not relevant:
        return Draft(relevant=False, reason=reason, post_html="")

    # Этап 2 (основная модель): пишем пост
    body = a.text or a.summary or "(текста нет, только заголовок)"
    if a.kind == "tweet":
        head = (f"Это короткая новость (твит/репост). Источник: {a.source}\nТекст: {a.title}\n"
                + (f"Комментарий/контекст: {a.summary}\n" if a.summary and a.summary != a.title else "")
                + f"Ссылка: {a.url}\n\n"
                "Напиши КОРОТКИЙ пост (200–500 символов): заголовок + 1–2 абзаца, "
                "без домыслов сверх того, что есть в твите. ")
    else:
        head = (f"Источник: {a.source}\nЗаголовок: {a.title}\nСсылка: {a.url}\n\n"
                f"Текст статьи:\n{body}\n\n")
    user = (
        head + "Напиши пост по правилам стиля. Не выдумывай факты, которых нет в источнике."
        + (f' В конце поста добавь строку: <a href="{a.url}">Источник</a>' if ADD_SOURCE_LINK else "")
        + " Ответь ТОЛЬКО текстом поста в Telegram HTML, без пояснений и без ```."
    )
    post = await no_latin(_clean_html(await ask(user)))
    return Draft(relevant=bool(post), reason=reason, post_html=post)


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
