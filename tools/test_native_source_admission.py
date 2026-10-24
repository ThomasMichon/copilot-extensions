"""Native admission compiles freshly before trusting actual SDK code objects."""

import ast
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from tools.coverage_guided_selection import child_audit, child_stream as stream


@pytest.fixture
def admission(tmp_path, monkeypatch):
    source = tmp_path / "fleet_manager" / "example.py"
    source.parent.mkdir()
    content = b'"""module"""\nassert True\ndef value():\n    return 22\n'
    source.write_bytes(content)
    for key, value in (
        ("_sources", {}), ("_expected_codes", {}), ("_code_sources", {}),
        ("_root", str(source.parent)), ("_package", "fleet_manager"),
        ("_generated_lock", threading.RLock()),
    ):
        monkeypatch.setattr(stream, key, value)
    errors, compiles = [], []
    monkeypatch.setattr(child_audit, "error", errors.append)
    stream._prepare_sources({
        str(source): (1, 4, hashlib.sha256(content).hexdigest()),
    }, compile_sources=False)
    entry = Path(stream.__file__).with_name("native_observer_entry.py")
    function = next(node for node in ast.parse(entry.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "source_check")
    namespace = {
        "os": os, "sys": sys, "hashlib": hashlib, "stream": stream,
        "auditor": SimpleNamespace(error=errors.append), "code_tree": stream._code_tree,
        "catalog": {"sources": {"fleet_manager/example.py": {
            "id": 1, "lines": 4, "sha256": hashlib.sha256(content).hexdigest(),
        }}},
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(entry), "exec"), namespace)

    def compiled(*args, **kwargs):
        compiles.append((args, kwargs))
        return compile(*args, **kwargs)

    namespace["compile"] = compiled
    return source, content, namespace, errors, compiles


def test_inventory_only_preparation_never_compiles_or_admits_code(tmp_path, monkeypatch):
    source = tmp_path / "example.py"
    content = b"value=22\n"
    source.write_bytes(content)
    for key in ("_sources", "_expected_codes", "_code_sources"):
        monkeypatch.setattr(stream, key, {})

    def forbidden(*_args, **_kwargs):
        pytest.fail("inventory-only preparation compiled unused SDK source")

    monkeypatch.setattr(stream, "compile", forbidden, raising=False)
    stream._prepare_sources({str(source): (1, 1, hashlib.sha256(content).hexdigest())},
                            compile_sources=False)
    assert stream._sources[os.path.normcase(str(source))] == (1, 1)
    assert not stream._expected_codes and not stream._code_sources
    source.write_bytes(b"value=11\n")
    with pytest.raises(ValueError, match="source differs"):
        stream._prepare_sources({str(source): (1, 1, hashlib.sha256(content).hexdigest())},
                                compile_sources=False)


@pytest.mark.parametrize("optimize", [0, 1, 2])
def test_native_admission_compiles_actual_mode_and_registers_only_after_proof(admission, optimize):
    source, content, namespace, errors, compiles = admission
    namespace["sys"] = SimpleNamespace(flags=SimpleNamespace(optimize=optimize))
    actual = compile(content, str(source), "exec", dont_inherit=True, optimize=optimize)
    assert not stream._code_sources and not stream._expected_codes
    namespace["source_check"]("compile", (content, str(source)))
    assert not compiles and not stream._code_sources
    namespace["source_check"]("exec", (actual,))
    assert not errors and len(compiles) == 1
    assert compiles[0][1] == {"dont_inherit": True, "optimize": optimize}
    for code in stream._code_tree(actual):
        assert stream._code_sources[id(code)] == (code, (1, 4))
        assert code in stream._expected_codes[os.path.normcase(str(source))]


@pytest.mark.parametrize("failure", ["changed-source", "stale-code", "optimization", "shadow", "unknown"])
def test_native_admission_refuses_unverified_source_or_executable(admission, failure):
    source, content, namespace, errors, _ = admission
    actual_content, filename = content, str(source)
    if failure == "changed-source":
        source.write_bytes(content.replace(b"22", b"11"))
    elif failure == "stale-code":
        actual_content = content.replace(b"22", b"11")
    elif failure == "optimization":
        namespace["sys"] = SimpleNamespace(flags=SimpleNamespace(optimize=2))
    elif failure in {"shadow", "unknown"}:
        other = source.parent.parent / "shadow" / "fleet_manager" / (
            "example.py" if failure == "shadow" else "unknown.py")
        other.parent.mkdir(parents=True)
        other.write_bytes(content)
        filename = str(other)
    actual = compile(actual_content, filename, "exec", dont_inherit=True, optimize=0)
    namespace["source_check"]("exec", (actual,))
    assert errors and not stream._expected_codes
    assert all(stream._code_sources[id(code)] == (code, None) for code in stream._code_tree(actual))


def test_native_reexecution_rechecks_source_and_revokes_previous_identity(admission):
    source, content, namespace, errors, compiles = admission
    actual = compile(content, str(source), "exec", dont_inherit=True)
    for _ in range(2):
        namespace["source_check"]("exec", (actual,))
    assert len(compiles) == 2 and not errors
    source.write_bytes(content.replace(b"22", b"11"))
    namespace["source_check"]("exec", (actual,))
    assert errors and not stream._expected_codes
    assert all(stream._code_sources[id(code)] == (code, None) for code in stream._code_tree(actual))


def test_inventory_only_mode_without_native_admission_cannot_trust_frames(admission):
    source, content, _namespace, errors, _compiles = admission
    actual = compile(content, str(source), "exec", dont_inherit=True)
    assert stream._frame_source(SimpleNamespace(
        f_code=actual, f_globals={"__name__": "fleet_manager.example"},
    )) is None
    assert errors == ["executed code differs from verified source inventory",
                      "owned package executed outside verified source inventory"]


@pytest.mark.parametrize("child", [0, 1])
def test_fresh_isolated_child_proves_admission_before_body_and_refuses_changed_reexec(tmp_path, child):
    source = tmp_path / "fleet_manager" / "example.py"
    source.parent.mkdir()
    source.write_text("seen.append(admitted())\ndef value():\n    return 22\n")
    entry = Path(stream.__file__).with_name("native_observer_entry.py")
    program = f"""
import ast,hashlib,os,sys,types
source={str(source)!r}
helper={str(Path(stream.__file__))!r}
entry={str(entry)!r}
stream=types.ModuleType('_cgs_child_stream')
stream.__file__=helper
with open(helper,'rb') as file: exec(compile(file.read(),helper,'exec',dont_inherit=True),stream.__dict__)
with open(source,'rb') as file: content=file.read()
stream._prepare_sources({{source:(1,3,hashlib.sha256(content).hexdigest())}},compile_sources=False)
assert not stream._expected_codes and not stream._code_sources
with open(entry) as file: tree=ast.parse(file.read())
function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='source_check')
errors=[]
namespace=dict(os=os,sys=sys,hashlib=hashlib,stream=stream,code_tree=stream._code_tree,
 auditor=types.SimpleNamespace(error=errors.append),
 catalog={{'sources':{{'fleet_manager/example.py':{{'id':1,'lines':3,'sha256':hashlib.sha256(content).hexdigest()}}}}}})
exec(compile(ast.Module(body=[function],type_ignores=[]),entry,'exec'),namespace)
actual=compile(content,source,'exec',dont_inherit=True)
seen=[]
scope=dict(seen=seen,admitted=lambda:stream._code_sources.get(id(actual),(None,None))[1]==(1,3))
sys.addaudithook(namespace['source_check'])
exec(actual,scope)
assert seen==[True] and scope['value']()==22 and not errors
with open(source,'wb') as file: file.write(content.replace(b'22',b'11'))
exec(actual,scope)
assert seen==[True,False] and scope['value']()==22 and errors
assert not stream._expected_codes
print('FRESH_CHILD_ADMISSION_PASS',{child})
"""
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program],
                            capture_output=True, text=True, timeout=15, **flags)
    assert result.returncode == 0, result.stderr
    assert f"FRESH_CHILD_ADMISSION_PASS {child}" in result.stdout
