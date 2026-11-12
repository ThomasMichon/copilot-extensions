"""Actor-free runtime path attribution and reference-evidence controls."""
from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import ntpath
from pathlib import Path, PureWindowsPath
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _runtime(monkeypatch):
    source = ROOT / "libs/versioned-runtime/versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("_unit_runtime_gc_paths", source)
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    return runtime


def test_gc_process_paths_preserve_native_aliases_and_slot_boundaries(monkeypatch):
    runtime = _runtime(monkeypatch)
    windows = SimpleNamespace(name="nt", path=ntpath, sep="\\")
    for module in (runtime, runtime._leases):
        monkeypatch.setattr(module, "os", windows)
        monkeypatch.setattr(module, "Path", PureWindowsPath)

    version = "1.0.0+repair." + "A" * 32
    for root in (r"C:\runtime\versions", r"\\server.invalid\share\runtime\versions"):
        native_root = "\\\\?\\UNC\\" + root[2:] if root.startswith("\\\\") else "\\\\?\\" + root
        for base in (root, native_root):
            for origin in (root, native_root):
                image = str(PureWindowsPath(origin) / version / "Scripts" / "python.exe")
                assert runtime._slot_of_path(image, base, {version}) == version
            sibling = str(PureWindowsPath(root + "-other") / version / "Scripts" / "python.exe")
            assert runtime._slot_of_path(sibling, base, {version}) is None
            unknown = str(PureWindowsPath(root) / "unrecognized" / "Scripts" / "python.exe")
            assert runtime._slot_of_path(unknown, base, {version}) is None


def test_reference_protocol_preserves_evidence_through_the_native_root(monkeypatch, tmp_path):
    runtime = _runtime(monkeypatch)
    leases = runtime._leases
    logical, physical = tmp_path / "logical", tmp_path / "physical"
    for version in ("old", "new"):
        (physical / "versions" / version).mkdir(parents=True)
    native_path = leases._native_path
    monkeypatch.setattr(leases, "_native_path", lambda root: physical if root == logical else native_path(root))
    monkeypatch.setattr(leases, "_publication_lease", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(leases, "_kernel_lease", lambda *_args, **_kwargs: nullcontext())
    leases.replace_runtime_references(physical, "fixture/prior", ["old"], expected=[])
    assert leases.read_runtime_references(logical) == {"fixture/prior": ["old"]}
    with pytest.raises(RuntimeError, match="changed"):
        leases.replace_runtime_references(logical, "fixture/prior", ["new"], expected=[])
    leases.begin_runtime_reference_transaction(logical, "fixture/prior")
    with pytest.raises(ValueError, match="pending"):
        leases.read_runtime_references(logical)
    ticket = leases.begin_runtime_reference_transaction(logical, "fixture/next", recover_foreign=True)
    prefix = leases.reference_recovery_prefix("fixture/prior")
    recovered = {key: values for key, values in ticket["owners"].items() if key.startswith(prefix)}
    assert set().union(*map(set, recovered.values())) == {"old", "new"}
    leases.commit_runtime_reference_transaction(
        logical, "fixture/next", ticket["token"], {"fixture/next": ["new"]},
        expected={"fixture/next": []},
    )
    assert leases.read_runtime_references(logical) == {
        "fixture/prior": ["old"], **recovered, "fixture/next": ["new"],
    }
