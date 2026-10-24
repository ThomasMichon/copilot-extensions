"""Source-only build guards; no compiler, native loading, workload or Docker."""

import ctypes
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import zipfile

import pytest

from tools.coverage_guided_selection import observer_native_build as build

ENTRY = "1" * 64


def test_canonical_native_source_is_versioned_and_matches_its_build_pin():
    source = Path(build.__file__).with_name("native_observer_entry.c").read_bytes()
    assert b"\r" not in source
    assert hashlib.sha256(source).hexdigest() == build.C_SHA256
    entry = Path(build.__file__).with_name("native_observer_entry.py")
    assert entry.is_file() and b"\r" not in entry.read_bytes()


def test_native_entry_embeds_the_exact_shared_codec_in_its_pinned_source():
    import ast
    import inspect
    from tools.coverage_guided_selection.measurement_launch import ChildJSON

    source = build.entry_source()
    assert b"\r" not in source
    tree = ast.parse(source)
    codec = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ChildJSON")
    assert ast.dump(codec) == ast.dump(ast.parse(inspect.getsource(ChildJSON)).body[0])
    for node in ast.walk(tree):
        names = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                 [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        assert not any(name == "json" or name.startswith("tools.") for name in names)


@pytest.mark.parametrize("declarations", [0, 2])
def test_native_entry_refuses_missing_or_ambiguous_codec_declaration(monkeypatch, declarations):
    original = Path.read_text
    declaration = "from tools.coverage_guided_selection.measurement_launch import ChildJSON\n"
    def read(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        if path.name == "native_observer_entry.py":
            return text.replace(declaration, declaration * declarations)
        return text
    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(ValueError, match="exactly one shared codec declaration"):
        build.entry_source()


@pytest.mark.parametrize("optimize", [0, 1, 2])
def test_rendered_native_codec_initializes_in_each_fresh_isolated_child(optimize):
    import ast
    import json

    source = build.entry_source().decode("utf-8")
    boundary = next(node.lineno for node in ast.parse(source).body if isinstance(node, ast.With))
    prefix = "\n".join(source.splitlines()[:boundary - 1]) + "\n"
    payload = {"catalog": {"nonce": "a" * 64, "n": 2, "values": [True, None, "\ud800", 1.25]}}
    expected = json.dumps(payload, sort_keys=True)
    program = (
        "import sys\n"
        "assert 'json' not in sys.modules\n"
        "namespace={}\n"
        f"exec(compile({prefix!r},'<native-entry-codec>','exec',dont_inherit=True,"
        f"optimize={optimize}),namespace)\n"
        "codec=namespace['json']\n"
        f"assert codec.dumps({payload!r},sort_keys=True)=={expected!r}\n"
        f"decoded=codec.loads({expected!r})\n"
        f"assert decoded=={payload!r} and decoded['catalog']['values'][0] is True\n"
        "assert not {'json','re','pathlib'} & sys.modules.keys()\n"
    )
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", program],
        capture_output=True, text=True, timeout=30, **flags,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("optimize", [0, 1, 2])
def test_native_entry_required_guards_survive_original_optimization_flags(optimize):
    import ast
    import re
    root = Path(build.__file__).parent
    text = (root / "native_observer_entry.py").read_text()
    tree = ast.parse(text)
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "require")
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<native-entry-guard>",
                 "exec", optimize=optimize), namespace)
    namespace["require"](True, "accepted")
    with pytest.raises(RuntimeError, match="source differs"):
        namespace["require"](False, "source differs")
    source = (root / "native_observer_entry.c").read_text()
    native_check = re.search(r'"(if hashlib[^\n]+)\\n"', source).group(1).replace(
        '" ENTRY_SHA "', hashlib.sha256(b"original").hexdigest(),
    )
    exec(compile(native_check, "<native-entry-source-guard>", "exec", optimize=optimize),
         {"hashlib": hashlib, "source": b"original"})
    with pytest.raises(RuntimeError, match="native entry source identity differs"):
        exec(compile(native_check, "<native-entry-source-guard>", "exec", optimize=optimize),
             {"hashlib": hashlib, "source": b"must refuse"})


def test_native_scoped_entry_preserves_nonstring_module_globals():
    import ast
    entry = Path(build.__file__).with_name("native_observer_entry.py")
    tree = ast.parse(entry.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "scoped_call")
    marker = object()
    namespace = {"original_call": lambda *_args: marker}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<native-entry-scope>", "exec"), namespace)
    frame = SimpleNamespace(f_globals={"__name__": 71})
    assert namespace["scoped_call"](frame, "call", None) is marker


@pytest.mark.parametrize("changed", [False, True])
def test_native_loader_verifies_raw_source_and_preserves_module_namespace(tmp_path, changed):
    import ast

    entry = Path(build.__file__).with_name("native_observer_entry.py")
    tree = ast.parse(entry.read_text())
    for node in ast.walk(tree):
        imports = ([name.name for name in node.names] if isinstance(node, ast.Import) else
                   [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        assert not any(name.startswith(("importlib", "pathlib")) for name in imports)
    assert not any(isinstance(node, ast.Name) and node.id == "Path" for node in ast.walk(tree))
    helpers = {}
    for name in ("_cgs_child_audit", "_cgs_child_stream"):
        path = tmp_path / (name + ".py")
        content = b"marker=1\ndef value():\n    return marker\n"
        path.write_bytes(content)
        helpers[name] = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}
    if changed:
        helpers["_cgs_child_audit"]["sha256"] = "0" * 64
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "require"
        or isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == "name"
    ]
    namespace = {
        "hashlib": hashlib, "ModuleType": ModuleType, "catalog": {"helpers": helpers},
        "sys": SimpleNamespace(modules={}), "phase": lambda name: None,
    }
    compiled = compile(ast.Module(body=nodes, type_ignores=[]), str(entry), "exec")
    if changed:
        with pytest.raises(RuntimeError, match="helper source identity differs"):
            exec(compiled, namespace)
        assert not namespace["sys"].modules
    else:
        exec(compiled, namespace)
        for name, module in namespace["sys"].modules.items():
            assert module.__file__ == helpers[name]["path"] and module.__package__ == ""
            assert module.__spec__ is module.__loader__ is module.__cached__ is None
            module.marker = 3
            assert module.value() == 3 and module.value.__globals__ is module.__dict__


def wheel(tmp_path, monkeypatch, entries=None):
    path = tmp_path / build.WHEEL
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in (entries or {
            "ziglang/zig": b"compiler-not-executed",
            "ziglang/lib/include/stdio.h": b"header",
            "ziglang/lib/libc/include/any-windows-any/example.h": b"not-pruned",
            "ziglang/lib/libcxx/header": b"not-pruned-either",
            "ziglang-0.14.1.dist-info/METADATA": b"metadata-not-installed",
        }).items():
            archive.writestr(name, value)
    monkeypatch.setattr(build, "WHEEL_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_fresh_extraction_keeps_complete_library_tree_and_never_installs_metadata(tmp_path, monkeypatch):
    source = wheel(tmp_path, monkeypatch)
    output = tmp_path / "toolchain"
    observed = []
    original = build.zipfile.ZipFile
    def same_file(file, *args, **kwargs):
        observed.append(hasattr(file, "read") and not file.closed)
        return original(file, *args, **kwargs)
    monkeypatch.setattr(build.zipfile, "ZipFile", same_file)
    result = build.extract_toolchain(source, output)
    assert observed == [True]
    assert Path(result["compiler"]).read_bytes() == b"compiler-not-executed"
    assert (output / "ziglang/lib/libc/include/any-windows-any/example.h").read_bytes() == b"not-pruned"
    assert (output / "ziglang/lib/libcxx/header").read_bytes() == b"not-pruned-either"
    assert not (output / "ziglang-0.14.1.dist-info").exists()
    assert result["extracted_files"] == 4
    assert result["extracted_bytes"] == sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    with pytest.raises(FileExistsError):
        build.extract_toolchain(source, output)
    assert Path(result["compiler"]).is_file()


def test_wheel_hash_refuses_before_allocating_toolchain(tmp_path, monkeypatch):
    source = wheel(tmp_path, monkeypatch)
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="wheel identity"):
        build.extract_toolchain(source, tmp_path / "toolchain")
    assert not (tmp_path / "toolchain").exists()


def test_changed_open_wheel_after_extraction_refuses_and_cleans(tmp_path, monkeypatch):
    source = wheel(tmp_path, monkeypatch)
    original = build.hashlib.file_digest
    calls = []
    def changed_on_second_read(file, algorithm):
        calls.append(file)
        actual = original(file, algorithm)
        return actual if len(calls) == 1 else SimpleNamespace(hexdigest=lambda: "0" * 64)
    monkeypatch.setattr(build.hashlib, "file_digest", changed_on_second_read)
    with pytest.raises(ValueError, match="changed during extraction"):
        build.extract_toolchain(source, tmp_path / "toolchain")
    assert len(calls) == 2 and calls[0] is calls[1] and calls[0].closed
    assert not (tmp_path / "toolchain").exists()


@pytest.mark.parametrize("entries", [
    {"ziglang/lib/../escape": b"no"},
    {"ziglang/zig": b"no-library-tree"},
    {"ziglang/lib/include/header": b"no-compiler"},
])
def test_invalid_or_partial_archive_refuses_and_cleans(tmp_path, monkeypatch, entries):
    source = wheel(tmp_path, monkeypatch, entries)
    with pytest.raises(ValueError):
        build.extract_toolchain(source, tmp_path / "toolchain")
    assert not (tmp_path / "toolchain").exists()


def test_partial_write_failure_cleans_and_rethrows_original_error(tmp_path, monkeypatch):
    source = wheel(tmp_path, monkeypatch)
    error = OSError("injected extraction failure")
    def fail(*args):
        raise error
    monkeypatch.setattr(build.shutil, "copyfileobj", fail)
    with pytest.raises(OSError) as caught:
        build.extract_toolchain(source, tmp_path / "toolchain")
    assert caught.value is error
    assert not (tmp_path / "toolchain").exists()


def elf_content():
    data = bytearray(1024)
    data[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HH", data, 16, 3, 62)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 2)
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 5, 0, 0, 0, len(data), len(data), 4096)
    struct.pack_into("<IIQQQQQQ", data, 120, 2, 6, 512, 512, 512, 96, 96, 8)
    for offset, pair in enumerate([(25, 768), (27, 8), (7, 800), (8, 24), (9, 24), (0, 0)]):
        struct.pack_into("<qQ", data, 512 + offset * 16, *pair)
    struct.pack_into("<QQq", data, 800, 768, 8, 896)
    data[912:976] = ENTRY.encode()
    return data


def test_artifact_requires_actual_constructor_relocation_and_entry_identity(tmp_path):
    artifact = tmp_path / "helper.so"
    artifact.write_bytes(elf_content())
    result = build.verify_artifact(artifact, ENTRY)
    assert result["bytes"] == 1024 and result["constructor_entries"] == 1
    assert result["static_shape_only"] and "all_admitted" not in result


def test_native_prelude_phase_diagnostics_are_opt_in_bounded_and_keep_original_clock(tmp_path, monkeypatch):
    import ast
    import json

    path = Path(build.__file__).with_name("native_observer_entry.py")
    tree = ast.parse(path.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"phase", "report_prelude"}]
    namespace = {
        "os": os, "Path": Path, "json": json, "_phases": [],
        "time": SimpleNamespace(perf_counter_ns=lambda: 200, process_time_ns=lambda: 100),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    monkeypatch.delenv("CGS_PRELUDE_PHASE_PROFILE", raising=False)
    namespace["phase"]("disabled")
    assert namespace["_phases"] == []
    monkeypatch.setenv("CGS_PRELUDE_PHASE_PROFILE", "1")
    monkeypatch.setenv("_CGS_PRELUDE_CPU_NS", "10")
    namespace["phase"]("native-entry-first")
    (tmp_path / "compile").mkdir()
    namespace["report_prelude"](tmp_path, start_ns=50, end_ns=300, end_cpu_ns=150, duration_us=0)
    report = json.loads(next((tmp_path / "compile").glob("prelude-phases-*.json")).read_text())
    assert report["diagnostic_only"] and report["start_ns"] == 50 and report["end_ns"] == 300
    assert report["start_cpu_ns"] == 10 and report["end_cpu_ns"] == 150
    assert report["phases"] == [{"phase": "native-entry-first", "wall_ns": 200, "cpu_ns": 100}]
    for _ in range(63):
        namespace["phase"]("bounded")
    with pytest.raises(RuntimeError, match="phase bound"):
        namespace["phase"]("excess")


@pytest.mark.parametrize("fault", [
    "missing-init", "unresolved-init", "changed-entry", "unknown-relocation", "wrong-abi",
    "missing-relocations", "duplicate-relocation", "null-relocated-init",
])
def test_missing_or_changed_native_initialization_refuses(tmp_path, fault):
    data = elf_content()
    if fault == "missing-init":
        struct.pack_into("<Q", data, 512 + 8, 0)
    elif fault == "unresolved-init":
        struct.pack_into("<q", data, 800 + 16, 5000)
    elif fault == "changed-entry":
        data[912:976] = b"2" * 64
    elif fault == "unknown-relocation":
        struct.pack_into("<Q", data, 800 + 8, 7)
    elif fault == "missing-relocations":
        # A plausible executable offset is still an unresolved absolute pointer.
        struct.pack_into("<Q", data, 768, 896)
        struct.pack_into("<q", data, 512 + 2 * 16, 0)
    elif fault == "duplicate-relocation":
        struct.pack_into("<Q", data, 512 + 3 * 16 + 8, 48)
        struct.pack_into("<QQq", data, 824, 768, 8, 896)
    elif fault == "null-relocated-init":
        struct.pack_into("<q", data, 800 + 16, 0)
    else:
        struct.pack_into("<H", data, 18, 183)
    artifact = tmp_path / "helper.so"
    artifact.write_bytes(data)
    with pytest.raises(ValueError):
        build.verify_artifact(artifact, ENTRY)


@pytest.fixture
def provider(tmp_path, monkeypatch):
    path = tmp_path / "runtime.so"
    path.write_bytes(elf_content())
    concrete = type(Path())
    class ProtectedPath(concrete):
        weak = False
        def stat(self, *args, **kwargs):
            actual = super().stat(*args, **kwargs)
            return SimpleNamespace(st_uid=0, st_mode=0o100777 if self.weak else 0o100755,
                                   st_dev=actual.st_dev, st_ino=actual.st_ino, st_size=actual.st_size)
    monkeypatch.setattr(build, "Path", ProtectedPath)
    return path, {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "device": path.stat().st_dev, "inode": path.stat().st_ino,
                  "symbols": list(build.RUNTIME_SYMBOLS)}, ProtectedPath


def test_runtime_providers_come_from_real_pointer_provider_queries(provider, monkeypatch):
    path, expected, protected = provider
    calls = []
    class Dladdr:
        def __call__(self, pointer, info):
            calls.append(pointer.value)
            info._obj.path = os.fsencode(path)
            return 1
    class Process:
        dladdr = Dladdr()
        def __getattr__(self, name):
            if name not in build.RUNTIME_SYMBOLS:
                raise AttributeError(name)
            return ctypes.c_void_p(4096 + build.RUNTIME_SYMBOLS.index(name))
    monkeypatch.setattr(build.sys, "platform", "linux")
    monkeypatch.setattr(build.ctypes, "CDLL", lambda name: Process())
    actual = build.runtime_link_inputs()
    assert actual == [expected] and len(calls) == len(build.RUNTIME_SYMBOLS)
    build.verify_runtime_inputs(actual)
    protected.weak = True
    with pytest.raises(ValueError, match="unprotected"):
        build.runtime_link_inputs()


@pytest.mark.parametrize("fault", ["missing-symbol", "duplicate-symbol", "changed-bytes", "changed-inode", "weak-permissions"])
def test_missing_or_changed_runtime_link_inputs_refuse(provider, fault):
    path, row, protected = provider
    if fault == "missing-symbol":
        row["symbols"].pop()
    elif fault == "duplicate-symbol":
        row["symbols"].append(row["symbols"][0])
    elif fault == "changed-bytes":
        path.write_bytes(bytes(elf_content()) + b"changed")
    elif fault == "changed-inode":
        row["inode"] += 1
    else:
        protected.weak = True
    with pytest.raises(ValueError):
        build.verify_runtime_inputs([row])


def test_missing_native_constructor_fails_setup_before_consumer_and_cleans(tmp_path):
    from tools.coverage_guided_selection.observer_lifecycle import OwnedObserverLifecycle
    artifact = tmp_path / "helper.so"
    content = elf_content()
    struct.pack_into("<Q", content, 512 + 8, 0)
    artifact.write_bytes(content)
    calls = []
    def cleanup(session):
        artifact.unlink(missing_ok=True)
        calls.append("cleanup")
    lifecycle = OwnedObserverLifecycle(lambda:build.verify_artifact(artifact, ENTRY),
        lambda:pytest.fail("capture after native initialization refusal"),
        lambda session:pytest.fail("admission after native initialization refusal"),cleanup)
    assert not lifecycle.begin()
    result = lifecycle.complete()
    assert not result["all_admitted"] and result["observer_setup_refused"]
    assert result["capture_started_ns"] is None and not artifact.exists()
    assert calls == ["cleanup", "cleanup"]


def test_build_variant_changes_only_helper_link_argv_without_fallback(tmp_path, provider, monkeypatch):
    path, row, protected = provider
    source = tmp_path / "entry.c"
    source.write_bytes(b"same-source")
    monkeypatch.setattr(build, "C_SHA256", hashlib.sha256(source.read_bytes()).hexdigest())
    old = build.build_argv("/compiler/zig", source, "/owned/helper.so", ENTRY, variant="original")
    new = build.build_argv("/compiler/zig", source, "/owned/helper.so", ENTRY,
                          variant="runtime-link-only", providers=[row])
    assert old[:-1] == new[:len(old)-1]
    assert old[-1] == "-ldl"
    assert new[len(old)-1:] == ["-nostdlib", "-nostartfiles", "-nodefaultlibs", "-Wl,-z,defs", str(path)]
    with pytest.raises(ValueError, match="missing observer runtime"):
        build.build_argv("/compiler/zig", source, "/owned/helper.so", ENTRY,
                         variant="runtime-link-only")
    with pytest.raises(ValueError, match="unknown"):
        build.build_argv("/compiler/zig", source, "/owned/helper.so", ENTRY, variant="fallback")
    source.write_bytes(b"changed-source")
    with pytest.raises(ValueError, match="source or entry"):
        build.build_argv("/compiler/zig", source, "/owned/helper.so", ENTRY, variant="original")
