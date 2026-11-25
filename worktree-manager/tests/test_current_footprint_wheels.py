"""Published inputs keep Manager's existing installation and activation owners."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

import agent_procutil

from worktree_manager import runtime_source_release as release
from worktree_manager import runtime_source_self_update as update
from worktree_manager import self_install, source_config
from worktree_manager import engine_client
from worktree_manager.runtime_source import ResolvedSource, SourceAcquisitionError
from worktree_manager.runtime_source_config import RuntimeSource


@pytest.fixture
def release_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    root = tmp_path / "manager"
    root.mkdir()
    before = b"[source]\nref = 'main'\n"
    (root / "config.toml").write_bytes(before)
    calls: list[str] = []
    wheels = object()
    resolved = ResolvedSource(
        RuntimeSource("main"), tmp_path / "source", "a" * 40, "b" * 40, "a" * 40,
    )
    selected = SimpleNamespace(
        payload=tmp_path / "selected",
        identity=SimpleNamespace(source_commit=resolved.commit, source_tree=resolved.tree),
    )
    state = SimpleNamespace(
        root=root, calls=calls, wheels=wheels, resolved=resolved, selected=selected,
        code=0, mismatch=False, now=time.monotonic(),
    )

    @contextmanager
    def acquire(selection: RuntimeSource, **options: Any) -> Iterator[ResolvedSource]:
        assert selection == resolved.selection
        assert options["root"] == root / "manager-source"
        assert options["environment"] == {"APPROVED_AUTH": "fixture"}
        assert options["compact"] is (os.name == "nt")
        assert time.monotonic() < options["deadline"] <= time.monotonic() + 600
        calls.append("source-held")
        try:
            yield resolved
        finally:
            calls.append("source-released")
            if state.now >= options["deadline"]:
                raise SourceAcquisitionError("source preparation deadline expired")

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        assert calls == ["source-held"]
        assert command[command.index("--commit") + 1] == resolved.commit
        assert command[command.index("--repository") + 1] == "ThomasMichon/copilot-extensions"
        assert "--snapshot" in command
        assert json.loads(command[command.index("--github-command") + 1]) == [
            "fixture-engine", "repos", "gh", "--require-account", "ThomasMichon/copilot-extensions", "--",
        ]
        output = Path(command[command.index("--output") + 1])
        assert output.parent == root / "manager-source/runtime-sources/released"
        calls.append("prepared")
        state.snapshot = output / "profile/snapshot"
        if state.code:
            raise subprocess.CalledProcessError(state.code, command, stderr="controlled refusal")
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "snapshot": str(tmp_path / "foreign" if state.mismatch else state.snapshot),
        }))

    @contextmanager
    def held(snapshot: Path) -> Iterator[tuple[SimpleNamespace, dict[str, Any]]]:
        assert snapshot == state.snapshot and calls == ["source-held", "prepared", "source-released"]
        calls.append("wheels-held")
        try:
            yield selected, {
                "wheel_inputs": wheels, "local_packages": ("copilot-extensions-worktree-manager",),
                "application": tmp_path / "new-footprint",
            }
        finally:
            calls.append("wheels-released")

    engine = SimpleNamespace(
        run_owned=run, _native_retained_path=str, _select_uv_executable=lambda _: str(tmp_path / "uv"),
    )

    @contextmanager
    def preparation_python(*_args: Any) -> Iterator[Path]:
        yield Path(sys.executable)

    monkeypatch.setattr(release, "_preparation_python", preparation_python)
    monkeypatch.setattr(release, "acquire", acquire)
    monkeypatch.setattr(release, "_provider", lambda _: tmp_path / "own-tools/bootstrap_manager_release.py")
    monkeypatch.setattr(release, "runtime_scripts", lambda *_: tmp_path / "own-scripts")
    monkeypatch.setattr(release, "load_runtime_installer", lambda _: SimpleNamespace(
        engine=engine, prepared_manager_inputs=held,
    ))
    monkeypatch.setattr(shutil, "which", lambda name: str(tmp_path / name))
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: ["fixture-engine"])
    monkeypatch.setattr(release, "_scoped_client", lambda client, repository: [
        *client, "repos", "gh", "--require-account", repository, "--",
    ])
    monkeypatch.setattr(release, "time", SimpleNamespace(monotonic=lambda: state.now))
    yield state
    assert (root / "config.toml").read_bytes() == before


def test_released_wheels_stay_held_without_selecting_application_layout(
    release_case: SimpleNamespace,
) -> None:
    state = release_case
    with release.prepared(
        RuntimeSource().repo, state.root, environment={"APPROVED_AUTH": "fixture"},
    ) as selected:
        assert selected == (state.selected.payload, {
            "_wheel_inputs": state.wheels,
            "_local_packages": ("copilot-extensions-worktree-manager",),
        })
        assert state.calls == ["source-held", "prepared", "source-released", "wheels-held"]
        state.calls.append("consumed")
    assert state.calls[-2:] == ["consumed", "wheels-released"]


def test_release_preparation_uses_bootstraps_qualified_uv(
    release_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    state = release_case
    uv = tmp_path / "qualified-uv"
    installer = getattr(release, "load_runtime_installer")(tmp_path)
    selected = []

    def select(command: str) -> str:
        selected.append(command)
        assert command == str(uv)
        return command

    original = cast(Callable[..., subprocess.CompletedProcess[str]], installer.engine.run_owned)

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        assert command[command.index("--uv") + 1] == str(uv)
        return original(command, **options)

    monkeypatch.setattr(installer.engine, "_select_uv_executable", select)
    monkeypatch.setattr(installer.engine, "run_owned", run)
    with release.prepared(
        RuntimeSource().repo, state.root, environment={"APPROVED_AUTH": "fixture"}, uv=uv,
    ) as prepared:
        assert prepared is not None
    assert selected == [str(uv)]


@pytest.mark.parametrize("failure", [None, "venv", "install", "provider"])
def test_preparation_tools_are_governed_and_held_until_provider_settles(
    tmp_path: Path, failure: str | None,
) -> None:
    calls: list[str] = []
    environment = {"UV_INDEX_URL": "https://configured.example/simple"}
    deadline = time.monotonic() + 600
    python = tmp_path / "tools" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    @contextmanager
    def temporary(parent: Path | None) -> Iterator[str]:
        assert parent is None
        calls.append("tools-held")
        try:
            yield str(tmp_path)
        finally:
            calls.append("tools-released")

    @contextmanager
    def retain(uv: str, arguments: list[str], **options: Any) -> Iterator[SimpleNamespace]:
        assert uv == "qualified-uv"
        assert arguments == ["--python", str(python), "packaging>=25"]
        assert options == {
            "env": environment, "cwd": tmp_path, "deadline": deadline,
        }
        calls.append("policy-held")
        try:
            yield SimpleNamespace(argv=("admitted-install",), cwd=tmp_path, environment=environment)
        finally:
            calls.append("policy-released")

    def run(command: list[str], **options: Any) -> None:
        phase = "install" if command == ["admitted-install"] else "venv"
        if phase == "venv":
            assert command == [
                "qualified-uv", "venv", "--no-project", "--no-python-downloads",
                "--python", sys.executable, str(tmp_path / "tools"),
            ]
        assert options["cwd"] == tmp_path and options["env"] is environment
        assert 0 < options["timeout"] <= 600 and options["deadline"] == deadline
        assert options["procutil"] is agent_procutil
        assert options["construction_fd"] is None
        calls.append(phase)
        if failure == phase:
            raise RuntimeError(phase)

    engine = SimpleNamespace(
        _temporary_inputs=temporary, _native_retained_path=str,
        _private_retained_root=lambda path: path, retain_uv_install=retain, run_owned=run,
    )

    def consume() -> None:
        with release._preparation_python(engine, "qualified-uv", environment, deadline) as selected:
            assert selected == python
            assert calls == ["tools-held", "venv", "policy-held", "install"]
            calls.append("provider")
            if failure == "provider":
                raise RuntimeError(failure)

    if failure is None:
        consume()
    else:
        with pytest.raises(RuntimeError, match=failure):
            consume()
    assert calls[-1] == "tools-released"
    if failure != "venv":
        assert calls[-2] == "policy-released"


@pytest.mark.parametrize("absent", [False, True])
def test_preparation_deadline_does_not_expire_after_consumption(
    release_case: SimpleNamespace, absent: bool,
) -> None:
    state = release_case
    state.code = 3 if absent else 0
    with release.prepared(
        RuntimeSource().repo, state.root, environment={"APPROVED_AUTH": "fixture"},
    ) as selected:
        assert (selected is None) is absent
        state.now += 601
        state.calls.append("installed")
    assert state.calls.index("source-released") < state.calls.index("installed")


@pytest.mark.parametrize("fault", ["absent", "verification", "output", "source"])
def test_only_absent_published_inputs_permit_source_fallback(
    release_case: SimpleNamespace, fault: str,
) -> None:
    state = release_case
    if fault == "source":
        state.selected.identity.source_commit = "c" * 40
    elif fault == "output":
        state.mismatch = True
    else:
        state.code = 3 if fault == "absent" else 1
    if fault == "absent":
        with release.prepared(
            RuntimeSource().repo, state.root, environment={"APPROVED_AUTH": "fixture"},
        ) as selected:
            assert selected is None
    else:
        with pytest.raises(
            subprocess.CalledProcessError if fault == "verification" else ValueError,
        ):
            with release.prepared(
                RuntimeSource().repo, state.root, environment={"APPROVED_AUTH": "fixture"},
            ):
                pytest.fail("invalid release reached installation")
    assert "source-released" in state.calls
    if "wheels-held" in state.calls:
        assert state.calls.index("source-released") < state.calls.index("wheels-held")
        assert state.calls[-1] == "wheels-released"
    else:
        assert state.calls[-1] == "source-released"
    assert "wheels-held" not in state.calls or fault == "source"


@pytest.mark.parametrize("locator,client", [
    ("https://example.invalid/owner/repository.git", True),
    ("https://github.com.evil.invalid/owner/repository.git", True),
    ("https://github.com/owner/repository.git", False),
])
def test_unsupported_acquisition_keeps_ordinary_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locator: str, client: bool,
) -> None:
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: ["fixture-engine"] if client else None)
    monkeypatch.setattr(release, "acquire", lambda *_args, **_kwargs: pytest.fail("no release source"))
    with release.prepared(locator, tmp_path) as selected:
        assert selected is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("support", [
    "current", "missing", "malformed", "wrong-version", "no-capability", "different-command",
])
def test_release_reader_selects_the_executing_engines_strict_credential_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, support: str,
) -> None:
    prefix = tmp_path / "engine"
    descriptor = prefix / (
        "Lib/site-packages/agent_worktrees/repos-gh-v1.json" if os.name == "nt"
        else "lib/python3.11/site-packages/agent_worktrees/repos-gh-v1.json"
    )
    descriptor.parent.mkdir(parents=True)
    body = {
        "contract": "agent-worktrees.repos-gh",
        "version": 2 if support == "wrong-version" else 1,
        "capabilities": [] if support == "no-capability" else ["require-account"],
    }
    if support != "missing":
        descriptor.write_text("invalid" if support == "malformed" else json.dumps(body), encoding="utf-8")
    client = [str(prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
              "-B", "-m", "agent_worktrees"]
    monkeypatch.setattr(engine_client, "installed_engine_command", lambda: client)
    requested = ["different-engine"] if support == "different-command" else client
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no negotiation or request"))
    assert release._scoped_client(requested, "example/repository") == (
        [*client, "repos", "gh", "--require-account", "example/repository", "--"]
        if support == "current" else None
    )


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("source_changed", [False, True])
def test_ordinary_main_update_consumes_wheels_at_original_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool, source_changed: bool,
) -> None:
    root = tmp_path / "manager"
    source_config.set_source(ref="main", root=root)
    before = (root / "config.toml").read_bytes()
    payload = tmp_path / "published"
    payload.mkdir()
    (payload / "pyproject.toml").write_text("[project]\nversion='1'\n")
    wheels = object()
    held: list[bool] = []
    installed: list[Path] = []

    @contextmanager
    def prepared(repo: str, destination: Path, **options: Any) -> Iterator[tuple[Path, dict[str, Any]]]:
        assert repo == self_install.manager_repo(root) and destination == root
        held.append(True)
        try:
            yield payload, {"_wheel_inputs": wheels, "_local_packages": ("manager",)}
        finally:
            held.clear()
            if source_changed:
                raise SourceAcquisitionError("source changed after installation")

    def install(*, payload_dir: Path, **options: Any) -> SimpleNamespace:
        assert payload_dir == payload and held == [True]
        assert options["root"] == root and options["dry_run"] is dry_run
        assert options["_wheel_inputs"] is wheels and "_application" not in options
        assert options["_transactional_activation"]
        installed.append(payload_dir)
        return SimpleNamespace(action="installed", version="1", slot=root / "versions/1")

    monkeypatch.setattr(release, "prepared", prepared)
    monkeypatch.setattr(shutil, "which", lambda _: "git")
    from worktree_manager import update_auth
    monkeypatch.setattr(update_auth, "environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no source rebuild"))
    monkeypatch.setattr(
        self_install, "_fetch_via_tarball",
        lambda *_args, **_kwargs: pytest.fail("no tarball fallback after wheel selection"),
    )
    monkeypatch.setattr(self_install, "self_install", install)
    result = update.run_update(root=root, dry_run=dry_run)
    assert result.action == ("error" if source_changed else "updated")
    if source_changed:
        assert result.version == "1" and "source changed after installation" in (result.reason or "")
    assert installed == [payload]
    assert not held
    assert (root / "config.toml").read_bytes() == before
