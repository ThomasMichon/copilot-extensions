"""Tests for the stale-cutover breadcrumb + recovery path (#1756)."""

from __future__ import annotations

from pathlib import Path

from zdd import breadcrumb


def test_recover_stale_cutover_brackets_ipv6_old_endpoint(tmp_path: Path):
    """Regression test: recover_stale_cutover() must bracket an IPv6 old
    endpoint when forming make_client()'s base_url -- unbracketed
    ("http://::1:1234") is not a valid URL and breaks make_client/urlparse on
    the host's own colons."""
    breadcrumb.write_breadcrumb(
        tmp_path, state="draining", old={"bind": "::", "port": 9281},
        new_port=9282, error=None, started_at="2026-07-02T22:40:00Z",
    )

    seen_urls: list[str] = []

    class FakeClient:
        def __init__(self, base_url: str) -> None:
            seen_urls.append(base_url)

        def undrain(self):
            return {"draining": False}

    result = breadcrumb.recover_stale_cutover(
        tmp_path, make_client=FakeClient, health_check=lambda host, port: True,
    )

    assert result["recovered"] is True
    assert seen_urls == ["http://[::1]:9281"]


def test_recover_stale_cutover_no_breadcrumb_is_noop(tmp_path: Path):
    result = breadcrumb.recover_stale_cutover(
        tmp_path, make_client=lambda base_url: None,
    )
    assert result["recovered"] is False
