from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_bridge
from agent_bridge import serving_reconcile, source_build, venue_cli

pytestmark = pytest.mark.guard

PACKAGE = "0.4.1-dev3"
FIRST = f"{PACKAGE}+source.{'f' * 40}.{'a' * 16}"
SECOND = f"{PACKAGE}+source.{'a' * 40}.{'b' * 16}"


def test_source_slot_advertises_build_without_changing_distribution_version(tmp_path: Path) -> None:
    prefix = tmp_path / "versions" / FIRST
    module = prefix / "lib" / "agent_bridge" / "source_build.py"
    module.parent.mkdir(parents=True)
    module.write_text("# installed\n", encoding="utf-8")
    assert source_build.effective_runtime_version(
        "0.4.1.dev3", prefix=prefix, module_path=module,
    ) == FIRST


def test_source_tree_or_mismatched_package_cannot_claim_slot_build(tmp_path: Path) -> None:
    prefix = tmp_path / "versions" / FIRST
    assert source_build.effective_runtime_version(
        PACKAGE, prefix=prefix, module_path=tmp_path / "checkout" / "source_build.py",
    ) == PACKAGE
    assert source_build.effective_runtime_version(
        "9.0.0", prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == "9.0.0"


def test_numeric_hash_components_keep_their_opaque_leading_zeroes(tmp_path: Path) -> None:
    version = f"{PACKAGE}+source.{'0' * 39}1.{'0' * 15}2"
    prefix = tmp_path / "versions" / version
    assert source_build.is_source_build_version(version)
    assert source_build.effective_runtime_version(
        PACKAGE, prefix=prefix, module_path=prefix / "lib" / "source_build.py",
    ) == version


@pytest.mark.parametrize("prefix", ["ordinary-venv", "versions/0.4.1-dev3", "versions/0.4.1+source.short"])
def test_ordinary_environment_keeps_package_version(tmp_path: Path, prefix: str) -> None:
    assert source_build.effective_runtime_version(
        PACKAGE, prefix=tmp_path / prefix, module_path=tmp_path / "source_build.py",
    ) == PACKAGE


@pytest.mark.parametrize(
    ("running", "target", "expected"),
    [
        (FIRST, FIRST, "current"),
        (FIRST, SECOND, "different-source"),
        (SECOND, FIRST, "different-source"),
        ("9.0.0", FIRST, "newer"),
        ("0.3.0", FIRST, "older"),
        (FIRST, "0.5.0", "older"),
        (FIRST, PACKAGE, "different-source"),
        ("1.0.0", "2.0.0", "older"),
        ("2.0.0", "1.0.0", "newer"),
        (FIRST, "0.4.0", "newer"),
    ],
)
def test_source_hashes_are_not_ordered_as_release_versions(
    running: str, target: str, expected: str,
) -> None:
    assert serving_reconcile.compare_serving_version(running, target) == expected


def test_different_source_probe_requests_cutover_instead_of_false_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def observed(_root: Path) -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(version=FIRST)

    monkeypatch.setattr(agent_bridge, "__version__", SECOND)
    monkeypatch.setattr(serving_reconcile, "observe_serving", observed)
    with pytest.raises(SystemExit) as stopped:
        serving_reconcile.reconcile_serving(argparse.Namespace(
            force=False, recover=False, probe_serving=True, json=False,
        ))
    assert stopped.value.code == 10


def test_different_source_reconcile_uses_existing_guarded_cutover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = SimpleNamespace(version=FIRST)

    @contextmanager
    def observed(_root: Path) -> Iterator[SimpleNamespace]:
        yield running

    calls = []
    monkeypatch.setattr(agent_bridge, "__version__", SECOND)
    monkeypatch.setattr(serving_reconcile, "observe_serving", observed)
    monkeypatch.setattr(venue_cli, "_cmd_deploy", lambda args, *, serving: calls.append(serving))
    serving_reconcile.reconcile_serving(argparse.Namespace(
        force=False, recover=False, probe_serving=False, json=False,
    ))
    assert calls == [running]


def test_source_target_cannot_downgrade_a_newer_serving_package(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    @contextmanager
    def observed(_root: Path) -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(version="9.0.0")

    calls = []
    monkeypatch.setattr(agent_bridge, "__version__", FIRST)
    monkeypatch.setattr(serving_reconcile, "observe_serving", observed)
    monkeypatch.setattr(venue_cli, "_cmd_deploy", lambda args, *, serving: calls.append(serving))
    with pytest.raises(SystemExit) as stopped:
        serving_reconcile.reconcile_serving(argparse.Namespace(
            force=False, recover=False, probe_serving=False, json=False,
        ))
    assert stopped.value.code == 1
    assert "serving daemon is newer" in capsys.readouterr().err
    assert calls == []
