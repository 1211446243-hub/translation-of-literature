import logging
import sqlite3

import pytest

from solarsystem.cache import Cache
from solarsystem.errors import SolarError
from solarsystem.pages import parse_pages
from solarsystem.spacing import polish_punctuation


def test_page_spec_is_one_based():
    assert parse_pages(None, 4) == [0, 1, 2, 3]
    assert parse_pages("1,3,5", 6) == [0, 2, 4]
    assert parse_pages("2-4", 6) == [1, 2, 3]
    assert parse_pages("1,3-5", 6) == [0, 2, 3, 4]


def test_page_spec_rejects_a_start_past_the_end():
    with pytest.raises(SolarError):
        parse_pages("1,9", 3)


def test_punctuation_follows_the_source_and_drops_spaces():
    assert polish_punctuation("Hello, world.", "你好 世界") == "你好，世界。"
    assert polish_punctuation("Results  Data from 49 studies", "结果  来自49项研究") == "结果：来自49项研究"
    assert polish_punctuation("in RET. Despite", "参与 RET。尽管有大量") == "参与RET。尽管有大量"
    assert polish_punctuation("CINAHL and SportDiscus.", "CINAHL 和 SportDiscus。") == "CINAHL和SportDiscus。"
    assert polish_punctuation("a (0.64, 4.33) value.", "一个 (0.64, 4.33) 值") == "一个（0.64，4.33）值。"
    assert polish_punctuation("Morton,1 Kevin", "莫顿,1 凯文") == "莫顿1，凯文"
    assert polish_punctuation("keep {{v0}} here.", "保留 {{v0}} 这里") == "保留{{v0}}这里。"
    assert polish_punctuation("价格100元", "价格100元") == "价格100元"
    assert polish_punctuation("Eric Helms stays.", "Eric Helms 保留") == "Eric Helms保留。"


def test_cache_write_failure_is_logged(tmp_path, caplog):
    cache = Cache(tmp_path / "cache.sqlite")

    class Broken:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("readonly")

        def commit(self):
            return None

    cache._conn = Broken()
    with caplog.at_level(logging.ERROR):
        cache.put("echo", "en", "zh", "echo", "hello", "你好")
    assert "写入翻译缓存失败" in caplog.text
