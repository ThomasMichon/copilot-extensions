"""Minimal bundles and lifecycle loaders need only the short-lived helpers."""

from __future__ import annotations

import importlib.util
import re
import os
import subprocess
import sys
from pathlib import Path

import pytest

import agent_procutil


@pytest.mark.parametrize("registered", [False, True], ids=["lifecycle-loader", "minimal-package"])
def test_short_lived_helpers_import_without_daemon_modules(tmp_path, monkeypatch, registered):
    package = tmp_path / "agent_procutil"
    package.mkdir()
    source = package / "__init__.py"
    source.write_bytes(Path(agent_procutil.__file__).read_bytes())
    (package / "python_cache.py").write_bytes(
        Path(agent_procutil.__file__).with_name("python_cache.py").read_bytes(),
    )
    name = "_standalone_procutil_fixture"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    if registered:
        monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert isinstance(module.no_window_flags(), int)
    assert callable(module.spawn_sync_in_kill_on_close_job)
    assert callable(module.spawn_daemon)
    first = module.python_cache_args(tmp_path, runtime=tmp_path / "uninstalled")
    second = module.python_cache_args(tmp_path, runtime=tmp_path / "uninstalled")
    assert first[0] == second[0] == "-X"
    prefixes = [
        Path(value[1].removeprefix("pycache_prefix=").removeprefix("\\\\?\\"))
        for value in (first, second)
    ]
    assert all(prefix.parent == (tmp_path / "cache" / "python").resolve() for prefix in prefixes)
    assert all(re.fullmatch(r"unsealed-[0-9a-f]{32}", prefix.name) for prefix in prefixes)
    assert prefixes[0] != prefixes[1]
    with pytest.raises(module.SyncProcessLaunchError) as raised:
        module.spawn_sync_in_kill_on_close_job([str(tmp_path / "missing-executable")])
    assert isinstance(raised.value, FileNotFoundError)
    assert name + ".daemon" not in sys.modules


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="native standalone Job accounting")
@pytest.mark.parametrize("registered", [False, True], ids=["lifecycle-loader", "minimal-package"])
def test_standalone_helper_confirms_real_native_job_retirement(tmp_path, monkeypatch, registered):
    source = tmp_path / "__init__.py"
    source.write_bytes(Path(agent_procutil.__file__).read_bytes())
    name = "_standalone_native_procutil_fixture"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    if registered:
        monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    process, job = module.spawn_sync_in_kill_on_close_job(
        [sys.executable, "-c", "print('standalone')"],
        require_job=True, allow_breakaway=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **module.no_window_kwargs(),
    )
    try:
        assert job is not None
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0 and stdout.strip() == b"standalone", stderr
        job.terminate_and_wait(5)
        assert job.handle is None
    finally:
        if job is not None and job.handle is not None:
            job.terminate_and_wait(5)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
