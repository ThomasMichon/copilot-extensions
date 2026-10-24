"""Actual HTTP/owned-stdio observations; no model, workspace owner or deployment."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

import httpx
import pytest
from zdd import process_exit

import bridge_http47_evidence as evidence
from agent_bridge import cold_outages
from agent_bridge.db import Database
from agent_bridge.session_cold_drain import owned_execution
from agent_bridge.session_manager import SessionManager
from test_cold_drain import NATIVE, api, cleanup, live_session, messages

ROOT = Path(__file__).resolve().parents[3]


def bound_request() -> dict | None:
    raw = os.environ.get("BRIDGE_HTTP47_CAPTURE_REQUEST")
    if raw is None:
        return None
    path = Path(raw)
    relative = path.relative_to(ROOT).as_posix()
    if not relative.startswith(".test-venvs/http47-capture-"):
        raise ValueError("capture probe request escaped its owned directory")
    source = evidence.safe(ROOT, relative)
    data = source.read_bytes()
    if evidence.digest(data) != os.environ.get("BRIDGE_HTTP47_CAPTURE_REQUEST_SHA256"):
        raise ValueError("capture probe request digest changed")
    request = json.loads(data)
    if request["root"] != str(ROOT) or Path(request["report"]) != path.parent / "observed.json":
        raise ValueError("capture probe report escaped its owned directory")
    for name, key in (
        (evidence.INPUT, "input_sha256"),
        (evidence.CONTROL_INPUT, "control_sha256"),
    ):
        if evidence.digest(evidence.safe(ROOT, name).read_bytes()) != request[key]:
            raise ValueError("independent capture input changed")
    for name, proof in request["source_proof"].items():
        if (
            evidence.git_evidence.sha256_bytes(evidence.safe(ROOT, name).read_bytes())
            != proof["sha256"]
        ):
            raise ValueError("capture probe source changed")
    return request


def loaded_source_proof(proof: dict) -> None:
    """Bind imported local production bytes, including copies installed in the test venv."""
    modules = {}
    for path, identity in proof.items():
        if "/src/" not in path or not path.endswith(".py"):
            continue
        module = path.split("/src/", 1)[1][:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[:-9]
        modules.setdefault(module, set()).add(identity["sha256"])
    for name, module in tuple(sys.modules.items()):
        if name not in modules:
            continue
        origin = getattr(module, "__file__", None)
        if (
            origin is None
            or evidence.git_evidence.sha256_bytes(Path(origin).read_bytes()) not in modules[name]
        ):
            raise ValueError(f"capture imported unproven production bytes: {name}")


@pytest.mark.asyncio
async def test_http47_capture_actual_observations(session_manager, tmp_path, monkeypatch):
    request = bound_request()
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    bodies = vector["requests"]
    manager = session_manager
    successor = None
    transport_api = api(manager)
    observed = {}
    try:
        session, transcript = await live_session(manager, tmp_path, monkeypatch)
        target = session.target.to_json()
        execution = owned_execution(manager, session, manager._host_index.get(session.session_id))

        def alive():
            return all(
                not process_exit.observe_original_exit(v["pid"], v["start_time"])
                for v in execution.values()
            )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport_api.app),
            base_url="http://fixture",
            headers=dict(transport_api.headers),
        ) as wire:
            health = (await wire.get("/health")).json()
            observed["advertised"] = {
                key: health[key]
                for key in (
                    "protocol_version",
                    "min_protocol_version",
                    "drain_contract",
                )
            }
            legacy = await wire.post("/api/v1/drain", json=bodies["legacy"])
            observed["legacy"] = {
                "status": legacy.status_code,
                **{key: legacy.json()[key] for key in ("mode", "contract_status")},
                "native_alive": alive(),
            }
            manager.set_draining(False)
            compatible = await wire.post("/api/v1/drain", json=bodies["compatible"])
            observed["compatible"] = {
                "status": compatible.status_code,
                **{key: compatible.json()[key] for key in ("mode", "clean", "forced")},
                "native_alive": alive(),
            }
            manager.set_draining(False)
            strict = await wire.post("/api/v1/drain", json=bodies["strict"])
            observed["strict"] = {
                "status": strict.status_code,
                "gate_closed": not manager.is_draining,
                "native_alive": alive(),
                "outages": len(manager.db.execute_read("SELECT * FROM cold_outages")),
            }
            invalid = []
            for body in bodies["invalid"]:
                invalid.append((await wire.post("/api/v1/drain", json=body)).status_code)
                assert not manager.is_draining and alive()
            observed["invalid_statuses"] = invalid
            wire.headers.pop("Authorization")
            refused = await wire.post("/api/v1/drain", json=bodies["cold"])
            observed["unauthenticated"] = {
                "status": refused.status_code,
                "gate_closed": not manager.is_draining,
            }
            wire.headers.update(dict(transport_api.headers))
            cold = await wire.post("/api/v1/drain", json=bodies["cold"])
            observed["cold"] = {"status": cold.status_code, **cold.json()}
            later = await wire.post("/api/v1/drain", json=bodies["compatible"])
            observed["post_cold_graceful_refusal"] = {
                "status": later.status_code,
                **{key: later.json()[key] for key in ("drained", "clean", "forced", "mode")},
            }
            observed["cold_watchdog_disabled"] = manager.drain_status()["auto_release_at"] is None
        observed["original_exit"] = {
            name: process_exit.observe_original_exit(value["pid"], value["start_time"])
            for name, value in execution.items()
        }
        successor = SessionManager(manager.db)
        successor._generation_id = "synthetic-capture-successor"
        resumed = await successor.resume_session(
            session.session_id, drain=False, allow_recreate=False
        )
        await asyncio.wait_for(asyncio.shield(resumed._prompt_task), timeout=10)
        repeat = await cold_outages.recover(successor)
        journal = cold_outages.latest(manager.db, session.session_id)
        delivery = json.loads(journal["reconnect_evidence_json"])
        transcript_rows = messages(transcript)
        loads = [
            m["params"]["sessionId"] for m in transcript_rows if m.get("method") == "session/load"
        ]
        prompts = [m for m in transcript_rows if m.get("method") == "session/prompt"]
        observed["recovery"] = {
            "same_bridge": resumed.session_id == session.session_id,
            "same_native": resumed.acp_session_id == journal["native_session_id"] == NATIVE,
            "same_target": resumed.target.to_json() == journal["target_json"] == target,
            "same_loaded_native": bool(loads) and all(value == NATIVE for value in loads),
            "native_boots": len([m for m in transcript_rows if "boot" in m]),
            "new_session_calls": len(
                [m for m in transcript_rows if m.get("method") == "session/new"]
            ),
            "sdk_prompts": [
                "".join(b.get("text", "") for b in m["params"]["prompt"]) for m in prompts
            ],
            "journal_turns": len(manager.db.get_turns(session.session_id)),
            "reconnect_keys": len(
                manager.db.execute_read(
                    "SELECT 1 FROM turns WHERE acquisition_delivery_id LIKE 'cold-outage:%'",
                )
            ),
            "provider_evidence_kind": delivery["kind"],
            "provider_stop_reason": delivery["stop_reason"],
            "repeated_recovery": repeat,
        }
        allocated_db = Database(tmp_path / "allocated.db")
        try:
            allocated = SessionManager(allocated_db)
            allocated_db.execute_write(
                "INSERT INTO workspace_embodiments(key_digest,facts_digest,state,session_id)"
                " VALUES ('fixture-key','fixture-facts','allocated','no-native')"
            )
            before = [
                dict(row)
                for row in allocated_db.execute_read("SELECT * FROM workspace_embodiments")
            ]
            from agent_bridge.session_cold_drain import cold_drain

            await cold_drain(
                allocated,
                timeout=0,
                poll=0.05,
                source="capture",
                reason=None,
                exclude_session_id=None,
            )
            after = [
                dict(row)
                for row in allocated_db.execute_read("SELECT * FROM workspace_embodiments")
            ]
            observed["allocated"] = {
                "unchanged": before == after,
                "sessions": len(allocated.list_sessions()),
                "outages": len(allocated_db.execute_read("SELECT * FROM cold_outages")),
                "state": after[0]["state"],
            }
            allocated.set_draining(False)
        finally:
            allocated_db.close()
        assert json.dumps(observed, sort_keys=True) == json.dumps(
            vector["expected"], sort_keys=True
        )
        if request is not None:
            loaded_source_proof(request["source_proof"])
            report = evidence.safe(ROOT, Path(request["report"]).relative_to(ROOT).as_posix())
            with report.open("xb") as stream:
                stream.write(evidence.encode(observed))
    finally:
        if successor is not None:
            await cleanup(successor)
        await cleanup(manager)
        transport_api.close()
