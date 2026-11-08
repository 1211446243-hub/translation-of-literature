from __future__ import annotations

import logging
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from solarsystem.cache import Cache
from solarsystem.errors import Cancelled, SolarError
from solarsystem.extract import read_page
from solarsystem.formula import compile_pattern, skip_translation
from solarsystem.formula import DEFAULT_FORMULA_CHAR, DEFAULT_FORMULA_FONT
from solarsystem.i18n import text
from solarsystem.ingest import expand_inputs
from solarsystem.render import apply_translation
from solarsystem.services import build_translator, instruction

log = logging.getLogger("solarsystem")

LANGUAGES = {"zh", "zh-TW", "en", "fr", "de", "ja", "ko", "ru", "es", "it"}


@dataclass
class Options:
    pages: str | None = None
    lang_in: str = "en"
    lang_out: str = "zh"
    service: object = "echo"
    output: str | None = None
    thread: int = 4
    prompt: str | None = None
    ignore_cache: bool = False
    skip_subset_fonts: bool = False
    formula_font: str | None = None
    formula_char: str | None = None
    cache_path: str | None = None
    cancel: threading.Event | None = None
    on_progress: object | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


def translate(files, **options) -> list[tuple[str, str]]:
    """翻译一个或多个文件，返回 ``[(mono 路径, dual 路径), ...]``。"""
    if isinstance(files, (str, Path)):
        items = [str(files)]
    else:
        items = [str(item) for item in files]
    opts = _options(options)
    guide = _load_prompt(opts.prompt)
    translator = build_translator(opts.service, opts.base_url, opts.api_key, opts.model)
    cache = Cache(Path(opts.cache_path) if opts.cache_path else Path.home() / ".solarsystem" / "cache.sqlite")
    results: list[tuple[str, str]] = []
    failures: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="solarsystem-") as temporary:
            workdir = Path(temporary)
            for source in expand_inputs(items, workdir):
                _check_cancel(opts.cancel)
                try:
                    results.append(
                        _translate_source(source, opts, workdir, translator, cache, guide)
                    )
                except Cancelled:
                    raise
                except SolarError as exc:
                    failures.append(str(exc))
                    results.extend(exc.outputs)
    finally:
        cache.close()
    if failures:
        raise SolarError("\n".join(failures), outputs=results)
    return results


def translate_stream(pdf_bytes: bytes, **options) -> tuple[bytes, bytes]:
    """翻译一份 PDF 的字节，返回 ``(mono 字节, dual 字节)``。"""
    with tempfile.TemporaryDirectory(prefix="solarsystem-stream-") as temporary:
        folder = Path(temporary)
        source = folder / "stream.pdf"
        source.write_bytes(pdf_bytes)
        chosen = dict(options)
        chosen.setdefault("output", str(folder))
        mono_path, dual_path = translate([source], **chosen)[0]
        return Path(mono_path).read_bytes(), Path(dual_path).read_bytes()


def _translate_source(source, opts: Options, workdir: Path, translator, cache: Cache, guide: str):
    from solarsystem.pages import parse_pages

    document = _open(source.path)
    try:
        selected = parse_pages(opts.pages, document.page_count)
        font_re = compile_pattern(opts.formula_font, DEFAULT_FORMULA_FONT)
        char_re = compile_pattern(opts.formula_char, DEFAULT_FORMULA_CHAR)
        mono = pymupdf.open()
        dual = pymupdf.open()
        problems: list[str] = []
        try:
            for position, index in enumerate(selected, start=1):
                _check_cancel(opts.cancel)
                _report(opts, position, len(selected))
                content = read_page(document[index], font_re, char_re)
                if not content.has_native_text:
                    key = "cli.scan_page" if content.has_images else "cli.empty_page"
                    message = text(key, page=index + 1)
                    log.error(message)
                    problems.append(message)
                    continue
                translations = _translate_paragraphs(
                    content.paragraphs,
                    translator,
                    cache,
                    opts,
                    instruction(opts.lang_in, opts.lang_out, guide),
                )
                _check_cancel(opts.cancel)
                edited = pymupdf.open()
                edited.insert_pdf(document, from_page=index, to_page=index)
                apply_translation(edited[0], content, translations, opts.lang_out)
                dual.insert_pdf(document, from_page=index, to_page=index)
                dual.insert_pdf(edited)
                mono.insert_pdf(edited)
                edited.close()
            if mono.page_count == 0:
                raise SolarError("\n".join(problems) if problems else text("cli.no_pages"))
            _check_cancel(opts.cancel)
            output_dir = Path(opts.output) if opts.output else _default_dir(source.path, workdir)
            mono_path, dual_path = _names(source.label, output_dir)
            _publish(mono, mono_path, opts.skip_subset_fonts, opts.cancel)
            try:
                _publish(dual, dual_path, opts.skip_subset_fonts, opts.cancel)
            except Cancelled:
                mono_path.unlink(missing_ok=True)
                raise
            pair = (str(mono_path), str(dual_path))
            if problems:
                raise SolarError("\n".join(problems), outputs=[pair])
            return pair
        finally:
            mono.close()
            dual.close()
    finally:
        document.close()


def _translate_paragraphs(paragraphs, translator, cache: Cache, opts: Options, guide: str):
    results: list[str | None] = [None] * len(paragraphs)
    pending: list[int] = []
    name = getattr(translator, "name", "custom")
    model = str(getattr(translator, "model", "") or "")
    for index, paragraph in enumerate(paragraphs):
        if skip_translation(paragraph.source):
            continue
        if not opts.ignore_cache:
            cached = cache.get(name, opts.lang_in, opts.lang_out, model, paragraph.source)
            if cached is not None:
                results[index] = cached
                continue
        pending.append(index)

    def work(index: int) -> str:
        _check_cancel(opts.cancel)
        return translator.translate_text(
            paragraphs[index].source,
            lang_in=opts.lang_in,
            lang_out=opts.lang_out,
            instruction=guide,
        )

    if pending:
        with ThreadPoolExecutor(max_workers=max(1, opts.thread)) as pool:
            futures = {pool.submit(work, index): index for index in pending}
            try:
                for future in as_completed(futures):
                    _check_cancel(opts.cancel)
                    index = futures[future]
                    translated = future.result()
                    results[index] = translated
                    if opts.cancel is None or not opts.cancel.is_set():
                        cache.put(
                            name,
                            opts.lang_in,
                            opts.lang_out,
                            model,
                            paragraphs[index].source,
                            translated,
                        )
            except Cancelled:
                pool.shutdown(cancel_futures=True, wait=False)
                raise
    _check_cancel(opts.cancel)
    return results


def _publish(document, path: Path, skip_subset: bool, cancel) -> None:
    _check_cancel(cancel)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    try:
        if not skip_subset:
            try:
                document.subset_fonts()
            except Exception as exc:
                log.error(text("cli.subset_fail", error=exc))
        document.save(partial, garbage=4, deflate=True)
        _check_cancel(cancel)
        partial.replace(path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _open(path: Path):
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        raise SolarError(text("cli.bad_pdf", path=path)) from exc
    if getattr(document, "needs_pass", False) or (
        getattr(document, "is_encrypted", False) and not document.authenticate("")
    ):
        document.close()
        raise SolarError(text("cli.encrypted", path=path))
    return document


def _names(label: str, folder: Path) -> tuple[Path, Path]:
    stem = Path(label).stem or "document"
    mono = folder / f"{stem}-mono.pdf"
    dual = folder / f"{stem}-dual.pdf"
    return mono, dual


def _default_dir(path: Path, workdir: Path) -> Path:
    try:
        path.resolve().relative_to(workdir.resolve())
    except ValueError:
        return path.parent
    return Path.cwd()


def _report(opts: Options, page: int, total: int) -> None:
    message = text("cli.progress", page=page, total=total)
    callback = opts.on_progress
    if callback is None:
        print(message, file=sys.stderr)
        return
    callback(page, total, message)


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled(text("cli.cancelled"))


def _load_prompt(path: str | None) -> str:
    if not path:
        return ""
    file = Path(path)
    if not file.is_file():
        raise SolarError(text("cli.prompt_missing", path=path))
    return file.read_text(encoding="utf-8")


def _options(raw: dict) -> Options:
    opts = Options()
    for key, value in raw.items():
        if not hasattr(opts, key):
            raise SolarError(f"未知参数：{key}")
        setattr(opts, key, value)
    if opts.lang_in not in LANGUAGES:
        raise SolarError(text("cli.bad_lang", code=opts.lang_in))
    if opts.lang_out not in LANGUAGES:
        raise SolarError(text("cli.bad_lang", code=opts.lang_out))
    try:
        opts.thread = int(opts.thread)
    except (TypeError, ValueError) as exc:
        raise SolarError(text("cli.thread")) from exc
    if opts.thread < 1:
        raise SolarError(text("cli.thread"))
    return opts
