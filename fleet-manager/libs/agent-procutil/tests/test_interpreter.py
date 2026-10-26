"""Inner interpreter links must be read before any descendant is inspected."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

import agent_procutil as pu
from agent_procutil._interpreter import physical_executable


def _venv(root, home):
    scripts = root / "Scripts"
    scripts.mkdir(parents=True)
    for name in ("python.exe", "pythonw.exe"):
        (scripts / name).write_text("")
    (root / "pyvenv.cfg").write_text(f"home = {home}\n", encoding="utf-8")
    return str(scripts / "python.exe")


def _links(monkeypatch, links):
    real_lstat = os.lstat
    real_stat = os.stat
    real_isfile = os.path.isfile
    normalized = {os.path.normcase(str(k)): str(v) for k, v in links.items()}

    def lstat(path, *args, **kwargs):
        key = os.path.normcase(os.fspath(path))
        if key in normalized:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=0xA0000003,
            )
        assert not any(key.startswith(link + os.sep) for link in normalized)
        return real_lstat(path, *args, **kwargs)

    def isfile(path):
        key = os.path.normcase(os.fspath(path))
        assert not any(key == link or key.startswith(link + os.sep) for link in normalized)
        return real_isfile(path)

    monkeypatch.setattr(os, "lstat", lstat)
    def stat_home(path, *args, **kwargs):
        key = os.path.normcase(os.fspath(path))
        if any(key == link or key.startswith(link + os.sep) for link in normalized):
            return SimpleNamespace(st_mode=stat.S_IFDIR)
        return real_stat(path, *args, **kwargs)
    monkeypatch.setattr(os, "stat", stat_home)
    monkeypatch.setattr(os, "readlink", lambda path: normalized[os.path.normcase(str(path))])
    monkeypatch.setattr(os.path, "isfile", isfile)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)


def test_inner_home_ancestor_and_final_links_preserve_intent(monkeypatch, tmp_path):
    physical = tmp_path / "physical"
    base = physical / "install"
    base.mkdir(parents=True)
    (base / "python.exe").write_text("")
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "renamed.exe").write_text("")
    alias = tmp_path / "minor"
    python = _venv(tmp_path / "venv", alias / "install")
    _links(monkeypatch, {
        alias: physical,
        base / "pythonw.exe": gui / "renamed.exe",
    })
    assert pu.windowless_python(python) == str(gui / "renamed.exe")
    assert pu.windowless_python(python, require_windowless=True) == str(gui / "renamed.exe")
    assert pu.windowless_python_env(python) == {"__PYVENV_LAUNCHER__": python}
    assert pu.direct_python(python) == (
        str(base / "python.exe"), {"__PYVENV_LAUNCHER__": python},
    )


def test_relative_chained_links_and_console_executable_link(monkeypatch, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "pythonw.exe").write_text("")
    (base / "console.exe").write_text("")
    alias = tmp_path / "minor"
    second = tmp_path / "second"
    python = _venv(tmp_path / "venv", alias)
    _links(monkeypatch, {
        alias: second.name,
        second: base.name,
        base / "python.exe": "console.exe",
    })
    assert pu.direct_python(python) == (
        str(base / "console.exe"), {"__PYVENV_LAUNCHER__": python},
    )


def test_broken_home_keeps_windowless_fallback_but_direct_refuses(monkeypatch, tmp_path):
    alias = tmp_path / "minor"
    python = _venv(tmp_path / "venv", alias)
    _links(monkeypatch, {alias: tmp_path / "missing"})
    assert pu.windowless_python(python) == str(Path(python).with_name("pythonw.exe"))
    assert pu.windowless_python_env(python) == {}
    with pytest.raises(RuntimeError, match="base pythonw.exe is unavailable"):
        pu.direct_python(python)


def test_missing_physical_home_keeps_windowless_fallback(monkeypatch, tmp_path):
    python = _venv(tmp_path / "venv", tmp_path / "missing")
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    assert pu.windowless_python(python) == str(Path(python).with_name("pythonw.exe"))
    assert pu.windowless_python_env(python) == {}
    with pytest.raises(RuntimeError, match="base pythonw.exe is unavailable"):
        pu.direct_python(python)


def test_strict_windowless_refuses_console_fallback(monkeypatch, tmp_path):
    python = _venv(tmp_path / "venv", tmp_path / "missing")
    (Path(python).with_name("pythonw.exe")).unlink()
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    assert pu.windowless_python(python) == python
    with pytest.raises(RuntimeError, match="windowless Python interpreter is unavailable"):
        pu.windowless_python(python, require_windowless=True)


def test_strict_windowless_is_still_noop_off_windows(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    assert pu.windowless_python("/synthetic/python", require_windowless=True) == "/synthetic/python"


def test_link_cycle_is_explicit_not_a_fallback(monkeypatch, tmp_path):
    alias = tmp_path / "minor"
    python = _venv(tmp_path / "venv", alias)
    _links(monkeypatch, {alias: alias})
    with pytest.raises(OSError) as error:
        pu.windowless_python(python)
    assert error.value.errno == errno.ELOOP


def test_unreadable_link_is_explicit_not_a_fallback(monkeypatch, tmp_path):
    alias = tmp_path / "minor"
    python = _venv(tmp_path / "venv", alias)
    _links(monkeypatch, {alias: tmp_path / "base"})
    def denied(path):
        raise PermissionError(errno.EACCES, "blocked link metadata", str(path))
    monkeypatch.setattr(os, "readlink", denied)
    with pytest.raises(PermissionError):
        pu.direct_python(python)


def test_non_file_target_is_not_an_interpreter(tmp_path):
    assert physical_executable(str(tmp_path)) is None


def test_root_targeted_executable_link_is_not_an_interpreter(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_text("")
    alias = tmp_path / "pythonw.exe"
    _links(monkeypatch, {alias: Path(tmp_path.anchor)})
    assert physical_executable(str(alias)) is None
    assert pu.windowless_python(str(python)) == str(python)


def test_parent_component_is_applied_after_reading_link(monkeypatch, tmp_path):
    base = tmp_path / "physical"
    (base / "nested").mkdir(parents=True)
    (base / "python.exe").write_text("")
    alias = tmp_path / "minor"
    _links(monkeypatch, {alias: base / "nested"})
    assert physical_executable(str(alias / ".." / "python.exe")) == str(base / "python.exe")


def test_missing_console_refuses_even_with_healthy_gui(monkeypatch, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "pythonw.exe").write_text("")
    python = _venv(tmp_path / "venv", base)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    with pytest.raises(RuntimeError, match="base python.exe is unavailable"):
        pu.direct_python(python)


def test_nonvenv_final_gui_link_does_not_select_a_different_console(monkeypatch, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    console = base / "python.exe"
    console.write_text("")
    elsewhere = tmp_path / "gui-host.exe"
    elsewhere.write_text("")
    _links(monkeypatch, {base / "pythonw.exe": elsewhere})
    assert pu.windowless_python(str(console)) == str(elsewhere)
    assert pu.direct_python(str(console)) == (str(console), {})


def test_nonvenv_console_link_is_physical_without_gui(monkeypatch, tmp_path):
    console = tmp_path / "python.exe"
    physical = tmp_path / "console-host.exe"
    physical.write_text("")
    _links(monkeypatch, {console: physical})
    assert pu.direct_python(str(console)) == (str(physical), {})


def test_nonvenv_gui_link_uses_physical_console_sibling_when_alias_has_none(
    monkeypatch, tmp_path,
):
    base = tmp_path / "base"
    base.mkdir()
    gui = base / "gui-host.exe"
    gui.write_text("")
    console = base / "python.exe"
    console.write_text("")
    alias_dir = tmp_path / "aliases"
    alias_dir.mkdir()
    alias = alias_dir / "pythonw.exe"
    _links(monkeypatch, {alias: gui})
    assert pu.windowless_python(str(alias)) == str(gui)
    assert pu.direct_python(str(alias)) == (str(console), {})


def test_nonvenv_gui_link_without_either_console_refuses(monkeypatch, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    gui = base / "gui-host.exe"
    gui.write_text("")
    alias_dir = tmp_path / "aliases"
    alias_dir.mkdir()
    alias = alias_dir / "pythonw.exe"
    _links(monkeypatch, {alias: gui})
    with pytest.raises(RuntimeError, match="base python.exe is unavailable"):
        pu.direct_python(str(alias))


@pytest.mark.parametrize("with_console", [False, True])
def test_external_local_gui_fallback_preserves_venv_context(monkeypatch, tmp_path, with_console):
    base = tmp_path / "base"
    base.mkdir()
    if with_console:
        (base / "python.exe").touch()
    python = _venv(tmp_path / "venv", base)
    gui_dir = tmp_path / "external-gui"
    gui_dir.mkdir()
    gui = gui_dir / "gui-host.exe"
    gui.touch()
    _links(monkeypatch, {Path(python).with_name("pythonw.exe"): gui})
    assert pu.windowless_python(python, require_windowless=True) == str(gui)
    assert pu.windowless_python_env(python) == {"__PYVENV_LAUNCHER__": python}
    if with_console:
        assert pu.direct_python(python) == (
            str(base / "python.exe"), {"__PYVENV_LAUNCHER__": python},
        )
    else:
        with pytest.raises(RuntimeError, match="base python.exe is unavailable"):
            pu.direct_python(python)


@pytest.mark.parametrize("with_console", [False, True])
def test_equal_input_and_gui_target_still_requires_console(monkeypatch, tmp_path, with_console):
    gui = tmp_path / "gui-host.exe"
    gui.touch()
    if with_console:
        (tmp_path / "python.exe").touch()
    _links(monkeypatch, {tmp_path / "pythonw.exe": gui})
    assert pu.windowless_python(str(gui)) == str(gui)
    if with_console:
        assert pu.direct_python(str(gui)) == (str(tmp_path / "python.exe"), {})
    else:
        with pytest.raises(RuntimeError, match="base python.exe is unavailable"):
            pu.direct_python(str(gui))


@pytest.mark.parametrize(
    "select", [pu.windowless_python, pu.windowless_python_env, pu.direct_python],
)
@pytest.mark.parametrize("with_gui", [False, True])
def test_blocked_cfg_home_refuses_even_when_physical_base_is_healthy(
    monkeypatch, tmp_path, select, with_gui,
):
    base = tmp_path / "base"
    base.mkdir()
    for name in ("python.exe", "pythonw.exe") if with_gui else ("python.exe",):
        (base / name).write_text("")
    alias = tmp_path / "minor"
    python = _venv(tmp_path / "venv", alias)
    _links(monkeypatch, {alias: base})
    real_stat = os.stat
    def blocked(path, *args, **kwargs):
        if str(path) == str(alias):
            error = OSError(errno.EINVAL, "untrusted mount point")
            error.winerror = 448
            raise error
        return real_stat(path, *args, **kwargs)
    monkeypatch.setattr(os, "stat", blocked)
    with pytest.raises(RuntimeError, match="rebuild the runtime with a physical Python home"):
        select(python)


@pytest.mark.parametrize("tag", [0x80000017, 0x9000001A], ids=["wof", "cloud"])
def test_non_link_reparse_metadata_is_not_read_as_a_link(monkeypatch, tmp_path, tag):
    base = tmp_path / "base"
    base.mkdir()
    executable = base / "python.exe"
    executable.write_text("")
    real_lstat = os.lstat
    def lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if str(path) in (str(base), str(executable)):
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400, st_reparse_tag=tag)
        return info
    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(
        os, "readlink", lambda _: pytest.fail("non-name-surrogate reparse point is not a link"),
    )
    assert physical_executable(str(executable)) == str(executable)
