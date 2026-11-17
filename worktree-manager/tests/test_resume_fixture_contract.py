"""Actor-free routing controls for the real native Resume fixture owner."""

from __future__ import annotations

import ast
import io
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

_FIXTURE = Path(__file__).resolve().parent / "native/test_resume_pane_startup.py"
_NO_WINDOW = 0x08000000


@pytest.mark.parametrize(
    "module,operation,supported",
    [
        ("agent_remote_driver.mux_creation", "--describe-contract", True),
        ("agent_remote_driver.mux_observation", "--describe-contract", True),
        ("agent_remote_driver.mux_observation", "--observe", True),
        ("agent_remote_driver.mux_creation", "--observe", False),
        ("unselected.module", "--observe", False),
    ],
)
def test_native_owner_forwards_selected_observation_stdin_and_exit(
    monkeypatch: pytest.MonkeyPatch,
    module: str,
    operation: str,
    supported: bool,
) -> None:
    tree = ast.parse(_FIXTURE.read_text(encoding="utf-8"))
    owner = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "OWNER" for target in node.targets)
    )
    arguments = ["-I", "-X", "utf8", "-m", module, operation]
    payload = b'{"version":1,"session":"wt-fixture"}\n'
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(sys, "argv", ["owner.py", *arguments])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8"))
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", _NO_WINDOW, raising=False)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SystemExit) as caught:
        exec(compile(owner, str(_FIXTURE) + ":OWNER", "exec"), {"__name__": "__main__"})
    assert caught.value.code == (17 if supported else 91)
    if supported:
        assert calls == [
            (
                [sys.executable, *arguments],
                {"input": payload, "timeout": 25, "creationflags": _NO_WINDOW},
            )
        ]
    else:
        assert calls == []


@pytest.mark.parametrize(
    "version,compiled",
    [
        ((3, 4, 0, 0), (3, 4, 0, 0)),
        ((3, 3, 8, 0), (3, 3, 8, 0)),
        ((0, 0, 0, 0), (0, 0, 0, 0)),
        (None, None),
        ((3, 4, 0, 0), (0, 0, 0, 0)),
        ((3, 4, 0, 0), None),
    ],
)
def test_private_mux_preserves_real_provider_version_before_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: tuple[int, int, int, int] | None,
    compiled: tuple[int, int, int, int] | None,
) -> None:
    tree = ast.parse(_FIXTURE.read_text(encoding="utf-8"))
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_private_psmux"
    )
    compiler = tmp_path / "system/Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    compiler.parent.mkdir(parents=True)
    compiler.touch()
    monkeypatch.setenv("SystemRoot", str(tmp_path / "system"))
    binary = tmp_path / "provider/psmux.exe"
    root = tmp_path / "adapter"
    shim = root / "psmux.exe"
    reads: list[str] = []
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def read_version(path: str) -> tuple[int, int, int, int] | None:
        reads.append(path)
        if path == str(binary):
            assert not root.exists()
            return version
        assert path == str(shim) and shim.is_file()
        return compiled

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, options))
        shim.write_bytes(b"inert compiler output")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    namespace: dict[str, Any] = {
        "Path": Path,
        "os": os,
        "subprocess": subprocess,
        "_NO_WINDOW": _NO_WINDOW,
        "file_version": read_version,
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(_FIXTURE), "exec"), namespace)
    env = {"PATH": "original-search-path"}
    if version is not None and compiled != version:
        with pytest.raises(AssertionError, match="lost provider file metadata"):
            namespace["_private_psmux"](root, str(binary), "isolated-fixture", env)
        assert env == {"PATH": "original-search-path"}
    else:
        namespace["_private_psmux"](root, str(binary), "isolated-fixture", env)
        assert env["PATH"] == str(root) + os.pathsep + "original-search-path"
        assert env["COPILOT_DRIVER_PSMUX_NAMESPACE"] == "isolated-fixture"
    source = (root / "mux.cs").read_text(encoding="utf-8")
    if version is None:
        assert "AssemblyFileVersion" not in source
        assert reads == [str(binary)]
    else:
        text = ".".join(map(str, version))
        assert f'[assembly: System.Reflection.AssemblyFileVersion("{text}")]' in source
        assert reads == [str(binary), str(shim)]
    assert len(calls) == 1
    command, options = calls[0]
    assert command == [
        str(compiler),
        "/nologo",
        "/target:exe",
        "/out:" + str(shim),
        str(root / "mux.cs"),
    ]
    assert options["creationflags"] == _NO_WINDOW
    assert options["timeout"] == 20
