from __future__ import annotations

import re

from solarsystem.errors import SolarError
from solarsystem.i18n import text

DEFAULT_FORMULA_FONT = (
    r"(?i)(math|cmsy|cmmi|cmex|cmr10|symbol|stix|xits|"
    r"asana|latin modern math|cambria)"
)
DEFAULT_FORMULA_CHAR = (
    r"^(?=.*[=^_∫∑√≤≥±∞∂])"
    r"[\w\\^_=\-+*/<>()\[\]{}.,\s≤≥±∞∑∫√∂παβγθλμσφω°%]+$"
)
_TOKEN = re.compile(r"\{\{v(\d+)\}\}")
_TOKEN_SPLIT = re.compile(r"\{\{v\d+\}\}")
_ONLY_TOKEN = re.compile(r"\s*\{\{v\d+\}\}\s*")


def compile_pattern(pattern: str | None, default: str) -> re.Pattern[str] | None:
    chosen = default if pattern is None else pattern
    if chosen.strip() == "":
        return None
    try:
        return re.compile(chosen)
    except re.error as exc:
        raise SolarError(text("cli.bad_regex", error=exc)) from exc


_PROSE_WORD = re.compile(r"[A-Za-z]{3,}")


def is_formula(value: str, font_name: str, font_re, char_re) -> bool:
    if font_re is not None and font_re.search(font_name or ""):
        return True
    stripped = (value or "").strip()
    if len(_PROSE_WORD.findall(stripped)) >= 3:
        return False
    if char_re is not None and char_re.search(stripped):
        return True
    return False


def protect_runs(runs) -> tuple[str, int]:
    """按阅读顺序拼接一段文字，公式换成 ``{{v0}}`` 这样的占位符。"""
    chunks: list[str] = []
    count = 0
    previous = None
    previous_text = ""
    for run in runs:
        if previous is not None:
            gap = run.bbox[0] - previous.bbox[2]
            new_line = run.bbox[1] > previous.bbox[3] - 1
            chunks.append(_glue(previous_text, run, gap, new_line, run.size))
        if run.formula:
            chunks.append(f"{{{{v{count}}}}}")
            count += 1
            previous_text = "}"
        else:
            chunks.append(run.text)
            previous_text = run.text
        previous = run
    return "".join(chunks), count


def _glue(previous_text: str, run, gap: float, new_line: bool, size: float) -> str:
    if not previous_text:
        return ""
    nxt = run.text if not run.formula else "x"
    if previous_text[-1].isspace() or (nxt and nxt[:1].isspace()):
        return ""
    if new_line:
        if _cjk(previous_text[-1]) and nxt and _cjk(nxt[0]):
            return ""
        return " "
    if gap > max(1.2, size * 0.25):
        return " "
    return ""


def _cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def placeholders_ok(source: str, translated: str, count: int) -> bool:
    source_ids = _TOKEN.findall(source)
    translated_ids = _TOKEN.findall(translated)
    if source_ids != translated_ids:
        return False
    return all(int(item) < count for item in translated_ids)


def pieces_around_placeholders(translated: str) -> list[str]:
    return _TOKEN_SPLIT.split(translated)


def skip_translation(source: str) -> bool:
    if source.strip() == "":
        return True
    return _ONLY_TOKEN.fullmatch(source) is not None
