"""Resumable article storage and isolated per-browser conversation history."""

import sqlite3
from contextlib import contextmanager

from config import DB_PATH


@contextmanager
def connect(path=DB_PATH):
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(path=DB_PATH):
    with connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            link TEXT UNIQUE NOT NULL,
            content TEXT,
            summary TEXT,
            published_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            category TEXT,
            reporter TEXT,
            img_path TEXT,
            summary_model TEXT,
            summary_created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS chat_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_text TEXT NOT NULL,
            answer TEXT NOT NULL,
            sources_json TEXT NOT NULL,
            used_retrieval INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS chat_session_idx ON chat_turns(session_id,id);
        """)


def counts():
    if not DB_PATH.exists():
        return {"articles": 0, "summaries": 0}
    with connect() as db:
        row = db.execute("SELECT COUNT(*), COUNT(NULLIF(TRIM(summary),'')) FROM news").fetchone()
    return {"articles": row[0], "summaries": row[1]}


if __name__ == "__main__":
    init_db()
    print(counts())
