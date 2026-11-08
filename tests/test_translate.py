import threading

import pymupdf
import pytest

from solarsystem import translate, translate_stream
from solarsystem.errors import Cancelled, SolarError
from solarsystem.extract import read_page
from solarsystem.formula import compile_pattern
from tests.conftest import Probe, write_formula_pdf, write_lines


def _swap(prefix):
    def replace(source):
        return source.replace("PAGE", prefix)

    return Probe(replace)


def test_formula_glyphs_stay_and_words_change(tmp_path):
    source = tmp_path / "formula.pdf"
    write_formula_pdf(source)

    def fake(text):
        assert "{{v0}}" in text
        return text.replace("Energy", "POWER").replace("remains", "HOLDS")

    mono, _dual = translate(
        [source],
        service=Probe(fake),
        output=str(tmp_path),
        cache_path=str(tmp_path / "cache.sqlite"),
        formula_font="Times",
        formula_char="",
    )[0]
    page = pymupdf.open(mono)[0]
    found = page.get_text()
    assert "E=mc^2" in found
    assert "POWER" in found
    assert "HOLDS" in found
    assert "Energy" not in found
    assert "remains" not in found
    fonts = {span["font"] for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) for span in line["spans"]}
    assert "Times-Roman" in fonts


def test_lost_placeholder_keeps_the_original_formula(tmp_path):
    source = tmp_path / "formula.pdf"
    write_formula_pdf(source)
    mono, _dual = translate(
        [source],
        service=Probe(lambda _text: "POWER HOLDS"),
        output=str(tmp_path),
        cache_path=str(tmp_path / "cache.sqlite"),
        formula_font="Times",
        formula_char="",
    )[0]
    found = pymupdf.open(mono)[0].get_text()
    assert "E=mc^2" in found
    assert "Energy" in found
    assert "POWER" not in found


def test_dual_of_ten_pages_interleaves_original_and_translation(tmp_path):
    source = tmp_path / "long.pdf"
    write_lines(source, [[f"PAGE-{index:02d}"] for index in range(1, 11)])
    _mono, dual = translate(
        [source],
        service=_swap("TRAN"),
        output=str(tmp_path),
        cache_path=str(tmp_path / "cache.sqlite"),
    )[0]
    document = pymupdf.open(dual)
    assert document.page_count == 20
    for index in range(10):
        original = document[index * 2].get_text()
        translated = document[index * 2 + 1].get_text()
        marker = f"{index + 1:02d}"
        assert f"PAGE-{marker}" in original
        assert "TRAN" not in original
        assert f"TRAN-{marker}" in translated
        assert "PAGE" not in translated


def test_pages_argument_limits_the_work(tmp_path):
    source = tmp_path / "pick.pdf"
    write_lines(source, [[f"PAGE-{index}"] for index in range(1, 5)])
    mono, dual = translate(
        [source],
        service=_swap("TRAN"),
        pages="1,3",
        output=str(tmp_path),
        cache_path=str(tmp_path / "cache.sqlite"),
    )[0]
    mono_doc = pymupdf.open(mono)
    dual_doc = pymupdf.open(dual)
    assert mono_doc.page_count == 2
    assert dual_doc.page_count == 4
    mono_text = "\n".join(page.get_text() for page in mono_doc)
    assert "TRAN-1" in mono_text
    assert "TRAN-3" in mono_text
    assert "PAGE-2" not in mono_text and "TRAN-2" not in mono_text
    assert "PAGE-4" not in mono_text and "TRAN-4" not in mono_text
    assert "PAGE-1" in dual_doc[0].get_text()
    assert "TRAN-1" in dual_doc[1].get_text()
    assert "PAGE-3" in dual_doc[2].get_text()
    assert "TRAN-3" in dual_doc[3].get_text()


def test_second_translation_uses_the_cache(tmp_path):
    source = tmp_path / "once.pdf"
    write_lines(source, [["Hello cache"]])
    calls = {"n": 0}

    def fake(text):
        calls["n"] += 1
        return "CACHED"

    cache = tmp_path / "cache.sqlite"
    common = dict(
        service=Probe(fake),
        output=str(tmp_path),
        cache_path=str(cache),
        formula_char="",
        formula_font="",
    )
    translate([source], **common)
    translate([source], **common)
    assert calls["n"] == 1


def test_a_prose_line_with_one_symbol_is_not_a_formula():
    from solarsystem.formula import DEFAULT_FORMULA_CHAR, DEFAULT_FORMULA_FONT, is_formula

    font_re = compile_pattern(None, DEFAULT_FORMULA_FONT)
    char_re = compile_pattern(None, DEFAULT_FORMULA_CHAR)
    prose = "with RET ≥6 weeks in duration and dietary protein"
    assert is_formula(prose, "ClassicalGaramondBT-Roma", font_re, char_re) is False
    assert is_formula("E=mc^2", "Times-Roman", font_re, char_re) is True


def test_translation_stays_inside_its_column(tmp_path):
    source = tmp_path / "columns.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((40, 140), "Sidebar note", fontsize=9, fontname="helv")
    page.insert_text((230, 140), "Body column", fontsize=9, fontname="helv")
    document.save(source)
    document.close()

    def fake(text):
        if "Sidebar" in text:
            return "侧栏说明应该留在左边"
        return "右栏正文留在原处"

    mono, _dual = translate(
        [source],
        service=Probe(fake),
        output=str(tmp_path),
        cache_path=str(tmp_path / "cache.sqlite"),
        formula_font="",
        formula_char="",
    )[0]
    sidebar = []
    body = []
    for block in pymupdf.open(mono)[0].get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                box = span["bbox"]
                if "侧栏" in text:
                    sidebar.append(box)
                if "右栏" in text:
                    body.append(box)
    assert sidebar and body
    assert max(box[2] for box in sidebar) < 210
    assert min(box[0] for box in body) > 180


def test_longer_translation_is_written_onto_the_page(tmp_path):
    source = tmp_path / "short.pdf"
    write_lines(source, [["Protein"]])
    mono, _dual = translate(
        [source],
        service=Probe(lambda _text: "蛋白质补充可以增加肌肉"),
        output=str(tmp_path),
        cache_path=str(tmp_path / "cache.sqlite"),
        formula_font="",
        formula_char="",
    )[0]
    found = pymupdf.open(mono)[0].get_text()
    assert "蛋白质补充可以增加肌肉" in found
    assert "Protein" not in found


def test_spacing_is_applied_before_drawing(tmp_path):
    source = tmp_path / "mix.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 120), "中文English", fontsize=16, fontname="china-ss")
    document.save(source)
    document.close()
    mono, _dual = translate(
        [source],
        service=Probe(lambda text: text),
        output=str(tmp_path),
        cache_path=str(tmp_path / "cache.sqlite"),
        lang_in="zh",
        lang_out="zh",
        formula_font="",
        formula_char="",
    )[0]
    assert "中文English" in pymupdf.open(mono)[0].get_text()


def test_cancel_writes_nothing(tmp_path):
    source = tmp_path / "stop.pdf"
    write_lines(source, [["PAGE-1"], ["PAGE-2"]])
    cancel = threading.Event()

    def fake(_text):
        cancel.set()
        return "SHOULD-NOT-LAND"

    with pytest.raises(Cancelled):
        translate(
            [source],
            service=Probe(fake),
            output=str(tmp_path),
            cache_path=str(tmp_path / "cache.sqlite"),
            cancel=cancel,
        )
    assert not (tmp_path / "stop-mono.pdf").exists()
    assert not (tmp_path / "stop-dual.pdf").exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_scanned_page_is_reported_and_skipped(tmp_path):
    source = tmp_path / "scan.pdf"
    document = pymupdf.open()
    page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 24, 24), 0)
    pixmap.set_rect(pixmap.irect, (40, 40, 40))
    page.insert_image(pymupdf.Rect(72, 72, 160, 160), pixmap=pixmap)
    document.save(source)
    document.close()
    with pytest.raises(SolarError) as caught:
        translate(
            [source],
            service=Probe(lambda text: text),
            output=str(tmp_path),
            cache_path=str(tmp_path / "cache.sqlite"),
        )
    assert "原生文字" in str(caught.value)
    assert not (tmp_path / "scan-mono.pdf").exists()


def test_missing_file_and_missing_key(tmp_path, monkeypatch):
    with pytest.raises(SolarError) as missing:
        translate([tmp_path / "nope.pdf"], output=str(tmp_path), cache_path=str(tmp_path / "c.sqlite"))
    assert missing.value.exit_code != 0
    monkeypatch.delenv("SOLARSYSTEM_API_KEY", raising=False)
    monkeypatch.delenv("SOLARSYSTEM_BASE_URL", raising=False)
    monkeypatch.delenv("SOLARSYSTEM_MODEL", raising=False)
    monkeypatch.setattr("solarsystem.config.config_files", lambda: [tmp_path / "absent.json"])
    sample = tmp_path / "one.pdf"
    write_lines(sample, [["Hello"]])
    with pytest.raises(SolarError) as secret:
        translate(
            [sample],
            service="openai_compatible",
            output=str(tmp_path),
            cache_path=str(tmp_path / "c.sqlite"),
        )
    message = str(secret.value)
    assert "API" in message
    assert "solarsystem.local.json" not in message
    assert "SOLARSYSTEM_API_KEY" not in message


def test_stream_returns_pdf_bytes(tmp_path):
    source = tmp_path / "stream-src.pdf"
    write_lines(source, [["PAGE-7"]])
    mono, dual = translate_stream(
        source.read_bytes(),
        service=_swap("TRAN"),
        output=str(tmp_path / "out"),
        cache_path=str(tmp_path / "cache.sqlite"),
    )
    assert pymupdf.open(stream=mono, filetype="pdf").page_count == 1
    assert pymupdf.open(stream=dual, filetype="pdf").page_count == 2


def test_images_and_notes_stay(tmp_path):
    source = tmp_path / "kept.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 90), "PAGE-1", fontsize=14, fontname="helv")
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), 0)
    pixmap.set_rect(pixmap.irect, (10, 20, 30))
    page.insert_image(pymupdf.Rect(300, 200, 360, 260), pixmap=pixmap)
    page.draw_rect(pymupdf.Rect(300, 280, 360, 300), color=(0, 0, 0), width=1)
    page.add_text_annot((200, 80), "keep-note")
    document.save(source)
    document.close()
    mono, _dual = translate(
        [source],
        service=_swap("TRAN"),
        output=str(tmp_path),
        cache_path=str(tmp_path / "cache.sqlite"),
    )[0]
    page = pymupdf.open(mono)[0]
    assert page.get_images()
    assert page.get_drawings()
    notes = list(page.annots() or [])
    assert any((annot.info or {}).get("content") == "keep-note" for annot in notes)


def test_rotated_text_is_not_sent(tmp_path):
    source = tmp_path / "turn.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "LEVEL", fontsize=16, fontname="helv")
    page.insert_text((200, 220), "TURNED", fontsize=16, fontname="helv", rotate=90)
    document.save(source)
    document.close()
    seen = []

    def fake(text):
        seen.append(text)
        return "FLAT"

    translate(
        [source],
        service=Probe(fake),
        output=str(tmp_path),
        cache_path=str(tmp_path / "cache.sqlite"),
        formula_font="",
        formula_char="",
    )
    assert seen == ["LEVEL"]
    found = pymupdf.open(tmp_path / "turn-mono.pdf")[0].get_text()
    assert "TURNED" in found
    assert "FLAT" in found


def test_extract_groups_a_formula_line(tmp_path):
    source = tmp_path / "formula.pdf"
    write_formula_pdf(source)
    page = pymupdf.open(source)[0]
    content = read_page(page, compile_pattern("Times", "Times"), compile_pattern("", ""))
    assert len(content.paragraphs) == 1
    assert content.paragraphs[0].source == "Energy {{v0}} remains"
    assert content.paragraphs[0].formula_count == 1
