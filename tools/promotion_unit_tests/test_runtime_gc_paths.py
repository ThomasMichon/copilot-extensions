"""Actor-free Windows path attribution controls for runtime garbage collection."""
from __future__ import annotations

import importlib.util
import ntpath
from pathlib import Path, PureWindowsPath
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]


def test_gc_process_paths_preserve_native_aliases_and_slot_boundaries(monkeypatch):
    source = ROOT / "libs/versioned-runtime/versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("_unit_runtime_gc_paths", source)
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
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
