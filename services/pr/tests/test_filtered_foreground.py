"""Foreground population host/CLI qualification with a synthetic native provider."""

import asyncio
import json
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from services.pr.tests.test_local_foreground import command, eventually, foreground, save
    from services.pr.tests.test_local_repository import configuration
else:
    from test_local_foreground import command, eventually, foreground, save
    from test_local_repository import configuration

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux OS peer credential ingress")

NATIVE = '''import hashlib,json,os,sys
from pathlib import Path
from pr_filters import Expression
identity=json.loads(sys.argv[1]);state=Path(sys.argv[2]);log=Path(sys.argv[3]);args=sys.argv[4:]
assert args[:2]==["pr-watch","population"]
with log.open("a") as stream:stream.write(json.dumps(args)+"\\n")
if "--contract" in args:
 value={"protocol":"agent-worktrees.pr-population/v1","filter_protocol":"agent-pull-requests.filters/v1",
 "providers":{"azure-devops":{"protocol":"agent-pull-requests.filters/v1",
 "fields":["repo","from","assigned","age","tags","publication"],"groups":[],"age_fields":["created"]}},
 "credential":"observe/per-request","effects":"read-only","max_pages":20,"max_pulls":1000,
 "max_requests":64,"max_seconds":60,"max_capture_bytes":1048584,"max_json_bytes":1048576}
else:
 data=json.loads(state.read_text())
 if data.get("unavailable"):raise SystemExit(3)
 path=Path(args[args.index("--filter-file")+1]);raw=path.read_bytes()
 selected=Expression.loads(raw);outcome=data.get("outcome","complete")
 pulls=[{"number":n,"facts":{"repo":identity["repo"],"from":["alice"],"assigned":["bob"],
 "created_at":data.get("created_at",100),"activity_at":None,"tags":data.get("tags",["ready"]),
 "publication":data.get("publication","published")}} for n in data.get("members",[7])]
 for pull in pulls:pull["match"]=selected.evaluate(pull["facts"],now=__import__("time").time())
 value={"schema":"agent-worktrees.pr-population","version":1,"provider":identity["provider"],
 "api_base":identity["endpoint"],"repo":identity["repo"],"filter_revision":selected.revision,
 "outcome":outcome,"pages":1,"max_pages":4,"max_pulls":200,"error":"" if outcome=="complete" else "partial",
 "pulls":pulls}
print(json.dumps(value))
'''


def selected_config(tmp_path: Path, *, sponsor: bool) -> tuple[Path, dict[str, Any], Path, Path]:
    path, config, state, log = configuration(tmp_path)
    config.update(version=3, profile="repository-filtered/v1")
    cache = config["repository_cache"]
    cache["protocol"] = "agent-pull-requests.filtered-population/v1"
    cache["repositories"] = cache["repositories"][:1]
    entry = cache["repositories"][0]
    entry["identity"].update(provider="azure-devops", endpoint="https://dev.azure.com/example")
    entry["prefix"][2] = json.dumps(entry["identity"])
    Path(entry["prefix"][1]).write_text(NATIVE)
    cache["bindings"] = cache["bindings"][:1]
    cache["definitions"] = {
        "protocol": "agent-pull-requests.filters/v1",
        "sets": {"team": {"protocol": "agent-pull-requests.filters/v1", "where": {"from": {"users": ["alice"]}}}},
    }
    cache["sponsors"] = [{
        "principal": "reader-a", "target": "repo-github", "request_id": "startup",
        "definition": "team", "ttl": 60,
    }] if sponsor else []
    save(path, config)
    return path, config, state, log


@pytest.mark.parametrize("sponsor", [False, True])
def test_actual_filtered_foreground_cli_ownership_restart_and_refusal(tmp_path: Path, sponsor: bool) -> None:
    path, config, state, log = selected_config(tmp_path, sponsor=sponsor)
    common = ["--socket", config["repository_cache"]["socket"], "--namespace", "test",
              "--target", "repo-github"]
    async def client(operation: str, *arguments: str) -> dict[str, Any]:
        code, output, error = await command("population-" + operation, *common, *arguments)
        assert code == 0, (output, error)
        result = json.loads(output)
        assert isinstance(result, dict)
        return result
    async def scenario() -> None:
        assert (await command("provision", "--config", str(path)))[0] == 0
        assert not log.exists()
        async with foreground(path):
            capabilities = await client("capabilities")
            assert capabilities["storage"] == "repository_cache_version:3"
            assert capabilities["driver"]["groups"] == []
            if not sponsor:
                await asyncio.sleep(1.2)
                assert all("--contract" in json.loads(line) for line in log.read_text().splitlines())
            first = await client("subscribe", "--request-id", "dynamic", "--definition", "team", "--ttl", "60")
            rid = first["subscription"]["registration_id"]
            async def query() -> dict[str, Any]:
                return await client("query", "--registration-id", rid)
            await eventually(query, lambda result: bool(result["baseline"]["pulls"]))
            second = await client("subscribe", "--request-id", "second", "--definition", "team", "--ttl", "60")
            assert second["subscription"]["registration_id"] != rid
            first_events = await client("events", "--registration-id", rid)
            save(state, {"members": [7, 8], "tags": []})
            await eventually(query, lambda result: len(result["baseline"]["pulls"]) == 2)
            pending = await client("events", "--registration-id", rid)
            assert any(event["kind"] == "entry" and event["number"] == 8 for event in pending["events"])
            await client("acknowledge", "--registration-id", rid, "--event-id", pending["events"][-1]["event_id"])
            assert not (await client("events", "--registration-id", rid))["events"]
            save(state, {"unavailable": True})
            await eventually(query, lambda result: result["baseline"]["inventory"]["outcome"] == "unavailable")
            save(state, {"members": [], "outcome": "partial"})
            partial = await eventually(query, lambda result: result["baseline"]["inventory"]["outcome"] == "partial")
            assert len(partial["baseline"]["pulls"]) == 2
            save(state, {"members": []})
            await eventually(query, lambda result: not result["baseline"]["pulls"])
            renewed = await client("renew", "--registration-id", rid, "--expires-at", str(int(time.time()) + 120))
            assert renewed["status"] == "active"
            await client("cancel", "--registration-id", second["subscription"]["registration_id"])
            # A legacy client must not see a broader repository profile.
            assert (await command("query", *common))[0] == 2
            unsupported = tmp_path / "unsupported.json"
            unsupported.write_text(json.dumps({
                "protocol": "agent-pull-requests.filters/v1", "where": {"from": {"groups": ["team"]}},
            }))
            assert (await command(
                "population-subscribe", *common, "--request-id", "unsupported",
                "--filter-file", str(unsupported),
            ))[0] == 2
            assert first_events["status"] == "active"
        async with foreground(path):
            retried = await client("subscribe", "--request-id", "dynamic", "--definition", "team", "--ttl", "60")
            assert retried["subscription"]["registration_id"] == rid
            assert retried["baseline"] == first["baseline"]
            assert (await client("inspect", "--registration-id", rid))["status"] == "active"
            await client("cancel", "--registration-id", rid)
            assert (await command(
                "population-renew", *common, "--registration-id", rid,
                "--expires-at", str(int(time.time()) + 120),
            ))[0] == 2
    asyncio.run(scenario())
