"""Генерация постов через Claude."""
import logging
from pathlib import Path

import anthropic
from pydantic import BaseModel

from config import MODEL, STYLE_PATH, ADD_SOURCE_LINK
from news import Article

log = logging.getLogger(__name__)
client = anthropic.AsyncAnthropic()


def style() -> str:
    return Path(STYLE_PATH).read_text(encoding="utf-8")


def _system() -> list[dict]:
    # Стиль канала кэшируем — он одинаков для всех запросов
    return [{"type": "text", "text": style(), "cache_control": {"type": "ephemeral"}}]


class Draft(BaseModel):
    relevant: bool          # стоит ли вообще постить это в канал про Chiefs
    reason: str             # почему да/нет (одна строка, для админа)
    post_html: str          # готовый пост в Telegram HTML (пусто, если relevant=false)


async def draft_from_article(a: Article) -> Draft:
    body = a.text or a.summary or "(текста нет, только заголовок)"
    user = (
        f"Источник: {a.source}\nЗаголовок: {a.title}\nСсылка: {a.url}\n\n"
        f"Текст статьи:\n{body}\n\n"
        "Оцени, интересна ли эта новость подписчикам канала про Kansas City Chiefs "
        "(новости о других командах — только если напрямую касаются Чифс: матч, обмен, соперник в плей-офф). "
        "Реклама, ставки, кликбейт без сути, повторы старых новостей — не интересны. "
        "Если интересна — напиши пост по правилам стиля."
        + (" В конце добавь строку: <a href=\"" + a.url + "\">Источник</a>" if ADD_SOURCE_LINK else "")
    )
    resp = await client.messages.parse(
        model=MODEL,
        max_tokens=4000,
        system=_system(),
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": user}],
        output_format=Draft,
    )
    return resp.parsed_output


async def rewrite(post_html: str, instruction: str, source_text: str | None = None) -> str:
    ctx = f"\n\nИсходный материал (для фактов):\n{source_text[:4000]}" if source_text else ""
    user = (
        f"Вот текущий пост:\n\n{post_html}\n\n"
        f"Переделай его по инструкции: «{instruction}». "
        "Сохрани стиль канала и факты. Ответь ТОЛЬКО текстом поста в Telegram HTML, без пояснений."
        + ctx
    )
    resp = await client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=_system(),
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": user}],
    )
    return _text(resp.content).strip()


async def write_on_topic(topic: str) -> str:
    user = (
        f"Напиши пост для канала на тему: «{topic}». "
        "Используй только общеизвестные факты, ничего не выдумывай — если данных не хватает, "
        "сделай пост мнением/рассуждением. Ответь ТОЛЬКО текстом поста в Telegram HTML."
    )
    resp = await client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=_system(),
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": user}],
    )
    return _text(resp.content).strip()


async def search_and_write(topic: str) -> str:
    """Ищет свежие новости в интернете по теме и пишет пост."""
    user = (
        f"Найди в интернете самые свежие новости по запросу: «{topic}» (контекст: Kansas City Chiefs, NFL). "
        "Затем напиши один пост для канала по самой важной/свежей новости. "
        "Финальный ответ — ТОЛЬКО текст поста в Telegram HTML, в конце строка "
        "<a href=\"URL\">Источник</a> с реальной ссылкой."
    )
    messages = [{"role": "user", "content": user}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}]

    for _ in range(4):  # pause_turn может потребовать продолжения
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=_system(),
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            tools=tools,
            messages=messages,
        )
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})

    # Берём только текст после последнего результата поиска
    last_search = -1
    for i, b in enumerate(resp.content):
        if b.type == "web_search_tool_result":
            last_search = i
    return _text(resp.content[last_search + 1:]).strip()


def _text(blocks) -> str:
    return "".join(b.text for b in blocks if b.type == "text")
