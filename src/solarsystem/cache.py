from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from pathlib import Path

from solarsystem.i18n import text

log = logging.getLogger("solarsystem")


def digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


class Cache:
    """用翻译器、语言对、模型名和原文哈希查找译文。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translations (
                translator TEXT NOT NULL,
                lang_in TEXT NOT NULL,
                lang_out TEXT NOT NULL,
                model TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                translated TEXT NOT NULL,
                PRIMARY KEY (translator, lang_in, lang_out, model, source_hash)
            )
            """
        )
        self._conn.commit()

    def get(
        self,
        translator: str,
        lang_in: str,
        lang_out: str,
        model: str,
        source: str,
    ) -> str | None:
        key = digest(source)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT translated FROM translations
                WHERE translator = ? AND lang_in = ? AND lang_out = ?
                  AND model = ? AND source_hash = ?
                """,
                (translator, lang_in, lang_out, model, key),
            ).fetchone()
        if row is None:
            return None
        return row[0]

    def put(
        self,
        translator: str,
        lang_in: str,
        lang_out: str,
        model: str,
        source: str,
        translated: str,
    ) -> None:
        key = digest(source)
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO translations (
                        translator, lang_in, lang_out, model, source_hash, translated
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(translator, lang_in, lang_out, model, source_hash)
                    DO UPDATE SET translated = excluded.translated
                    """,
                    (translator, lang_in, lang_out, model, key, translated),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            log.error(text("cli.cache_write_fail", error=exc))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
