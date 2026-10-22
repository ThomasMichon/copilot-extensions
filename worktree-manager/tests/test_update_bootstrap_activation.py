"""Fresh bootstrap retains candidate custody through transactional daemon activation."""

from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from worktree_manager import self_install


class CommandDispatched(BaseException):
    pass


@pytest.fixture
def constructed_update(tmp_path, monkeypatch):
    installer = self_install._runtime_installer()
    root = tmp_path / "manager"
    previous = "1.2.2"
    version = "1.2.3"
    slot = root / "versions" / version
    (slot / ".runtime-bootstrap").mkdir(parents=True)
    (root / "versions" / previous).mkdir()
    (root / "versions" / "1.2.1").mkdir()
    selectors = {"current-version": b"1.2.2\r\n", "last-known-good": b"1.2.1"}
    for name, content in selectors.items():
        (root / name).write_bytes(content)
    python = slot / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    python.parent.mkdir(exist_ok=True)
    python.write_text("fixture interpreter; execution is intercepted\n")
    python.chmod(0o700)
    payload = tmp_path / "source" / "worktree-manager"
    (payload / "src/worktree_manager").mkdir(parents=True)
    (payload / "src/worktree_manager/__init__.py").write_text(f'__version__ = "{version}"\n')
    state = SimpleNamespace(
        installer=installer, root=root, slot=slot, python=python, payload=payload,
        previous=previous, selectors=selectors, active=False, resident=previous, cutovers=[], dispatch=[],
    )

    @contextmanager
    def candidate(*args, **kwargs):
        state.active = True
        try:
            yield SimpleNamespace(slot=slot, reused=False)
        finally:
            state.active = False

    monkeypatch.setattr(installer, "candidate", candidate)
    monkeypatch.setattr(self_install, "needs_install", lambda *args: True)
    monkeypatch.setattr(self_install, "clean_legacy_artifacts", lambda *args: [])
    monkeypatch.setattr(self_install, "_deploy_binstubs", lambda: ())
    monkeypatch.setattr(self_install, "_write_control_plane_provider_manifest", lambda *args, **kwargs: None)
    monkeypatch.setitem(
        sys.modules, "update_entry", SimpleNamespace(validate_manager_ownership=lambda *args: None),
    )

    def dispatch(executable, arguments):
        state.dispatch.append((executable, arguments))
        raise CommandDispatched

    monkeypatch.setattr(installer.os, "execv", dispatch)
    return state


@pytest.mark.parametrize("cutover_ok", [True, False], ids=["running-daemon", "failed-cutover"])
def test_repair_bootstrap_activates_before_releasing_candidate(
    constructed_update, monkeypatch, cutover_ok,
):
    state = constructed_update

    def activate(**kwargs):
        assert state.active
        assert (state.root / "current-version").read_text().strip() == state.slot.name
        state.cutovers.append(kwargs)
        if cutover_ok:
            state.resident = state.slot.name
        return {"action": "cutover", "result": {"ok": cutover_ok}}

    def popen(arguments, **kwargs):
        from worktree_manager import bootstrap_activation, mux_daemon_cutover

        assert arguments[:2] == [str(state.python), "-I"]
        assert arguments[2:6] == ["-S", "-B", "-X", "utf8"]
        assert Path(arguments[6]).parent == state.slot / ".runtime-bootstrap"
        assert arguments[7:9] == ["-m", "worktree_manager.bootstrap_activation"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        with monkeypatch.context() as child:
            child.setattr(sys, "prefix", str(state.slot))
            child.setattr(mux_daemon_cutover, "activate_after_update", activate)
            code = bootstrap_activation.main(arguments[9:])
        return SimpleNamespace(returncode=code, communicate=lambda: ("", ""))

    monkeypatch.setattr(state.installer.subprocess, "Popen", popen)
    arguments = [
        "--root", str(state.root), "--payload", str(state.payload),
        "--repair-update", "--", "update",
    ]
    if cutover_ok:
        with pytest.raises(CommandDispatched):
            state.installer.main(arguments)
        assert state.resident == state.slot.name
        assert len(state.dispatch) == 1
    else:
        assert state.installer.main(arguments) == 1
        assert not state.dispatch
        assert state.resident == state.previous
    assert len(state.cutovers) == 1
    assert not state.active
    for name, before in state.selectors.items():
        assert (state.root / name).read_bytes() == (
            (state.slot.name + "\n").encode() if cutover_ok else before
        )


@pytest.mark.parametrize("result", [
    None, {}, {"action": ""}, {"action": "cutover", "result": "not-an-outcome"},
    {"action": "unknown"}, {"action": "cutover"}, {"action": "cutover", "result": {}},
    {"action": "cutover", "result": {"ok": 1}},
    {"action": "legacy-cutover", "reason": "legacy-lock-invalid"},
    {"action": "noop", "result": {}},
])
def test_activation_refuses_unrecognized_result(constructed_update, monkeypatch, result):
    from worktree_manager import bootstrap_activation, mux_daemon_cutover

    state = constructed_update
    monkeypatch.setattr(sys, "prefix", str(state.slot))
    monkeypatch.setattr(mux_daemon_cutover, "activate_after_update", lambda **kwargs: result)
    with pytest.raises(RuntimeError, match="invalid activation"):
        bootstrap_activation.main(["--root", str(state.root)])


@pytest.mark.parametrize(("result", "code"), [
    ({"action": "noop", "reason": "no-live-daemon"}, 0),
    ({"action": "noop", "reason": "already-active-version"}, 0),
    ({"action": "legacy-cutover", "result": {"ok": True}}, 0),
    ({"action": "legacy-cutover", "result": {"ok": False}}, 1),
    ({"action": "error"}, 1),
    ({"action": "refused"}, 1),
])
def test_activation_preserves_daemon_owner_outcome(constructed_update, monkeypatch, result, code):
    from worktree_manager import bootstrap_activation, mux_daemon_cutover

    state = constructed_update
    monkeypatch.setattr(sys, "prefix", str(state.slot))
    monkeypatch.setattr(mux_daemon_cutover, "activate_after_update", lambda **kwargs: result)
    assert bootstrap_activation.main(["--root", str(state.root)]) == code


def test_activation_refuses_a_base_interpreter(constructed_update):
    from worktree_manager import bootstrap_activation

    with pytest.raises(RuntimeError, match="constructed Manager slot"):
        bootstrap_activation.main(["--root", str(constructed_update.root)])


def test_interruption_waits_for_owned_activation_before_rollback(constructed_update, monkeypatch):
    state = constructed_update
    calls = []

    def communicate():
        assert state.active
        assert (state.root / "current-version").read_text().strip() == state.slot.name
        calls.append("communicate")
        if len(calls) == 1:
            raise KeyboardInterrupt
        return "", ""

    monkeypatch.setattr(
        state.installer.subprocess, "Popen",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, communicate=communicate),
    )
    with pytest.raises(KeyboardInterrupt):
        state.installer.main([
            "--root", str(state.root), "--payload", str(state.payload),
            "--repair-update", "--", "update",
        ])
    assert calls == ["communicate", "communicate"]
    assert not state.active and not state.dispatch
    for name, before in state.selectors.items():
        assert (state.root / name).read_bytes() == before
