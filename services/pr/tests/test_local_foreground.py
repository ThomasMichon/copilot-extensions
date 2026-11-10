"""Packaged foreground and CLI clients over isolated synthetic native reads."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, TypedDict

import pytest

from agent_pull_requests_service.hosting import ready

if TYPE_CHECKING:
    from services.pr.tests.test_local_repository import configuration
else:
    from test_local_repository import configuration

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux peer credential transport")


class ProcessFlags(TypedDict, total=False):
    creationflags: int


def process_flags() -> ProcessFlags:
    return (
        {"creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0))}
        if sys.platform == "win32" else {}
    )


NATIVE = '''import json,os,sys,time
from pathlib import Path
identity=json.loads(sys.argv[1]);state=Path(sys.argv[2]);log=Path(sys.argv[3]);args=sys.argv[4:]
kind="contract" if "--contract" in args else args[1] if len(args)>1 else "unknown"
allowed=args[:2] in (["pr-watch","inventory"],["pr-watch","snapshot"])
record={"kind":kind,"provider":identity["provider"],"forge_writes":int(not allowed),"task_emissions":int("dispatch" in args)}
fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
os.write(fd,(json.dumps(record)+"\\n").encode());os.close(fd)
if not allowed: raise SystemExit(2)
if kind=="contract":
 print(json.dumps({"protocol":"agent-worktrees.pr-inventory/v1","providers":["github","gitea"],"credential":"observe/per-request","effects":"none","snapshot_request_budget":64,"snapshot_capture_bytes":1048584}))
else:
 data=json.loads(state.read_text())
 if data.get("unavailable") or kind=="snapshot" and data.get("snapshot_unavailable"): raise SystemExit(3)
 if kind=="inventory":
  if data.get("hold"):
   marker=Path(data["hold"]);marker.write_text("entered")
   while marker.exists(): time.sleep(.02)
  outcome=data.get("outcome","complete")
  print(json.dumps({"schema":"agent-worktrees.pr-inventory","version":1,"provider":identity["provider"],"api_base":identity["endpoint"],"repo":identity["repo"],"outcome":outcome,"numbers":data.get("members",[7]),"pages":2,"error":"" if outcome=="complete" else "synthetic_partial","max_pages":4,"max_pulls":200}))
 else:
  print(json.dumps({"schema":"agent-worktrees.pr-snapshot","version":1,"provider":identity["provider"],"api_base":identity["endpoint"],"repo":identity["repo"],"pr":int(args[3]),"pr_state":data.get("state","open"),"merged":data.get("merged",False),"head_sha":data.get("head","a"),"base_ref":"dev","mergeable":None,"checks_state":data.get("checks",""),"title":"Synthetic","draft":False,"reviews":data.get("reviews",[])}))
'''


def save(path: Path, value: dict[str, Any]) -> None:
    staged = path.with_suffix(".next")
    staged.write_text(json.dumps(value))
    os.replace(staged, path)


async def eventually(read: Callable[[], Awaitable[Any]], matches: Callable[[Any], bool]) -> Any:
    deadline = time.monotonic() + 15
    value: Any = None
    while time.monotonic() < deadline:
        value = await read()
        if matches(value):
            return value
        await asyncio.sleep(.1)
    raise AssertionError(f"foreground condition not reached: {value!r}")


async def command(*arguments: str) -> tuple[int | None, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-m", "agent_pull_requests_service", *arguments,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **process_flags(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        return process.returncode, stdout, stderr
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@asynccontextmanager
async def foreground(
    path: Path, *, require_ready: bool = True,
) -> AsyncIterator[asyncio.subprocess.Process]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-m", "agent_pull_requests_service",
        "serve", "--config", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **process_flags(),
    )
    try:
        deadline = time.monotonic() + 15
        config = json.loads(path.read_text())
        endpoint = Path(config["repository_cache"]["socket"])
        while not endpoint.is_socket() or (
            require_ready and not ready(Path(config["root"]) / "service-status.json")
        ):
            assert process.returncode is None, "foreground exited before local readiness"
            assert time.monotonic() < deadline, "foreground local readiness timed out"
            await asyncio.sleep(.05)
        yield process
    finally:
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
            if stderr:
                print(stderr.decode(errors="replace")[-12000:])
            assert process.returncode == 0, (stdout, stderr)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


@pytest.mark.parametrize("provider", ["github", "gitea"])
def test_installed_foreground_cache_continuing_events_and_restart(
    tmp_path: Path, provider: str,
) -> None:
    path, config, github, log = configuration(tmp_path)
    script = Path(config["repository_cache"]["repositories"][0]["prefix"][1])
    script.write_text(NATIVE)
    gitea = tmp_path / "gitea.json"
    save(gitea, {"head": "a"})
    config["repository_cache"]["repositories"][1]["prefix"][3] = str(gitea)
    config["repository_cache"]["bindings"][1]["principal"] = "reader-a"
    save(path, config)
    common = ["--socket", config["repository_cache"]["socket"], "--namespace", "test"]
    other_provider = "gitea" if provider == "github" else "github"
    if provider == "gitea":
        github, gitea = gitea, github

    async def client(operation: str, *arguments: str, target: str | None = None) -> dict[str, Any]:
        target = target or "repo-" + provider
        code, output, error = await command(
            operation, *common, *(["--target", target] if operation != "list" else []), *arguments,
        )
        assert code == 0, (output, error)
        result = json.loads(output)
        assert isinstance(result, dict)
        return result

    async def query() -> dict[str, Any]:
        return await client("query")

    async def scenario() -> None:
        assert (await command("provision", "--config", str(path)))[0] == 0
        assert not log.exists()
        assert (await command("provision", "--config", str(path)))[0] == 1
        async with foreground(path) as owner:
            await eventually(query, lambda row: bool(row["pulls"]) and row["pulls"][0]["facts"] is not None)
            assert owner.returncode is None and ready(Path(config["root"]) / "service-status.json")
            assert (await command("ready", "--status", str(Path(config["root"]) / "service-status.json")))[0] == 0
            assert (await command("serve", "--config", str(path)))[0] == 1
            await eventually(
                lambda: client("query", target="repo-" + other_provider),
                lambda row: bool(row["pulls"]) and row["pulls"][0]["facts"] is not None,
            )
            listed = await client("list")
            assert {row["target"] for row in listed["repositories"]} == {"repo-github", "repo-gitea"}
            a = (await client("subscribe", "--request-id", "a", "--ttl", "120", "--after", "0"))["subscription"]
            b = (await client("subscribe", "--request-id", "b", "--ttl", "120", "--after", "0"))["subscription"]
            baseline = await client("events", "--registration-id", a["registration_id"])
            pending = await client("events", "--registration-id", b["registration_id"])
            assert baseline["events"] == pending["events"]
            assert any(row["kind"] == "baseline" for row in baseline["events"])
            assert not any(row["kind"] == "entry" for row in baseline["events"])
            await client("acknowledge", "--registration-id", a["registration_id"],
                         "--event-id", baseline["events"][-1]["event_id"])

            save(github, {"head": "b", "members": [7, 8]})
            changed = await eventually(query, lambda row: len(row["pulls"]) == 2 and all(
                pull["facts"] and pull["facts"]["head_sha"] == "b" for pull in row["pulls"]
            ))
            review = {"id": 1, "state": "APPROVED", "user": "reviewer", "commit_id": "b", "dismissed": False}
            save(github, {"head": "b", "members": [7, 8], "reviews": [review], "checks": "failure"})
            changed = await eventually(query, lambda row: all(
                pull["facts"] and pull["facts"]["checks_state"] == "failure"
                and pull["facts"]["reviews"] for pull in row["pulls"]
            ))
            separate = await client("query", target="repo-" + other_provider)
            assert separate["identity"]["provider"] == other_provider
            assert separate["pulls"][0]["facts"]["head_sha"] == "a"
            stable_cursor, observed = changed["cursor"], changed["inventory"]["observed_at"]
            identical = await eventually(query, lambda row: (
                row["inventory"]["observed_at"] > observed
                and all(pull["observed_at"] == row["inventory"]["observed_at"]
                        for pull in row["pulls"])
            ))
            assert identical["cursor"] == stable_cursor

            save(github, {"head": "b", "members": [], "outcome": "partial"})
            partial = await eventually(query, lambda row: row["inventory"]["outcome"] == "partial")
            assert all(pull["member"] for pull in partial["pulls"])
            save(github, {"unavailable": True})
            unavailable = await eventually(query, lambda row: row["inventory"]["outcome"] == "unavailable")
            assert all(pull["member"] and pull["facts"]["head_sha"] == "b" for pull in unavailable["pulls"])
            assert owner.returncode is None
            async def readiness() -> bool:
                return ready(Path(config["root"]) / "service-status.json")
            await eventually(readiness, lambda value: not value)
            degraded = await query()
            assert all(pull["member"] and pull["facts"]["head_sha"] == "b"
                       for pull in degraded["pulls"])
            save(github, {"members": [], "snapshot_unavailable": True})
            departed = await eventually(query, lambda row: all(
                not pull["member"] and pull["error"] == "snapshot_unavailable" for pull in row["pulls"]
            ))
            assert all(pull["facts"]["pr_state"] == "open" for pull in departed["pulls"])
            save(github, {"members": [], "head": "b", "state": "closed", "merged": True})
            await eventually(query, lambda row: all(pull["facts"]["merged"] for pull in row["pulls"]))
            changes = await client("events", "--registration-id", a["registration_id"])
            kinds = {row["kind"] for row in changes["events"]}
            assert {"entry", "change", "departure", "terminal", "coverage"} <= kinds
            await client("cancel", "--registration-id", b["registration_id"])
            assert (await client("events", "--registration-id", b["registration_id"]))["events"] == pending["events"]

            marker = tmp_path / "inventory-blocked"
            save(github, {"members": [], "hold": str(marker)})
            async def held() -> bool:
                return marker.exists()
            await eventually(held, bool)
            try:
                count = len(log.read_text().splitlines())
                for _ in range(3):
                    await query()
                    await client("inspect", "--registration-id", a["registration_id"])
                assert len(log.read_text().splitlines()) == count
            finally:
                save(github, {"members": [], "head": "b", "state": "closed", "merged": True})
                marker.unlink(missing_ok=True)

        assert not Path(common[1]).exists()
        async with foreground(path):
            replay = await client("events", "--registration-id", a["registration_id"])
            assert replay["events"] == changes["events"]
            assert (await client("subscribe", "--request-id", "a", "--ttl", "120", "--after", "0"))["subscription"]["registration_id"] == a["registration_id"]
            assert (await client("events", "--registration-id", b["registration_id"]))["events"] == pending["events"]
            await client("acknowledge", "--registration-id", b["registration_id"],
                         "--event-id", pending["events"][-1]["event_id"])
            assert not (await client("events", "--registration-id", b["registration_id"]))["events"]
            await client("acknowledge", "--registration-id", a["registration_id"],
                         "--event-id", changes["events"][-1]["event_id"])
            save(github, {"members": [9], "head": "c"})
            await eventually(query, lambda row: any(
                pull["number"] == 9 and pull["facts"] for pull in row["pulls"]
            ))
            continued = await client("events", "--registration-id", a["registration_id"])
            assert any(row["number"] == 9 and row["kind"] == "entry" for row in continued["events"])
            following = asyncio.create_task(command(
                "follow", *common, "--target", "repo-" + provider,
                "--registration-id", a["registration_id"], "--timeout", "5", "--interval", "1", "--ack",
            ))
            try:
                await eventually(
                    lambda: client("inspect", "--registration-id", a["registration_id"]),
                    lambda row: (
                        row["subscription"]["acknowledged_event"]
                        == continued["events"][-1]["event_id"]
                    ),
                )
                assert not following.done(), "follow exited after only its initial batch"
                save(github, {"members": [9], "head": "d"})
                code, output, error = await following
            finally:
                if not following.done():
                    following.cancel()
                    await asyncio.gather(following, return_exceptions=True)
            assert code == 0, (output, error)
            batches = [json.loads(line) for line in output.splitlines()]
            assert any(event["kind"] == "change" and event["facts"]["head_sha"] == "d"
                       for batch in batches for event in batch["events"])
            assert not (await client("events", "--registration-id", a["registration_id"]))["events"]
            short = (await client("subscribe", "--request-id", "short", "--ttl", "1"))["subscription"]
            await asyncio.sleep(1.1)
            assert (await client("inspect", "--registration-id", short["registration_id"]))["status"] == "expired"
        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert {row["provider"] for row in records} == {"github", "gitea"}
        assert all(row["forge_writes"] == row["task_emissions"] == 0 for row in records)

    asyncio.run(scenario())


def test_installed_front_door_distinguishes_unavailable_from_initial_empty_complete(tmp_path: Path) -> None:
    path, config, state, log = configuration(tmp_path)
    Path(config["repository_cache"]["repositories"][0]["prefix"][1]).write_text(NATIVE)
    save(state, {"unavailable": True})
    common = [
        "--socket", config["repository_cache"]["socket"], "--namespace", "test",
        "--target", "repo-github",
    ]
    async def query() -> dict[str, Any]:
        code, output, error = await command("query", *common)
        assert code == 0, (output, error)
        result = json.loads(output)
        assert isinstance(result, dict)
        return result
    async def scenario() -> None:
        assert (await command("provision", "--config", str(path)))[0] == 0
        assert not log.exists()
        async with foreground(path, require_ready=False):
            unknown = await eventually(query, lambda row: row["inventory"]["outcome"] == "unavailable")
            assert unknown["pulls"] == [] and unknown["inventory"]["last_complete_at"] is None
            assert not ready(Path(config["root"]) / "service-status.json")
            save(state, {"head": "a", "members": []})
            empty = await eventually(query, lambda row: row["inventory"]["outcome"] == "complete")
            assert empty["pulls"] == [] and empty["inventory"]["last_complete_at"] is not None
            code, _output, error = await command(
                "query", "--socket", common[1], "--namespace", "wrong", "--target", "repo-github",
            )
            assert code == 2 and b"namespace/contract differs" in error
            code, _output, error = await command(
                "query", "--socket", common[1], "--namespace", "test", "--target", "unconfigured",
            )
            assert code == 2 and b"repository_scope_refused" in error
        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert all(row["forge_writes"] == row["task_emissions"] == 0 for row in records)
    asyncio.run(scenario())
