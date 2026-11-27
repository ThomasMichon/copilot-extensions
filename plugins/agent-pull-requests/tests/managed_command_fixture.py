"""Prepared runtimes and inert selection surfaces for command-entry controls."""
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
def prepared_python() -> tuple[Path, Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires prepared uv")
    interpreters: list[Path] = []
    for version in ("3.10.20", "3.11.18"):
        result = subprocess.run(
            [uv, "python", "find", "--no-project", "--system", "--managed-python",
             "--no-python-downloads", f"cpython@{version}"],
            capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
        )
        if result.returncode:
            pytest.skip(f"This acquisition-free control requires prepared CPython {version}")
        interpreters.append(Path(result.stdout.strip()).resolve())
    store = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, creationflags=HEADLESS,
    ).strip()
    return Path(uv), interpreters[0], interpreters[1], Path(store)


@pytest.fixture(scope="module")
def packaged_command(tmp_path_factory: pytest.TempPathFactory) -> Path:
    payload = tmp_path_factory.mktemp("pull-requests-command") / "agent-pull-requests"
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


def prepare_application(uv: Path, application: Path, slot: Path, env: dict[str, str]) -> Path:
    built = subprocess.run(
        [str(uv), "venv", "--no-project", "--no-python-downloads",
         "--python", str(application), str(slot)],
        env=env, capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    module = site / "agent_pull_requests"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "__main__.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "transport=Path(os.environ['PULL_REQUESTS_TRANSPORT_LOG'])\n"
        "artifact=json.loads(transport.read_text(encoding='utf-8').splitlines()[-1]) if transport.exists() else {}\n"
        "if os.environ.get('INHERITED_FD_TARGET'):\n"
        " for fd in range(5,10): os.write(fd,('application-fd-%s\\n'%fd).encode())\n"
        "value={'python':sys.executable,'prefix':sys.prefix,'version':list(sys.version_info[:3]),"
        "'argv':sys.argv[1:],'stdin':sys.stdin.read(),'cwd':os.getcwd(),"
        "'root':os.environ.get('AGENT_RT_ROOT'),"
        "'context':os.environ.get('COPILOT_EXTENSIONS_CONTEXT'),"
        "'transport_keys':[key for key in os.environ if key.startswith('COPILOT_EXTENSIONS_FORWARDED_ARGUMENTS_')],"
        "'argument_artifact_present':any(Path(artifact[key]).exists() for key in ('path','directory') if key in artifact),"
        "'caller_state':os.environ.get('PULL_REQUESTS_CALLER_STATE'),"
        "'sibling_state':os.environ.get('PULL_REQUESTS_SIBLING_STATE'),"
        "'python_home':os.environ.get('PYTHONHOME'),'python_path':os.environ.get('PYTHONPATH'),"
        "'isolated':bool(sys.flags.isolated),'dont_write':bool(sys.dont_write_bytecode),"
        "'selector':os.environ.get('UV_PYTHON')}\n"
        "Path(os.environ['APPLICATION_PROOF']).write_text(json.dumps(value),encoding='utf-8')\n"
        "print('application stdout')\nprint('application stderr',file=sys.stderr)\nraise SystemExit(37)\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.2.3", "completed_at": "fixture", "pid": 0,
    }), encoding="utf-8")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    compiled = subprocess.run(
        [str(python), "-I", "-m", "compileall", "-q", str(module)],
        env=env, capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    return python


def isolate_selection(payload: Path) -> None:
    """Keep actual dispatch/admission, replacing governance with inert selections.

    Canonical receipt and selection validation remains in test_cell_invocation;
    these native controls never adopt an OS profile or fabricate a native account.
    """
    scripts = payload / "scripts"
    (scripts / "payload-mode.sh").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$PULL_REQUESTS_TEST_MODE"\n', encoding="utf-8",
    )
    (scripts / "payload-mode.ps1").write_text(
        "$env:PULL_REQUESTS_TEST_MODE\n", encoding="utf-8",
    )
    shutil.copy2(scripts / "cell_invoke.py", scripts / "cell_invoke_owner.py")
    (scripts / "cell_invoke.py").write_text(
        "import importlib.util,json,os,sys\n"
        "from pathlib import Path\nfrom types import SimpleNamespace\n"
        "sys.dont_write_bytecode=True\n"
        "spec=importlib.util.spec_from_file_location('command_owner',Path(__file__).with_name('cell_invoke_owner.py'))\n"
        "owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)\n"
        "adapter=owner.installer()\n"
        "owner.installer=lambda:SimpleNamespace(foundation=lambda:None,"
        "management_version=lambda:'1.2.3',process_helpers=adapter.process_helpers)\n"
        "root=Path(os.environ['PULL_REQUESTS_TEST_ROOT'])\n"
        "python=Path(os.environ['PULL_REQUESTS_TEST_PYTHON'])\n"
        "status={'status':'ready','reason':'namespaced-active','actualMode':'namespaced',"
        "'desiredMode':'namespaced','context':os.environ['PULL_REQUESTS_TEST_CONTEXT']}\n"
        "owner.discover=lambda _:(status,root,root,{'result':'absent'})\n"
        "checks=[]\n"
        "def selection(*_):\n"
        " checks.append(1)\n"
        " return (python.with_name('changed') if len(checks)>1 and "
        "os.environ['PULL_REQUESTS_TEST_SCENARIO']=='cell-changed' else python,root)\n"
        "owner.selection=selection\n"
        "Path(os.environ['BOOTSTRAP_PROOF']).write_text(json.dumps({"
        "'python':sys.executable,'version':list(sys.version_info[:3]),'argv':sys.argv[1:]}),encoding='utf-8')\n"
        "raise SystemExit(owner.main())\n",
        encoding="utf-8",
    )
