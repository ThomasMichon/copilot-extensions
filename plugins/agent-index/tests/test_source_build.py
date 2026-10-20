from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_index.source_build import effective_runtime_version

pytestmark = pytest.mark.guard
PACKAGE = "1.2.3-dev4"
SOURCE = f"{PACKAGE}+source.{'0' * 39}1.{'0' * 15}2"


@pytest.mark.parametrize("server", [False, True])
def test_installed_primary_and_server_report_same_generation(tmp_path: Path, server: bool) -> None:
    generation = tmp_path / "versions" / SOURCE
    prefix = generation / "server" if server else generation
    assert effective_runtime_version(
        "1.2.3.dev4", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == SOURCE


@pytest.mark.parametrize("version", ["1.2.3-dev4", "9.0.0"])
def test_checkout_cannot_claim_installed_source(tmp_path: Path, version: str) -> None:
    prefix = tmp_path / "versions" / SOURCE
    assert effective_runtime_version(
        version, prefix=prefix, module_path=tmp_path / "checkout" / "source_build.py",
    ) == version


def test_source_health_and_version_do_not_accept_remote_override(tmp_path: Path, monkeypatch, capsys) -> None:
    import argparse
    from importlib import metadata

    import agent_index
    from agent_index import __main__ as cli, source_build
    from agent_index.runtime_version import current_runtime_version

    prefix = tmp_path / "versions" / SOURCE / "server"
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(source_build, "__file__", str(prefix / "lib" / "source_build.py"))
    monkeypatch.setattr(metadata, "version", lambda _: "1.2.3.dev4")
    monkeypatch.setenv("AGENT_INDEX_RUNTIME_VERSION", "unrelated-selection")
    assert agent_index.__version__ == SOURCE
    assert current_runtime_version() == SOURCE
    monkeypatch.setattr(cli, "_status_payload", lambda: pytest.fail("source version queried a remote service"))
    assert cli.cmd_version(argparse.Namespace()) == 0
    assert capsys.readouterr().out == SOURCE + "\n"
