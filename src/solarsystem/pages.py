from __future__ import annotations

from solarsystem.errors import SolarError
from solarsystem.i18n import text


def parse_pages(spec: str | None, page_count: int) -> list[int]:
    """把 1 起始的页码写成内部使用的 0 起始序号。

    接受 ``1``、``1,3,5``、``2-4``。空值表示全部页面。
    超出文档末尾的区间会被截掉；起点已经超过末页时则报错。
    """
    if spec is None or str(spec).strip() == "" or str(spec).strip().lower() == "all":
        return list(range(page_count))

    chosen: list[int] = []
    for chunk in str(spec).split(","):
        piece = chunk.strip()
        if not piece:
            raise SolarError(text("cli.bad_page", spec=spec))
        if "-" in piece:
            left, right = piece.split("-", 1)
            start, end = _number(left, spec), _number(right, spec)
            if start < 1 or end < start:
                raise SolarError(text("cli.bad_page", spec=spec))
            if start > page_count:
                raise SolarError(text("cli.page_oob", page=start, count=page_count))
            for human in range(start, end + 1):
                if human <= page_count:
                    chosen.append(human - 1)
        else:
            human = _number(piece, spec)
            if human < 1 or human > page_count:
                raise SolarError(text("cli.page_oob", page=human, count=page_count))
            chosen.append(human - 1)

    ordered: list[int] = []
    seen: set[int] = set()
    for index in chosen:
        if index not in seen:
            seen.add(index)
            ordered.append(index)
    if not ordered:
        raise SolarError(text("cli.no_pages"))
    return ordered


def _number(raw: str, spec: str) -> int:
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise SolarError(text("cli.bad_page", spec=spec)) from exc
