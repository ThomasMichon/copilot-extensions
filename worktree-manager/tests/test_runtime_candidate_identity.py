"""Candidate containment uses filesystem identity across Windows path spellings."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import NoReturn

import pytest

import worktree_manager
from worktree_manager import runtime_candidate, self_install, self_install_payload


class _PastContainment(Exception):
    pass


def _past_containment(*_args: object, **_kwargs: object) -> NoReturn:
    raise _PastContainment


def _native(path: Path) -> Path:
    return Path(chr(92) * 2 + "?" + chr(92) + str(path.resolve()))


def _candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
    application: bool = False, spelling: str = "ordinary",
) -> tuple[Path, SimpleNamespace]:
    slot = tmp_path / "versions" / "fixture"
    environment = slot / ".venv" if application else slot
    metadata = environment / "Lib" / "site-packages"
    package = metadata / "worktree_manager" / "__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("", encoding="utf-8")
    if application:
        (slot / "application.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys, "prefix",
        str(_native(environment) if spelling == "prefix-native" else environment),
    )
    monkeypatch.setattr(
        worktree_manager, "__file__",
        str(_native(package) if spelling == "package-native" else package),
    )
    installed = SimpleNamespace(
        locate_file=lambda _name: _native(metadata) if spelling == "package-native" else metadata,
        read_text=_past_containment,
    )
    monkeypatch.setattr(runtime_candidate, "distribution", lambda _name: installed)
    monkeypatch.setattr(self_install, "payload_version", lambda _path: worktree_manager.__version__)
    monkeypatch.setattr(self_install, "_runtime_installer", _past_containment)
    monkeypatch.setattr(self_install_payload, "_copy_payload", _past_containment)
    return slot, installed


@pytest.mark.skipif(os.name != "nt", reason="Native Windows namespace aliases")
@pytest.mark.parametrize("application", [False, True])
@pytest.mark.parametrize("spelling", ["package-native", "prefix-native"])
def test_native_aliases_pass_both_candidate_containment_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, application: bool, spelling: str,
) -> None:
    _candidate(tmp_path, monkeypatch, application=application, spelling=spelling)
    with pytest.raises(_PastContainment):
        runtime_candidate.prepare_runtime_candidate({"frozen_plugin": str(tmp_path / "source")})
    with pytest.raises(_PastContainment):
        runtime_candidate.verify_runtime_candidate()


@pytest.mark.parametrize("outside", ["import", "distribution"])
def test_real_outside_candidates_still_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outside: str,
) -> None:
    slot, installed = _candidate(tmp_path, monkeypatch)
    foreign = Path(str(slot) + "-other") / "package"
    foreign.mkdir(parents=True)
    if outside == "import":
        package = foreign / "__init__.py"
        package.touch()
        monkeypatch.setattr(worktree_manager, "__file__", str(package))
    else:
        installed.locate_file = lambda _name: foreign
    with pytest.raises(RuntimeError, match="Manager " + outside + " is outside"):
        runtime_candidate.verify_runtime_candidate()


def test_editable_candidate_still_refuses_after_containment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _slot, installed = _candidate(tmp_path, monkeypatch)
    installed.read_text = lambda _name: '{"dir_info":{"editable":true}}'
    with pytest.raises(RuntimeError, match="must install a non-editable package"):
        runtime_candidate.verify_runtime_candidate()
