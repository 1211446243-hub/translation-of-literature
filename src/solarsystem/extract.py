from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from solarsystem.formula import is_formula, protect_runs

_PAGE_COVER = 0.85


@dataclass
class Run:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    font: str
    color: int
    formula: bool = False


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    source: str = ""
    formula_count: int = 0


@dataclass
class PageContent:
    paragraphs: list[Paragraph]
    obstacles: list[tuple[float, float, float, float]]
    has_native_text: bool
    has_images: bool
    images: list[tuple[float, float, float, float]] = field(default_factory=list)


def read_page(page: pymupdf.Page, font_re, char_re) -> PageContent:
    raw = page.get_text("dict")
    runs: list[Run] = []
    obstacles: list[tuple[float, float, float, float]] = []
    images: list[tuple[float, float, float, float]] = []
    has_images = False
    page_area = max(page.rect.get_area(), 1)

    for block in raw.get("blocks", []):
        if block.get("type") == 1:
            has_images = True
            image_box = _tuple(block["bbox"])
            images.append(image_box)
            obstacles.append(image_box)
            continue
        for line in block.get("lines", []):
            rotated = _rotated(line.get("dir") or (1, 0), line.get("wmode") or 0)
            for span in line.get("spans", []):
                value = span.get("text") or ""
                if value == "":
                    continue
                box = _tuple(span["bbox"])
                if rotated:
                    obstacles.append(box)
                    continue
                runs.append(
                    Run(
                        text=value,
                        bbox=box,
                        size=float(span.get("size") or 12),
                        font=str(span.get("font") or ""),
                        color=int(span.get("color") or 0),
                    )
                )

    if page.get_images():
        has_images = True
    for info in page.get_image_info():
        box = info.get("bbox")
        if box:
            image_box = _tuple(box)
            images.append(image_box)
            obstacles.append(image_box)
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not rect:
            continue
        box = pymupdf.Rect(rect)
        if box.get_area() >= page_area * _PAGE_COVER:
            continue
        obstacles.append(_tuple(box))

    for run in runs:
        run.formula = is_formula(run.text, run.font, font_re, char_re)

    paragraphs = _paragraphs(runs)
    native = any(run.text.strip() for run in runs) or _has_rotated_text(raw)
    return PageContent(paragraphs, _unique(obstacles), native, has_images, _unique(images))


def _has_rotated_text(raw: dict) -> bool:
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            if _rotated(line.get("dir") or (1, 0), line.get("wmode") or 0):
                if any((span.get("text") or "").strip() for span in line.get("spans", [])):
                    return True
    return False


def _paragraphs(runs: list[Run]) -> list[Paragraph]:
    if not runs:
        return []
    ordered = sorted(runs, key=lambda run: (round(run.bbox[1], 1), run.bbox[0]))
    lines: list[list[Run]] = []
    for run in ordered:
        if lines and _same_line(_union(item.bbox for item in lines[-1]), run.bbox):
            lines[-1].append(run)
        else:
            lines.append([run])

    seeds: list[list[Run]] = []
    for line in lines:
        line.sort(key=lambda run: run.bbox[0])
        current = [line[0]]
        for run in line[1:]:
            previous = current[-1]
            gap = run.bbox[0] - previous.bbox[2]
            # 词距大约三分之一个字号；栏间距通常接近一个字号。超过这个距离就另起一栏。
            # 同一行上贴着的上标字号更小，仍然算在这一句里。
            close_enough = _size_close(previous.size, run.size) or gap <= 1.2
            if close_enough and gap <= max(min(previous.size, run.size) * 0.65, 3):
                current.append(run)
            else:
                seeds.append(current)
                current = [run]
        seeds.append(current)

    paragraphs: list[Paragraph] = []
    for seed in seeds:
        box = _union(run.bbox for run in seed)
        size = seed[0].size
        if paragraphs and _can_merge(paragraphs[-1], box, size):
            paragraphs[-1].runs.extend(seed)
            paragraphs[-1].bbox = _union([paragraphs[-1].bbox, box])
        else:
            paragraphs.append(Paragraph(runs=list(seed), bbox=box))

    for paragraph in paragraphs:
        paragraph.bbox = _union(run.bbox for run in paragraph.runs)
        paragraph.source, paragraph.formula_count = protect_runs(paragraph.runs)
    return paragraphs


def _can_merge(paragraph: Paragraph, box, size: float) -> bool:
    if not _size_close(paragraph.runs[-1].size, size):
        return False
    gap = box[1] - paragraph.bbox[3]
    if gap < -2 or gap > max(size * 0.42, 3.5):
        return False
    return _aligned(paragraph.bbox, box)


def _aligned(left, right) -> bool:
    overlap = min(left[2], right[2]) - max(left[0], right[0])
    narrow = min(left[2] - left[0], right[2] - right[0])
    if narrow <= 0:
        return False
    if overlap >= narrow * 0.4:
        return True
    return abs(left[0] - right[0]) <= 14


def _same_line(one, two) -> bool:
    top = max(one[1], two[1])
    bottom = min(one[3], two[3])
    overlap = bottom - top
    if overlap <= 0:
        return False
    shorter = min(one[3] - one[1], two[3] - two[1])
    return overlap >= shorter * 0.5


def _size_close(left: float, right: float) -> bool:
    return abs(left - right) / max(left, right, 0.1) <= 0.2


def _rotated(direction, wmode: int) -> bool:
    if wmode:
        return True
    dx, dy = direction
    return abs(dx - 1) > 0.05 or abs(dy) > 0.05


def _tuple(box) -> tuple[float, float, float, float]:
    return (float(box[0]), float(box[1]), float(box[2]), float(box[3]))


def _union(boxes) -> tuple[float, float, float, float]:
    boxes = list(boxes)
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[2] for box in boxes)
    y1 = max(box[3] for box in boxes)
    return (x0, y0, x1, y1)


def _unique(boxes: list[tuple[float, float, float, float]]):
    seen: list[tuple[float, float, float, float]] = []
    for box in boxes:
        if box not in seen:
            seen.append(box)
    return seen
