"""Actor-free installation evidence and external bytecode namespace controls."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs/agent-procutil/src/agent_procutil/python_cache.py"


@pytest.fixture
def cache():
    spec = importlib.util.spec_from_file_location("unit_python_cache", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def installed_target(root):
    target = root / "versions" / "1.2.3"
    record = target / "Lib" / "site-packages" / "fixture.dist-info" / "RECORD"
    record.parent.mkdir(parents=True)
    record.write_text("fixture.py,sha256=" + "A" * 43 + ",12\n", encoding="utf-8")
    (target / ".install-complete.json").write_text(json.dumps({
        "version": target.name, "completed_at": "fixture", "pid": 1,
        "payload_hash": "a" * 64,
    }), encoding="utf-8")
    return target, record


def test_recorded_installation_reuses_cache_but_same_stat_record_change_does_not(cache, tmp_path):
    root = tmp_path / "owner"
    target, record = installed_target(root)
    first = cache.cache_arguments(root, runtime=target)
    assert first == cache.cache_arguments(root, runtime=target)
    assert "installed-" in first[1]
    assert str(target) not in first[1]
    before = record.stat()
    record.write_text("fixture.py,sha256=" + "B" * 43 + ",12\n", encoding="utf-8")
    os.utime(record, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert record.stat().st_size == before.st_size
    assert record.stat().st_mtime_ns == before.st_mtime_ns
    second = cache.cache_arguments(root, runtime=target)
    assert second != first
    assert second == cache.cache_arguments(root, runtime=target)


def test_selected_interpreter_uses_its_target_installation_not_current_redirect(cache, tmp_path):
    root = tmp_path / "owner"
    target, _ = installed_target(root)
    expected = cache.cache_arguments(root, runtime=target)
    assert cache.cache_arguments(root, runtime=target / "Scripts" / "python.exe") == expected
    assert cache.cache_arguments(root, runtime=target / "bin" / "python") == expected
    assert cache.cache_arguments(root, runtime=root / "versions" / "other") != expected


@pytest.mark.parametrize("evidence", ["missing", "malformed"])
def test_unrecorded_completion_uses_fresh_external_namespace(cache, tmp_path, evidence):
    root = tmp_path / "owner"
    target = root / "versions" / "1.2.3"
    target.mkdir(parents=True)
    if evidence == "malformed":
        (target / ".install-complete.json").write_text(
            '{"version":"1.2.3","version":"1.2.3"}', encoding="utf-8",
        )
    first = cache.cache_arguments(root, runtime=target)
    second = cache.cache_arguments(root, runtime=target)
    assert first != second
    assert all("unsealed-" in arguments[1] for arguments in (first, second))
    assert all(str(target) not in arguments[1] for arguments in (first, second))
    assert not (root / "cache").exists()


@pytest.mark.parametrize("registered", [False, True])
def test_minimal_loader_keeps_cache_and_cleanup_metadata_without_resident_modules(tmp_path, monkeypatch, registered):
    source = SOURCE.with_name("__init__.py")
    spec = importlib.util.spec_from_file_location("unit_minimal_procutil", source)
    module = importlib.util.module_from_spec(spec)
    if registered:
        monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    first = module.python_cache_args(tmp_path, runtime=tmp_path / "uninstalled")
    assert first != module.python_cache_args(tmp_path, runtime=tmp_path / "uninstalled")
    assert "unsealed-" in first[1]
    outer, inner = ("Local\\ce-owned-job-" + character * 32 for character in ("a", "b"))
    monkeypatch.setenv(module._JOB_SCOPES_ENV, outer)
    assert module._job_child_environment({"literal": "kept"}, append=inner) == {
        "literal": "kept", module._JOB_SCOPES_ENV: outer + ";" + inner,
    }
    assert spec.name + ".daemon" not in sys.modules
    assert spec.name + "._job_scopes" not in sys.modules
