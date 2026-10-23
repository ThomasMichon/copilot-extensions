"""PR bridge, native consumer and the existing typed TLS credential exchange."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from agent_pull_requests.watch_provider import fetch_snapshot
from agent_pull_requests import watch_provider


@pytest.mark.parametrize("lose_provider", [False, True])
@pytest.mark.parametrize("replay", [False, True])
def test_native_bridge_consumes_real_typed_exchange_without_ambient_fallback(
    tmp_path, monkeypatch, lose_provider, replay,
):
    repository = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repository / "fleet-manager" / "tests"))
    from fleet_manager.gateway_server import build_server
    from test_gateway_pr_credentials import (
        ScopedProvider, configured, forge, responding, worker_policy,
    )
    from test_gateway_runtime import running

    scope = replace(forge("github"), api_base="https://api.github.com")
    fixture, parameters = configured(tmp_path, scope)
    source = ScopedProvider(
        lambda material: None if lose_provider and material.token.endswith("-2") else material,
    )
    requests = tmp_path / "forge-requests"
    forge_command = tmp_path / "forge.py"
    forge_command.write_text(
        "import json,os,pathlib,sys\n"
        f"path=pathlib.Path({str(requests)!r})\n"
        "count=len(path.read_text().splitlines())+1 if path.exists() else 1\n"
        "expected='synthetic-forge-secret-'+str(count)\n"
        "assert os.environ['GH_TOKEN']==os.environ['GITHUB_TOKEN']==expected\n"
        "assert not any('synthetic-forge-secret-' in part for part in sys.argv)\n"
        "with path.open('a') as stream: stream.write('request\\n')\n"
        "url=sys.argv[-1]\n"
        "assert url.startswith('https://api.github.com/repos/project/repository/')\n"
        "if url.endswith('/pulls/7'):\n"
        f"    value={{'state':{'closed' if replay else 'open'!r},'merged':{replay!r},"
        "'head':{'sha':'head'},'title':'Fixture'}\n"
        "elif url.endswith('/status'):\n"
        "    value={'total_count':0,'statuses':[]}\n"
        "elif '/check-runs?' in url:\n"
        "    value={'total_count':0,'check_runs':[]}\n"
        "else: value=[]\n"
        "print(json.dumps(value))\n",
        encoding="utf-8",
    )
    native = tmp_path / "native.py"
    native.write_text(
        "import json,sys\n"
        f"sys.path.insert(0,{str(repository / 'plugins' / 'agent-worktrees' / 'src')!r})\n"
        "from agent_worktrees.pr_credential_command import command_source\n"
        "from agent_worktrees.providers import github\n"
        "from agent_worktrees.providers.base import run_cli\n"
        "from agent_worktrees.pr_snapshot_payload import snapshot_payload\n"
        "assert sys.argv[1:7]==['pr-watch','snapshot','project/repository','7','--json',"
        "'--credential-command']\n"
        f"github.run_cli=lambda argv,**options: run_cli([sys.executable,{str(forge_command)!r},"
        "*argv[1:]],**options)\n"
        "source=command_source(sys.argv[7])\n"
        "snapshot=github.GitHubProvider().get_snapshot('project/repository',7,credential_source=source)\n"
        "print(json.dumps(snapshot_payload(snapshot,'project/repository',7,'github',"
        "'https://api.github.com')))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_TOKEN", "ambient-credential-must-not-win")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-credential-must-not-win")
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        policy = worker_policy(fixture, parameters, gateway.server_port)
        credential_command = [
            sys.executable, "-m", "fleet_manager", "credential", "--config", str(policy),
            "--source", parameters["source"], "--account", parameters["account"],
            "--forge", json.dumps(scope.to_dict()), "--timeout", "5",
        ]
        with responding(fixture, gateway, source):
            def run(argv):
                result = watch_provider._run_native(argv)
                if not lose_provider:
                    assert result.returncode == 0, result.stderr
                return result

            def fetch():
                return fetch_snapshot(
                    "project/repository", 7, sys.executable, prefix=[sys.executable, str(native)],
                    credential_command=credential_command, runner=run,
                )

            if replay:
                from agent_pull_requests.watch_observer import (
                    NativeObservationBinding, ReplayObserver,
                )
                from agent_pull_requests.watch_replay import ConsumerScope
                from agent_pull_requests.watch_replay_store import ReplayStore

                store = ReplayStore(tmp_path.resolve(), "fleet/service/credential-test")
                store.provision()
                consumer = ConsumerScope("consumer", parameters["account"], "project/repository")
                expires = int(time.time()) + 60
                original = {
                    "request_id": "request", "issued_at": time.time(), "number": 7,
                    "until": ("merged",),
                }
                receipt = store.execute(consumer, "register", original, expires_at=expires)
                owner = ReplayObserver(store, bindings=(NativeObservationBinding(
                    consumer.account, consumer.repo, sys.executable,
                    prefix=(sys.executable, str(native)),
                    credential_command=tuple(credential_command),
                ),))
                result = owner.cycle(expires_at=expires)
                assert result == {
                    "status": "observed", "observed": int(not lose_provider),
                    "unavailable": int(lose_provider), "unbound": 0,
                }
                assert store.execute(consumer, "register", original, expires_at=expires) == receipt
                events = store.execute(consumer, "pending", {}, expires_at=expires)["events"]
                if lose_provider:
                    assert events == []
                    assert len(source.calls) == 2 and requests.read_text().splitlines() == ["request"]
                else:
                    assert len(events) == 1 and events[0]["transitions"] == ["merged"]
                    assert events[0]["registration_id"] == receipt["registration_id"]
                    restored = ReplayStore(store.root, store.namespace)
                    assert restored.execute(
                        consumer, "pending", {}, expires_at=expires,
                    )["events"] == events
                    assert owner.cycle(expires_at=expires)["observed"] == 0
                    assert len(source.calls) == len(requests.read_text().splitlines()) == 4
            elif lose_provider:
                with pytest.raises(ValueError, match="native snapshot command failed"):
                    fetch()
                assert len(source.calls) == 2 and requests.read_text().splitlines() == ["request"]
            else:
                snapshot = fetch()
                assert snapshot.head_sha == "head" and snapshot.native_snapshot["checks_complete"]
                assert len(source.calls) == len(requests.read_text().splitlines()) == 4
                assert "synthetic-forge-secret" not in repr(snapshot)
                assert all(item.state == "ACKED" and item.delivery is None
                           for item in gateway.gateway.broker._items.values())
