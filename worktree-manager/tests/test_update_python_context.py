"""Bootstrap retains explicit Python and UV rather than a draining worker's selection."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable
import venv

import pytest
import update_driver

from worktree_manager import update_driver_cli as manager


KEY = "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"


@pytest.mark.parametrize("prepared", [False, True])
def test_installation_keeps_bootstraps_uv_through_the_real_constructor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prepared: bool,
) -> None:
    from worktree_manager import self_install

    installer_factory: Callable[[], Any] = self_install._runtime_installer
    adapter = installer_factory()
    import source_acquisition
    payload, root, uv = tmp_path / "payload", tmp_path / "manager", tmp_path / "uv"
    package = payload / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    (package / "__main__.py").touch()
    (payload / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.2.3"\n', encoding="utf-8",
    )
    uv.touch()
    seen: list[Path] = []

    @contextmanager
    def candidate(actual_root: Path, version: str, **options: Any) -> Iterator[SimpleNamespace]:
        seen.append(options["uv"])
        yield SimpleNamespace(slot=actual_root / "versions" / version, reused=False)

    @contextmanager
    def selected_source(*args: Any, **kwargs: Any) -> Iterator[tuple[Any, dict[str, Any]]]:
        if not prepared:
            assert kwargs == {"uv": uv}
        yield (SimpleNamespace(payload=payload) if prepared else payload), {}

    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    monkeypatch.setenv("PATH", str(tmp_path / "unusable-ambient-tools"))
    monkeypatch.setattr(adapter.engine, "numbered_candidate", candidate)
    monkeypatch.setattr(
        self_install, "_complete_candidate",
        lambda ready, *args, **kwargs: self_install.SelfInstallResult(
            "1.2.3", "installed", str(root), slot=str(ready.slot),
        ),
    )
    if prepared:
        monkeypatch.setattr(adapter, "prepared_manager_inputs", selected_source)
        monkeypatch.setattr(source_acquisition, "manager_payload", lambda *args: pytest.fail(
            "prepared installation must not acquire another source",
        ))
    else:
        monkeypatch.setattr(source_acquisition, "manager_payload", selected_source)
    args = SimpleNamespace(
        bootstrap_uv=uv, root=root, recover_adopted=None, payload=payload,
        source_digest=None, repair_update=False, skip_instance_reconcile=True,
        install_source=tmp_path / "snapshot" if prepared else None,
    )
    assert adapter._install(args) == root / "versions" / "1.2.3"
    assert seen == [uv]


@pytest.mark.parametrize("extra", [
    ["--describe-source-inputs"], ["--describe-install"], ["--recover-for-repair"],
    ["--uninstall"], ["--wheel-uv", "another-tool"],
])
def test_bootstrap_uv_cannot_change_read_only_recovery_or_native_wheel_inputs(
    tmp_path: Path, extra: list[str],
) -> None:
    from worktree_manager import self_install

    installer_factory: Callable[[], Any] = self_install._runtime_installer
    uv = tmp_path / "uv"
    uv.touch()
    with pytest.raises(SystemExit) as refusal:
        installer_factory().main(["--bootstrap-uv", str(uv), *extra])
    assert refusal.value.code == 2


def test_dev_source_preparation_executes_bootstraps_uv_instead_of_the_first_path_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import self_install, runtime_source_self_update as source_update
    from worktree_manager.runtime_source import ResolvedSource
    from worktree_manager.runtime_source_config import RuntimeSource

    installer_factory: Callable[[], Any] = self_install._runtime_installer
    adapter = installer_factory()
    import source_acquisition
    payload, root = tmp_path / "payload", tmp_path / "manager"
    payload.mkdir()
    package = tmp_path / "process-helper"
    package.mkdir()
    (package / "pyproject.toml").write_text('[project]\nname="agent-procutil"\n', encoding="utf-8")
    monkeypatch.setattr(adapter.engine, "_package_roots", lambda path: (package,))
    monkeypatch.setattr(source_acquisition, "importlib", SimpleNamespace(
        import_module=lambda name: SimpleNamespace(__file__=str(package / "src/agent_procutil/__init__.py")),
    ))
    monkeypatch.setattr(self_install, "manager_ref", lambda root: "dev")
    monkeypatch.setattr(self_install, "manager_repo", lambda root: str(tmp_path / "repo"))
    suffix = ".cmd" if os.name == "nt" else ""
    uv = tmp_path / ("qualified-uv" + suffix)
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    bad = shadow / ("uv" + suffix)
    uv.write_text(
        "@echo off\necho uv 0.13.7\n" if os.name == "nt"
        else "#!/bin/sh\nprintf '%s\\n' 'uv 0.13.7'\n", encoding="utf-8",
    )
    bad.write_text("@exit /b 19\n" if os.name == "nt" else "#!/bin/sh\nexit 19\n", encoding="utf-8")
    uv.chmod(0o755)
    bad.chmod(0o755)
    monkeypatch.setenv("PATH", str(shadow) + os.pathsep + os.environ.get("PATH", ""))
    tools = tmp_path / "tools"
    tools.mkdir()
    tool = tools / "prepare_runtime_source.py"
    (tools / "local_install_source.py").write_text("""
import json,subprocess,sys
from pathlib import Path
uv=sys.argv[sys.argv.index("--uv")+1]
result=subprocess.run([uv,"--version"],capture_output=True,text=True,check=True,timeout=10,
                      creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
assert result.stdout.strip()=="uv 0.13.7"
output=Path(sys.argv[sys.argv.index("--output")+1])
print(json.dumps({"worktree-manager":str(output/"profiles"/"worktree-manager"/"snapshot")}))
""", encoding="utf-8")
    resolved = ResolvedSource(
        RuntimeSource("dev", allow_dev=True), tmp_path / "checkout",
        "a" * 40, "b" * 40, "c" * 40, "d" * 40,
    )

    @contextmanager
    def acquire(*args: Any, **kwargs: Any) -> Iterator[ResolvedSource]:
        yield resolved

    @contextmanager
    def prepared_inputs(snapshot: Path) -> Iterator[tuple[SimpleNamespace, dict[str, Path]]]:
        assert snapshot.name == "snapshot"
        yield SimpleNamespace(
            payload=tmp_path / "selected",
            identity=SimpleNamespace(source_commit=resolved.commit, source_tree=resolved.tree),
            composition={"main_seed": resolved.main_head},
        ), {"application": tmp_path / "application"}

    monkeypatch.setattr(source_update, "acquire", acquire)
    monkeypatch.setattr(source_update, "preparer", lambda: tool)
    monkeypatch.setattr(source_update, "runtime_scripts", lambda *args: tools)
    monkeypatch.setattr(source_update, "load_runtime_installer", lambda scripts: adapter)
    monkeypatch.setattr(adapter, "prepared_manager_inputs", prepared_inputs)
    with source_acquisition.manager_payload(payload, root, uv=uv) as (selected, options):
        assert selected == tmp_path / "selected"
        assert options == {"application": tmp_path / "application"}


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap installation participant")
def test_foreground_install_selects_its_python_for_the_real_queued_participant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager import self_install

    host = shutil.which("pwsh") or shutil.which("powershell")
    if host is None:
        pytest.skip("PowerShell is unavailable")
    installer_factory: Callable[[], Any] = self_install._runtime_installer
    adapter = installer_factory()
    monkeypatch.setattr(sys, "path", list(sys.path))
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    root = tmp_path / "manager"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    payload = tmp_path / "payload"
    (payload / "src" / "worktree_manager").mkdir(parents=True)
    (payload / "src" / "worktree_manager" / "__init__.py").write_text(
        '__version__ = "1.2.3"\n', encoding="utf-8",
    )
    (payload / "src" / "worktree_manager" / "__main__.py").touch()
    (payload / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.2.3"\n', encoding="utf-8",
    )
    uv = tmp_path / "qualified-uv.cmd"
    uv.write_text("@echo off\necho uv 0.13.7\n", encoding="utf-8")
    adapter.runtime.mark_complete(root, slot.name, payload_hash="b" * 64)
    queue = tmp_path / "queue"
    report = tmp_path / "participant.json"
    sdk = Path(__file__).resolve().parents[2] / "libs" / "installation-context" / "installation-context.ps1"
    (scripts / "runtime_install.py").write_text(f"""
import json,os,subprocess,sys
from contextlib import contextmanager
from pathlib import Path
sys.path[:0]={sys.path!r}
from agent_procutil import no_window_kwargs
from update_driver import core
from worktree_manager import self_install
adapter=self_install._runtime_installer()
core.default_root=lambda:Path({str(queue)!r})
adapter._execution_driver=lambda payload:core
report={{"before":os.environ.get({KEY!r})}}
@contextmanager
def candidate(root, version, **options):
    from types import SimpleNamespace
    report["uv"]=str(options["uv"])
    result=subprocess.run(
        [str(options["uv"]),"--version"],capture_output=True,text=True,timeout=10,**no_window_kwargs())
    assert result.returncode==0,result.stdout+result.stderr
    report["uv_version"]=result.stdout.strip()
    yield SimpleNamespace(slot=Path({str(slot)!r}),reused=False)
adapter.engine.numbered_candidate=candidate
self_install._complete_candidate=lambda ready,*a,**k:self_install.SelfInstallResult(
    "1.2.3","installed",{str(root)!r},slot=str(ready.slot))
def install(args):
    report["during"]=os.environ.get({KEY!r})
    result=subprocess.run([
        {host!r},"-NoProfile","-NonInteractive","-File",{str(sdk)!r},
        "source-id","-SourceJson",'{{"source":"opaque","id":"install"}}',
        "-CopilotHome",{str(tmp_path / "copilot")!r},
        "-DurableHome",{str(tmp_path / "durable")!r},
    ],capture_output=True,text=True,timeout=15,**no_window_kwargs())
    report.update(code=result.returncode,stdout=result.stdout,stderr=result.stderr)
    if result.returncode:
        raise RuntimeError(result.stdout+result.stderr)
    return adapter._install_selected_payload(
        args,args.payload,before_publish=lambda:None,reconcile=False,
        wheel_inputs=None,local_packages=None,application=None,native_wheel_inputs=None)
adapter._install=install
try:
    code=adapter.main(sys.argv[1:])
finally:
    report["after"]=os.environ.get({KEY!r})
    Path({str(report)!r}).write_text(json.dumps(report),encoding="utf-8")
raise SystemExit(code)
""", encoding="utf-8")
    monkeypatch.setattr(update_driver.core, "default_root", lambda: queue)
    monkeypatch.setattr(adapter, "SCRIPTS", scripts)
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: "a" * 64)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv(KEY, "different-worker-selection")
    args = SimpleNamespace(
        payload=payload, root=root, command=[], repair_update=False, recover_adopted=None,
        skip_instance_reconcile=False, bootstrap_uv=uv,
    )
    assert adapter._foreground_install(args, update_driver.core) == slot
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["before"] is None and result["after"] is None
    assert result["during"] == getattr(sys, "_base_executable", sys.executable)
    assert result["code"] == 0 and result["stderr"] == "", result
    assert Path(result["uv"]) == uv
    assert result["uv_version"] == "uv 0.13.7"
    assert json.loads(result["stdout"])["canonical"] == "opaque:install"
    assert os.environ[KEY] == "different-worker-selection"
    requests = update_driver.status(root=queue)
    assert isinstance(requests, tuple) and len(requests) == 1
    assert KEY not in dict(requests[0].spec.context)


def test_bootstrap_update_carries_uv_in_participant_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bootstrap_inputs
    import bootstrap_update
    from update_driver.authority import UpdateAuthority
    from worktree_manager import update_python_path

    payload = tmp_path / "source"
    payload.mkdir()
    runners: list[Any] = []
    monkeypatch.setattr(bootstrap_inputs, "retain", lambda payload, *a, **k: (payload, True))
    monkeypatch.setattr(manager, "request_context", lambda: {})
    monkeypatch.setattr(manager, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(manager, "_update_context_directory", lambda context: None)
    monkeypatch.setattr(update_python_path, "finish_update", lambda command, code: code)

    def enqueue(spec: Any, *, runner: Any, **kwargs: Any) -> SimpleNamespace:
        runners.append(runner)
        assert not any("BOOTSTRAP_UV" in key for key, value in spec.context)
        return SimpleNamespace(id="fixture", log_path=tmp_path / "update.log")

    driver = SimpleNamespace(
        RequestSpec=update_driver.RequestSpec, TrustedRunner=update_driver.TrustedRunner,
        current_authority=lambda **kwargs: UpdateAuthority(),
        default_root=lambda: tmp_path, enqueue=enqueue, drain=lambda: None,
        wait=lambda *args, **kwargs: SimpleNamespace(state="completed", exit_code=0),
    )
    args = SimpleNamespace(
        payload=payload, root=tmp_path / "manager", install_source=None,
        command=["update", "--supervised-debug"], repair_update=False,
        bootstrap_uv=tmp_path / "qualified-uv",
    )
    execute: Callable[..., int] = bootstrap_update.execute
    assert execute(args, driver, source_digest="a" * 64) == 0
    arguments = runners[0].argv
    assert arguments[arguments.index("--bootstrap-uv") + 1] == str(args.bootstrap_uv)


@pytest.mark.skipif(os.name != "nt", reason="Windows shell-to-constructor process contract")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_shell_selection_reaches_the_real_queued_constructor(
    tmp_path: Path, host_name: str,
) -> None:
    from test_bootstrap_ps1 import BOOTSTRAP, _fake_tools, _GIT_ID, _quote, _source_repo

    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    repository = _source_repo(tmp_path)
    payload = repository / "worktree-manager"
    package = payload / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    (package / "__main__.py").touch()
    (payload / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.2.3"\n', encoding="utf-8",
    )
    root = tmp_path / "manager"
    root.mkdir()
    report = tmp_path / "construction.json"
    (payload / "scripts" / "runtime_install.py").write_text(f"""
# copilot-extensions:bootstrap-uv=1
import json,subprocess,sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]={sys.path!r}
from agent_procutil import no_window_kwargs
from update_driver import core
from worktree_manager import self_install
adapter=self_install._runtime_installer()
adapter.SCRIPTS=Path(__file__).parent
core.default_root=lambda:Path({str(root / "queue")!r})
adapter._execution_driver=lambda payload:core
@contextmanager
def candidate(root,version,**options):
    uv=options["uv"]
    result=subprocess.run([str(uv),"--version"],capture_output=True,text=True,timeout=10,**no_window_kwargs())
    assert result.returncode==0,result.stdout+result.stderr
    Path({str(report)!r}).write_text(json.dumps({{"uv":str(uv),"version":result.stdout.strip()}}),encoding="utf-8")
    yield SimpleNamespace(slot=root/"versions"/version,reused=False)
adapter.engine.numbered_candidate=candidate
def complete(ready,*args,**kwargs):
    adapter.runtime.mark_complete(Path({str(root)!r}),ready.slot.name,payload_hash="a"*64)
    return self_install.SelfInstallResult("1.2.3","installed",{str(root)!r},slot=str(ready.slot))
self_install._complete_candidate=complete
finish=adapter._finish
adapter._finish=lambda args,slot,command:finish(args,slot,command) if args.install_only or isinstance(slot,int) else 0
raise SystemExit(adapter.main())
""", encoding="utf-8")
    native: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    for arguments in (["add", "."], [*_GIT_ID, "commit", "-qm", "constructor fixture"]):
        subprocess.run(["git", "-C", str(repository), *arguments], check=True, **native)
    (root / "config.toml").write_text(
        f'[source]\nrepo="{repository.as_posix()}"\nref="main"\n', encoding="utf-8",
    )
    tools = tmp_path / "qualified-tools"
    _fake_tools(tools, uv_exit=0, python_exit=0)
    (tools / "fake-python.cmd").write_text(
        f'@echo off\n"{sys.executable}" %*\nexit /b %errorlevel%\n', encoding="utf-8",
    )
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "uv.ps1").write_text("throw 'PowerShell-only UV was selected'\n", encoding="utf-8")
    (shadow / "uv.cmd").write_text("@exit /b 19\n", encoding="utf-8")
    script = (
        "function Invoke-RestMethod { throw 'Unexpected prerequisite acquisition' }; "
        f"& {_quote(BOOTSTRAP)} --version; exit $LASTEXITCODE"
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30, **native,
        env={
            **os.environ, "WORKTREE_MANAGER_ROOT": str(root),
            "PATH": os.pathsep.join((str(shadow), str(tools), os.environ.get("PATH", ""))),
            "USERPROFILE": str(tmp_path / "profile"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "uv": str(tools / "uv.cmd"), "version": "uv 0.13.7",
    }


@pytest.mark.parametrize("previous", [None, "different-worker-selection"])
def test_installation_python_scope_restores_the_hint_after_failure(
    monkeypatch: pytest.MonkeyPatch, previous: str | None,
) -> None:
    from worktree_manager import self_install

    if previous is None:
        monkeypatch.delenv(KEY, raising=False)
    else:
        monkeypatch.setenv(KEY, previous)
    with pytest.raises(RuntimeError, match="controlled callback failure"):
        installer_factory: Callable[[], Any] = self_install._runtime_installer
        with installer_factory()._bootstrap_python_environment():
            assert os.environ[KEY] == getattr(sys, "_base_executable", sys.executable)
            raise RuntimeError("controlled callback failure")
    assert os.environ.get(KEY) == previous


@pytest.mark.parametrize("prepared", [False, True])
def test_request_python_uses_the_application_interpreter_and_restores_ambient_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prepared: bool,
) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    selected = str(Path(sys.executable).resolve())
    if prepared:
        monkeypatch.setenv(KEY, selected)
    else:
        monkeypatch.delenv(KEY, raising=False)
    context = manager.request_context(capture_engine=False)
    assert KEY not in context

    monkeypatch.setenv(KEY, "different-worker-selection")
    with pytest.raises(RuntimeError, match="controlled callback failure"):
        with manager._request_environment(context):
            assert os.environ[KEY] == getattr(sys, "_base_executable", sys.executable)
            raise RuntimeError("controlled callback failure")
    assert os.environ[KEY] == "different-worker-selection"


def test_real_queued_children_do_not_inherit_the_draining_workers_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    queue = tmp_path / "queue"
    selected = str(Path(sys.executable).resolve())
    program = f"""
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from worktree_manager.update_driver_cli import _request_environment
report={{"before":os.environ.get({KEY!r}),
        "interpreter":getattr(sys,"_base_executable",sys.executable)}}
with _request_environment({{}}):
    report["during"]=os.environ.get({KEY!r})
report["after"]=os.environ.get({KEY!r})
Path(sys.argv[1]).write_text(json.dumps(report),encoding="utf-8")
"""
    for prepared in (True, False):
        if prepared:
            monkeypatch.setenv(KEY, selected)
        else:
            monkeypatch.delenv(KEY, raising=False)
        context = manager.request_context(capture_engine=False)
        report = tmp_path / f"child-{prepared}.json"
        request = update_driver.enqueue(
            update_driver.RequestSpec(
                backend="manager", argv=(str(prepared),), cwd=str(tmp_path),
                source="prepared-python-context", context=tuple(context.items()),
            ),
            runner=update_driver.TrustedRunner(
                (sys.executable, "-I", "-S", "-B", "-c", program, str(report),
                 str(Path(__file__).resolve().parents[1] / "src")),
                str(tmp_path), context_keys=manager._RUNNER_CONTEXT_KEYS,
            ),
            root=queue,
        )
        monkeypatch.setenv(KEY, "different-worker-selection")
        assert update_driver.drain(root=queue) == 0
        completed = update_driver.status(request.id, root=queue)
        assert not isinstance(completed, tuple)
        assert completed.state == "completed" and completed.exit_code == 0
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["before"] is None and result["after"] is None
        assert result["during"] == result["interpreter"]
        assert Path(result["during"]).resolve() == Path(
            getattr(sys, "_base_executable", sys.executable),
        ).resolve()
        assert os.environ[KEY] == "different-worker-selection"


@pytest.mark.parametrize("configured", [True, False])
def test_real_queued_toolchain_preflight_uses_caller_not_drainer_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: bool,
) -> None:
    index = "https://caller.example.invalid/simple/"
    home, queue, root = tmp_path / "caller", tmp_path / "queue", tmp_path / "manager"
    home.mkdir()
    config, pip = home / "toolchain.conf", home / "selected-pip.ini"
    config.write_text(f"schema=1\npython_index_url={index}\n", encoding="ascii")
    pip.write_text("[global]\ntimeout = 73\n", encoding="utf-8")
    for key, path in {
        "HOME": home, "USERPROFILE": home, "APPDATA": home / "appdata",
        "PROGRAMDATA": home / "programdata", "XDG_CONFIG_HOME": home / ".config",
        "XDG_CONFIG_DIRS": home / "global-config", "WORKTREE_MANAGER_ROOT": root,
    }.items():
        monkeypatch.setenv(key, str(path))
    selectors = (
        "COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG", "PIP_CONFIG_FILE",
        "UV_DEFAULT_INDEX", "PIP_INDEX_URL", "UV_CONFIG_FILE", "UV_NO_CONFIG",
    )
    for key in (*selectors, "UV_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "PIP_EXTRA_INDEX_URL",
                "UV_FIND_LINKS", "PIP_FIND_LINKS", "PIP_TRUSTED_HOST", "PIP_NO_INDEX",
                "UV_INSECURE_HOST", "UV_NO_INDEX", "VIRTUAL_ENV"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(selectors[0], str(config))
    monkeypatch.delenv("npm_config_userconfig", raising=False)
    if configured:
        for key, value in {
            "PIP_CONFIG_FILE": str(pip), "npm_config_userconfig": str(home / ".npmrc"),
            "UV_DEFAULT_INDEX": index, "PIP_INDEX_URL": index,
        }.items():
            monkeypatch.setenv(key, value)
    context = manager.request_context(capture_engine=False, capture_toolchain=True)
    report = tmp_path / "toolchain-participant.json"
    program = """
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from worktree_manager import toolchain_config
keys=json.loads(sys.argv[3])
result={"before":{key:os.environ.get(key) for key in keys}}
toolchain_config.configure_selected(root=Path(os.environ["WORKTREE_MANAGER_ROOT"]),apply=True)
result["index"]=os.environ.get("PIP_INDEX_URL")
result["retained"]=os.environ.get("COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG")
result["npm"]=os.environ.get("npm_config_userconfig")
Path(sys.argv[1]).write_text(json.dumps(result),encoding="utf-8")
"""
    request = update_driver.enqueue(
        update_driver.RequestSpec(
            "manager", ("toolchain", str(configured)), cwd=str(tmp_path),
            source="toolchain-request-context", context=tuple(context.items()),
        ),
        runner=update_driver.TrustedRunner(
            (sys.executable, "-I", "-S", "-B", "-c", program, str(report),
             str(Path(__file__).resolve().parents[1] / "src"), json.dumps(selectors)),
            str(tmp_path), context_keys=manager._runner_context_keys(context),
        ), root=queue,
    )
    for key in selectors:
        monkeypatch.setenv(key, "foreign-drainer-selection")
    monkeypatch.setenv("npm_config_userconfig", "foreign-drainer-selection")
    assert update_driver.drain(root=queue) == 0
    completed = update_driver.status(request.id, root=queue)
    assert not isinstance(completed, tuple) and completed.state == "completed"
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["before"] == {key: context.get(key) for key in selectors}
    assert result["index"] == index
    assert result["retained"] == str(root / "toolchain.conf")
    assert result["npm"] == (str(home / ".npmrc") if configured else None)
    assert all(os.environ[key] == "foreign-drainer-selection" for key in selectors)
    if configured:
        assert index in pip.read_text(encoding="utf-8")
        assert "timeout = 73" in pip.read_text(encoding="utf-8")


def test_helper_base_interpreter_survives_application_environment_retirement(
    tmp_path: Path,
) -> None:
    application = tmp_path / "application"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(application)
    python = application / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    program = f"""
import json,os,sys
sys.path.insert(0,sys.argv[1])
from worktree_manager.update_driver_cli import _request_environment
with _request_environment({{}}):
    print(json.dumps(os.environ[{KEY!r}]))
"""
    environment = {**os.environ, "PATH": str(tmp_path)}
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW")} if os.name == "nt" else {}
    selected = subprocess.run(
        [str(python), "-I", "-S", "-B", "-c", program,
         str(Path(__file__).resolve().parents[1] / "src")],
        capture_output=True, text=True, timeout=20, env=environment, **options,
    )
    assert selected.returncode == 0, selected.stdout + selected.stderr
    helper = Path(json.loads(selected.stdout))
    assert helper.is_absolute() and not helper.is_relative_to(application)
    shutil.rmtree(application)
    result = subprocess.run(
        [str(helper), "-I", "-S", "-B", "-c", "print('independent-helper')"],
        capture_output=True, text=True, timeout=20, env=environment, **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "independent-helper"


@pytest.mark.timeout(60)
def test_successor_resident_drops_updaters_hint_before_the_old_environment_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from _manager_process_fixture import synthetic_runtime
    from worktree_manager import mux_daemon_cutover

    host = shutil.which("pwsh") or shutil.which("powershell")
    if host is None:
        pytest.skip("PowerShell is unavailable")
    previous = tmp_path / "previous"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(previous)
    old_python = previous / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    root = tmp_path / "stable"
    root.mkdir()
    sdk = Path(__file__).resolve().parents[2] / "libs" / "installation-context" / "installation-context.ps1"
    base = Path(getattr(sys, "_base_executable", sys.executable))
    monkeypatch.setenv("PATH", str(base.parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv(KEY, str(old_python))
    monkeypatch.setenv("RESIDENT_PROBE_HOST", host)
    monkeypatch.setenv("RESIDENT_PROBE_SDK", str(sdk))
    slot = synthetic_runtime(root, "successor", program=f"""
import json,os,subprocess,sys,time
from pathlib import Path
from agent_procutil import no_window_kwargs
root=Path(next(value.split("=",1)[1] for value in sys.argv if value.startswith("--root=")))
pending=root/"ready.tmp"
pending.write_text(json.dumps({{"hint":os.environ.get({KEY!r})}}),encoding="utf-8")
pending.replace(root/"ready.json")
deadline=time.monotonic()+15
while not (root/"old-retired").exists():
    if time.monotonic()>=deadline:
        raise TimeoutError("old environment retirement was not signalled")
    time.sleep(0.01)
result=subprocess.run([
    os.environ["RESIDENT_PROBE_HOST"],"-NoProfile","-NonInteractive","-File",
    os.environ["RESIDENT_PROBE_SDK"],"source-id","-SourceJson",
    '{{"source":"opaque","id":"resident"}}',"-CopilotHome",str(root/"copilot"),
    "-DurableHome",str(root/"durable"),
],capture_output=True,text=True,timeout=15,**no_window_kwargs())
(root/"result.json").write_text(json.dumps({{
    "code":result.returncode,"stdout":result.stdout,"stderr":result.stderr,
}}),encoding="utf-8")
raise SystemExit(result.returncode)
""")
    process = mux_daemon_cutover.spawn_passive(slot, root=root, port=54321)
    try:
        ready = root / "ready.json"
        deadline = time.monotonic() + 15
        while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.is_file(), (root / "mux-daemon-passive.log").read_text(encoding="utf-8")
        status = json.loads(ready.read_text(encoding="utf-8"))
        shutil.rmtree(previous)
        (root / "old-retired").touch()
        assert process.wait(timeout=20) == 0, (root / "mux-daemon-passive.log").read_text(encoding="utf-8")
        result = json.loads((root / "result.json").read_text(encoding="utf-8"))
        assert status["hint"] is None
        assert result["code"] == 0 and result["stderr"] == "", result
        assert json.loads(result["stdout"])["canonical"] == "opaque:resident"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
