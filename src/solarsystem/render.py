from __future__ import annotations

import logging
import os
import re

import pymupdf

from solarsystem.formula import pieces_around_placeholders, placeholders_ok
from solarsystem.i18n import text
from solarsystem.spacing import polish_punctuation

log = logging.getLogger("solarsystem")

_CJK_CHAR = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def apply_translation(page: pymupdf.Page, content, translations: list[str | None], lang_out: str) -> None:
    formula_boxes = [
        run.bbox
        for paragraph in content.paragraphs
        for run in paragraph.runs
        if run.formula
    ]
    blockers = list(content.obstacles) + formula_boxes
    cover = list(getattr(content, "images", [])) + formula_boxes
    redactions: list[tuple[float, float, float, float]] = []
    writes: list[tuple] = []
    plain: list[tuple] = []

    for paragraph, translated in zip(content.paragraphs, translations):
        if translated is None:
            plain.append((paragraph, None))
            continue
        if not placeholders_ok(paragraph.source, translated, paragraph.formula_count):
            log.warning(text("cli.placeholder_kept"))
            plain.append((paragraph, None))
            continue
        prepared = polish_punctuation(paragraph.source, translated)
        for run in paragraph.runs:
            if run.formula or not run.text.strip():
                continue
            redactions.extend(_free(_pad(run.bbox, 0.6), cover))
        if paragraph.formula_count:
            segments = pieces_around_placeholders(prepared)
            groups = _text_groups(paragraph.runs)
            if len(segments) != len(groups):
                log.warning(text("cli.placeholder_kept"))
                continue
            column_x1 = _column_edge(paragraph, content.paragraphs, content.images, page)
            floor = _lower_limit(paragraph, content.paragraphs, page, cover)
            for group, segment in zip(groups, segments):
                if not group or segment.strip() == "":
                    continue
                box = _widen(_union(run.bbox for run in group), column_x1, formula_boxes)
                writes.append((box, segment, _median_size(group), group[0].color, floor))
            plain.append((paragraph, None))
        else:
            plain.append((paragraph, prepared))

    flows = _flow_regions(plain, content.paragraphs, page)

    for box in redactions:
        page.add_redact_annot(pymupdf.Rect(box), fill=(1, 1, 1))
    if redactions:
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE,
        )
    for box, segment, size, color, floor in writes:
        _place(page, box, segment, size, color, lang_out, floor)
    for box, value, size, color in flows:
        _place_flow(page, box, value, size, color, lang_out)


def _flow_regions(items: list[tuple], paragraphs, page) -> list[tuple]:
    """把同一栏、同一字号的译文收成一块，按原栏的矩形整段填入。"""
    if not items:
        return []
    anchors = _column_anchors([paragraph for paragraph, _text in items])
    columns: dict[float, list[tuple]] = {anchor: [] for anchor in anchors}
    for paragraph, value in items:
        anchor = min(anchors, key=lambda item: abs(paragraph.bbox[0] - item))
        columns[anchor].append((paragraph, value))

    regions = []
    for anchor in anchors:
        members = sorted(columns[anchor], key=lambda item: (item[0].bbox[1], item[0].bbox[0]))
        current: list[tuple] = []
        for paragraph, value in members:
            if value is None:
                regions.extend(_close_region(current, paragraphs, page))
                current = []
                continue
            if current and _region_break(current[-1][0], paragraph):
                regions.extend(_close_region(current, paragraphs, page))
                current = []
            current.append((paragraph, value))
        regions.extend(_close_region(current, paragraphs, page))
    return regions


def _column_anchors(paragraphs) -> list[float]:
    anchors: list[float] = []
    for paragraph in sorted(paragraphs, key=lambda item: item.bbox[0]):
        left = paragraph.bbox[0]
        if not anchors or left - anchors[-1] > 26:
            anchors.append(left)
    return anchors or [0.0]


def _region_break(previous, paragraph) -> bool:
    gap = paragraph.bbox[1] - previous.bbox[3]
    # 行距大约为零；空出将近一行，或左边明显错开，就是下一块（如页脚）。
    if gap > 7.5 or abs(paragraph.bbox[0] - previous.bbox[0]) > 16:
        return True
    old = _median_size(previous.runs)
    new = _median_size(paragraph.runs)
    return abs(old - new) / max(old, new, 0.1) > 0.18


def _close_region(members: list[tuple], paragraphs, page) -> list[tuple]:
    if not members:
        return []
    texts = [value.strip() for _paragraph, value in members if value and value.strip()]
    if not texts:
        return []
    boxes = [paragraph.bbox for paragraph, _value in members]
    x0, y0, _x1, y1 = _union(boxes)
    cap = page.rect.x1 - 36
    for other in paragraphs:
        if any(other is paragraph for paragraph, _value in members):
            continue
        if other.bbox[0] <= x0 + 12:
            continue
        if other.bbox[3] < y0 - 1 or other.bbox[1] > y1 + 1:
            continue
        cap = min(cap, other.bbox[0] - 5)
    x1 = cap if cap > x0 + 18 else max(box[2] for box in boxes)
    rights = sorted(box[2] for box in boxes)
    full_edge = rights[min(len(rights) - 1, int(len(rights) * 0.7))]
    size = _median_size([run for paragraph, _value in members for run in paragraph.runs])
    color = members[0][0].runs[0].color
    return [((x0, y0, x1, y1), _compose(members, full_edge), size, color)]


def _compose(members: list[tuple], full_edge: float) -> str:
    """满行的译文接成一段重新排，短行和空行才另起一段。"""
    blocks: list[str] = []
    previous = None
    for paragraph, value in members:
        piece = value.strip()
        if not piece:
            continue
        if previous is None:
            blocks.append(piece)
        else:
            gap = paragraph.bbox[1] - previous.bbox[3]
            size = _median_size(previous.runs)
            reached_edge = previous.bbox[2] >= full_edge - 12
            next_item = paragraph.bbox[0] < 100 and re.match(r"\d{1,2}(?=\D)", piece)
            if next_item or gap > max(3.6, size * 0.45) or not reached_edge:
                blocks.append(piece)
            else:
                blocks[-1] = _soft_join(blocks[-1], piece)
        previous = paragraph
    return "\n".join(blocks)


def _soft_join(left: str, right: str) -> str:
    if left.endswith(("-", "‐", "‑")) and right:
        stem = left[:-1]
        if stem and (
            (_CJK_CHAR.match(stem[-1]) and _CJK_CHAR.match(right[0]))
            or (stem[-1].isascii() and stem[-1].isalpha() and right[0].isascii() and right[0].isalpha())
        ):
            return stem + right
    if not left:
        return right
    end, start = left[-1], right[0]
    if end.isspace() or start.isspace():
        return left + right
    if end.isascii() and end.isalnum() and start.isascii() and start.isalnum():
        return left + " " + right
    return left + right


_FONT_OBJECTS: dict[str, pymupdf.Font] = {}
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+<>≤≥=/\-]*")
_NO_START = set("，。、；：！？）》」』〕〉】%.,;:!?)]}")
_NO_END = set("（《「『〔〈【([{$")
_AFFIL = re.compile(r"([,，])(\s*)(\d{1,2})(?!\d)")


def _cjk_fontfile() -> str | None:
    chosen = os.environ.get("SOLARSYSTEM_CJK_FONT")
    candidates = [chosen] if chosen else []
    candidates.extend(
        (
            r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
            r"C:\Windows\Fonts\NotoSansSC-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        )
    )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _font_object(value: str, lang_out: str) -> pymupdf.Font:
    path = _cjk_fontfile() if _CJK_CHAR.search(value) else None
    key = path or _font_for(value, lang_out)
    font = _FONT_OBJECTS.get(key)
    if font is None:
        font = pymupdf.Font(fontfile=path) if path else pymupdf.Font(key)
        _FONT_OBJECTS[key] = font
    return font


def _place_flow(page, box, value, size, color, lang_out: str) -> None:
    """按中文换行规则把译文填进原来的栏，正文两端对齐。"""
    font = _font_object(value, lang_out)
    x0, y0, x1, y1 = box
    width = max(8.0, x1 - x0)
    height = max(size * 1.2, y1 - y0)
    heading = size >= 16
    point = min(size, 22.0)
    while point >= 6.0:
        leading = 1.24 if heading else 1.5
        lines = _typeset(font, value, width, point, heading=heading, hanging=width < 145)
        if lines and _stack_height(lines, point, leading) <= height + 0.8:
            if not heading and len(lines) >= 5:
                spare = height - _stack_height(lines, point, leading)
                step = spare / max(1, len(lines) - 1)
                leading += min(step, point * 0.22) / point
            _draw_typeset(page, font, lines, x0, y0, width, point, leading, color, superscript=11 <= size <= 15.5)
            return
        point *= 0.94
    log.warning(text("cli.overflow"))


def _typeset(font, value: str, width: float, point: float, heading: bool, hanging: bool) -> list[tuple]:
    """返回 (文字, 左缩进, 是否齐行, 段前距)。"""
    records = []
    paragraphs = value.split("\n")
    for index, paragraph in enumerate(paragraphs):
        piece = paragraph.strip()
        if not piece:
            continue
        indent = _hanging_indent(font, piece, point) if hanging else 0.0
        lines = _wrap(font, piece, width, point, indent)
        if heading:
            lines = _balance_tail(font, lines, width, point)
        gap = point * 0.32 if index and width >= 160 else 0.0
        for line_index, line in enumerate(lines):
            last = line_index == len(lines) - 1
            records.append((line, indent if line_index else 0.0, not last and not heading, gap if line_index == 0 else 0.0))
    return records


def _hanging_indent(font, text: str, point: float) -> float:
    match = re.match(r"\d{1,2}(?=\D)", text)
    if not match:
        return 0.0
    return font.text_length(match.group(), fontsize=point) + point * 0.35


def _wrap(font, text: str, width: float, point: float, indent: float) -> list[str]:
    atoms = _atoms(text)
    lines: list[list[str]] = []
    current: list[str] = []
    used = 0.0
    limit = width

    def break_line(carry: list[str]) -> None:
        nonlocal current, used, limit
        if current:
            lines.append(current)
        current = list(carry)
        used = _length(font, "".join(current), point)
        limit = max(8.0, width - indent)

    for atom in atoms:
        pieces = _split_wide(font, atom, point, width)
        for piece in pieces:
            wide = _length(font, piece, point)
            if not current:
                current.append(piece)
                used = wide
                continue
            if used + wide <= limit + 0.4:
                current.append(piece)
                used += wide
                continue
            if piece[:1] in _NO_START:
                while len(current) > 1 and current[-1].strip() == "":
                    current.pop()
                if len(current) > 1 and current[-1][:1] not in _NO_START:
                    break_line([current.pop()])
                current.append(piece)
                used = _length(font, "".join(current), point)
                continue
            carry = _dangling(font, current, piece, width, point)
            break_line(carry)
            if piece.strip() == "" and not current:
                continue
            current.append(piece)
            used += wide
    if current and "".join(current).strip():
        lines.append(current)
    cooked = ["".join(line).strip() for line in lines if "".join(line).strip()]
    if len(cooked) >= 2 and len(cooked[-1]) == 1 and len(cooked[-2]) >= 3:
        cooked[-1] = cooked[-2][-1] + cooked[-1]
        cooked[-2] = cooked[-2][:-1]
    return cooked


def _dangling(font, current: list[str], nxt: str, width: float, point: float) -> list[str]:
    """把行尾的英文或连字符带到下一行，避免 meta- / 分析 被拆开。"""
    if not current:
        return []
    tail = current[-1]
    follows = bool(nxt) and (_CJK_CHAR.match(nxt[0]) is not None or nxt in _NO_START)
    movable = tail.endswith("-") or any(char.isascii() and char.isalnum() for char in tail)
    if follows and movable and _length(font, tail + nxt, point) <= width and len(current) > 1:
        current.pop()
        return [tail]
    return []


def _split_wide(font, atom: str, point: float, width: float) -> list[str]:
    if _length(font, atom, point) <= width or len(atom) <= 1:
        return [atom]
    pieces: list[str] = []
    chunk = ""
    for char in atom:
        trial = chunk + char
        if chunk and _length(font, trial, point) > width:
            pieces.append(chunk)
            chunk = char
        else:
            chunk = trial
    if chunk:
        pieces.append(chunk)
    return pieces


def _atoms(text: str) -> list[str]:
    atoms: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            atoms.append(char)
            index += 1
            continue
        if _CJK_CHAR.match(char):
            atoms.append(char)
            index += 1
            continue
        match = _WORD.match(text, index)
        if match and match.start() == index:
            atoms.append(match.group())
            index = match.end()
            continue
        atoms.append(char)
        index += 1
    return _glue_dashes(atoms)


def _glue_dashes(atoms: list[str]) -> list[str]:
    """中文之间的连字符贴在前一个字上，避免折成“二-”另起一行。"""
    glued: list[str] = []
    index = 0
    while index < len(atoms):
        atom = atoms[index]
        nxt = atoms[index + 1] if index + 1 < len(atoms) else ""
        if atom in "-‐‑–—" and glued and nxt and _CJK_CHAR.match(nxt[0]):
            glued[-1] = glued[-1] + atom
        else:
            glued.append(atom)
        index += 1
    return glued


def _balance_tail(font, lines: list[str], width: float, point: float) -> list[str]:
    if len(lines) < 2:
        return lines
    previous, last = lines[-2], lines[-1]
    if _length(font, last, point) >= width * 0.4:
        return lines
    while len(previous) > 6 and _length(font, last, point) < width * 0.42:
        if _length(font, previous[:-1], point) < width * 0.48:
            break
        last = previous[-1] + last
        previous = previous[:-1]
    lines[-2] = previous
    lines[-1] = last
    return lines


def _stack_height(lines: list[tuple], point: float, leading: float) -> float:
    if not lines:
        return 0.0
    gaps = sum(item[3] for item in lines)
    return point * 1.22 + (len(lines) - 1) * point * leading + gaps


def _draw_typeset(page, font, lines, x0, y0, width, point, leading, color, superscript: bool) -> None:
    writer = pymupdf.TextWriter(page.rect)
    baseline = y0 + point * 0.92
    for text, indent, justify, gap in lines:
        baseline += gap
        _draw_line(writer, font, text, x0 + indent, baseline, width - indent, point, justify and not superscript, superscript)
        baseline += point * leading
    writer.write_text(page, color=_rgb(color))


def _space_font() -> pymupdf.Font:
    font = _FONT_OBJECTS.get("helv-space")
    if font is None:
        font = pymupdf.Font("helv")
        _FONT_OBJECTS["helv-space"] = font
    return font


def _draw_line(writer, font, text, x, y, width, point, justify: bool, superscript: bool) -> None:
    runs = _split_affil(text) if superscript else [(text, False)]
    widths = [_length(font, run, point * (0.62 if small else 1)) for run, small in runs]
    natural = sum(widths)
    extra = width - natural if justify and natural < width and len(text) > 1 else 0.0
    each = extra / max(1, len(text) - 1)
    if each > point * 0.16:
        each = 0.0
    cursor = x
    seen = 0
    for (run, small), wide in zip(runs, widths):
        size = point * 0.62 if small else point
        top = y - point * 0.34 if small else y
        if each and not small:
            for char in run:
                cursor = _append_piece(writer, font, char, cursor, top, size)
                seen += 1
                if seen < len(text):
                    cursor += each
        else:
            cursor = _append_piece(writer, font, run, cursor, top, size)
            seen += len(run)


def _append_piece(writer, font, piece: str, x: float, y: float, size: float) -> float:
    """空格用西文字体写入，避免中文字体把空格提取成不间断空格。"""
    space_font = _space_font()
    for part in re.split(r"( )", piece):
        if not part:
            continue
        chosen = space_font if part == " " else font
        writer.append((x, y), part, font=chosen, fontsize=size)
        x += chosen.text_length(part, fontsize=size)
    return x


def _split_affil(text: str) -> list[tuple[str, bool]]:
    parts: list[tuple[str, bool]] = []
    position = 0
    for match in _AFFIL.finditer(text):
        parts.append((text[position : match.start()], False))
        parts.append((match.group(1) + match.group(2), False))
        parts.append((match.group(3), True))
        position = match.end()
    tail = text[position:]
    ending = re.search(r"(?<=[\u4e00-\u9fffA-Za-z])(\d{1,2})$", tail)
    if ending:
        parts.append((tail[: ending.start(1)], False))
        parts.append((ending.group(1), True))
    else:
        parts.append((tail, False))
    return [(part, small) for part, small in parts if part]


def _length(font, value: str, point: float) -> float:
    if not value:
        return 0.0
    return font.text_length(value, fontsize=point)


def _text_groups(runs) -> list[list]:
    groups: list[list] = []
    current: list = []
    for run in runs:
        if run.formula:
            groups.append(current)
            current = []
        else:
            current.append(run)
    groups.append(current)
    return groups


def _place(page, box, value, size, color, lang_out: str, y_limit: float) -> None:
    fontname = _font_for(value, lang_out)
    x0, y0, x1, y1 = box
    ceiling = min(page.rect.y1 - 28, max(y_limit, y1))
    point_sizes = [size]
    if size > 6:
        point_sizes.extend((size * 0.85, size * 0.7, size * 0.55, max(4.2, size * 0.4)))
    for point in point_sizes:
        if not _fits_width(fontname, value, point, x1 - x0):
            continue
        remainder = page.insert_textbox(
            pymupdf.Rect(x0, y0, x1, min(ceiling, max(y1, y0 + point * 1.35))),
            value,
            fontname=fontname,
            fontsize=point,
            color=_rgb(color),
            align=pymupdf.TEXT_ALIGN_LEFT,
        )
        if remainder >= 0:
            return
    for point in point_sizes:
        bottom = max(y1, y0 + point * 1.45)
        while bottom <= ceiling + 0.5:
            remainder = page.insert_textbox(
                pymupdf.Rect(x0, y0, x1, bottom),
                value,
                fontname=fontname,
                fontsize=point,
                color=_rgb(color),
                align=pymupdf.TEXT_ALIGN_LEFT,
            )
            if remainder >= 0:
                return
            if bottom >= ceiling - 0.5:
                break
            bottom = min(ceiling, bottom + point * 1.15)
    log.warning(text("cli.overflow"))


def _fits_width(fontname: str, value: str, point: float, width: float) -> bool:
    try:
        font = pymupdf.Font(fontname)
    except Exception:
        return False
    return font.text_length(value, fontsize=point) <= width + 1


def _column_edge(paragraph, paragraphs, images, page) -> float:
    own = paragraph.bbox
    column_right = own[2]
    right_limit = page.rect.x1 - 36
    for other in paragraphs:
        if other is paragraph:
            continue
        if _shares_column(own, other.bbox):
            column_right = max(column_right, other.bbox[2])
            continue
        if other.bbox[0] <= own[0] + 8 or not _vertically_near(own, other.bbox):
            continue
        right_limit = min(right_limit, other.bbox[0] - 4)
    for image in images:
        if image[2] - image[0] > page.rect.width * 0.7:
            continue
        if image[0] <= own[0] + 8 or not _vertically_near(own, image):
            continue
        right_limit = min(right_limit, image[0] - 4)
    return max(column_right, min(right_limit, page.rect.x1 - 36))


def _lower_limit(paragraph, paragraphs, page, blockers) -> float:
    limit = page.rect.y1 - 28
    for other in paragraphs:
        if other.bbox[1] <= paragraph.bbox[3] + 1:
            continue
        if _shares_column(paragraph.bbox, other.bbox):
            limit = min(limit, other.bbox[1] - 1)
    for box in blockers:
        if box[1] <= paragraph.bbox[3] + 1:
            continue
        if _shares_column(paragraph.bbox, box):
            limit = min(limit, box[1] - 1)
    return max(limit, paragraph.bbox[3] + 2)


def _widen(box, column_x1, formula_boxes):
    x0, y0, x1, y1 = box
    limit = max(x1, column_x1)
    for formula in formula_boxes:
        if formula[3] < y0 - 1 or formula[1] > y1 + 1:
            continue
        if formula[0] >= x0:
            limit = min(limit, formula[0] - 0.4)
    if limit < x1:
        limit = x1
    return (x0, y0, limit, y1)


def _vertically_near(one, two) -> bool:
    return two[3] >= one[1] - 2 and two[1] <= one[3] + 2


def _shares_column(one, two) -> bool:
    overlap = min(one[2], two[2]) - max(one[0], two[0])
    if overlap > 12:
        return True
    return abs(one[0] - two[0]) < 14


def _font_for(value: str, lang_out: str) -> str:
    if _CJK_CHAR.search(value):
        if lang_out == "zh-TW":
            return "china-ts"
        if lang_out == "ja":
            return "japan"
        if lang_out == "ko":
            return "korea"
        return "china-ss"
    return "helv"


def _rgb(color: int) -> tuple[float, float, float]:
    return (
        ((color >> 16) & 255) / 255,
        ((color >> 8) & 255) / 255,
        (color & 255) / 255,
    )


def _median_size(runs) -> float:
    sizes = sorted(run.size for run in runs)
    return sizes[len(sizes) // 2]


def _pad(box, amount: float):
    return (box[0] - amount, box[1] - amount, box[2] + amount, box[3] + amount)


def _free(box, blockers):
    pieces = [box]
    for blocker in blockers:
        expanded = _pad(blocker, 0.6)
        nxt = []
        for piece in pieces:
            nxt.extend(_difference(piece, expanded))
        pieces = nxt
    return [piece for piece in pieces if piece[2] - piece[0] > 0.4 and piece[3] - piece[1] > 0.4]


def _difference(one, other):
    area = pymupdf.Rect(one)
    block = pymupdf.Rect(other)
    shared = area & block
    if shared.is_empty or shared.width <= 0 or shared.height <= 0:
        return [one]
    parts = []
    if shared.y0 > area.y0 + 0.2:
        parts.append((area.x0, area.y0, area.x1, shared.y0))
    if shared.y1 < area.y1 - 0.2:
        parts.append((area.x0, shared.y1, area.x1, area.y1))
    if shared.x0 > area.x0 + 0.2:
        parts.append((area.x0, shared.y0, shared.x0, shared.y1))
    if shared.x1 < area.x1 - 0.2:
        parts.append((shared.x1, shared.y0, area.x1, shared.y1))
    return parts


def _hits(rect: pymupdf.Rect, blockers) -> bool:
    for box in blockers:
        shared = rect & pymupdf.Rect(box)
        if not shared.is_empty and shared.width > 0.5 and shared.height > 0.5:
            return True
    return False


def _union(boxes):
    boxes = list(boxes)
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
