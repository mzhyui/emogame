"""SQLite persistence for Weibo WZRY crawl output.

Stores crawled posts + comments in a dedicated database
``data/weibo_comments/weibo.sqlite3`` alongside the JSON dumps. Keeping the
Weibo store separate from the skins database avoids coupling crawled social
data to the official skin catalog.

Schema
------
``weibo_posts`` — one row per crawled post (dedup by ``mid``).
``weibo_comments`` — one row per meaningful comment (dedup by
``(mid, user_id, created_at)``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEIBO_DB = PROJECT_ROOT / "data" / "weibo_comments" / "weibo.sqlite3"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS weibo_posts (
    mid            TEXT PRIMARY KEY,
    user_id        INTEGER,
    user_name      TEXT,
    title          TEXT,
    created_at     TEXT,
    reposts_count  INTEGER,
    comments_count INTEGER,
    attitudes_count INTEGER,
    source         TEXT,
    pics           TEXT,            -- JSON array of urls
    crawled_at     TEXT,
    meaningful     INTEGER,
    low_quality    INTEGER
);

CREATE TABLE IF NOT EXISTS weibo_comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mid         TEXT NOT NULL,
    user        TEXT,
    user_id     INTEGER,
    text        TEXT,
    like_count  INTEGER,
    total_number INTEGER,
    created_at  TEXT,
    source      TEXT,
    UNIQUE (mid, user_id, created_at, text)
);

CREATE INDEX IF NOT EXISTS idx_weibo_comments_mid ON weibo_comments(mid);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(db_path: Path = DEFAULT_WEIBO_DB) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def store_crawl(
    results: list[Any],
    crawled_at: str,
    db_path: Path = DEFAULT_WEIBO_DB,
) -> dict[str, int]:
    """Persist crawl entries into the Weibo SQLite store.

    Idempotent upserts: posts keyed by ``mid``, comments keyed by
    ``(mid, user_id, created_at, text)``. Returns counts of stored posts and
    comments.
    """
    ensure_schema(db_path)
    posts_stored = 0
    comments_stored = 0
    with _connect(db_path) as conn:
        for entry in results:
            post = entry.post
            pics = "[]"
            if post is not None:
                try:
                    import json

                    pics = json.dumps(post.pics or [], ensure_ascii=False)
                except Exception:
                    pics = "[]"
            conn.execute(
                """
                INSERT INTO weibo_posts (
                    mid, user_id, user_name, title, created_at,
                    reposts_count, comments_count, attitudes_count,
                    source, pics, crawled_at, meaningful, low_quality
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mid) DO UPDATE SET
                    user_id=excluded.user_id,
                    user_name=excluded.user_name,
                    title=excluded.title,
                    created_at=excluded.created_at,
                    reposts_count=excluded.reposts_count,
                    comments_count=excluded.comments_count,
                    attitudes_count=excluded.attitudes_count,
                    source=excluded.source,
                    pics=excluded.pics,
                    crawled_at=excluded.crawled_at,
                    meaningful=excluded.meaningful,
                    low_quality=excluded.low_quality
                """,
                (
                    entry.mid,
                    post.user_id if post else 0,
                    post.user_name if post else "",
                    entry.title,
                    post.created_at if post else "",
                    post.reposts_count if post else 0,
                    post.comments_count if post else 0,
                    post.attitudes_count if post else 0,
                    post.source if post else "",
                    pics,
                    crawled_at,
                    entry.meaningful,
                    entry.low_quality,
                ),
            )
            posts_stored += 1

            for c in entry.comments:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO weibo_comments (
                        mid, user, user_id, text, like_count,
                        total_number, created_at, source
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.mid,
                        c.get("user", ""),
                        c.get("user_id", 0),
                        c.get("text", ""),
                        c.get("like_count", 0),
                        c.get("total_number", 0),
                        c.get("created_at", ""),
                        c.get("source", ""),
                    ),
                )
                comments_stored += cur.rowcount

    return {"posts": posts_stored, "comments": comments_stored}
