"""Finite synthetic CLI and native-shell checks; no fleet or live forge."""

import base64
import json
import os
import subprocess
import sys
import time

import pytest
from agent_procutil.owned_process import run_owned

from agent_worktrees import locks, pr_sweep_transport as transport


@pytest.mark.parametrize("adapter", [False, True])
def test_real_cli_process_observes_original_root_without_mutating_tracking(tmp_path, adapter):
    script = r"""
import hashlib,json,pathlib,sys
from agent_worktrees import config as cfg,installer,obligations,pr_sweep,tracking
from agent_worktrees import __main__ as cli
root=pathlib.Path(sys.argv[1]);tdir=root/'host'/'worktrees';tdir.mkdir(parents=True)
config=cfg.Config(srcroot=str(root),machine='worker-a',platform='linux',repo_name='host',
                 repos={'host':cfg.RepoConfig(anchor=str(root),worktree_root=str(root/'checkouts'))})
record=tracking.create_new_record('root','worktree/root',str(root/'checkout'),'host','worker-a','linux',tdir)
record.resources=[tracking.ResourceClaim(kind='pr',ref='example/widgets#7',state=obligations.ACTIVE)]
tracking.save_record(record,tdir/'root.yaml')
cfg.load_config=lambda:config
cfg.project_dir=lambda name=None:root/'host'
installer.read_projects_registry=lambda **kwargs:{'projects':{'host':{}}}
pr_sweep.machine_identity.is_local_machine=lambda name,configuration:name=='worker-a'
pr_sweep.claims_find_cli._authority_resolver=lambda project:lambda slug,provider:'github.com'
pr_sweep.provider_inventory=lambda *args:dict(status='ok',complete=True,authority='github.com',
 checked_utc='2026-01-01T00:00:00Z',prs=[dict(number=7,url='https://github.com/example/widgets/pull/7',
 author='author-a',head_sha='a'*40,created_at='2026-01-01T00:00:00Z',title='Synthetic change')])
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.yaml')}
cli.front_door_cli.resolve_invocation_project=lambda *args:True
def broad_startup():
 raise AssertionError('The one-shot collector must not load the full command cluster')
if sys.argv[2]=='direct':
 cli._ensure_cluster_loaded=broad_startup
 arguments=['pr-sweep','example/widgets','--json']
else:
 arguments=['claims','find','pr','--repo','example/widgets','--state','all','--sweep-observation','--json']
result=cli.main(arguments)
after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.yaml')}
assert before==after
raise SystemExit(result)
"""
    result = run_owned(
        [sys.executable, "-c", script, str(tmp_path), "adapter" if adapter else "direct"],
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    if adapter:
        assert payload["schema"] == "copilot-extensions.repository-pr-sweep"
        assert payload["errors"] == []
        assert payload["claims"][0]["path"] == ["root"]
    else:
        assert payload["complete"] is True
        assert payload["summary"]["open_count"] == 1
        assert payload["prs"][0]["claimants"][0]["path"] == ["root"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows PowerShell command boundary")
@pytest.mark.parametrize("supported", [True, False])
def test_native_windows_quoted_catalog_path_and_no_provisioning(tmp_path, supported):
    command = tmp_path / "owner's catalog.ps1"
    payload = {
        "schema": "copilot-extensions.repository-pr-sweep", "version": 1,
        "repo": "example/widgets", "cell": "worker-a/windows",
        "checked_utc": "2026-01-01T00:00:00Z", "roots": [], "claims": [], "errors": [],
    }
    encoded_payload = base64.b64encode(json.dumps(payload).encode()).decode()
    refusal = "" if supported else "[Console]::Error.WriteLine('unrecognized arguments: --sweep-observation');exit 2\n"
    command.write_text(
        "if($env:AGENT_WORKTREES_NO_SELFPROVISION -ne '1'){exit 9}\n" +
        refusal +
        "Set-Content -LiteralPath (Join-Path $PSScriptRoot 'collector-ran') -Value 'ran'\n"
        "if(($args -join '|') -ne '-p|host|claims|find|pr|--repo|example/widgets|--state|all|--sweep-observation|--json'){exit 8}\n"
        f"[Console]::WriteLine([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_payload}')))\n"
        "exit 0\n",
        encoding="utf-8",
    )
    argv = transport.remote_command("powershell.exe", str(command), "host", "example/widgets").split()
    result = run_owned(argv, timeout=30)
    if not supported:
        assert result.returncode == 2
        assert not (tmp_path / "collector-ran").exists()
        return
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "collector-ran").is_file()
    assert transport.validate_observation(json.loads(result.stdout), repo="example/widgets") == payload


@pytest.mark.skipif(os.name == "nt", reason="native POSIX shell command boundary")
@pytest.mark.parametrize("supported", [True, False])
def test_native_posix_quoted_catalog_path_and_no_provisioning(tmp_path, supported):
    command = tmp_path / "owner's catalog"
    payload = {
        "schema": "copilot-extensions.repository-pr-sweep", "version": 1,
        "repo": "example/widgets", "cell": "worker-a/linux",
        "checked_utc": "2026-01-01T00:00:00Z", "roots": [], "claims": [], "errors": [],
    }
    refusal = "" if supported else "printf '%s\\n' 'unrecognized arguments: --sweep-observation' >&2; exit 2\n"
    command.write_text(
        "#!/bin/sh\n"
        "test \"$AGENT_WORKTREES_NO_SELFPROVISION\" = 1 || exit 9\n" +
        refusal +
        "printf 'ran' > \"$(dirname \"$0\")/collector-ran\"\n"
        "test \"$*\" = '-p host claims find pr --repo example/widgets --state all --sweep-observation --json' || exit 8\n"
        f"printf '%s\\n' '{json.dumps(payload)}'\n",
    )
    command.chmod(0o700)
    import shlex

    argv = shlex.split(transport.remote_command("bash", str(command), "host", "example/widgets"))
    result = run_owned(argv, timeout=30)
    if not supported:
        assert result.returncode == 2
        assert not (tmp_path / "collector-ran").exists()
        return
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "collector-ran").is_file()
    assert transport.validate_observation(json.loads(result.stdout), repo="example/widgets") == payload


def test_owned_timeout_stops_its_real_descendant(tmp_path):
    pid_file = tmp_path / "child.pid"
    script = """
import pathlib,subprocess,sys,time
from agent_procutil import no_window_kwargs
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**no_window_kwargs())
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(120)
"""
    with pytest.raises(subprocess.TimeoutExpired):
        run_owned([sys.executable, "-c", script, str(pid_file)], timeout=5)
    assert pid_file.is_file(), "The real descendant did not reach its setup boundary"
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 2
    while locks.pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not locks.pid_alive(pid)
