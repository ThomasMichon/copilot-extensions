"""Actual reservation at the nested passive-start boundary, without starting providers."""
import ast
import asyncio
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil.daemon_instances import (
    DaemonInstanceSpec, cancel_instance_reservation, reserve_instance,
)


@pytest.mark.parametrize("linked", (False, True))
def test_passive_start_parent_root_reserves_without_hiding_links(tmp_path, monkeypatch, linked):
    import agent_index.__main__ as cli

    home = tmp_path / "runtime"
    launch = tmp_path / "launch"
    home.mkdir()
    launch.mkdir()
    selected = home
    if linked:
        selected = tmp_path / "linked"
        try:
            selected.symlink_to(home, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"native directory symlink unavailable: {error}")
    monkeypatch.chdir(launch)
    source = Path(cli.__file__)
    tree = ast.parse(source.read_text())
    definition = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == "spawn_passive")
    reached = []

    def reserve_before_provider(command, **kwargs):
        spec = replace(kwargs["instance"], purpose="production", registry_root=tmp_path / "registry")
        receipt = reserve_instance(spec)
        try:
            assert receipt.record_path.is_file()
            assert spec.runtime_root == selected
            assert kwargs["cwd"] == selected
            reached.append(True)
        finally:
            cancel_instance_reservation(receipt, creation_refused=True)
        return SimpleNamespace(pid=12345)

    namespace = {
        "__file__": str(source), "os": os, "Path": Path,
        "cfg": SimpleNamespace(host="127.0.0.1"),
        "host_runtime_python": lambda: "private-python-never-executed",
        "windowless_python": lambda value: value, "windowless_python_env": lambda value: {},
        "install_dir": lambda: Path("..") / selected.name,
        "spawn_daemon": reserve_before_provider, "DaemonInstanceSpec": DaemonInstanceSpec,
        "passive_instance": {},
    }
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), "exec"), namespace)
    if linked:
        with pytest.raises((ValueError, OSError)):
            namespace["spawn_passive"](8123)
        assert reached == []
    else:
        namespace["spawn_passive"](8123)
        assert reached == [True]


@pytest.mark.parametrize("linked", (False, True))
def test_shim_worker_module_anchor_does_not_hide_links(tmp_path, monkeypatch, linked):
    from agent_index.engine import shim

    target = tmp_path / "module"
    launch = tmp_path / "launch"
    target.mkdir()
    launch.mkdir()
    (target / "shim.py").write_text("private source provenance; never executed\n")
    selected = target
    if linked:
        selected = tmp_path / "linked-module"
        try:
            selected.symlink_to(target, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"native directory symlink unavailable: {error}")
    monkeypatch.chdir(launch)
    monkeypatch.setattr(shim, "__file__", str(Path("..") / selected.name / "shim.py"))
    reached = []

    def reserve_before_worker(command, *, instance, **kwargs):
        spec = replace(instance, purpose="production", registry_root=tmp_path / "registry")
        receipt = reserve_instance(spec)
        try:
            assert receipt.record_path.is_file()
            assert spec.runtime_root == selected
            reached.append(True)
        finally:
            cancel_instance_reservation(receipt, creation_refused=True)
        raise OSError("synthetic positively refused process creation")

    monkeypatch.setattr(shim, "spawn_instance", reserve_before_worker)
    supervisor = shim.WorkerSupervisor()
    if linked:
        with pytest.raises(RuntimeError, match="runtime anchor is redirected"):
            asyncio.run(supervisor.ensure(None))
        assert reached == []
    else:
        with pytest.raises(RuntimeError, match="synthetic positively refused"):
            asyncio.run(supervisor.ensure(None))
        assert reached == [True]


def test_detached_engine_pins_selected_home_before_cwd_change(tmp_path, monkeypatch):
    from agent_index.engine import daemon

    launch = tmp_path / "launch"
    root = tmp_path / "engine"
    launch.mkdir()
    root.mkdir()
    monkeypatch.chdir(launch)
    monkeypatch.setenv("AGENT_INDEX_ENGINE_HOME", "../engine")
    reached = []

    def reserve_before_provider(command, **kwargs):
        spec = replace(kwargs["instance"], purpose="production", registry_root=tmp_path / "registry")
        receipt = reserve_instance(spec)
        try:
            assert receipt.record_path.is_file()
            assert kwargs["env"]["AGENT_INDEX_ENGINE_HOME"] == str(root)
            assert kwargs["cwd"] == spec.runtime_root == root
            reached.append(True)
        finally:
            cancel_instance_reservation(receipt, creation_refused=True)
        return object()

    monkeypatch.setattr(daemon, "spawn_daemon", reserve_before_provider)
    daemon._spawn(["private-python"], python="private-python", home=Path("../engine"))
    assert reached == [True]
