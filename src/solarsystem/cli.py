from __future__ import annotations

import argparse
import logging
import sys

from solarsystem.errors import SolarError
from solarsystem.i18n import text
from solarsystem.pipeline import translate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="solarsystem")
    parser.add_argument("inputs", nargs="*", help="PDF、DOC、DOCX、目录，或 http/https 链接")
    parser.add_argument("--pages", default=None, help="页码，例如 1,3-5。省略则处理全部页面")
    parser.add_argument("--lang-in", default="en")
    parser.add_argument("--lang-out", default="zh")
    parser.add_argument("--service", default="echo", choices=["echo", "openai_compatible"])
    parser.add_argument("--output", default=None, help="输出目录")
    parser.add_argument("--thread", type=int, default=4)
    parser.add_argument("--prompt", default=None, help="追加到翻译提示词末尾的文本文件")
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--skip-subset-fonts", action="store_true")
    parser.add_argument("--formula-font", default=None, help="判定公式的字体名正则")
    parser.add_argument("--formula-char", default=None, help="判定公式的字符正则")
    parser.add_argument("--web", action="store_true", help="只启动本地网页")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7474)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    if args.web:
        from solarsystem.webapp import serve

        serve(args.host, args.port)
        return 0
    if not args.inputs:
        print(text("cli.need_input"), file=sys.stderr)
        return 2
    try:
        translate(
            args.inputs,
            pages=args.pages,
            lang_in=args.lang_in,
            lang_out=args.lang_out,
            service=args.service,
            output=args.output,
            thread=args.thread,
            prompt=args.prompt,
            ignore_cache=args.ignore_cache,
            skip_subset_fonts=args.skip_subset_fonts,
            formula_font=args.formula_font,
            formula_char=args.formula_char,
            on_progress=_print_progress,
        )
    except SolarError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code
    return 0


def _print_progress(_page: int, _total: int, message: str) -> None:
    print(message, file=sys.stderr)
