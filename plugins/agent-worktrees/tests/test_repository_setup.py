"""Revision-bound setup with a real Git source and native script consumer."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees import repository_setup as setup

URL = "https://example.invalid/team/sample.git"
pytestmark = pytest.mark.timeout(180)


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True,
        **no_window_kwargs(),
    ).stdout.strip()


def commit(path: Path) -> None:
    git(path, "add", ".")
    git(path, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture", "--no-verify")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "source with spaces"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "core.autocrlf", "false")
    git(path, "remote", "add", "origin", URL)
    (path / ".agent-worktrees").mkdir()
    (path / setup.DECLARATION).write_text(json.dumps({
        "version": 1, "entrypoints": {
            "windows": {"runtime": "powershell", "script": "setup.ps1"},
            "posix": {"runtime": "bash", "script": "setup.sh"},
        },
    }), encoding="utf-8")
    (path / "setup.ps1").write_text("param([string]$RequestFile)\n", encoding="utf-8")
    (path / "setup.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    commit(path)
    return path


@pytest.mark.parametrize("url", [
    "", "sample", "http://example.invalid/a", "ssh://example.invalid/a",
    "https://name:secret@example.invalid/a", "https://name@example.invalid/a",
    "https://example.invalid/a?", "https://example.invalid/a#",
    "https://example.invalid/a/../b", "https://example.invalid/",
    "https://example.invalid:broken/a", "https://example.invalid/a\n",
    "https://bad%host/a", "https://example.invalid/a\\b",
    "https://example.invalid/a\u009b",
])
def test_rejects_unsupported_urls(url: str) -> None:
    with pytest.raises(setup.SetupError):
        setup.https_url(url)


def test_url_identity_is_canonical() -> None:
    assert setup.https_url("https://EXAMPLE.invalid:443/team/sample.git/") == (
        "https://example.invalid/team/sample.git"
    )
    assert setup.https_url("https://example.invalid:8443/a") == "https://example.invalid:8443/a"
    assert setup.https_url(URL) != setup.https_url(URL.removesuffix(".git"))


def test_inspect_is_read_only_and_revision_bound(source: Path) -> None:
    before = git(source, "status", "--porcelain")
    selected = setup.inspect(source, URL)
    assert selected.source.commit == git(source, "rev-parse", "HEAD")
    assert selected.source.url == URL
    selected.verify()
    assert git(source, "status", "--porcelain") == before == ""
    (source / "setup.ps1").write_text("changed", encoding="utf-8")
    with pytest.raises(setup.SetupError, match="source changes"):
        selected.verify()


@pytest.mark.parametrize("kind", ["foreign", "transport-path", "untracked", "modified", "linked", "nested"])
def test_preserves_unqualified_source(source: Path, kind: str) -> None:
    selected = source
    if kind == "foreign":
        git(source, "remote", "set-url", "origin", "https://example.invalid/other/sample")
    elif kind == "transport-path":
        git(source, "remote", "set-url", "origin", URL.removesuffix(".git"))
    elif kind == "untracked":
        (source / "precious.txt").write_text("preserve", encoding="utf-8")
    elif kind == "modified":
        (source / "setup.ps1").write_text("preserve", encoding="utf-8")
    elif kind == "linked":
        selected = source.parent / "linked"
        git(source, "worktree", "add", "-q", "-b", "other", str(selected))
    else:
        selected = source / ".agent-worktrees"
    before = git(source, "status", "--porcelain")
    with pytest.raises(setup.SetupError):
        setup.inspect(selected, URL)
    assert git(source, "status", "--porcelain") == before


@pytest.mark.parametrize("declaration", [
    '{"version":1,"version":1,"entrypoints":{}}',
    '{"version":true,"entrypoints":{}}',
    '{"version":2,"entrypoints":{}}',
    '{"version":1,"entrypoints":{},"extra":1}',
    '{"version":1,"entrypoints":{"other":{}}}',
    '{"version":1,"entrypoints":{"windows":{"runtime":"cmd","script":"setup.ps1"}}}',
    '{"version":1,"entrypoints":{"windows":{"runtime":"powershell","script":"../outside"}}}',
    '{"version":1,"entrypoints":{"windows":{"runtime":"powershell","script":"C:/outside"}}}',
    '{"version":1,"entrypoints":{"windows":{"runtime":"powershell","script":"missing"}}}',
    "[]", "not json", " " * (setup.LIMIT + 1),
])
def test_rejects_malformed_tracked_declarations(source: Path, declaration: str) -> None:
    (source / setup.DECLARATION).write_text(declaration, encoding="utf-8")
    commit(source)
    with pytest.raises(setup.SetupError):
        setup.inspect(source, URL)


def test_never_executes_without_trust(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected = setup.inspect(source, URL)
    monkeypatch.setattr(setup, "_call", lambda *_: pytest.fail("untrusted execution"))
    assert setup.run(selected, apply=True, trusted=False).status == "refused"


@pytest.mark.parametrize("refusal", ["apply", "commit", "abbreviation", "remote", "home"])
def test_native_configuration_trust_refuses_unqualified_effects(
    source: Path, monkeypatch, capsys, refusal,
) -> None:
    from agent_worktrees import permissions

    args = argparse.Namespace(
        repo=source, repository=URL, commit=git(source, "rev-parse", "HEAD"), apply=True,
    )
    if refusal == "apply":
        args.apply = False
    elif refusal == "commit":
        args.commit = "0" * 40
    elif refusal == "abbreviation":
        args.commit = args.commit[:8]
    elif refusal == "remote":
        args.repository = "https://example.invalid/another/repository"
    else:
        monkeypatch.setenv("COPILOT_HOME", str(source.parent / "another-state"))
    monkeypatch.setattr(
        permissions, "ensure_trusted_folder",
        lambda *_: pytest.fail("unqualified native configuration was trusted"),
    )
    assert setup.cmd_trust_configuration(args) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert git(source, "status", "--porcelain") == ""


@pytest.mark.parametrize("home_lookup", [False, True])
def test_native_configuration_trust_preserves_config_and_is_idempotent(
    source: Path, tmp_path, monkeypatch, capsys, home_lookup,
) -> None:
    if home_lookup:
        from agent_worktrees import permissions
        monkeypatch.setattr(permissions.platform, "system", lambda: "Linux")
    home = tmp_path / "home"
    copilot = home / ".copilot"
    copilot.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_HOME", str(copilot))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    config = copilot / "config.json"
    config.write_text(json.dumps({"trustedFolders": ["existing"], "unrelated": True}))
    args = argparse.Namespace(
        repo=source, repository=URL, commit=git(source, "rev-parse", "HEAD"), apply=True,
    )
    for changed in (True, False):
        assert setup.cmd_trust_configuration(args) == 0
        assert json.loads(capsys.readouterr().out)["changed"] is changed
        assert json.loads(config.read_text()) == {
            "trustedFolders": ["existing", str(source.resolve())], "unrelated": True,
        }
    assert git(source, "status", "--porcelain") == ""


@pytest.mark.parametrize("phase", ["before", "after"])
def test_configuration_trust_git_timeout_retains_structured_effect_state(
    source: Path, tmp_path, monkeypatch, capsys, phase,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    args = argparse.Namespace(
        repo=source, repository=URL, commit=git(source, "rev-parse", "HEAD"), apply=True,
    )
    real_qualify = setup.qualify
    calls = 0

    def qualify(path, url):
        nonlocal calls
        calls += 1
        if calls == (1 if phase == "before" else 2):
            raise subprocess.TimeoutExpired(["git", "qualification"], 30)
        return real_qualify(path, url)

    monkeypatch.setattr(setup, "qualify", qualify)
    assert setup.cmd_trust_configuration(args) == 1
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["status"] == "refused"
    assert outcome["changed"] == (True if phase == "after" else None)
    config = home / ".copilot" / "config.json"
    assert config.exists() is (phase == "after")
    if phase == "after":
        assert json.loads(config.read_text())["trustedFolders"] == [str(source.resolve())]


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_cannot_hide_modified_entry_in_index_flags(source: Path, flag: str) -> None:
    git(source, "update-index", flag, "setup.ps1")
    (source / "setup.ps1").write_text("hidden modification", encoding="utf-8")
    with pytest.raises(setup.SetupError, match="index hides"):
        setup.inspect(source, URL)


def test_ignored_pathspec_cannot_impersonate_tracked_script(source: Path) -> None:
    declaration = json.loads((source / setup.DECLARATION).read_text())
    declaration["entrypoints"]["windows"]["script"] = "setu[p].ps1"
    (source / setup.DECLARATION).write_text(json.dumps(declaration), encoding="utf-8")
    (source / ".gitignore").write_text("setu[[]p].ps1\n", encoding="utf-8")
    commit(source)
    ignored = source / "setu[p].ps1"
    ignored.write_text("throw 'Untracked entry executed'\n", encoding="utf-8")
    assert git(source, "status", "--porcelain") == ""
    with pytest.raises(setup.SetupError, match="tracked as a regular file"):
        setup.inspect(source, URL)


def test_tracked_glob_characters_are_literal_entries(source: Path) -> None:
    (source / "setu[p].ps1").write_text("param([string]$RequestFile)\n", encoding="utf-8")
    declaration = json.loads((source / setup.DECLARATION).read_text())
    declaration["entrypoints"]["windows"]["script"] = "setu[p].ps1"
    (source / setup.DECLARATION).write_text(json.dumps(declaration), encoding="utf-8")
    commit(source)
    assert setup._tracked(source, "setu[p].ps1") == source / "setu[p].ps1"


@pytest.mark.parametrize("control", ["\x00", "\x1b", "\t", "\n", "\r", "\x7f", "\u009b", "\u009d"])
def test_control_characters_refuse_before_path_lookup(tmp_path, monkeypatch, control) -> None:
    monkeypatch.setattr(setup, "_git", lambda *a: pytest.fail("untrusted control path inspected"))
    with pytest.raises(setup.SetupError, match="repository-relative regular file"):
        setup._tracked(tmp_path, f"setup{control}.sh")


def consumer(source: Path, body: str) -> setup.Setup:
    """Native Windows coverage; POSIX uses the equivalent argv/file consumer."""
    if sys.platform == "win32":
        if not (shutil.which("pwsh") or shutil.which("powershell")):
            pytest.skip("Native PowerShell unavailable")
        script = source / "setup.ps1"
        script.write_text(
            "param([Parameter(Mandatory=$true)][string]$RequestFile)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "$r = Get-Content -LiteralPath $RequestFile -Raw | ConvertFrom-Json\n"
            "$state = Join-Path (Split-Path $r.repository.path -Parent) 'state.txt'\n"
            "$log = Join-Path (Split-Path $r.repository.path -Parent) 'calls.txt'\n"
            "Add-Content -LiteralPath $log -Value $r.mode\n" + body,
            encoding="utf-8",
        )
    else:
        pytest.skip("Windows script case; POSIX native consumer covered separately")
    commit(source)
    return setup.inspect(source, URL)


def test_real_setup_child_uses_selected_engine_for_native_trust_and_payload_observation(
    source: Path, tmp_path, monkeypatch,
) -> None:
    home = tmp_path / "selected-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    monkeypatch.setenv(setup.ENGINE_CONTEXT, "unqualified inherited invocation")
    monkeypatch.setenv(setup.ENGINE_PYTHON, "unqualified inherited Python")
    (source / "new-admission-v1.json").write_text(
        json.dumps({"contract": "repository-impostor", "required_applications": 77}),
        encoding="utf-8",
    )
    selected = consumer(source, """
$engine = $env:AGENT_WORKTREES_REPOSITORY_SETUP_ENGINE | ConvertFrom-Json
if ($engine.schema -ne 'copilot-extensions.repository-setup-engine' -or
    $engine.version -ne 1 -or -not [IO.Path]::IsPathRooted($engine.argv[0])) {
    throw 'Missing selected engine contract'
}
$python = $env:AGENT_WORKTREES_REPOSITORY_SETUP_PYTHON
if ($python -ne $engine.argv[0]) { throw 'Selected interpreter differs' }
$admission = $engine.capabilities.new_admission
if ($admission.contract -ne 'agent-worktrees.new-admission' -or
    $admission.version -ne 1 -or $admission.required_applications -ne 1 -or
    $admission.configuration -ne 'require_application_readiness' -or
    $admission.read_only -ne $true) {
    throw 'Executing engine did not supply its New admission contract'
}
if (($r.PSObject.Properties.Name | Sort-Object) -join ',' -ne
    'mode,repository,result_file,version') { throw 'Request v1 changed' }
$arguments = @($engine.argv | Select-Object -Skip 1)
if ($r.mode -eq 'apply') {
    $trusted = & $python @arguments trust-repository-configuration `
        --repo $r.repository.path --repository $r.repository.url `
        --commit $r.repository.commit --apply | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $trusted.status -ne 'ready') { throw 'Trust failed' }
    $payloads = & $python @arguments prepare-plugin-payloads `
        --repo $r.repository.path | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $payloads.payloadsReady) { throw 'Observation failed' }
    Set-Content -LiteralPath $state -Value 'done'
}
$status = if (Test-Path -LiteralPath $state) { 'ready' } else { 'needed' }
@{version=1;status=$status;message='Selected engine consumer checked'} |
    ConvertTo-Json -Compress | Set-Content -LiteralPath $r.result_file -Encoding utf8
""")
    assert setup.run(selected, apply=True, trusted=True).status == "ready"
    config = json.loads((home / ".copilot" / "config.json").read_text())
    assert config["trustedFolders"] == [str(source.resolve())]
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check", "apply", "check"]
    assert git(source, "status", "--porcelain") == ""


def test_real_consumer_check_apply_final_check(source: Path) -> None:
    selected = consumer(source, """
if ($r.mode -eq 'apply') { Set-Content -LiteralPath $state -Value 'done' }
$s = if (Test-Path -LiteralPath $state) { 'ready' } else { 'needed' }
@{version=1;status=$s;message='Repository owner checked'} |
    ConvertTo-Json -Compress | Set-Content -LiteralPath $r.result_file -Encoding utf8
""")
    outcome = setup.run(selected, apply=True, trusted=True)
    assert outcome.status == "ready"
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check", "apply", "check"]


@pytest.mark.parametrize("dont_write_bytecode", [False, True])
def test_selected_engine_callback_preserves_caller_bytecode_policy(
    source: Path, tmp_path: Path, monkeypatch, dont_write_bytecode: bool,
) -> None:
    probe = tmp_path / "cold callback"
    probe.mkdir()
    (probe / "sample.py").write_text("value = 42\n", encoding="utf-8")
    callback = source / "callback.py"
    callback.write_text("""
import json, os, pathlib, subprocess, sys
from agent_procutil import no_window_kwargs
request = json.loads(pathlib.Path(sys.argv[1]).read_text())
engine = json.loads(os.environ["AGENT_WORKTREES_REPOSITORY_SETUP_ENGINE"])
assert engine["argv"][-2:] == ["-m", "agent_worktrees"]
probe = pathlib.Path(request["repository"]["path"]).parent / "cold callback"
code = (
    "import json,pathlib,sys; sys.path.insert(0,sys.argv[1]); import sample; "
    "print(json.dumps({'disabled':sys.dont_write_bytecode,'value':sample.value}))"
)
completed = subprocess.run(
    [*engine["argv"][:-2], "-c", code, str(probe)],
    check=True, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
)
(probe / "observation.json").write_text(completed.stdout)
pathlib.Path(request["result_file"]).write_text(json.dumps({
    "version": 1, "status": "ready", "message": "Callback checked",
}))
""", encoding="utf-8")
    if sys.platform == "win32":
        selected = consumer(source, """
& $env:AGENT_WORKTREES_REPOSITORY_SETUP_PYTHON `
    (Join-Path $r.repository.path 'callback.py') $RequestFile
if ($LASTEXITCODE -ne 0) { throw 'Callback failed' }
""")
    else:
        (source / "setup.sh").write_text(
            f"#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} "
            f"{shlex.quote(str(callback))} \"$2\"\n", encoding="utf-8",
        )
        commit(source)
        selected = setup.inspect(source, URL)
    monkeypatch.setattr(sys, "dont_write_bytecode", dont_write_bytecode)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    assert setup.run(selected, apply=False, trusted=True).status == "ready"
    assert json.loads((probe / "observation.json").read_text()) == {
        "disabled": dont_write_bytecode, "value": 42,
    }
    assert bool(list(probe.rglob("*.pyc"))) is (not dont_write_bytecode)
    assert git(source, "status", "--porcelain") == ""


def test_redirected_consumer_cannot_inherit_attended_git_prompt(source: Path, monkeypatch) -> None:
    selected = consumer(source, """
if ($env:GIT_TERMINAL_PROMPT -ne '0') { throw 'Redirected caller enabled a terminal prompt' }
if ($env:GCM_INTERACTIVE -ne '0' -or $env:GCM_GUI_PROMPT -ne '0') {
    throw 'Redirected caller enabled credential-owner interaction'
}
if ($env:GIT_ASKPASS -or $env:SSH_ASKPASS) { throw 'Redirected caller inherited askpass' }
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"ready","message":"Owner checked"}')
""")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    monkeypatch.setenv("GCM_INTERACTIVE", "1")
    monkeypatch.setenv("GCM_GUI_PROMPT", "1")
    monkeypatch.setenv("GIT_ASKPASS", "must-not-run")
    monkeypatch.setenv("SSH_ASKPASS", "must-not-run")
    assert setup.run(selected, apply=False, trusted=True).status == "ready"
    assert os.environ["GIT_TERMINAL_PROMPT"] == "1"
    assert os.environ["GCM_INTERACTIVE"] == "1"
    assert os.environ["GIT_ASKPASS"] == "must-not-run"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows PowerShell fallback")
def test_trusted_windows_powershell_uses_only_process_policy(
    source: Path, tmp_path: Path, monkeypatch,
) -> None:
    powershell = shutil.which("powershell")
    assert powershell is not None
    selected = consumer(source, """
if ((Get-ExecutionPolicy -Scope Process) -ne 'Bypass') { throw 'Wrong process policy' }
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"ready","message":"Owner checked"}')
""")
    monkeypatch.setenv("PSExecutionPolicyPreference", "Restricted")
    monkeypatch.setattr(setup, "_interpreter", lambda runtime: powershell)
    marker = tmp_path / "not-executed.txt"
    control = tmp_path / "policy-control.ps1"
    control.write_text(
        "[IO.File]::WriteAllText('" + str(marker).replace("'", "''") + "', 'ran')\n",
        encoding="utf-8",
    )
    refused = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-File", str(control)],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert refused.returncode != 0 and not marker.exists()
    before = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-Command",
         "Get-ExecutionPolicy -List | ConvertTo-Json -Compress"],
        capture_output=True, text=True, check=True, **no_window_kwargs(),
    ).stdout
    assert setup.run(selected, apply=False, trusted=True).status == "ready"
    after = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-Command",
         "Get-ExecutionPolicy -List | ConvertTo-Json -Compress"],
        capture_output=True, text=True, check=True, **no_window_kwargs(),
    ).stdout
    assert before == after
    assert os.environ["PSExecutionPolicyPreference"] == "Restricted"


def test_real_consumer_accepts_caller_scratch_linked_ancestor(source: Path, monkeypatch) -> None:
    selected = consumer(source, """
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"ready","message":"Owner checked"}')
""")
    physical = source.parent / "physical-scratch"
    physical.mkdir()
    logical = source.parent / "logical-scratch"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(physical), str(logical))
    else:
        logical.symlink_to(physical, target_is_directory=True)
    monkeypatch.setattr(setup.tempfile, "tempdir", str(logical))
    assert setup.run(selected, apply=False, trusted=True).status == "ready"
    assert not list(physical.iterdir())


@pytest.mark.skipif(sys.platform != "win32", reason="native inherited Windows console")
def test_native_attended_clone_and_setup_preserve_console(source: Path, tmp_path: Path) -> None:
    python = getattr(sys, "_base_executable", sys.executable).replace("'", "''")
    probe = (
        "import ctypes,json; p=(ctypes.c_ulong*64)(); "
        "n=ctypes.windll.kernel32.GetConsoleProcessList(p,64); "
        "assert 0<n<=64; print(json.dumps(list(p)[:n]))"
    )
    consumer(source, f"""
$members = & '{python}' -I -S -c '{probe}'
if ($LASTEXITCODE -ne 0) {{ throw 'Setup descendant lost console' }}
[IO.File]::WriteAllText($state, [string]$members)
[IO.File]::WriteAllText($r.result_file, '{{"version":1,"status":"ready","message":"Console retained"}}')
""")
    driver = tmp_path / "attended.py"
    driver.write_text("""
import ctypes, json, msvcrt, os, pathlib, subprocess, sys
sys.stderr = open(pathlib.Path(sys.argv[3]).with_suffix('.error'), 'w')
sys.path[:0] = json.loads(pathlib.Path(sys.argv[1]).read_text())
from agent_worktrees import repository_bootstrap as bootstrap, repository_setup as setup
root, result = map(pathlib.Path, sys.argv[2:])
sys.stdin = open('CONIN$', 'r')
sys.stdout = open('CONOUT$', 'w')
os.dup2(sys.stdin.fileno(), 0)
os.dup2(sys.stdout.fileno(), 1)
set_handle = ctypes.windll.kernel32.SetStdHandle
set_handle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
assert set_handle(-10, msvcrt.get_osfhandle(sys.stdin.fileno()))
assert set_handle(-11, msvcrt.get_osfhandle(sys.stdout.fileno()))
cp = ctypes.windll.kernel32.GetConsoleCP()
assert cp and setup._terminal()
original = subprocess.run
native_python = getattr(sys, '_base_executable', sys.executable)
probe = ('import ctypes,json; p=(ctypes.c_ulong*64)(); '
         'n=ctypes.windll.kernel32.GetConsoleProcessList(p,64); '
         'assert 0<n<=64; print(json.dumps(list(p)[:n]))')
control = original([native_python, '-I', '-S', '-c', probe],
                   capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
assert control.returncode == 0, control.stderr
assert os.getpid() not in json.loads(control.stdout)
clone_result = result.with_suffix('.clone')
def run(argv, **kwargs):
    if 'clone' in argv:
        assert kwargs['stdin'] == 0 and kwargs['stdout'] == 1
        assert not kwargs.get('creationflags', 0) & subprocess.CREATE_NO_WINDOW
        argv = [native_python, '-I', '-S', '-c', probe +
                '; import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('
                'json.dumps(list(p)[:n]))', str(clone_result)]
    return original(argv, **kwargs)
bootstrap.subprocess.run = run
bootstrap._clone('https://example.invalid/team/sample.git', root.parent / 'clone', [])
assert os.getpid() in json.loads(clone_result.read_text())
outcome = setup.run(setup.inspect(root, 'https://example.invalid/team/sample.git'),
                    apply=False, trusted=True)
assert outcome.status == 'ready', outcome
assert os.getpid() in json.loads((root.parent / 'state.txt').read_text())
result.write_text(json.dumps({'console': cp, 'clone': True, 'setup': True}))
""", encoding="utf-8")
    result = tmp_path / "attended.json"
    paths = tmp_path / "paths.json"
    paths.write_text(json.dumps(sys.path), encoding="utf-8")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    observer = runpy.run_path(str(
        Path(__file__).resolve().parents[3] / "libs" / "agent-procutil" / "tests"
        / "test_spawn_daemon_windows_integration.py"
    ))
    with observer["_WindowObserver"](*observer["_configure_win32"]()) as windows:
        process = subprocess.run(
            [sys.executable, str(driver), str(paths), str(source), str(result)],
            capture_output=True, text=True, timeout=120, startupinfo=startup,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    assert windows.surfaced == {} and windows.focus_changes == {}
    error = result.with_suffix(".error")
    diagnostics = error.read_text() if error.exists() else process.stdout + process.stderr
    assert process.returncode == 0, diagnostics
    assert result.is_file(), diagnostics
    evidence = json.loads(result.read_text())
    assert evidence["console"] and evidence["clone"] and evidence["setup"]


def test_redirected_launch_remains_headless(monkeypatch) -> None:
    monkeypatch.setattr(setup, "_terminal", lambda: False)
    expected = no_window_kwargs()
    if os.name == "nt":
        expected.update(stdin=0, stdout=1, stderr=2)
    assert setup._stdio_kwargs() == expected


@pytest.mark.parametrize("status", ["waiting", "refused", "failed", "ready"])
def test_real_consumer_terminal_check_never_applies(source: Path, status: str) -> None:
    selected = consumer(source, f"""
[IO.File]::WriteAllText($r.result_file,
    '{{"version":1,"status":"{status}","message":"Owner status"}}')
""")
    assert setup.run(selected, apply=True, trusted=True).status == status
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check"]


@pytest.mark.parametrize("result", [
    '{"version":1,"status":"ready","status":"ready","message":"duplicate"}',
    '{"version":1,"status":"unknown","message":"unknown"}',
    '{"version":1,"status":"ready","message":""}',
    '{"version":1,"status":"ready","message":"ok","extra":true}',
    '{"version":true,"status":"ready","message":"ok"}',
    '{"version":1,"status":"ready","message":"\\u009b31m"}',
    "[]", "bad", "x" * (setup.LIMIT + 1),
])
def test_real_consumer_cannot_fake_success(source: Path, result: str) -> None:
    literal = result.replace("'", "''")
    selected = consumer(source, f"[IO.File]::WriteAllText($r.result_file, '{literal}')\n")
    with pytest.raises(setup.SetupError):
        setup.run(selected, apply=True, trusted=True)


def test_real_consumer_nonzero_overrides_ready(source: Path) -> None:
    selected = consumer(source, """
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"ready","message":"not proof"}')
exit 7
""")
    assert setup.run(selected, apply=True, trusted=True).status == "failed"


def test_real_consumer_source_drift_requires_new_trust(source: Path) -> None:
    selected = consumer(source, """
Add-Content -LiteralPath (Join-Path $r.repository.path 'setup.ps1') -Value '# changed'
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"ready","message":"not proof"}')
""")
    with pytest.raises(setup.SetupError, match="source changes"):
        setup.run(selected, apply=True, trusted=True)


def test_real_consumer_missing_result_is_unknown_not_success(source: Path) -> None:
    selected = consumer(source, "exit 0\n")
    with pytest.raises(setup.SetupError, match="no result"):
        setup.run(selected, apply=True, trusted=True)


def test_real_consumer_check_only_does_not_apply(source: Path) -> None:
    selected = consumer(source, """
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"needed","message":"Owner needs setup"}')
""")
    assert setup.run(selected, apply=False, trusted=True).status == "needed"
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check"]


def test_real_consumer_interrupted_apply_is_not_replayed(source: Path) -> None:
    selected = consumer(source, """
if ($r.mode -eq 'apply') { exit 9 }
[IO.File]::WriteAllText($r.result_file, '{"version":1,"status":"needed","message":"Check first"}')
""")
    assert setup.run(selected, apply=True, trusted=True).status == "failed"
    assert setup.run(selected, apply=False, trusted=True).status == "needed"
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check", "apply", "check"]


@pytest.mark.skipif(sys.platform == "win32", reason="Native POSIX subprocess contract")
def test_real_posix_consumer_check_apply_final_check(source: Path, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_HOME", str(Path.home() / ".copilot"))
    monkeypatch.setenv(setup.ENGINE_CONTEXT, "unqualified inherited invocation")
    (source / "new-admission-v1.json").write_text(
        json.dumps({"contract": "repository-impostor", "required_applications": 77}),
        encoding="utf-8",
    )
    body = """
import json, os, pathlib, subprocess, sys
assert sys.argv[1] == '--request-file'
r = json.loads(pathlib.Path(sys.argv[2]).read_text())
assert set(r) == {'version', 'mode', 'repository', 'result_file'}
engine = json.loads(os.environ['AGENT_WORKTREES_REPOSITORY_SETUP_ENGINE'])
assert engine['schema'] == 'copilot-extensions.repository-setup-engine'
assert engine['version'] == 1
assert engine['argv'][0] == sys.executable
assert os.environ['AGENT_WORKTREES_REPOSITORY_SETUP_PYTHON'] == engine['argv'][0]
assert engine['capabilities']['new_admission'] == {
    'contract': 'agent-worktrees.new-admission', 'version': 1,
    'required_applications': 1, 'configuration': 'require_application_readiness',
    'read_only': True,
}
root = pathlib.Path(r['repository']['path'])
assert pathlib.Path.cwd() == root
state = root.parent / 'state.txt'
with (root.parent / 'calls.txt').open('a') as log:
    log.write(r['mode'] + '\\n')
if r['mode'] == 'apply':
    subprocess.run([
        *engine['argv'], 'trust-repository-configuration', '--repo', str(root),
        '--repository', r['repository']['url'], '--commit', r['repository']['commit'],
        '--apply',
    ], check=True)
    observed = subprocess.run([
        *engine['argv'], 'prepare-plugin-payloads', '--repo', str(root),
    ], check=True, capture_output=True, text=True)
    assert json.loads(observed.stdout)['payloadsReady']
    state.write_text('done')
pathlib.Path(r['result_file']).write_text(json.dumps({
    'version': 1, 'status': 'ready' if state.exists() else 'needed', 'message': 'Owner checked'
}))
"""
    (source / "setup.sh").write_text(
        f"#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} -c {shlex.quote(body)} \"$@\"\n",
        encoding="utf-8",
    )
    commit(source)
    assert setup.run(setup.inspect(source, URL), apply=True, trusted=True).status == "ready"
    assert (source.parent / "calls.txt").read_text().splitlines() == ["check", "apply", "check"]
