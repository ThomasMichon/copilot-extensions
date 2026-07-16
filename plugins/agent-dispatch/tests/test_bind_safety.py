"""Tests for the coordinator bind-safety guard."""

from __future__ import annotations

import pytest

from agent_dispatch.config import Config, requires_token_bind
from agent_dispatch.server import UnsafeBindError, check_bind_safety


def test_requires_token_bind_flags_wildcards():
    assert requires_token_bind("0.0.0.0")  # noqa: S104
    assert requires_token_bind("::")
    assert not requires_token_bind("127.0.0.1")
    assert not requires_token_bind("172.17.0.1")  # a docker bridge gateway (host-local)
    assert not requires_token_bind("172.19.240.1")  # a Windows vEthernet(WSL) IP


def test_wildcard_bind_without_token_is_refused():
    cfg = Config(host="0.0.0.0", token=None)  # noqa: S104
    with pytest.raises(UnsafeBindError):
        check_bind_safety(cfg)


def test_wildcard_bind_with_token_is_allowed():
    cfg = Config(host="0.0.0.0", token="s3cret")  # noqa: S104
    check_bind_safety(cfg)  # no raise


def test_loopback_without_token_is_allowed():
    cfg = Config(host="127.0.0.1", token=None)
    check_bind_safety(cfg)  # no raise


def test_specific_hostlocal_ip_without_token_is_allowed():
    # A deliberate non-LAN interface bind (vEthernet / docker gateway) is not
    # guarded -- only the wildcard bind is.
    cfg = Config(host="172.17.0.1", token=None)
    check_bind_safety(cfg)  # no raise
