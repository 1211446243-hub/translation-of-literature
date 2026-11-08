from __future__ import annotations

import json
import os
from pathlib import Path

from solarsystem.errors import SolarError
from solarsystem.i18n import text

MASK = "********"


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    return MASK


def config_files() -> list[Path]:
    return [
        Path.cwd() / "solarsystem.local.json",
        Path.home() / ".solarsystem" / "config.json",
    ]


def read_local_config() -> dict:
    for path in config_files():
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                nested = data.get("openai_compatible")
                if isinstance(nested, dict):
                    return nested
                return data
    return {}


def _pick(*values: str | None) -> str:
    for value in values:
        if value is None:
            continue
        cleaned = str(value).strip()
        if cleaned and cleaned != MASK:
            return cleaned
    return ""


def openai_settings(base_url: str | None, api_key: str | None, model: str | None) -> dict[str, str]:
    stored = read_local_config()
    return {
        "base_url": _pick(base_url, os.environ.get("SOLARSYSTEM_BASE_URL"), stored.get("base_url")),
        "api_key": _pick(api_key, os.environ.get("SOLARSYSTEM_API_KEY"), stored.get("api_key")),
        "model": _pick(model, os.environ.get("SOLARSYSTEM_MODEL"), stored.get("model")),
    }


def save_openai_settings(base_url: str | None, api_key: str | None, model: str | None) -> dict[str, str]:
    path = config_files()[0]
    document: dict = {}
    current: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            nested = loaded.get("openai_compatible")
            if isinstance(nested, dict):
                document = dict(loaded)
                current = nested
            else:
                current = loaded
    settings = {
        "base_url": _pick(base_url, str(current.get("base_url") or "")),
        "api_key": _pick(api_key, str(current.get("api_key") or "")),
        "model": _pick(model, str(current.get("model") or "")),
    }
    if not settings["base_url"]:
        raise SolarError(text("web.config_need_url"))
    if not settings["api_key"]:
        raise SolarError(text("web.config_need_key"))
    if not settings["model"]:
        raise SolarError(text("web.config_need_model"))
    document["openai_compatible"] = settings
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return settings
