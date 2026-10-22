"""Owned ASGI/CLI transport adapter, not a mocked Bridge or ACP implementation."""

from dataclasses import asdict
from contextlib import redirect_stdout
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.agent_registry import AgentResolver
from agent_bridge.agent_registry_topology import parse_agent_registry
from agent_bridge.db import Database
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.routes.health import router as health_router
from agent_bridge.routes.sessions import router
from agent_bridge.session_manager import SessionManager


class AsgiBridgeClient(BridgeClient):
    """Only the HTTP carrier is substituted; real handlers return every fact."""

    def __init__(self, witness):
        super().__init__("http://testserver", "", connect_grace=0)
        self.witness = witness

    def _request(self, method, path, body=None, **kwargs):
        response = self.witness.api.request(method, path, json=body)
        self.witness.evidence["commands"].append(
            {"method": method, "path": path, "http_status": response.status_code,
             "body": response.json()})
        self.witness.save()
        if not response.is_success:
            raise BridgeClientError(response.status_code, response.json().get("detail", response.json()))
        return response.json()

    def list_agents(self, **kwargs):
        return []  # The fixture declares the exact qualified target; no discovery.


class OrdinaryWitness:
    def __init__(self, root, monkeypatch, *, supervised=False, assignment=None):
        repository = Path(__file__).resolve().parents[3]
        monkeypatch.syspath_prepend(str(repository / "plugins/agent-dispatch/src"))
        monkeypatch.syspath_prepend(str(repository / "plugins/agent-dispatch"))
        self.ordinary = importlib.import_module("agent_dispatch.ordinary_bridge")
        self.bridge = importlib.import_module("agent_dispatch.bridge")
        queue_type = importlib.import_module("agent_dispatch.queue").TaskQueue
        self.root = root
        self.evidence = {"compatibility_only": True, "native_source": "UNQUALIFIED",
                         "supervised": supervised,
                         "commands": [], "censuses": []}
        self.transcript = root / "peer.jsonl"
        peer = Path(__file__).with_name("mock_acp_peer.py")
        launcher = root / ("mock-peer.cmd" if os.name == "nt" else "mock-peer")
        if os.name == "nt":
            launcher.write_text(f'@"{sys.executable}" -u "{peer}" %*\n', encoding="utf-8")
        else:
            import shlex
            launcher.write_text(
                "#!/bin/sh\nexec " + shlex.join([sys.executable, "-u", str(peer)])
                + ' "$@"\n', encoding="utf-8",
            )
            launcher.chmod(0o700)
        registry = parse_agent_registry({"fixture@mock": {
            "project": "fixture", "copilot_path": str(launcher),
            "copilot_args": ["--agent", "test-only"],
            "env": {"MOCK_ACP_TRANSCRIPT": str(self.transcript)},
        }})
        self.resolver = AgentResolver(registry, {})
        self.db = Database(root / "bridge.db")
        self.manager = SessionManager(self.db, session_host_state_dir=str(root / "hosts"))
        self.manager.set_resolver(self.resolver)
        app = FastAPI()
        app.state.session_manager = self.manager
        app.state.resolver = self.resolver
        app.state.ready = True
        app.state.config = ServiceConfig()
        app.include_router(router)
        app.include_router(health_router)
        self.api = TestClient(app)
        self.http = AsgiBridgeClient(self)
        self.queue = queue_type(root / "dispatch.db")
        self.task = self.queue.create(title="Protocol preamble only", repo="fixture",
                                      require_verification=True, prompt=assignment if assignment is not None else "")
        self.snapshot = None
        if not supervised:
            reservation, admitted = self.queue.reserve_spawn(
                self.task.id, reserved_by="test-supervisor", driver="ordinary-bridge",
            )
            assert admitted
            self.snapshot = {**asdict(self.task), "spawn_reservation_key": reservation.key}
        self.drop_settled_response = False
        self.dropped = False
        monkeypatch.setattr(self.ordinary, "agent_bridge_launch_prefix", lambda: ["test-bridge"])
        monkeypatch.setattr(self.ordinary, "subprocess", SimpleNamespace(
            run=self.run, TimeoutExpired=subprocess.TimeoutExpired,
        ))
        # Discovery is fixture metadata; creation/send/status use real HTTP handlers.
        monkeypatch.setattr(self.bridge, "registered_agent_project", lambda *_a, **_k: "fixture")
        from agent_bridge import __main__ as core
        monkeypatch.setattr(core, "_get_caller_session_id", lambda: None)
        monkeypatch.setattr(core, "_worktrees_get", lambda *_a, **_kw: None)
        monkeypatch.setattr(core, "_get_client", lambda: self.http)
        if supervised:
            self._prepare_supervisor()

    def _prepare_supervisor(self):
        from agent_dispatch.supervisor import Supervisor
        from agent_dispatch.spawn_factories import make_headless_spawn
        from tests.test_supervisor import QueueBackedClient

        witness = self

        class Client(QueueBackedClient):
            def reserve_spawn(self, task_id, *, coordinator_launch=False, **kwargs):
                return super().reserve_spawn(task_id, **kwargs)

            def get_reservation(self, key):
                return witness.get_reservation(key)

            def record_created_agent(self, key, **facts):
                return witness.record_created_agent(key, **facts)

            def record_spawn(self, key, *, session_info=None, **kwargs):
                if session_info is not None:
                    witness.ordinary.record_session_info(witness.queue, key, session_info)
                return super().record_spawn(key, **kwargs)

        self.supervisor = Supervisor(
            Client(self.queue), spawn_fn=make_headless_spawn(agent="mock"),
            repo="fixture", machine="fixture", supervisor_id="test-supervisor",
            max_concurrent=1, max_attempts=1, heartbeat=False, recover=False,
            nudge=False, reactive=False, consistency_sweep=False,
        )

    def poll(self):
        spawned = self.supervisor.poll_once()
        self.evidence.setdefault("supervisor_passes", []).append({
            "spawned": spawned,
            "reservations": [asdict(r) for r in self.queue.list_reservations(task_id=self.task.id)],
            "task": asdict(self.queue.get(self.task.id)),
        })
        self.save()
        return spawned

    def get_reservation(self, key):
        return asdict(self.queue.get_reservation(key))

    def record_spawn_allocation(self, key, project):
        return asdict(self.queue.record_spawn_allocation(key, project))

    def record_created_agent(self, key, **facts):
        return asdict(self.ordinary.record_created_agent(self.queue, key, **facts))

    def record_spawn_worktree(self, key, worktree, **facts):
        return asdict(self.queue.record_spawn_worktree(key, worktree, **facts))

    def get(self, task_id):
        return asdict(self.queue.get(task_id))

    def start(self):
        return self.ordinary.start(
            self.snapshot, client=self, agent="mock", project="fixture",
            prompt="Protocol preamble only; do not claim or complete a task.",
        )

    def settle(self, session_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            session = self.manager.get_session(session_id)
            task = session._prompt_task
            if (session.status == SessionStatus.IDLE and session.turn_count
                    and (task is None or task.done())):
                time.sleep(0.05)
                return
            time.sleep(0.01)
        raise AssertionError("peer turn did not settle within the existing protocol budget")

    def run(self, argv, **kwargs):
        from agent_bridge import __main__ as core, session_targeting_cli, workspace_creation_cli

        command = argv[2]
        capture = io.StringIO()
        code = 0
        args = core.build_parser().parse_args(argv[1:])
        stdin = io.TextIOWrapper(io.BytesIO((kwargs.get("input") or "").encode("utf-8")),
                                 encoding="utf-8")
        with redirect_stdout(capture), patch("sys.stdin", stdin):
            if command == "create":
                workspace_creation_cli.create(args, self.http, caller_id=None)
            elif command == "send":
                try:
                    session_targeting_cli._cmd_send(args)
                except SystemExit as exc:
                    code = int(exc.code or 0)
            elif command == "status":
                print(json.dumps(self.http.get_session(argv[3])))
            else:
                raise AssertionError(f"unexpected ordinary CLI operation: {argv}")
        stdout = capture.getvalue()
        self.evidence["commands"].append(
            {"argv": argv, "stdin": kwargs.get("input"), "stdout": stdout, "exit": code})
        self.save()
        if command == "send" and code == 0:
            self.settle(argv[3])
            if self.drop_settled_response and not self.dropped:
                self.dropped = True
                self.census("accepted-and-settled-before-response-loss")
                raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 180))
        return subprocess.CompletedProcess(argv, code, stdout, "")

    def census(self, label):
        rows = self.db.execute_read("SELECT * FROM turns ORDER BY turn_index")
        boots = [json.loads(line) for line in self.transcript.read_text().splitlines()
                 if json.loads(line)["kind"] == "boot"]
        sessions = self.manager.list_sessions()
        physical = [p for p in (self.root / "worktrees").iterdir() if (p / ".git").is_file()]
        result = {"label": label, "allocations": len(physical), "peer_boots": len(boots),
                  "conversations": len(sessions), "turns": len(rows),
                  "peer_ids": [b["session_id"] for b in boots],
                  "task_status": self.queue.get(self.task.id).status,
                  "task_events": self.queue.events(self.task.id),
                  "host_records": [asdict(r) for r in self.manager._host_index.all()]}
        wire = [json.loads(line) for line in self.transcript.read_text().splitlines()]
        result["peer_prompts"] = [row["message"] for row in wire
                                  if row["kind"] == "inbound"
                                  and row["message"].get("method") == "session/prompt"]
        if sessions:
            worktree_id = sessions[0].target.worktree_id
            record_path = self.root / "state/worktrees" / (worktree_id + ".yaml")
            result["persisted_owner_record"] = yaml.safe_load(record_path.read_text())
            if not self.evidence["supervised"]:
                from agent_bridge.acquisition_authority import worktree_command
                result["owner_record"] = self.api.portal.call(
                    worktree_command, ["record", "--worktree-id", worktree_id, "--json"], "fixture",
                )
        self.evidence["censuses"].append(result)
        self.save()
        return result

    def save(self):
        destination = os.environ.get("MOCK_ACP_EVIDENCE_DIR")
        if destination:
            path = Path(destination)
            path.mkdir(parents=True, exist_ok=True)
            (path / (self.task.id + ".json")).write_text(
                json.dumps(self.evidence, indent=2, default=str), encoding="utf-8")
            if self.transcript.exists():
                (path / (self.task.id + ".peer.jsonl")).write_bytes(self.transcript.read_bytes())

    def close(self):
        from agent_bridge.session_host.osutil import pid_alive

        records = list(self.manager._host_index.all())
        peer_pids = []
        if self.transcript.exists():
            peer_pids = [row["pid"] for row in
                         (json.loads(line) for line in self.transcript.read_text().splitlines())
                         if row["kind"] == "boot"]
        owned_pids = {pid for r in records for pid in (r.host_pid, r.child_pid)} | set(peer_pids)
        try:
            for session in list(self.manager.list_sessions()):
                self.api.portal.call(self.manager.end_session, session.session_id)
            deadline = time.monotonic() + 5
            while any(pid_alive(pid) for pid in owned_pids):
                assert time.monotonic() < deadline, "owned host/peer survived normal teardown"
                time.sleep(0.01)
            self.evidence["cleanup"] = {
                "sessions_remaining": len(self.manager.list_sessions()),
                "owned_host_and_child_absent": True,
                "owned_pids": [[r.host_pid, r.child_pid] for r in records],
                "actual_peer_pids": peer_pids,
                "actual_peer_cleanup_census": [
                    {"pid": pid, "alive": pid_alive(pid)} for pid in peer_pids],
            }
        finally:
            self.api.__exit__(None, None, None)
            self.db.close()
            self.save()
