import asyncio
import html
import logging
import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message)

import news
import storage
import writer
from config import (ADMIN_IDS, BOT_TOKEN, CHANNEL_ID, CHECK_INTERVAL_MIN,
                    FAST_INTERVAL_MIN, FAST_MAX_PER_RUN, MAX_POSTS_PER_RUN)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
# Бот отвечает только админам
router.message.filter(F.from_user.id.in_(ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))
dp.include_router(router)

fetch_lock = asyncio.Lock()
fast_lock = asyncio.Lock()

HELP = (
    "<b>Чифс-бот</b> — помощник по постам.\n\n"
    f"Каждые {CHECK_INTERVAL_MIN} мин я проверяю статьи о Chiefs, каждые {FAST_INTERVAL_MIN} мин — "
    "репосты твитов инсайдеров (Reddit, Bluesky), и присылаю готовые черновики.\n\n"
    "Команды:\n"
    "/news — проверить новости прямо сейчас\n"
    "/find тема — найти в интернете и написать пост\n"
    "/post тема — написать пост на свою тему (без поиска)\n"
    "/style — показать текущий стиль канала\n"
    "/model — какая модель пишет; /model имя — переключить (до перезапуска)\n\n"
    "Под каждым черновиком есть кнопки. Чтобы переписать по-своему — "
    "<b>ответьте</b> на сообщение с черновиком текстом, что поменять."
)


# ---------- отправка ----------

def kb(draft_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{draft_id}"),
         InlineKeyboardButton(text="❌ Пропустить", callback_data=f"skip:{draft_id}")],
        [InlineKeyboardButton(text="✂️ Короче", callback_data=f"short:{draft_id}"),
         InlineKeyboardButton(text="🔄 Другой вариант", callback_data=f"redo:{draft_id}")],
    ])


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


async def send_post(chat_id, text: str, image_url: str | None, reply_markup=None) -> Message:
    """Отправляет пост так, как он будет выглядеть в канале. При битом HTML — без разметки."""

    async def _send(t: str, parse_mode):
        if image_url and len(t) <= 1024:
            try:
                return await bot.send_photo(chat_id, image_url, caption=t,
                                            parse_mode=parse_mode, reply_markup=reply_markup)
            except TelegramBadRequest as e:
                if "parse" in str(e).lower():
                    raise
                log.warning("photo failed, sending text: %s", e)
        return await bot.send_message(chat_id, t, parse_mode=parse_mode, reply_markup=reply_markup,
                                      disable_web_page_preview=not image_url)

    try:
        return await _send(text, ParseMode.HTML)
    except TelegramBadRequest as e:
        log.warning("HTML rejected (%s), sending plain", e)
        return await _send(strip_html(text), None)


async def deliver_draft(chat_id: int, draft_id: int, note: str | None = None) -> None:
    d = storage.get_draft(draft_id)
    if note:
        await bot.send_message(chat_id, note)
    msg = await send_post(chat_id, d["text"], d["image_url"], reply_markup=kb(draft_id))
    storage.set_admin_msg(draft_id, chat_id, msg.message_id)


# ---------- проверка новостей ----------

async def _make_drafts(articles, notify_chat_ids: set[int], icon: str) -> int:
    made = 0
    for a in articles:
        try:
            draft = await writer.draft_from_article(a)
        except Exception:
            log.exception("draft failed for %s", a.url)
            continue
        if not draft.relevant or not draft.post_html.strip():
            log.info("skip %s: %s", a.title, draft.reason)
            continue
        draft_id = storage.save_draft(draft.post_html, a.image_url, a.url, a.title, a.text)
        note = f"{icon} <i>{html.escape(a.source)}: {html.escape(draft.reason)}</i>"
        for cid in notify_chat_ids:
            await deliver_draft(cid, draft_id, note=note)
        made += 1
    return made


async def check_news(notify_chat_ids: set[int], manual: bool = False) -> None:
    """Статьи из RSS (медленный контур)."""
    if fetch_lock.locked():
        if manual:
            for cid in notify_chat_ids:
                await bot.send_message(cid, "Уже проверяю статьи, подождите…")
        return
    async with fetch_lock:
        try:
            articles = await news.fetch_new(MAX_POSTS_PER_RUN)
        except Exception:
            log.exception("fetch_new failed")
            return
        log.info("new articles: %d", len(articles))
        made = await _make_drafts(articles, notify_chat_ids, "📰")
        if manual:
            for cid in notify_chat_ids:
                if not articles:
                    await bot.send_message(cid, "Статьи: новых нет.")
                elif made == 0:
                    await bot.send_message(cid, f"Статьи: просмотрел {len(articles)} новых, ничего стоящего.")


async def check_fast(notify_chat_ids: set[int], manual: bool = False) -> None:
    """Репосты твитов из Reddit/Bluesky (быстрый контур)."""
    if fast_lock.locked():
        return
    async with fast_lock:
        try:
            items = await news.fetch_fast(FAST_MAX_PER_RUN)
        except Exception:
            log.exception("fetch_fast failed")
            return
        log.info("new fast items: %d", len(items))
        made = await _make_drafts(items, notify_chat_ids, "⚡")
        if manual:
            for cid in notify_chat_ids:
                if not items:
                    await bot.send_message(cid, "Твиты: новых нет.")
                elif made == 0:
                    await bot.send_message(cid, f"Твиты: просмотрел {len(items)} новых, ничего стоящего.")


async def scheduler() -> None:
    await asyncio.sleep(10)
    while True:
        try:
            await check_news(ADMIN_IDS)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(CHECK_INTERVAL_MIN * 60)


async def fast_scheduler() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await check_fast(ADMIN_IDS)
        except Exception:
            log.exception("fast scheduler tick failed")
        await asyncio.sleep(FAST_INTERVAL_MIN * 60)


# ---------- команды ----------

@router.message(Command("start", "help"))
async def cmd_start(m: Message):
    await m.answer(HELP)


@router.message(Command("style"))
async def cmd_style(m: Message):
    await m.answer(f"<pre>{html.escape(writer.style())}</pre>")


@router.message(Command("model"))
async def cmd_model(m: Message, command: CommandObject):
    arg = (command.args or "").strip()
    if arg:
        writer.current["model"] = arg
    await m.answer(
        f"Пишет посты: <code>{html.escape(writer.current['model'])}</code>\n"
        f"Фильтрует новости: <code>{html.escape(writer.current['filter'])}</code>\n\n"
        "Сменить: /model claude-opus-4-8 — действует до перезапуска, постоянно — в .env (MODEL=...)"
    )


@router.message(Command("news"))
async def cmd_news(m: Message):
    await m.answer("Проверяю твиты и статьи…")
    asyncio.create_task(check_fast({m.chat.id}, manual=True))
    asyncio.create_task(check_news({m.chat.id}, manual=True))


@router.message(Command("find"))
async def cmd_find(m: Message, command: CommandObject):
    topic = (command.args or "").strip() or "Kansas City Chiefs latest news"
    await m.answer(f"Ищу: <i>{html.escape(topic)}</i>…")
    try:
        articles = await news.search(topic)
        if not articles:
            return await m.answer("Ничего не нашёл.")
        text = await writer.write_from_search(topic, articles)
    except Exception as e:
        log.exception("find failed")
        return await m.answer(f"Ошибка: {html.escape(str(e))}")
    source_text = "\n\n".join(a.text or a.summary for a in articles)
    draft_id = storage.save_draft(text, None, None, topic, source_text)
    await deliver_draft(m.chat.id, draft_id)


@router.message(Command("post"))
async def cmd_post(m: Message, command: CommandObject):
    topic = (command.args or "").strip()
    if not topic:
        return await m.answer("Напишите тему: /post Махоумс и рекорд по пасовым ярдам")
    await write_topic(m, topic)


async def write_topic(m: Message, topic: str):
    await m.answer("Пишу…")
    try:
        text = await writer.write_on_topic(topic)
    except Exception as e:
        log.exception("post failed")
        return await m.answer(f"Ошибка: {html.escape(str(e))}")
    draft_id = storage.save_draft(text, None, None, topic, None)
    await deliver_draft(m.chat.id, draft_id)


# ---------- кнопки ----------

async def _replace_kb(cq: CallbackQuery, label: str):
    try:
        await cq.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=label, callback_data="noop")]]))
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "noop")
async def cb_noop(cq: CallbackQuery):
    await cq.answer()


@router.callback_query(F.data.startswith("pub:"))
async def cb_publish(cq: CallbackQuery):
    d = storage.get_draft(int(cq.data.split(":")[1]))
    if not d:
        return await cq.answer("Черновик не найден", show_alert=True)
    if d["published"]:
        return await cq.answer("Уже опубликовано")
    try:
        await send_post(CHANNEL_ID, d["text"], d["image_url"])
    except Exception as e:
        log.exception("publish failed")
        return await cq.answer(f"Не удалось опубликовать: {e}"[:200], show_alert=True)
    storage.mark_published(d["id"])
    await _replace_kb(cq, "✅ Опубликовано")
    await cq.answer("Опубликовано в канал")


@router.callback_query(F.data.startswith("skip:"))
async def cb_skip(cq: CallbackQuery):
    await _replace_kb(cq, "❌ Пропущено")
    await cq.answer()


async def _rewrite_and_send(cq: CallbackQuery, instruction: str):
    d = storage.get_draft(int(cq.data.split(":")[1]))
    if not d:
        return await cq.answer("Черновик не найден", show_alert=True)
    await cq.answer("Переписываю…")
    try:
        text = await writer.rewrite(d["text"], instruction, d["source_text"])
    except Exception as e:
        log.exception("rewrite failed")
        return await bot.send_message(cq.message.chat.id, f"Ошибка: {html.escape(str(e))}")
    new_id = storage.save_draft(text, d["image_url"], d["source_url"], d["source_title"], d["source_text"])
    await _replace_kb(cq, "🔄 Новый вариант ниже")
    await deliver_draft(cq.message.chat.id, new_id)


@router.callback_query(F.data.startswith("short:"))
async def cb_short(cq: CallbackQuery):
    await _rewrite_and_send(cq, "сделай в 2 раза короче, оставь только главное")


@router.callback_query(F.data.startswith("redo:"))
async def cb_redo(cq: CallbackQuery):
    await _rewrite_and_send(cq, "напиши другой вариант: другой заголовок, другой заход, другая подача")


# ---------- ответ на черновик = инструкция для переписывания ----------

@router.message(F.reply_to_message, F.text)
async def on_reply(m: Message):
    d = storage.draft_by_admin_msg(m.chat.id, m.reply_to_message.message_id)
    if not d:
        return await m.answer("Это не мой черновик. Ответьте на сообщение с постом.")
    await m.answer("Переписываю…")
    try:
        text = await writer.rewrite(d["text"], m.text, d["source_text"])
    except Exception as e:
        log.exception("rewrite failed")
        return await m.answer(f"Ошибка: {html.escape(str(e))}")
    new_id = storage.save_draft(text, d["image_url"], d["source_url"], d["source_title"], d["source_text"])
    await deliver_draft(m.chat.id, new_id)


@router.message(F.text)
async def on_text(m: Message):
    """Просто текст без команды — считаем темой для поста."""
    await write_topic(m, m.text)


# ---------- запуск ----------

async def main():
    storage.init()
    asyncio.create_task(scheduler())
    asyncio.create_task(fast_scheduler())
    log.info("bot started; admins=%s channel=%s", ADMIN_IDS, CHANNEL_ID)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
