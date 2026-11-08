from __future__ import annotations

import re

_CJK = (
    "\u3400-\u9fff"
    "\uf900-\ufaff"
    "\u3040-\u30ff"
    "\uac00-\ud7af"
)
_CJK_CHAR = re.compile(f"[{_CJK}]")
_PLACEHOLDER = re.compile(r"\{\{v\d+\}\}")
_URL = re.compile(r"https?://\S+|www\.\S+")
_SHIELD = re.compile("\ue000(\\d+)\ue001")
_HEADING = re.compile(
    r"^[A-Za-z][A-Za-z'’\-]{1,24}(?:[\u2000-\u200b\u3000]+[ ]*|[ ]{2,})[A-Z]"
)
_ABBREV = re.compile(r"(?i)(?:e\.g|i\.e|et al|etc|vs|dr|mr|mrs|fig|no|prof|st)\.$")
_STOP = "。！？…：；，、）』」】"


def separate_cjk_latin(source: str) -> str:
    """保留旧调用。中文排版不再在汉字和字母之间插入空格。"""
    return polish_punctuation(source, source)


def polish_punctuation(source: str, translated: str) -> str:
    """按原文的标点补上中文标点，并去掉用来顶替标点的空格。"""
    kept: list[str] = []

    def hide(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        return f"\ue000{len(kept) - 1}\ue001"

    text = _URL.sub(hide, _PLACEHOLDER.sub(hide, translated))
    origin = _PLACEHOLDER.sub(" ", source or "")
    text = _move_comma_after_number(text)
    text = _to_fullwidth(text)
    text = _heading_colon(origin, text)
    text = _fill_commas(origin, text)
    text = _drop_gaps(text)
    text = _finish(origin, text)
    return _SHIELD.sub(lambda match: kept[int(match.group(1))], text)


def _move_comma_after_number(text: str) -> str:
    """原文 Morton,1 Kevin 里的逗号是作者之间的分隔，数字是上标。"""
    text = re.sub(r"[,，](\d{1,2})(?=\s*[\u4e00-\u9fffA-Z])", r"\1，", text)
    return re.sub(r"[,，](\d{1,2})\s*$", r"\1，", text)


def _to_fullwidth(text: str) -> str:
    if _CJK_CHAR.search(text) is None:
        return text
    text = text.replace("(", "（").replace(")", "）")
    text = re.sub(r"(?<=\d),\s+(?=\d)", "，", text)
    text = re.sub(r"(?<!\d),(?!\d{3}(?!\d))", "，", text)
    text = text.replace(";", "；").replace("?", "？").replace("!", "！")
    text = re.sub(r"(?<!\d):(?!\d)", "：", text)

    def period(match: re.Match[str]) -> str:
        prefix = text[max(0, match.start() - 12) : match.start()]
        if _ABBREV.search(prefix + ".") or re.search(r"[A-Z]$", prefix):
            return "."
        return "。"

    return re.sub(r"(?<![\d.])\.(?!\d)", period, text)


def _heading_colon(source: str, text: str) -> str:
    if _HEADING.match(source.strip()) is None:
        return text
    return re.sub(r"^([\u4e00-\u9fff·]{1,12})[\s\u3000]+", r"\1：", text, count=1)


def _fill_commas(source: str, text: str) -> str:
    """原文有逗号、译文却写成空格时，把这个空格换成逗号。"""
    owed = _comma_count(source) - text.count("，") - text.count("、")
    if owed <= 0:
        return text
    pieces: list[str] = []
    last = 0
    for match in re.finditer(r"[ \t\u3000]+", text):
        pieces.append(text[last : match.start()])
        left = text[match.start() - 1 : match.start()]
        right = text[match.end() : match.end() + 1]
        if owed and (_CJK_CHAR.match(left) or _CJK_CHAR.match(right)):
            pieces.append("，")
            owed -= 1
        else:
            pieces.append(match.group(0))
        last = match.end()
    pieces.append(text[last:])
    return "".join(pieces)


def _comma_count(source: str) -> int:
    return len(re.findall(r"(?<!\d),(?!\d{3}(?!\d))", source))


def _drop_gaps(text: str) -> str:
    text = re.sub(r"[\s\u3000]*·[\s\u3000]*", "·", text)
    text = re.sub(r"[\s\u3000]*([，。；：？！、）』」])[\s\u3000]*", r"\1", text)
    text = re.sub(r"[\s\u3000]+([（『「])", r"\1", text)
    text = re.sub(r"([（『「])[\s\u3000]+", r"\1", text)
    text = re.sub(r"-[\s\u3000]+(?=[\u4e00-\u9fff])", "-", text)
    text = re.sub(rf"(?<=[{_CJK}])[\s\u3000]+", "", text)
    text = re.sub(rf"[\s\u3000]+(?=[{_CJK}])", "", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _finish(source: str, text: str) -> str:
    if not text or _CJK_CHAR.search(text) is None or text[-1] in _STOP:
        return text
    origin = source.rstrip()
    if not origin:
        return text
    mark = {".": "。", ",": "，", ";": "；", ":": "：", "?": "？", "!": "！"}.get(origin[-1])
    if mark is None:
        return text
    if origin[-1] == "." and _ABBREV.search(origin):
        return text
    return text + mark
