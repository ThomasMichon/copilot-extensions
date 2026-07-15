"""Tests for Windows/WSL networking topology detection (coordinator inversion).

Covers the Phase 2 detection primitives -- ``is_wsl``, ``.wslconfig`` mode
detection corroborated by the ``vEthernet (WSL)`` adapter, the Windows
bind-host resolution, and the WSL client-URL probe order -- without touching a
real Windows host (every OS/subprocess seam is monkeypatched).
"""

from __future__ import annotations

import agent_dispatch.netinfo as netinfo

# -- is_wsl -----------------------------------------------------------------


def test_is_wsl_false_off_linux(monkeypatch):
    monkeypatch.setattr(netinfo.sys, "platform", "win32")
    assert netinfo.is_wsl() is False


def test_is_wsl_true_via_env(monkeypatch):
    monkeypatch.setattr(netinfo.sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert netinfo.is_wsl() is True


def test_is_wsl_true_via_proc(monkeypatch, tmp_path):
    monkeypatch.setattr(netinfo.sys, "platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    osrelease = tmp_path / "osrelease"
    osrelease.write_text("5.15.153.1-microsoft-standard-WSL2\n")
    monkeypatch.setattr(netinfo, "_WSL_PROBE_FILES", (str(osrelease),))
    assert netinfo.is_wsl() is True


def test_is_wsl_false_standalone_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(netinfo.sys, "platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    osrelease = tmp_path / "osrelease"
    osrelease.write_text("6.1.0-18-amd64\n")  # plain Debian (Wheatley)
    monkeypatch.setattr(netinfo, "_WSL_PROBE_FILES", (str(osrelease),))
    assert netinfo.is_wsl() is False


# -- .wslconfig parsing -----------------------------------------------------


def test_read_wslconfig_mode_mirrored(tmp_path):
    cfg = tmp_path / ".wslconfig"
    cfg.write_text("[wsl2]\nnetworkingMode=mirrored\nmemory=8GB\n")
    assert netinfo._read_wslconfig_mode(cfg) == "mirrored"


def test_read_wslconfig_mode_case_and_comment(tmp_path):
    cfg = tmp_path / ".wslconfig"
    cfg.write_text("[WSL2]\n  NetworkingMode = NAT  # inline comment\n")
    assert netinfo._read_wslconfig_mode(cfg) == "nat"


def test_read_wslconfig_mode_absent(tmp_path):
    cfg = tmp_path / ".wslconfig"
    cfg.write_text("[wsl2]\nmemory=8GB\n")
    assert netinfo._read_wslconfig_mode(cfg) is None


def test_read_wslconfig_mode_missing_file(tmp_path):
    assert netinfo._read_wslconfig_mode(tmp_path / "nope") is None


def test_read_wslconfig_mode_ignores_other_section(tmp_path):
    cfg = tmp_path / ".wslconfig"
    cfg.write_text("[experimental]\nnetworkingMode=mirrored\n")
    assert netinfo._read_wslconfig_mode(cfg) is None


# -- get_wsl_networking_mode ------------------------------------------------


def test_mode_explicit_mirrored_wins(monkeypatch):
    monkeypatch.setattr(netinfo, "_read_wslconfig_mode", lambda _p: "mirrored")
    # vEthernet present would say nat, but the explicit config wins.
    monkeypatch.setattr(netinfo, "_query_vethernet_wsl", lambda: ("present", "172.19.240.1"))
    assert netinfo.get_wsl_networking_mode("x") == "mirrored"


def test_mode_corroborate_nat_when_vethernet_present(monkeypatch):
    monkeypatch.setattr(netinfo, "_read_wslconfig_mode", lambda _p: None)
    monkeypatch.setattr(netinfo, "_query_vethernet_wsl", lambda: ("present", "172.19.240.1"))
    assert netinfo.get_wsl_networking_mode("x") == "nat"


def test_mode_corroborate_mirrored_when_vethernet_absent(monkeypatch):
    monkeypatch.setattr(netinfo, "_read_wslconfig_mode", lambda _p: None)
    monkeypatch.setattr(netinfo, "_query_vethernet_wsl", lambda: ("absent", None))
    assert netinfo.get_wsl_networking_mode("x") == "mirrored"


def test_mode_ambiguous_defaults_nat(monkeypatch):
    monkeypatch.setattr(netinfo, "_read_wslconfig_mode", lambda _p: None)
    monkeypatch.setattr(netinfo, "_query_vethernet_wsl", lambda: ("unknown", None))
    assert netinfo.get_wsl_networking_mode("x") == "nat"


# -- resolve_bind_host ------------------------------------------------------


def test_bind_host_mirrored_is_loopback(monkeypatch):
    monkeypatch.setattr(netinfo, "get_wsl_networking_mode", lambda _p=None: "mirrored")
    assert netinfo.resolve_bind_host("x") == "127.0.0.1"


def test_bind_host_nat_is_vethernet_ip(monkeypatch):
    monkeypatch.setattr(netinfo, "get_wsl_networking_mode", lambda _p=None: "nat")
    monkeypatch.setattr(netinfo, "_query_vethernet_wsl", lambda: ("present", "172.19.240.1"))
    assert netinfo.resolve_bind_host("x") == "172.19.240.1"


def test_bind_host_nat_fails_loud_when_unresolvable(monkeypatch):
    monkeypatch.setattr(netinfo, "get_wsl_networking_mode", lambda _p=None: "nat")
    monkeypatch.setattr(netinfo, "_query_vethernet_wsl", lambda: ("unknown", None))
    try:
        netinfo.resolve_bind_host("x")
    except RuntimeError as exc:
        assert "0.0.0.0" in str(exc)  # noqa: S104 -- asserting the error names it, never binds it
    else:
        raise AssertionError("expected resolve_bind_host to fail loud on NAT")


def test_bind_host_never_returns_wildcard(monkeypatch):
    monkeypatch.setattr(netinfo, "get_wsl_networking_mode", lambda _p=None: "nat")
    monkeypatch.setattr(netinfo, "_query_vethernet_wsl", lambda: ("present", "172.19.240.5"))
    host = netinfo.resolve_bind_host("x")
    assert host not in ("0.0.0.0", "")  # noqa: S104 -- asserting we never bind it


# -- resolve_wsl_client_url (probe order) -----------------------------------


def test_client_url_uses_valid_cache(monkeypatch):
    monkeypatch.setattr(netinfo, "_read_url_cache", lambda: "http://172.19.240.1:9847")
    monkeypatch.setattr(netinfo, "_probe_health", lambda url, timeout: True)
    monkeypatch.setattr(netinfo, "wsl_default_gateway", lambda: "172.19.240.1")
    assert netinfo.resolve_wsl_client_url(9847) == "http://172.19.240.1:9847"


def test_client_url_prefers_loopback_mirrored(monkeypatch):
    monkeypatch.setattr(netinfo, "_read_url_cache", lambda: None)
    monkeypatch.setattr(netinfo, "wsl_default_gateway", lambda: "192.168.0.1")
    monkeypatch.setattr(netinfo, "_probe_health", lambda url, timeout: "127.0.0.1" in url)
    saved = {}
    monkeypatch.setattr(netinfo, "_write_url_cache", lambda u: saved.setdefault("u", u))
    assert netinfo.resolve_wsl_client_url(9847) == "http://127.0.0.1:9847"
    assert saved["u"] == "http://127.0.0.1:9847"


def test_client_url_falls_through_to_gateway_nat(monkeypatch):
    monkeypatch.setattr(netinfo, "_read_url_cache", lambda: None)
    monkeypatch.setattr(netinfo, "wsl_default_gateway", lambda: "172.19.240.1")
    # loopback fails (Windows coordinator bound the vEthernet IP), gateway wins.
    monkeypatch.setattr(
        netinfo, "_probe_health", lambda url, timeout: "172.19.240.1" in url
    )
    monkeypatch.setattr(netinfo, "_write_url_cache", lambda u: None)
    assert netinfo.resolve_wsl_client_url(9847) == "http://172.19.240.1:9847"


def test_client_url_default_when_nothing_reachable(monkeypatch):
    monkeypatch.setattr(netinfo, "_read_url_cache", lambda: None)
    monkeypatch.setattr(netinfo, "wsl_default_gateway", lambda: "172.19.240.1")
    monkeypatch.setattr(netinfo, "_probe_health", lambda url, timeout: False)
    monkeypatch.setattr(netinfo, "_write_url_cache", lambda u: None)
    # Documented default; the caller's request then fails loud.
    assert netinfo.resolve_wsl_client_url(9847) == "http://127.0.0.1:9847"


# -- wsl_default_gateway ----------------------------------------------------


def test_default_gateway_parses_ip_route(monkeypatch):
    import types

    monkeypatch.setattr(netinfo.shutil, "which", lambda _n: "/usr/sbin/ip")
    monkeypatch.setattr(
        netinfo.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=0,
            stdout="default via 172.19.240.1 dev eth0 proto kernel\n",
            stderr="",
        ),
    )
    assert netinfo.wsl_default_gateway() == "172.19.240.1"


def test_default_gateway_none_without_ip(monkeypatch):
    monkeypatch.setattr(netinfo.shutil, "which", lambda _n: None)
    assert netinfo.wsl_default_gateway() is None
