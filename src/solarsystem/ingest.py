from __future__ import annotations

import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from solarsystem.errors import SolarError
from solarsystem.i18n import text

_OFFICE = {".doc", ".docx"}
_PDF = {".pdf"}
_SUPPORTED = _OFFICE | _PDF
_MAX_BYTES = 100 * 1024 * 1024


@dataclass
class Source:
    path: Path
    label: str


def expand_inputs(items: list[str], workdir: Path) -> list[Source]:
    found: list[Source] = []
    for item in items:
        found.extend(_one(item, workdir))
    if not found:
        raise SolarError(text("cli.need_input"))
    return found


def _one(item: str, workdir: Path) -> list[Source]:
    if item.startswith("http://") or item.startswith("https://"):
        return [_download(item, workdir)]
    path = Path(item)
    if not path.exists():
        raise SolarError(text("cli.file_missing", path=item), exit_code=2)
    if path.is_dir():
        files = [
            child
            for child in sorted(path.iterdir(), key=lambda candidate: candidate.name.lower())
            if child.is_file() and child.suffix.lower() in _SUPPORTED
        ]
        if not files:
            raise SolarError(text("cli.empty_dir", path=item))
        return [Source(_prepare(child, workdir), child.name) for child in files]
    if path.suffix.lower() not in _SUPPORTED:
        raise SolarError(text("cli.bad_type", path=item))
    return [Source(_prepare(path, workdir), path.name)]


def _prepare(path: Path, workdir: Path) -> Path:
    if path.suffix.lower() in _OFFICE:
        return _office_to_pdf(path, workdir)
    return path


def _download(url: str, workdir: Path) -> Source:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SolarError(text("cli.bad_scheme", url=url))
    name = Path(parsed.path).name or "remote.pdf"
    if Path(name).suffix.lower() not in _SUPPORTED:
        name = f"{name}.pdf"
    destination = workdir / name
    request = urllib.request.Request(url, headers={"User-Agent": "SolarSystem"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read(_MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SolarError(text("cli.download_fail", url=url, error=exc)) from exc
    if len(payload) > _MAX_BYTES:
        raise SolarError(text("cli.download_fail", url=url, error="file too large"))
    destination.write_bytes(payload)
    return Source(_prepare(destination, workdir), name)


def find_soffice() -> str | None:
    import os

    candidates = [
        os.environ.get("SOLARSYSTEM_SOFFICE"),
        "soffice",
        "libreoffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        located = shutil.which(candidate)
        if located:
            return located
        if Path(candidate).is_file():
            return candidate
    return None


def _office_to_pdf(path: Path, workdir: Path) -> Path:
    soffice = find_soffice()
    if not soffice:
        raise SolarError(text("cli.office_missing", path=path))
    try:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                str(workdir),
                str(path),
            ],
            check=True,
            timeout=180,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise SolarError(text("cli.office_failed", path=path, error=exc)) from exc
    produced = workdir / f"{path.stem}.pdf"
    if not produced.is_file():
        raise SolarError(text("cli.office_failed", path=path, error="no pdf"))
    return produced
