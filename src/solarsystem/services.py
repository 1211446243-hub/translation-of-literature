from __future__ import annotations

import json
import urllib.error
import urllib.request

from solarsystem.config import openai_settings
from solarsystem.errors import SolarError
from solarsystem.i18n import text


def instruction(lang_in: str, lang_out: str, extra: str) -> str:
    base = (
        "把用户给出的文本从 "
        + lang_in
        + " 翻译成 "
        + lang_out
        + "。只输出译文，不要解释。形如 {{v0}} 的占位符必须原样保留，不要翻译、增删或改编号。"
    )
    if lang_out in ("zh", "zh-TW"):
        base += "译文使用中文标点：逗号、句号、分号、冒号写成，。；：。不要用空格代替标点，也不要在汉字与英文、数字之间加空格。"
    extra = (extra or "").strip()
    if extra:
        return base + "\n" + extra
    return base


class EchoTranslator:
    name = "echo"
    model = "echo"

    def translate_text(self, source: str, *, lang_in: str, lang_out: str, instruction: str) -> str:
        return source


class OpenAICompatibleTranslator:
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str):
        if not api_key:
            raise SolarError(text("cli.missing_key"))
        if not base_url:
            raise SolarError(text("cli.missing_base_url"))
        if not model:
            raise SolarError(text("cli.missing_model"))
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def translate_text(self, source: str, *, lang_in: str, lang_out: str, instruction: str) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": source},
            ],
        }
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise SolarError(text("cli.service_fail", error=exc)) from exc
        try:
            return str(body["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise SolarError(text("cli.service_fail", error=exc)) from exc


def build_translator(service, base_url: str | None, api_key: str | None, model: str | None):
    if not isinstance(service, str):
        return service
    if service == "echo":
        return EchoTranslator()
    if service == "openai_compatible":
        settings = openai_settings(base_url, api_key, model)
        return OpenAICompatibleTranslator(**settings)
    raise SolarError(text("cli.bad_service", name=service))
