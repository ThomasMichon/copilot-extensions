"""A pending claim must have enough callback capacity before peer admission."""

from __future__ import annotations

import base64
import copy
import hashlib
import json

import pytest
from fastapi import HTTPException

from agent_bridge.acp_client import AcpClient
from test_remote_relationships import create, managed as managed, remote_manager as remote_manager


def _subject(metadata):
    envelope = {
        key: metadata[key]
        for key in ("version", "token", "creator", "request_id", "recovery")
    }
    envelope["phase"] = "bound" if "identity" in metadata else "pending"
    if "identity" in metadata:
        envelope["identity"] = metadata["identity"]
    encoded = base64.urlsafe_b64encode(
        json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii"),
    ).decode("ascii").rstrip("=")
    return metadata["namespace"] + ":l1." + encoded


@pytest.mark.asyncio
async def test_large_allowed_native_id_does_not_duplicate_the_binding_in_subject(managed, monkeypatch):
    manager, calls, _, _ = managed
    native_id = "native-" + "n" * 1017

    async def new(client, **kwargs):
        calls.append("new")
        client._acp_session_id = native_id
        return native_id

    monkeypatch.setattr(AcpClient, "new_session", new)
    result = await create(manager, target_dir="/" + "w" * 1200)
    row = manager._db.get_relationship(result.session_id)
    assert row["native_id"] == native_id
    assert row["claim"]["producer"]["identity"] == {
        "native_id_sha256": hashlib.sha256(native_id.encode("utf-8")).hexdigest(),
    }
    assert row["claim"]["producer"]["recovery"]["binding"] == row["recovery"]["binding"]
    assert len(row["claim"]["ref"]) <= 4096 and calls == ["connect", "new"]
    await manager.end_session(result.session_id)


@pytest.mark.asyncio
async def test_pending_subject_that_cannot_fit_bound_identity_refuses_before_peer(managed):
    manager, calls, claims, _ = managed
    first = await create(manager)
    original = manager._db.get_relationship(first.session_id)["claim"]["producer"]
    await manager.end_session(first.session_id)
    template = copy.deepcopy(original)
    template.pop("identity")
    template["phase"] = "pending"
    for count in range(4096):
        cwd = "/" + "b" * count
        template["recovery"]["binding"]["cwd"] = cwd
        bound = {**template, "identity": {"native_id_sha256": "0" * 64}}
        if len(_subject(template)) <= 4096 < len(_subject(bound)):
            break
    else:
        pytest.fail("no independently calculated callback boundary found")
    before = list(calls)
    with pytest.raises(HTTPException):
        await create(manager, target_dir=cwd)
    assert calls == before and len(claims) == 2
    pending = next(iter(manager._sessions.values()))
    row = manager._db.get_relationship(pending.session_id)
    assert row["claim"]["phase"] == "pending" and row["phase"] == "reserved"
    assert len(row["claim"]["ref"]) <= 4096
    await manager.end_session(pending.session_id)
