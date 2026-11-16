"""Core presentation writes cross the actual bounded native socket boundary."""

from collections.abc import Callable
from contextlib import AbstractContextManager
import json
from pathlib import Path
import socket
import subprocess

import pytest

from agent_remote_driver.mux_status import apply_psmux_status
from test_mux_observation import native_endpoint as _native_endpoint

native_endpoint: Callable[
    [Path, tuple[bytes, ...]], AbstractContextManager[list[bytes]],
] = _native_endpoint


def test_native_status_options_are_quoted_and_read_back_without_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("native CLI"))
    identity = b"OK\nwt-fixture\t$7:100\n"
    value = '#[fg=green] title "quoted"; C:\\fixture \u00e8'
    with native_endpoint(tmp_path, (
        identity, identity, b"OK\n", b"OK\n" + value.encode("utf-8") + b"\n",
        identity, b"OK\n", b"OK\nstatus\n", identity,
    )) as captured:
        assert apply_psmux_status(
            "wt-fixture", "$7:100", {"@aw_ctx": value, "@aw_seg": "status"},
            data_dir=tmp_path,
        )
    scheme = b"AU" + b"TH "
    assert captured[::2] == [scheme + b"synthetic-key\n"] * 8
    assert captured[5] == (
        'set-option -t "$7" @aw_ctx ' + json.dumps(value, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    assert captured[7] == b'show-options -v -t "$7" @aw_ctx\n'
    assert len(captured) == 16


@pytest.mark.parametrize("response", [
    b"ERROR: authentication\n", b"OK\nwt-other\t$7:100\n", b"OK\nwt-fixture\t$8:101\n",
])
def test_native_status_refuses_unconfirmed_receiver_before_writing(
    tmp_path: Path, response: bytes,
) -> None:
    with native_endpoint(tmp_path, (response,)) as captured:
        assert not apply_psmux_status(
            "wt-fixture", "$7:100", {"@aw_ctx": "title"}, data_dir=tmp_path,
        )
    assert len(captured) == 2 and captured[1].startswith(b"display-message ")


def test_native_status_rejects_partial_or_unconfirmed_apply(tmp_path: Path) -> None:
    identity = b"OK\nwt-fixture\t$7:100\n"
    with native_endpoint(tmp_path, (
        identity, identity, b"OK\n", b"OK\nnot-the-requested-value\n",
    )) as captured:
        assert not apply_psmux_status(
            "wt-fixture", "$7:100", {"@aw_ctx": "title", "@aw_seg": "status"},
            data_dir=tmp_path,
        )
    assert len(captured) == 8
    assert all(b"@aw_seg" not in command for command in captured[1::2])


@pytest.mark.parametrize("values", [
    {"default-command": "forbidden"}, {"@aw_updater": "foreign-owner"},
    {"@aw_ctx": "title\nkill-server"}, {"@aw_ctx": "title\x00"},
])
def test_native_status_rejects_nonpresentation_effects_before_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str],
) -> None:
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("invalid request"))
    assert not apply_psmux_status("wt-fixture", "$7:100", values, data_dir=tmp_path)
