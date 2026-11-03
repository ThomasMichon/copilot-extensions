"""Successful waited updates publish their physical Python, including no-op installs."""

from contextlib import nullcontext
from types import SimpleNamespace
import sys

import pytest

from worktree_manager import self_install, update_driver_cli
from worktree_manager import update_python_path as path_update


@pytest.fixture
def public_python(tmp_path, monkeypatch):
    base = tmp_path / "public python" / "python.exe"
    base.parent.mkdir()
    base.write_bytes(b"fixture; never executed")
    older, unrelated = str(tmp_path / "older python"), str(tmp_path / "other tools")
    original = f"{older};{base.parent}\\;{unrelated}"
    state = SimpleNamespace(value=(original, 2), writes=[], notifications=0, events=[])
    key = object()
    registry = SimpleNamespace(
        HKEY_CURRENT_USER=1, KEY_READ=1, KEY_WRITE=2, REG_SZ=1, REG_EXPAND_SZ=2,
    )
    registry.CreateKeyEx = lambda *args: nullcontext(key)
    registry.QueryValueEx = lambda *args: state.value

    def write(*args):
        state.events.append("publish")
        state.writes.append(args)
        state.value = (args[-1], args[-2])

    def notify():
        state.notifications += 1

    registry.SetValueEx = write
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(path_update, "_notify_environment_change", notify)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "_base_executable", str(base))
    monkeypatch.setattr(path_update, "os", SimpleNamespace(environ={"PATH": original}))
    state.base, state.original, state.registry = base, original, registry
    state.expected = f"{base.parent};{older};{base.parent}\\;{unrelated}"
    return state


def test_waited_update_publishes_physical_base_once_and_keeps_other_entries(public_python, capsys):
    state = public_python
    assert path_update.finish_update(["update", "--wait"], 0) == 0
    assert state.value == (state.expected, 2)
    assert path_update.os.environ["PATH"] == state.expected
    assert len(state.writes) == state.notifications == 1
    assert "Restart existing terminals" in capsys.readouterr().err
    assert path_update.finish_update(["update"], 0) == 0
    assert len(state.writes) == 1 and state.notifications == 2


def test_publication_preserves_distinct_windows_names_with_equal_unicode_casefold(public_python):
    state = public_python
    directory = str(state.base.parent.parent / "Stra\N{LATIN SMALL LETTER SHARP S}e")
    unrelated = str(state.base.parent.parent / "Strasse")
    state.value = (unrelated, 2)
    assert path_update._publish_user_path(directory) is True
    assert state.value == (directory + ";" + unrelated, 2)


def test_publication_keeps_nonidentical_case_aliases_and_existing_empty_entries(public_python):
    state = public_python
    directory = str(state.base.parent)
    original = directory.upper() + ";;" + directory + "\\"
    state.value = (original, 2)
    assert path_update._publish_user_path(directory) is True
    assert state.value == (directory + ";" + original, 2)


def test_retry_notifies_an_already_written_path_without_rewriting(public_python, monkeypatch):
    state = public_python

    def notify():
        state.notifications += 1
        if state.notifications == 1:
            raise OSError("notification refused")

    monkeypatch.setattr(path_update, "_notify_environment_change", notify)
    assert path_update.finish_update(["update"], 0) == 1
    assert state.value == (state.expected, 2)
    assert path_update.os.environ["PATH"] == state.original
    assert path_update.finish_update(["update"], 0) == 0
    assert state.notifications == 2 and len(state.writes) == 1
    assert path_update.os.environ["PATH"] == state.expected


@pytest.mark.parametrize("kind", [1, 2], ids=["literal", "expandable"])
def test_literal_python_directory_is_never_published_as_a_variable_expression(public_python, kind):
    state = public_python
    directory = state.base.parent.parent / "%TEMP%" / "Python311"
    directory.mkdir(parents=True)
    state.value = (state.original, kind)
    if kind == 2:
        with pytest.raises(ValueError, match="expandable user PATH"):
            path_update._publish_user_path(str(directory))
        assert state.writes == [] and state.notifications == 0
        assert state.value == (state.original, kind)
    else:
        assert path_update._publish_user_path(str(directory)) is True
        assert state.value[0].split(";")[0] == str(directory)
        assert state.value[1] == kind


@pytest.mark.parametrize("command,code", [
    (["update"], 17), (["update", "--auto"], 0), (["update", "--detach"], 0),
    (["update", "--auth-report"], 0), (["update", "--request-status=example"], 0),
    (["update", "--recovery-status"], 0), (["update", "--recover-after-restart"], 0),
    (["update", "--recover-for-repair"], 0), (["update", "--target=worktree-manager"], 0),
    (["update", "--help"], 0), (["self-install"], 0),
])
def test_noncompleted_or_nonordinary_modes_leave_environment_unchanged(public_python, command, code):
    state = public_python
    assert path_update.finish_update(command, code) == code
    assert state.writes == [] and state.notifications == 0
    assert path_update.os.environ["PATH"] == state.original


@pytest.mark.parametrize("marker", ["COPILOT_EXTENSIONS_TEST_CONTAINED", "PYTEST_CURRENT_TEST"])
def test_contained_update_changes_only_process_path(public_python, marker):
    state = public_python
    path_update.os.environ[marker] = "1"
    assert path_update.finish_update(["update"], 0) == 0
    assert path_update.os.environ["PATH"] == state.expected
    assert state.writes == [] and state.notifications == 0


@pytest.mark.parametrize("failure", ["unsupported", "missing", "registry-type", "readback", "notify"])
def test_unqualified_python_or_failed_publication_is_not_update_success(
    public_python, monkeypatch, capsys, failure,
):
    state = public_python
    if failure == "unsupported":
        monkeypatch.setattr(sys, "version_info", (3, 9, 0))
    elif failure == "missing":
        state.base.unlink()
    elif failure == "registry-type":
        state.value = (12, 4)
    elif failure == "readback":
        state.registry.SetValueEx = lambda *args: None
    else:
        def fail():
            raise OSError("notification refused")
        monkeypatch.setattr(path_update, "_notify_environment_change", fail)
    assert path_update.finish_update(["update"], 0) == 1
    assert path_update.os.environ["PATH"] == state.original
    assert "Python PATH:" in capsys.readouterr().err


def test_installed_already_current_frontdoor_publishes_only_after_verified_completion(
    public_python, monkeypatch, tmp_path,
):
    import update_driver

    state = public_python
    request = SimpleNamespace(id="fixture", log_path=tmp_path / "log")
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "enqueue", lambda *args, **kwargs: request)
    monkeypatch.setattr(update_driver, "runtime_command", lambda *args: (("fixture",), {}))
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(update_driver_cli, "source_identity", lambda: {"ref": "already-current"})

    def wait(*args, **kwargs):
        assert state.writes == []
        state.events.append("completed")
        return SimpleNamespace(state="completed", exit_code=0)

    monkeypatch.setattr(update_driver, "wait", wait)
    assert update_driver_cli.cmd_update_request([]) == 0
    assert state.events == ["completed", "publish"]
    assert state.value == (state.expected, 2)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows bootstrap completion boundary")
def test_bootstrap_completion_uses_same_publication_after_dispatch(public_python, monkeypatch, tmp_path):
    state = public_python
    installer = self_install._runtime_installer()
    args = SimpleNamespace(install_only=False, repair_update=False, root=tmp_path)
    slot = tmp_path / "versions" / "fixture"
    monkeypatch.setattr(installer.runtime, "slot_python", lambda *args: state.base)
    monkeypatch.setattr(installer.runtime, "retention_lease", lambda *args: nullcontext())
    monkeypatch.setattr(update_driver_cli, "handoff_environment", lambda *args: {})

    def dispatch(*args):
        assert state.writes == []
        state.events.append("completed")
        return 0

    monkeypatch.setattr(installer, "_dispatch_manager", dispatch)
    assert installer._finish(args, slot, ["update", "--wait"]) == 0
    assert state.events == ["completed", "publish"]
    assert state.value == (state.expected, 2)
