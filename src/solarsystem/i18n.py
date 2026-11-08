from __future__ import annotations

import contextvars
import json
import os
from functools import lru_cache
from pathlib import Path

_language = contextvars.ContextVar("solarsystem_language", default="zh")


def asset_root() -> Path:
    override = os.environ.get("SOLARSYSTEM_ROOT")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "locale" / "zh.json").is_file():
            return parent
    return Path.cwd()


def use_language(lang: str | None) -> None:
    _language.set("en" if str(lang or "").lower().startswith("en") else "zh")


@lru_cache(maxsize=4)
def catalog(lang: str = "zh") -> dict:
    filename = "en.json" if lang == "en" else "zh.json"
    path = asset_root() / "locale" / filename
    if not path.is_file():
        path = asset_root() / "locale" / "zh.json"
    return json.loads(path.read_text(encoding="utf-8"))


def text(key: str, **values) -> str:
    node: object = catalog(_language.get())
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return key
        node = node[part]
    if not isinstance(node, str):
        return key
    return node.format(**values) if values else node
