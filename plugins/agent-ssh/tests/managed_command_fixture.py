"""Prepared-only prerequisites for isolated SSH command-entry controls."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
HEADLESS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture(scope="module")
def prepared_python() -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires prepared uv")
    result = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.10.20"],
        capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    if result.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.10.20")
    store = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, creationflags=HEADLESS,
    ).strip()
    return Path(uv), Path(result.stdout.strip()).resolve(), Path(store)


@pytest.fixture(scope="module")
def packaged_command(tmp_path_factory: pytest.TempPathFactory) -> Path:
    payload = tmp_path_factory.mktemp("ssh-command-package") / "agent-ssh"
    shutil.copytree(PLUGIN / "scripts", payload / "scripts")
    for name in ("plugin.json", "payload-invocation.json", "pyproject.toml"):
        shutil.copy2(PLUGIN / name, payload / name)
    previous = sys.path[:]
    try:
        owner = runpy.run_path(str(REPO / "tools/materialize_payload_invocation.py"))
        changes = owner["materialize"](payload, canonical_root=REPO, source_consumer=PLUGIN)
    finally:
        sys.path[:] = previous
    assert changes and not any(line.startswith("SKIP") for line in changes), changes
    return payload


def isolate_context(payload: Path) -> None:
    scripts = payload / "scripts"
    (scripts / "installation-context/installation-context.sh").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$1" >> "$SSH_CONTEXT_CALLS"\n'
        'case "$1" in\n'
        ' status) printf "%s\\n" "$SSH_TEST_STATUS" ;;\n'
        ' validate) printf "%s\\n" "$SSH_TEST_VALIDATED" ;;\n'
        " *) printf 'unexpected context operation\\n' >&2; exit 97 ;;\nesac\n",
        encoding="utf-8",
    )
    (scripts / "installation-context/installation-context.ps1").write_text(
        "param($Action,$PayloadRoot,$PluginId,$LegacyRoot,$Context,$DurableHome,"
        "$ExpectedPluginId,$ExpectedPayloadRoot)\n"
        "[IO.File]::AppendAllText($env:SSH_CONTEXT_CALLS,$Action+\"`n\")\n"
        "switch -CaseSensitive ($Action) {\n"
        " 'status' { Write-Output $env:SSH_TEST_STATUS }\n"
        " 'validate' { Write-Output $env:SSH_TEST_VALIDATED }\n"
        " default { throw 'unexpected context operation' }\n"
        "}\nexit 0\n", encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        "printf 'installer should not run\\n' >&2\nexit 98\n", encoding="utf-8",
    )
    (scripts / "install.ps1").write_text("throw 'installer should not run'\n", encoding="utf-8")


def prepare_application(uv: Path, application: Path, slot: Path, env: dict[str, str]) -> Path:
    built = subprocess.run(
        [str(uv), "venv", "--no-project", "--no-python-downloads",
         "--python", str(application), str(slot)],
        env=env, capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    package = site / "agent_ssh"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "if os.environ.get('INHERITED_FD_TARGET'):\n"
        " for fd in range(5,10): os.write(fd,('application-fd-%s\\n'%fd).encode())\n"
        "value={'python':sys.executable,'prefix':sys.prefix,'version':list(sys.version_info[:3]),"
        "'argv':sys.argv[1:],'stdin':sys.stdin.read(),'cwd':os.getcwd(),"
        "'root':os.environ.get('AGENT_SSH_HOME'),"
        "'context':os.environ.get('COPILOT_EXTENSIONS_CONTEXT'),"
        "'selector':os.environ.get('UV_PYTHON')}\n"
        "Path(os.environ['APPLICATION_PROOF']).write_text(json.dumps(value),encoding='utf-8')\n"
        "print('application stdout')\nprint('application stderr',file=sys.stderr)\nraise SystemExit(37)\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.2.3", "completed_at": "fixture", "pid": 0,
    }), encoding="utf-8")
    return slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
