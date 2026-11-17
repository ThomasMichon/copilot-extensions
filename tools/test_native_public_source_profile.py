"""Source profiles accept public defaults without relaxing governed callers."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import os
from pathlib import Path
import ssl
from types import SimpleNamespace
import sys
import time
from typing import Any, cast

import local_install_profile as profiles
import pytest

import installer_dependency_policy as dependencies
import installer_native_feed as native
from install_source import ProjectSource
import installer_wheel_acquisition as acquisition


@pytest.mark.parametrize("index", [
    None, ("https://pypi.org/simple", None),
    ("https://packages.example/simple/", "internal"),
])
def test_source_profile_selects_public_default_or_configured_feed(
    monkeypatch: pytest.MonkeyPatch, index: tuple[str, str | None] | None,
) -> None:
    import governed_feed_trust

    monkeypatch.setattr(governed_feed_trust, "_effective_default_index_url", lambda _env: index)
    monkeypatch.setattr(governed_feed_trust, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    expected = index or ("https://pypi.org/simple", None)
    observed: dict[str, Any] = {}
    feed = SimpleNamespace(feeds=(SimpleNamespace(index_url=expected[0], identity="fixture"),))

    def capture(*_args: Any, **options: Any) -> native.NativeFeedInputs:
        observed.update(options)
        return cast(native.NativeFeedInputs, feed)

    monkeypatch.setattr(native, "capture_native_feed_inputs", capture)
    _, result = profiles._profile_feed(
        cast(ProjectSource, SimpleNamespace(identity=SimpleNamespace(document=lambda: {}))),
        cast(dependencies.RetainedUVInstall, SimpleNamespace(environment={})),
        ["original", "retained"], uv="unused",
        download_origins=(), wheels=None, deadline=time.monotonic() + 10,
    )
    assert result.feeds == feed.feeds
    assert observed["allow_public_indexes"] is True


@pytest.mark.parametrize("index", [
    None, "https://pypi.org/simple", "https://packages.example/simple",
])
@pytest.mark.parametrize("allow_public", [False, True])
def test_actual_native_settings_keep_public_admission_explicit_and_seasoned(
    tmp_path: Path, index: str | None, allow_public: bool,
) -> None:
    config = tmp_path / "uv.toml"
    config.write_text(
        "system-certs=true\n" + (
            '[[index]]\nname="selected"\ndefault=true\nurl="' + index + '"\n'
            'authenticate="never"\n' if index is not None else ""
        ),
    )
    requirements = tmp_path / "requirements.in"
    requirements.write_text("")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432",
        }
    }
    environment.update(
        HOME=str(tmp_path), USERPROFILE=str(tmp_path),
        UV_CONFIG_FILE=str(config), UV_CACHE_DIR=str(tmp_path / "cache"),
    )
    projections: list[str] = []
    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + 30
    with dependencies.retain_uv_compile(
        "uv", [str(requirements), "--python", sys.executable, "--only-binary", ":all:",
               "--no-python-downloads", "--system-certs"],
        cwd=tmp_path, env=environment, deadline=deadline, native_target=True, now=started,
        _projection=projections,
    ) as transaction:
        def capture() -> native.NativeFeedInputs:
            return native.capture_native_feed_inputs(
                transaction, projections[1], projections[0], None,
                {"policyId": "fixture", "selection": {"downloadOrigins": []}},
                "uv", deadline, wheels=[{"name": "fixture"}],
                profile_policy="native-source-profile",
                opaque_identity=lambda value: hashlib.sha256(value.encode()).hexdigest(),
                allow_public_indexes=allow_public,
            )
        if not allow_public and (index is None or index == "https://pypi.org/simple"):
            with pytest.raises(native.NativeFeedError, match="affirmative|selected indexes"):
                capture()
        else:
            captured = capture()
            assert {feed.index_url.rstrip("/") for feed in captured.feeds} == {
                index or "https://pypi.org/simple",
            }
            assert all(not feed.headers for feed in captured.feeds)
            assert captured.cutoffs["fixture"].timestamp() <= started.timestamp() - 7 * 86400
            assert captured.context.check_hostname
            assert len(captured.policy_id) == len("sha256:") + 64


@pytest.mark.parametrize("selected,accepted", [
    ("https://pypi.org/simple", True),
    ("https://packages.example/simple", False),
    ("https://test.pypi.org/simple", False),
])
def test_public_metadata_is_selected_not_substituted(
    monkeypatch: pytest.MonkeyPatch, selected: str, accepted: bool,
) -> None:
    client = acquisition._Client(
        acquisition.AcquisitionLimits(
            max_packages=1, max_requests=2, max_redirects=1, max_candidates_per_project=1,
            max_index_bytes=4096, max_total_metadata_bytes=4096,
            max_wheel_bytes=4096, max_closure_bytes=4096, max_temporary_bytes=8192, max_workers=1,
        ), time.monotonic() + 10, ssl.create_default_context(), {},
    )
    response = object()
    opened: list[str] = []

    def open_request(request: Any, *, timeout: float) -> object:
        opened.append(request.full_url)
        return response

    monkeypatch.setattr(client.opener, "open", open_request)
    feed = acquisition.WheelFeed("fixture", selected)
    location = "https://pypi.org/simple/fixture/"
    if accepted:
        result, actual, _ = client.request(location, feed, None, "application/json", index=True)
        assert result is response and actual == location
        assert opened == [location]
    else:
        with pytest.raises(acquisition.WheelError, match="public package index"):
            client.request(location, feed, None, "application/json", index=True)
        assert opened == []
