"""Fresh bootstrap retains candidate custody through transactional daemon activation."""

from contextlib import contextmanager, nullcontext
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest

from worktree_manager import self_install
from test_update_engine_repair import repair_case as ownership_repair_case

repair_case = ownership_repair_case


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
    monkeypatch.setattr(installer, "_execution_driver", lambda payload: None)
    monkeypatch.setattr(installer, "_installation_custody", lambda: nullcontext(object()))
    monkeypatch.setattr(self_install, "needs_install", lambda *args: True)
    monkeypatch.setattr(self_install, "clean_legacy_artifacts", lambda *args: [])
    monkeypatch.setattr(self_install, "_deploy_binstubs", lambda: ())
    monkeypatch.setattr(self_install, "_write_control_plane_provider_manifest", lambda *args, **kwargs: None)
    monkeypatch.setitem(
        sys.modules, "update_entry",
        SimpleNamespace(validate_manager_ownership=lambda *args, **kwargs: None),
    )

    def dispatch(arguments):
        state.dispatch.append((arguments[0], arguments))
        raise CommandDispatched

    monkeypatch.setattr(installer, "_dispatch_manager", dispatch)
    return state


@pytest.mark.skipif(os.name != "nt", reason="native Windows rollback-backup permissions")
@pytest.mark.parametrize("failure", [False, True], ids=["repeat-install", "rollback"])
def test_selector_backups_stay_private_under_a_writable_parent(
    constructed_update, monkeypatch, failure,
):
    state = constructed_update
    publication = state.installer.runtime._runtime_component("versioned_runtime_publication")
    directory = state.root / ".installer-inputs"
    directory.mkdir()
    flags = subprocess.CREATE_NO_WINDOW
    grant = subprocess.run(
        ["icacls", str(directory), "/grant", "*S-1-5-11:(OI)(CI)(M)"],
        capture_output=True, text=True, creationflags=flags, timeout=15,
    )
    assert grant.returncode == 0, grant.stdout + grant.stderr
    before = subprocess.run(
        ["icacls", str(directory)], capture_output=True, text=True,
        creationflags=flags, timeout=15, check=True,
    ).stdout
    plain = directory / "plain-control"
    plain.write_bytes(b"untrusted inherited permissions")
    with pytest.raises(publication.PublicationProofError, match="not owned"):
        publication.state_path(plain)
    original_directory = state.installer.tempfile.TemporaryDirectory

    @contextmanager
    def inherited_workspace(*args, **kwargs):
        with original_directory(*args, **kwargs) as workspace:
            inherited = subprocess.run(
                ["icacls", workspace, "/grant", "*S-1-5-11:(OI)(CI)(M)"],
                capture_output=True, text=True, creationflags=flags, timeout=15,
            )
            assert inherited.returncode == 0, inherited.stdout + inherited.stderr
            yield workspace

    monkeypatch.setattr(state.installer.tempfile, "TemporaryDirectory", inherited_workspace)

    def refused():
        raise RuntimeError("activation refused")

    if failure:
        with pytest.raises(RuntimeError, match="activation refused"):
            state.installer.activate(
                state.root, state.slot.name, expected=state.previous, after_publish=refused,
            )
    else:
        state.installer.activate(state.root, state.slot.name, expected=state.previous)
        state.installer.activate(state.root, state.slot.name, expected=state.slot.name)
    for name, original in state.selectors.items():
        path = state.root / name
        assert path.read_bytes() == (original if failure else (state.slot.name + "\n").encode())
        assert publication.state_path(path) is not None
    after = subprocess.run(
        ["icacls", str(directory)], capture_output=True, text=True,
        creationflags=flags, timeout=15, check=True,
    ).stdout
    assert after == before


@pytest.mark.parametrize("cutover_ok", [True, False], ids=["running-daemon", "failed-cutover"])
@pytest.mark.parametrize("repair", [False, True], ids=["normal", "repair"])
def test_bootstrap_activates_before_releasing_candidate(
    constructed_update, monkeypatch, cutover_ok, repair,
):
    state = constructed_update

    def activate(**kwargs):
        assert state.active
        assert (state.root / "current-version").read_text().strip() == state.previous
        state.cutovers.append(kwargs)
        if cutover_ok:
            state.resident = state.slot.name
        return {
            "action": "cutover",
            "result": {"ok": cutover_ok, **({"error": "health-timeout"} if not cutover_ok else {})},
        }

    def popen(arguments, **kwargs):
        from worktree_manager import bootstrap_activation, mux_daemon_cutover

        assert arguments[:2] == [str(state.python), "-I"]
        assert arguments[2:6] == ["-S", "-B", "-X", "utf8"]
        assert Path(arguments[6]).parent == state.slot / ".runtime-bootstrap"
        assert arguments[7:9] == ["-m", "worktree_manager.bootstrap_activation"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        output = io.StringIO()
        with monkeypatch.context() as child, redirect_stdout(output):
            child.setattr(sys, "prefix", str(state.slot))
            child.setattr(mux_daemon_cutover, "activate_after_update", activate)
            code = bootstrap_activation.main(arguments[9:])
        return SimpleNamespace(returncode=code, communicate=lambda: (output.getvalue(), ""))

    monkeypatch.setattr(state.installer.subprocess, "Popen", popen)
    arguments = [
        "--root", str(state.root), "--payload", str(state.payload),
    ]
    arguments += ["--repair-update", "--", "update"] if repair else ["--", "--version"]
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
@pytest.mark.parametrize("nested", [False, True])
def test_activation_preserves_daemon_owner_outcome(constructed_update, monkeypatch, result, code, nested):
    from worktree_manager import bootstrap_activation, mux_daemon_cutover

    state = constructed_update
    monkeypatch.setattr(sys, "prefix", str(state.slot / ".venv" if nested else state.slot))
    monkeypatch.setattr(mux_daemon_cutover, "activate_after_update", lambda **kwargs: result)
    assert bootstrap_activation.main(["--root", str(state.root)]) == code


def test_activation_refuses_a_base_interpreter(constructed_update):
    from worktree_manager import bootstrap_activation

    with pytest.raises(RuntimeError, match="constructed Manager slot"):
        bootstrap_activation.main(["--root", str(constructed_update.root)])


@pytest.mark.skipif(os.name != "nt", reason="Windows path aliases")
@pytest.mark.parametrize("alias", ["root", "prefix"])
@pytest.mark.parametrize("foreign", [False, True])
def test_activation_binds_physical_root_across_native_path_aliases(
    constructed_update, monkeypatch, alias, foreign,
):
    from worktree_manager import bootstrap_activation, mux_daemon_cutover

    state = constructed_update
    root = state.root / "foreign" if foreign else state.root
    prefix = state.slot / ".venv"
    monkeypatch.setattr(sys, "prefix", "\\\\?\\" + str(prefix) if alias == "prefix" else str(prefix))
    activate = []
    monkeypatch.setattr(mux_daemon_cutover, "activate_after_update", lambda **options: (
        activate.append(options) or {"action": "noop", "reason": "no-live-daemon"}
    ))
    arguments = ["--root", "\\\\?\\" + str(root) if alias == "root" else str(root)]
    if foreign:
        with pytest.raises(RuntimeError, match="constructed Manager slot"):
            bootstrap_activation.main(arguments)
        assert not activate
    else:
        assert bootstrap_activation.main(arguments) == 0
        assert len(activate) == 1 and activate[0]["root"].samefile(state.root)


@pytest.mark.parametrize("start,ready", [(False, False), (True, False), (True, True)])
def test_explicit_application_start_preserves_update_noop_and_checks_startup(
    constructed_update, monkeypatch, start, ready,
):
    from worktree_manager import bootstrap_activation, mux_daemon, mux_daemon_cutover

    state = constructed_update
    monkeypatch.setattr(sys, "prefix", str(state.slot / ".venv"))
    activations, starts = [], []

    def activate(**options):
        activations.append(options)
        return {"action": "noop", "reason": (
            "already-active-version" if starts and ready else "no-live-daemon"
        )}

    def ensure(root):
        assert root == state.root
        starts.append(root)
        return ready

    monkeypatch.setattr(mux_daemon_cutover, "activate_after_update", activate)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", ensure)
    arguments = ["--root", str(state.root), *(["--start-if-missing"] if start else [])]
    if start and not ready:
        with pytest.raises(RuntimeError, match="startup did not become ready"):
            bootstrap_activation.main(arguments)
    else:
        assert bootstrap_activation.main(arguments) == 0
    assert len(starts) == int(start)
    assert len(activations) == (2 if start and ready else 1)


@pytest.mark.parametrize("repair", [False, True], ids=["normal", "repair"])
def test_interrupted_acknowledged_activation_keeps_the_serving_generation_selected(
    constructed_update, monkeypatch, repair,
):
    state = constructed_update
    calls = []
    outcome = json.dumps({
        "action": "cutover", "root": str(state.root),
        "runtime_generation": state.slot.name,
        "result": {"ok": True, "new_pid": 123, "retirement": "self-observed"},
    })

    def communicate():
        assert state.active
        calls.append("communicate")
        if len(calls) == 1:
            state.resident = state.slot.name
            raise KeyboardInterrupt
        return outcome, ""

    monkeypatch.setattr(
        state.installer.subprocess, "Popen",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, communicate=communicate),
    )
    arguments = ["--root", str(state.root), "--payload", str(state.payload)]
    arguments += ["--repair-update", "--", "update"] if repair else ["--", "--version"]
    with pytest.raises(KeyboardInterrupt):
        state.installer.main(arguments)
    assert calls == ["communicate", "communicate"] and not state.dispatch
    assert state.resident == state.slot.name
    assert (state.root / "current-version").read_text().strip() == state.resident
    assert (state.root / "last-known-good").read_text().strip() == state.resident


@pytest.mark.parametrize("failure", [KeyboardInterrupt, OSError], ids=["cancel", "io-error"])
def test_v2_start_is_not_replayed_or_rolled_back_by_publication_failure(
    constructed_update, monkeypatch, failure,
):
    state = constructed_update
    launches, publications = [], []
    outcome = json.dumps({
        "action": "cutover", "runtime_generation": state.slot.name,
        "result": {"ok": True},
    })

    def communicate():
        launches.append(state.slot.name)
        state.resident = state.slot.name
        return outcome, ""

    monkeypatch.setattr(
        state.installer.subprocess, "Popen",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, communicate=communicate),
    )
    activate = state.installer.runtime._activate

    def publish(*args, **kwargs):
        publications.append(state.slot.name)
        result = activate(*args, **kwargs)
        if len(publications) == 1:
            raise failure("publication interrupted")
        return result

    monkeypatch.setattr(state.installer.runtime, "_activate", publish)
    arguments = ["--root", str(state.root), "--payload", str(state.payload), "--", "--version"]
    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            state.installer.main(arguments)
        assert len(publications) == 2
    else:
        assert state.installer.main(arguments) == 1
        assert len(publications) == 1
        assert list((state.root / ".installer-inputs").glob("manager-selection-*/context.json"))
    assert launches == [state.slot.name] and not state.dispatch
    assert (state.root / "current-version").read_text().strip() == state.resident
    assert (state.root / "last-known-good").read_text().strip() == state.resident


@pytest.mark.parametrize("outcome", [
    {"action": "cutover", "runtime_generation": "1.2.2", "result": {"ok": True}},
    {"action": "noop", "runtime_generation": "1.2.3", "reason": "no-live-daemon"},
])
def test_v1_state_cannot_authorize_v2_publication(constructed_update, monkeypatch, outcome):
    state = constructed_update
    monkeypatch.setattr(
        state.installer.subprocess, "Popen",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, communicate=lambda: (json.dumps(outcome), ""),
        ),
    )
    assert state.installer.main([
        "--root", str(state.root), "--payload", str(state.payload), "--", "--version",
    ]) == 1
    assert not state.dispatch
    for name, before in state.selectors.items():
        assert (state.root / name).read_bytes() == before


def test_unconfirmed_interruption_settles_without_publishing_selection(constructed_update, monkeypatch):
    state = constructed_update
    calls = []

    def communicate():
        assert state.active
        assert (state.root / "current-version").read_text().strip() == state.previous
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


@pytest.mark.parametrize("apply", [False, True], ids=["plan", "apply"])
def test_self_install_command_uses_the_same_incoming_start_boundary(
    constructed_update, monkeypatch, apply,
):
    from worktree_manager import __main__ as cli

    state = constructed_update
    starts = []

    def install(**options):
        assert options["dry_run"] is not apply
        if apply:
            assert options["_transactional_activation"] is True
            options["_after_activate"](state.slot)
        else:
            assert options.get("_after_activate") is None
        return self_install.SelfInstallResult(
            version=state.slot.name, action="installed" if apply else "planned",
            root=str(state.root), slot=str(state.slot),
        )

    monkeypatch.setattr(cli, "self_install", install)
    monkeypatch.setattr(cli, "self_status", lambda: SimpleNamespace(installed_version=state.previous))
    monkeypatch.setattr(
        state.installer, "_activate_after_bootstrap_update",
        lambda root, slot: starts.append((root, slot)),
    )
    assert cli._render_self_install(["--apply"] if apply else []) == 0
    assert starts == ([(state.root, state.slot)] if apply else [])


@pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="real fresh-SDK ownership reader; not promotion-unit evidence",
)
@pytest.mark.parametrize("changed_owner", ["manager-command", "provider"])
def test_repair_revalidates_ownership_after_construction_before_publication(
    constructed_update, repair_case, monkeypatch, changed_owner,
):
    state, case = constructed_update, repair_case
    state.payload = case.source / "worktree-manager"
    package = state.payload / "src/worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'__version__ = "{state.slot.name}"\n')
    (state.root / "versions" / state.previous / "pyproject.toml").write_text(
        '[project]\nname = "copilot-extensions-worktree-manager"\n',
    )
    validator = runpy.run_path(str(state.payload / "scripts/update_entry.py"))[
        "validate_manager_ownership"
    ]
    monkeypatch.setitem(
        sys.modules, "update_entry", SimpleNamespace(validate_manager_ownership=validator),
    )
    local_bin = case.home / ".local/bin"
    local_bin.mkdir(parents=True)
    registry = Path(os.environ["AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR"])
    registry.mkdir(parents=True, exist_ok=True)
    primary = "worktree-manager.cmd" if os.name == "nt" else "worktree-manager"
    provider = registry / "worktree-manager.json"
    provider.write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager",
        "provider_root": str(state.root), "command": [str(local_bin / primary)],
    }), encoding="utf-8")
    candidate = state.installer.candidate
    changed = []

    @contextmanager
    def change_while_constructing(*args, **kwargs):
        with candidate(*args, **kwargs) as ready:
            if changed_owner == "provider":
                document = json.loads(provider.read_text())
                document["provider_root"] = str(case.home / "another-manager")
                path, content = provider, json.dumps(document).encode()
            else:
                path, content = local_bin / primary, b"foreign command: preserve me\n"
            path.write_bytes(content)
            changed.append((path, content))
            yield ready

    def publication_forbidden(*args, **kwargs):
        pytest.fail("ownership changed during construction; publication must not run")

    monkeypatch.setattr(state.installer, "candidate", change_while_constructing)
    monkeypatch.setattr(self_install, "clean_legacy_artifacts", publication_forbidden)
    monkeypatch.setattr(self_install, "_deploy_binstubs", publication_forbidden)
    monkeypatch.setattr(self_install, "_write_control_plane_provider_manifest", publication_forbidden)
    assert state.installer.main([
        "--root", str(state.root), "--payload", str(state.payload),
        "--repair-update", "--", "update",
    ]) == 1
    assert len(changed) == 1
    assert changed[0][0].read_bytes() == changed[0][1]
    assert not state.active and not state.dispatch and not state.cutovers
    for name, before in state.selectors.items():
        assert (state.root / name).read_bytes() == before
