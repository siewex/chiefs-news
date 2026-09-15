import json
import os
import sqlite3
import time

from config import DB_PATH


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (
                url TEXT PRIMARY KEY,
                seen_at REAL
            );
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                image_url TEXT,
                source_url TEXT,
                source_title TEXT,
                source_text TEXT,
                created_at REAL,
                published INTEGER DEFAULT 0,
                admin_chat_id INTEGER,
                admin_msg_id INTEGER
            );
            """
        )


def is_seen(url: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM seen WHERE url = ?", (url,)).fetchone() is not None


def seen_count() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM seen").fetchone()[0]


def mark_seen(url: str) -> None:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO seen(url, seen_at) VALUES (?, ?)", (url, time.time()))


def save_draft(text: str, image_url: str | None, source_url: str | None,
               source_title: str | None, source_text: str | None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO drafts(text, image_url, source_url, source_title, source_text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (text, image_url, source_url, source_title, source_text, time.time()),
        )
        return cur.lastrowid


def get_draft(draft_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(row) if row else None


def update_draft_text(draft_id: int, text: str) -> None:
    with _conn() as c:
        c.execute("UPDATE drafts SET text = ? WHERE id = ?", (text, draft_id))


def set_admin_msg(draft_id: int, chat_id: int, msg_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE drafts SET admin_chat_id = ?, admin_msg_id = ? WHERE id = ?",
                  (chat_id, msg_id, draft_id))


def draft_by_admin_msg(chat_id: int, msg_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM drafts WHERE admin_chat_id = ? AND admin_msg_id = ?",
                        (chat_id, msg_id)).fetchone()
        return dict(row) if row else None


def recent_titles(hours: int = 36, limit: int = 30) -> list[str]:
    """Заголовки источников недавних черновиков — чтобы модель не дублировала уже освещённое."""
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT source_title FROM drafts WHERE created_at > ? AND source_title IS NOT NULL "
            "ORDER BY created_at DESC LIMIT ?", (time.time() - hours * 3600, limit)).fetchall()
        return [r[0] for r in rows]


def mark_published(draft_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE drafts SET published = 1 WHERE id = ?", (draft_id,))
