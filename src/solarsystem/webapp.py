from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from solarsystem.config import MASK, mask_secret, openai_settings, save_openai_settings
from solarsystem.errors import Cancelled, SolarError
from solarsystem.i18n import asset_root, text, use_language
from solarsystem.pipeline import translate

_MAX_BODY = 100 * 1024 * 1024
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_UPLOADS = {".pdf", ".doc", ".docx"}


class Job:
    def __init__(self, folder: Path, public_id: str):
        self.folder = folder
        self.public_id = public_id
        self.state = "queued"
        self.progress = text("web.queued")
        self.error = ""
        self.cancel = threading.Event()
        self.mono: Path | None = None
        self.dual: Path | None = None
        self.source: Path | None = None
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        with self.lock:
            payload = {
                "state": self.state,
                "progress": self.progress,
                "error": self.error,
                "mono": "",
                "dual": "",
            }
            if self.state == "done" and self.mono and self.dual:
                payload["mono"] = f"/api/jobs/{self.public_id}/mono"
                payload["dual"] = f"/api/jobs/{self.public_id}/dual"
            return payload


class App:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def create_job(self) -> Job:
        folder = Path(tempfile.mkdtemp(prefix="solarsystem-web-"))
        job = Job(folder, uuid.uuid4().hex)
        with self.lock:
            self.jobs[job.public_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)


def make_server(host: str = "127.0.0.1", port: int = 7474) -> ThreadingHTTPServer:
    app = App()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self._send_file(asset_root() / "web" / "index.html", "text/html; charset=utf-8")
                return
            if path == "/app.js":
                self._send_file(asset_root() / "web" / "app.js", "text/javascript; charset=utf-8")
                return
            if path == "/theme.css":
                self._send_file(asset_root() / "styles" / "theme.css", "text/css; charset=utf-8")
                return
            if path in {"/locale/zh.json", "/locale/en.json"}:
                self._send_file(asset_root() / "locale" / Path(path).name, "application/json; charset=utf-8")
                return
            if path == "/api/config":
                self._json(200, _public_config())
                return
            job, kind = _job_route(path)
            if job is None and kind is None:
                self._json(404, {"error": "not found"})
                return
            found = app.get(job)
            if found is None:
                self._json(404, {"error": "not found"})
                return
            if kind == "":
                self._json(200, found.snapshot())
                return
            target = {"mono": found.mono, "dual": found.dual, "source": found.source}.get(kind)
            if target is None or not target.is_file() or found.state == "cancelled":
                self._json(404, {"error": "not found"})
                return
            self._send_file(target, "application/pdf")

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/jobs":
                self._start_job()
                return
            if path == "/api/config":
                self._save_config()
                return
            job_id, kind = _job_route(path)
            if kind == "cancel" and job_id:
                found = app.get(job_id)
                if found is None:
                    self._json(404, {"error": "not found"})
                    return
                found.cancel.set()
                self._json(200, {"state": "cancelling"})
                return
            self._json(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            return

        def _start_job(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > _MAX_BODY:
                self._json(413, {"error": text("cli.download_fail", url="upload", error="file too large")})
                return
            body = self.rfile.read(length)
            try:
                form = parse_multipart(body, self.headers.get("Content-Type", ""))
            except ValueError:
                self._json(400, {"error": text("web.bad_upload")})
                return
            job = app.create_job()
            try:
                stored = _store_input(job, form)
            except SolarError as exc:
                job.state = "error"
                job.error = str(exc)
                job.progress = str(exc)
                self._json(400, {"error": str(exc)})
                return
            if stored is not None and stored.suffix.lower() == ".pdf":
                job.source = stored
            threading.Thread(target=_run_job, args=(job, form), daemon=True).start()
            self._json(200, {"id": job.public_id, "state": job.state})

        def _save_config(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 65536:
                self._json(413, {"error": "too large"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                self._json(400, {"error": "bad request"})
                return
            if not isinstance(payload, dict):
                self._json(400, {"error": "bad request"})
                return
            try:
                save_openai_settings(
                    str(payload.get("base_url") or ""),
                    str(payload.get("api_key") or ""),
                    str(payload.get("model") or ""),
                )
            except SolarError as exc:
                self._json(400, {"error": str(exc)})
                return
            except OSError as exc:
                self._json(500, {"error": text("web.config_write_fail", error=exc)})
                return
            self._json(200, _public_config())

        def _json(self, status: int, payload: dict):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, path: Path, content_type: str):
            if not path.is_file():
                self._json(404, {"error": "not found"})
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    server.app = app
    return server


def serve(host: str = "127.0.0.1", port: int = 7474) -> None:
    server = make_server(host, port)
    actual_host, actual_port = server.server_address
    print(f"SolarSystem 网页：http://{actual_host}:{actual_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def parse_multipart(body: bytes, content_type: str) -> dict:
    match = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type or "")
    if not match:
        raise ValueError("boundary")
    boundary = (match.group(1) or match.group(2)).encode("utf-8")
    parsed: dict = {}
    for raw in body.split(b"--" + boundary):
        part = raw
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if part in (b"", b"--") or not part:
            continue
        header_blob, separator, data = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        header = header_blob.decode("utf-8", "replace")
        name_match = re.search(r'name="([^"]*)"', header)
        if not name_match:
            continue
        file_match = re.search(r'filename="([^"]*)"', header)
        name = name_match.group(1)
        if file_match is not None:
            parsed[name] = {"filename": file_match.group(1), "data": data}
        else:
            parsed[name] = data.decode("utf-8")
    return parsed


def _store_input(job: Job, form: dict) -> Path | None:
    upload = form.get("file")
    link = form.get("link", "")
    if isinstance(upload, dict) and upload.get("filename"):
        name = Path(upload["filename"]).name
        if Path(name).suffix.lower() not in _UPLOADS:
            raise SolarError(text("web.bad_upload"))
        destination = job.folder / name
        destination.write_bytes(upload.get("data") or b"")
        return destination
    if isinstance(link, str) and link.strip():
        return None
    raise SolarError(text("web.need_source"))


def _run_job(job: Job, form: dict) -> None:
    use_language(_field(form, "ui_lang"))
    with job.lock:
        job.state = "running"
        job.progress = text("web.queued")
    try:
        target, prompt_path = _target_and_prompt(job, form)
        pair = translate(
            [target],
            pages=_pages(form),
            lang_in=_field(form, "lang_in") or "en",
            lang_out=_field(form, "lang_out") or "zh",
            service=_field(form, "service") or "openai_compatible",
            output=str(job.folder),
            thread=_thread(form),
            prompt=str(prompt_path) if prompt_path else None,
            ignore_cache=_flag(form, "ignore_cache"),
            skip_subset_fonts=_flag(form, "skip_subset_fonts"),
            formula_font=_optional(form, "formula_font"),
            formula_char=_optional(form, "formula_char"),
            base_url=_optional(form, "base_url"),
            api_key=_optional(form, "api_key"),
            model=_optional(form, "model"),
            cache_path=str(job.folder / "cache.sqlite"),
            cancel=job.cancel,
            on_progress=lambda _page, _total, message: _set_progress(job, message),
        )[0]
        with job.lock:
            job.mono = Path(pair[0])
            job.dual = Path(pair[1])
            job.state = "done"
            job.progress = text("web.echo_done" if _field(form, "service") == "echo" else "web.done")
    except Cancelled as exc:
        _clear_outputs(job)
        with job.lock:
            job.state = "cancelled"
            job.progress = str(exc)
            job.error = str(exc)
            job.mono = None
            job.dual = None
    except SolarError as exc:
        _keep_partial(job, exc)
        with job.lock:
            job.state = "error"
            job.error = str(exc)
            job.progress = str(exc)
    except Exception as exc:
        with job.lock:
            job.state = "error"
            job.error = str(exc)
            job.progress = str(exc)


def _set_progress(job: Job, message: str) -> None:
    with job.lock:
        if job.state == "running":
            job.progress = message


def _target_and_prompt(job: Job, form: dict) -> tuple[str, Path | None]:
    upload = form.get("file")
    if isinstance(upload, dict) and upload.get("filename"):
        target = str(job.folder / Path(upload["filename"]).name)
    else:
        target = _field(form, "link")
    prompt = _field(form, "prompt")
    prompt_path = None
    if prompt.strip():
        prompt_path = job.folder / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
    return target, prompt_path


def _clear_outputs(job: Job) -> None:
    for path in job.folder.glob("*-mono.pdf"):
        path.unlink(missing_ok=True)
    for path in job.folder.glob("*-dual.pdf"):
        path.unlink(missing_ok=True)
    for path in job.folder.glob("*.partial"):
        path.unlink(missing_ok=True)


def _keep_partial(job: Job, exc: SolarError) -> None:
    if not exc.outputs:
        return
    with job.lock:
        job.mono = Path(exc.outputs[0][0])
        job.dual = Path(exc.outputs[0][1])


def _job_route(path: str) -> tuple[str | None, str | None]:
    parts = [piece for piece in path.split("/") if piece]
    if len(parts) < 3 or parts[0] != "api" or parts[1] != "jobs":
        return None, None
    if not _JOB_ID.fullmatch(parts[2]):
        return None, None
    kind = parts[3] if len(parts) > 3 else ""
    if kind not in {"", "cancel", "mono", "dual", "source"}:
        return None, None
    return parts[2], kind


def _public_config() -> dict:
    settings = openai_settings(None, None, None)
    return {
        "base_url": settings["base_url"],
        "api_key": mask_secret(settings["api_key"]),
        "model": settings["model"],
        "api_key_set": bool(settings["api_key"]),
    }


def _field(form: dict, name: str) -> str:
    value = form.get(name, "")
    if isinstance(value, str):
        return value.strip()
    return ""


def _optional(form: dict, name: str) -> str | None:
    value = _field(form, name)
    if not value or value == MASK:
        return None
    return value


def _flag(form: dict, name: str) -> bool:
    return _field(form, name).lower() in {"1", "true", "on", "yes"}


def _pages(form: dict) -> str | None:
    value = _field(form, "pages")
    if value in {"", "all"}:
        return None
    return value


def _thread(form: dict) -> int:
    raw = _field(form, "thread") or "4"
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


def cleanup_jobs(app: App) -> None:
    for job in list(app.jobs.values()):
        job.cancel.set()
        shutil.rmtree(job.folder, ignore_errors=True)
