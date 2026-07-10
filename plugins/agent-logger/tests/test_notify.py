"""Tests for the target-independent post-push notify helper."""
from __future__ import annotations

import json

import agent_logger.sync.notify as notify_mod
from agent_logger._build_info import __version__


def test_post_notify_sets_explicit_user_agent(monkeypatch):
    """A default ``Python-urllib/*`` UA is 403'd by common bot/WAF filters
    (e.g. Cloudflare Browser Integrity Check), so the notify must send an
    explicit ``agent-logger/<ver>`` User-Agent."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        captured["timeout"] = timeout
        return None

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", fake_urlopen)

    ok = notify_mod.post_notify("https://hub.example/hook?m={machine}", "borealis")

    assert ok is True
    req = captured["req"]
    assert req.get_header("User-agent") == f"agent-logger/{__version__}"
    assert req.get_header("Content-type") == "application/json"
    assert req.get_method() == "POST"
    assert json.loads(req.data) == {"machine": "borealis"}
    # ``{machine}`` in the URL is substituted too.
    assert req.full_url == "https://hub.example/hook?m=borealis"


def test_post_notify_empty_url_is_noop(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(
        notify_mod.urllib.request,
        "urlopen",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )
    assert notify_mod.post_notify("", "borealis") is False
    assert called["n"] == 0
