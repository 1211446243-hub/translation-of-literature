import json
import re
import threading
import urllib.error
import urllib.request

import pymupdf
import pytest

from solarsystem.errors import Cancelled
from solarsystem.i18n import asset_root, text
from solarsystem.webapp import make_server
from tests.conftest import write_lines

IDS = [
    "file-input",
    "link-input",
    "lang-in",
    "lang-out",
    "pages",
    "start",
    "cancel",
    "progress",
    "preview",
    "download-mono",
    "download-dual",
]


@pytest.fixture
def site():
    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", server
    finally:
        server.shutdown()
        server.server_close()


def test_page_ids_title_and_theme():
    html = (asset_root() / "web" / "index.html").read_text(encoding="utf-8")
    css = (asset_root() / "styles" / "theme.css").read_text(encoding="utf-8")
    assert "<title>SolarSystem</title>" in html
    for element_id in IDS:
        assert f'id="{element_id}"' in html
    assert re.search(r"--color-canvas:\s*#[0-9a-fA-F]{3,8}", css)
    settings = html.split('id="settings"', 1)[1].split("</dialog>", 1)[0]
    assert 'id="switch-language"' in settings
    assert 'id="open-model"' in settings
    assert 'id="api-key"' in settings
    assert 'id="api-notice"' in html
    assert "solarsystem.local.json" not in html
    english = json.loads((asset_root() / "locale" / "en.json").read_text(encoding="utf-8"))
    assert english["language_switch"] == "中文"
    assert "solarsystem.local.json" not in json.dumps(english)


def test_server_page_matches_and_does_not_reveal_secrets(site, monkeypatch):
    base, _server = site
    monkeypatch.setenv("SOLARSYSTEM_API_KEY", "secret-value")
    monkeypatch.setenv("SOLARSYSTEM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("SOLARSYSTEM_MODEL", "demo-model")
    page = urllib.request.urlopen(base + "/").read().decode("utf-8")
    script = urllib.request.urlopen(base + "/app.js").read().decode("utf-8")
    assert "<title>SolarSystem</title>" in page
    for element_id in IDS:
        assert f'id="{element_id}"' in page
    theme = urllib.request.urlopen(base + "/theme.css").read().decode("utf-8")
    assert "--color-canvas:" in theme
    assert "secret-value" not in page
    assert "secret-value" not in script
    assert "solarsystem.local.json" not in page
    assert "solarsystem.local.json" not in script
    for name in ("zh.json", "en.json"):
        catalog = urllib.request.urlopen(base + "/locale/" + name).read().decode("utf-8")
        assert "secret-value" not in catalog
        assert "solarsystem.local.json" not in catalog
        assert "SOLARSYSTEM_" not in catalog
    config = json.loads(urllib.request.urlopen(base + "/api/config").read().decode("utf-8"))
    assert config["api_key"] == "********"
    assert config["api_key_set"] is True
    assert "secret-value" not in json.dumps(config)


def test_save_config_masks_the_key(site, tmp_path, monkeypatch):
    base, _server = site
    monkeypatch.delenv("SOLARSYSTEM_API_KEY", raising=False)
    monkeypatch.delenv("SOLARSYSTEM_BASE_URL", raising=False)
    monkeypatch.delenv("SOLARSYSTEM_MODEL", raising=False)
    path = tmp_path / "config.json"
    monkeypatch.setattr("solarsystem.config.config_files", lambda: [path])
    saved = _post(
        base + "/api/config",
        json.dumps(
            {"base_url": "https://api.example.test", "api_key": "user-secret", "model": "demo"}
        ).encode("utf-8"),
        "application/json",
    )
    assert saved["api_key"] == "********"
    assert saved["api_key_set"] is True
    assert "user-secret" not in json.dumps(saved)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["openai_compatible"]["api_key"] == "user-secret"
    kept = _post(
        base + "/api/config",
        json.dumps({"base_url": "https://api.example.test", "api_key": "", "model": "demo"}).encode("utf-8"),
        "application/json",
    )
    assert kept["api_key"] == "********"
    assert "user-secret" not in json.dumps(kept)
    assert json.loads(path.read_text(encoding="utf-8"))["openai_compatible"]["api_key"] == "user-secret"


def test_english_copy_is_served_and_selected(site):
    from solarsystem.i18n import text, use_language

    base, _server = site
    english = json.loads(urllib.request.urlopen(base + "/locale/en.json").read().decode("utf-8"))
    assert english["settings"] == "Settings"
    use_language("en")
    try:
        assert text("web.queued") == "Preparing"
    finally:
        use_language("zh")
    assert text("web.queued") == "正在准备"


def test_upload_translate_and_download(site, tmp_path):
    base, _server = site
    source = tmp_path / "paper.pdf"
    write_lines(source, [["Hello web"]])
    body, content_type = _form(
        {"service": "echo", "lang_in": "en", "lang_out": "zh", "pages": "all"},
        {"file": ("paper.pdf", source.read_bytes())},
    )
    payload = _post(base + "/api/jobs", body, content_type)
    status = _wait(base, payload["id"])
    assert status["state"] == "done"
    mono = pymupdf.open(stream=_get(base + status["mono"]), filetype="pdf")
    dual = pymupdf.open(stream=_get(base + status["dual"]), filetype="pdf")
    assert mono.page_count == 1
    assert "Hello web" in mono[0].get_text()
    assert dual.page_count == 2


def test_cancel_leaves_no_download(site, tmp_path, monkeypatch):
    base, server = site

    def blocked(_files, **options):
        if not options["cancel"].wait(5):
            raise AssertionError("cancel was not requested")
        raise Cancelled(text("cli.cancelled"))

    monkeypatch.setattr("solarsystem.webapp.translate", blocked)
    source = tmp_path / "paper.pdf"
    write_lines(source, [["Wait here"]])
    body, content_type = _form({"service": "echo"}, {"file": ("paper.pdf", source.read_bytes())})
    payload = _post(base + "/api/jobs", body, content_type)
    _post(base + f"/api/jobs/{payload['id']}/cancel", b"", "application/octet-stream")
    status = _wait(base, payload["id"])
    assert status["state"] == "cancelled"
    job = server.app.jobs[payload["id"]]
    assert list(job.folder.glob("*-mono.pdf")) == []
    assert list(job.folder.glob("*-dual.pdf")) == []
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(base + f"/api/jobs/{payload['id']}/mono")
    assert caught.value.code == 404


def _form(fields, files):
    boundary = "----SolarBoundary"
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    for name, (filename, data) in files.items():
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n".encode()
            + data
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _post(url, body, content_type):
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", content_type)
    request.add_header("Content-Length", str(len(body)))
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url):
    with urllib.request.urlopen(url) as response:
        return response.read()


def _wait(base, job_id):
    import time

    last = {}
    for _ in range(50):
        last = json.loads(_get(base + f"/api/jobs/{job_id}").decode("utf-8"))
        if last["state"] in {"done", "cancelled", "error"}:
            return last
        time.sleep(0.1)
    return last
