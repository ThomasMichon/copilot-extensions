"""Tests for the built-in ``/ui`` control surface and the ``agent-bridge ui`` opener."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import service_start_cli
from agent_bridge.routes import ui

STATIC = Path(str(resources.files("agent_bridge").joinpath("ui_static")))
JS = ("app.js", "viewer.js", "model.js", "dom.js")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ui.router)
    return TestClient(app)


def _src(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_ui_is_served_with_a_strict_csp_and_no_inline_code() -> None:
    resp = _client().get("/ui")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    csp = resp.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert "script-src 'self'" in csp and "style-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["x-content-type-options"] == "nosniff"
    page = resp.text
    # Every script/style is an external same-origin file; no inline handlers.
    assert re.search(r"<script(?![^>]*\bsrc=)", page) is None
    assert "<style" not in page and " onclick=" not in page and "style=" not in page
    assert '<script type="module" src="/ui/assets/app.js">' in page


def test_every_allowlisted_asset_is_packaged_and_served_with_its_type() -> None:
    client = _client()
    for name, media in ui.ASSETS.items():
        resp = client.get(f"/ui/assets/{name}")
        assert resp.status_code == 200, name
        assert resp.headers["content-type"] == media
        assert resp.headers["content-security-policy"] == ui._CSP
        assert resp.content == (STATIC / name).read_bytes()
    shipped = {p.name for p in STATIC.iterdir() if p.suffix in (".js", ".css")}
    assert shipped == set(ui.ASSETS)


@pytest.mark.parametrize("name", [
    "ui.py", "index.html", "..%2Froutes%2Fui.py", "%2e%2e%2f__init__.py", "APP.JS", "app.js.map",
])
def test_assets_outside_the_allowlist_are_not_served(name: str) -> None:
    assert _client().get(f"/ui/assets/{name}").status_code == 404


def test_assets_revalidate_by_etag() -> None:
    client = _client()
    first = client.get("/ui/assets/app.js")
    assert first.headers["cache-control"] == "no-cache"
    again = client.get("/ui/assets/app.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and not again.content


def test_assets_are_public_but_ui_data_routes_need_the_token() -> None:
    client = _authed_app()
    assert client.get("/ui").status_code == 200
    assert client.get("/ui/assets/app.css").status_code == 200
    assert client.get("/api/v1/ui/workspaces").status_code == 401
    assert client.post("/api/v1/ui/tasks", json={}).status_code == 401


def test_package_data_ships_the_ui_assets() -> None:
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"ui_static/*.js"' in pyproject and '"ui_static/*.css"' in pyproject
    assert '"ui_static/*.html"' in pyproject


def test_ui_talks_only_to_existing_token_protected_routes() -> None:
    code = "".join(_src(n) for n in JS)
    for path in ("/api/v1/live-sessions", "/events?after=", "/messages", "/api/v1/ui/workspaces",
                 "/api/v1/ui/tasks", "/api/v1/sessions", "/api/v1/agents", "/ui/exchange"):
        assert path in code, path
    assert "idempotency_key" in code
    assert re.search(r"https?://(?!acp-ui\.github\.io)", _src("index.html") + code) is None


def test_content_is_rendered_as_text_never_markup() -> None:
    for name in JS:
        src = _src(name)
        assert "innerHTML" not in src and "outerHTML" not in src, name
        assert "insertAdjacentHTML" not in src and "document.write" not in src, name
    assert "createTextNode" in _src("dom.js")


@needs_node
@pytest.mark.parametrize("name", JS)
def test_ui_scripts_are_valid_javascript_modules(tmp_path, name: str) -> None:
    path = tmp_path / (name[:-3] + ".mjs")
    path.write_text(_src(name), encoding="utf-8")
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

class _FakeClient:
    def __init__(self, code="c0de-1", fail=False):
        self.code, self.fail, self.calls = code, fail, []

    def _request(self, method, path, *a, **k):
        self.calls.append((method, path))
        if self.fail:
            raise RuntimeError("old daemon")
        return {"code": self.code, "expires_in": 60}


def _core_with(client):
    class _Core:
        @staticmethod
        def _service_port():
            return 43210

        @staticmethod
        def _get_client(**_kw):
            return client

    return _Core


def test_ui_command_prints_a_one_time_link_never_the_token(monkeypatch, capsys) -> None:
    client = _FakeClient()
    monkeypatch.setattr(service_start_cli, "_core", lambda: _core_with(client))
    monkeypatch.setattr("agent_bridge.config.load_or_create_auth_token", lambda: "t/k+n")
    service_start_cli._cmd_ui(argparse.Namespace(print_url=True))
    out = capsys.readouterr().out
    assert out.startswith("http://127.0.0.1:43210/ui#code=c0de-1")
    assert "t/k+n" not in out
    assert client.calls == [("POST", "/api/v1/ui/login-codes")]


def test_ui_command_opens_the_browser_signed_in_with_a_code(monkeypatch, capsys) -> None:
    """Browsers keep visited URLs (fragment included) in synced history: only a code goes there."""
    opened = []
    monkeypatch.setattr(service_start_cli, "_core", lambda: _core_with(_FakeClient()))
    monkeypatch.setattr("agent_bridge.config.load_or_create_auth_token", lambda: "t/k+n")
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    service_start_cli._cmd_ui(argparse.Namespace(print_url=False))
    assert opened == ["http://127.0.0.1:43210/ui#code=c0de-1"]
    assert "t/k+n" not in capsys.readouterr().out + opened[0]


def test_ui_command_falls_back_to_pasting_on_an_older_daemon(monkeypatch, capsys) -> None:
    opened = []
    monkeypatch.setattr(service_start_cli, "_core", lambda: _core_with(_FakeClient(fail=True)))
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    service_start_cli._cmd_ui(argparse.Namespace(print_url=False))
    assert opened == ["http://127.0.0.1:43210/ui"]
    assert "agent-bridge token" in capsys.readouterr().out


def _authed_app(token="secret-token"):
    from agent_bridge.auth import BearerAuthMiddleware

    app = FastAPI()
    app.state.auth_token = token
    app.include_router(ui.router)
    app.add_middleware(BearerAuthMiddleware, token=token)
    return TestClient(app)


def test_login_code_minting_requires_the_token() -> None:
    assert _authed_app().post("/api/v1/ui/login-codes").status_code == 401


def test_login_code_exchanges_once_for_the_token() -> None:
    client = _authed_app()
    code = client.post(
        "/api/v1/ui/login-codes", headers={"Authorization": "Bearer secret-token"},
    ).json()["code"]
    first = client.post("/ui/exchange", json={"code": code})
    assert first.status_code == 200 and first.json() == {"token": "secret-token"}
    assert first.headers["cache-control"] == "no-store"
    assert client.post("/ui/exchange", json={"code": code}).status_code == 403


def test_unknown_or_expired_login_code_is_refused(monkeypatch) -> None:
    client = _authed_app()
    assert client.post("/ui/exchange", json={"code": "guess"}).status_code == 403
    assert client.post("/ui/exchange", content=b"not json").status_code == 403
    code = client.post(
        "/api/v1/ui/login-codes", headers={"Authorization": "Bearer secret-token"},
    ).json()["code"]
    later = ui.time.monotonic() + ui.LOGIN_CODE_TTL + 1
    monkeypatch.setattr(ui.time, "monotonic", lambda: later)
    assert client.post("/ui/exchange", json={"code": code}).status_code == 403


def test_page_trades_a_login_code_and_never_reads_a_token_from_the_url() -> None:
    app = _src("app.js")
    assert "/ui/exchange" in app and "code=" in app
    assert "token=" not in app
    assert "history.replaceState" in app

