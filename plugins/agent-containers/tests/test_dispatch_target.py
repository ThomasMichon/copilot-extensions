"""Prepared namespace addresses retain physical identity and provider fences."""

from types import SimpleNamespace

import pytest

from agent_containers import dispatch_projection, dispatch_target, lease, resolver
from .test_dispatch_projection import FACTS, OTHER_ID, PHYSICAL_ID, target as _target

target = _target


@pytest.fixture
def prepared(target):
    target.workspace_folder = "/workspace"
    binding = dispatch_projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    return target, binding, dispatch_target.reference("pool-1", PHYSICAL_ID, binding["binding_id"])


def test_ordinary_name_is_not_prepared_and_malformed_reference_refuses():
    assert dispatch_target.parse("pool-1") is None
    for value in ("dispatch/pool-1", "dispatch/" + "a" * 64, "dispatch/" + "../pool-1"):
        with pytest.raises(RuntimeError, match="Invalid prepared"):
            dispatch_target.parse(value)
    with pytest.raises(RuntimeError):
        dispatch_target.reference("../pool-1", PHYSICAL_ID, "c" * 32)


@pytest.mark.asyncio
async def test_provider_namespace_resolves_exact_prepared_command_without_worktree(prepared):
    _, binding, reference = prepared
    provider = resolver.ContainerResolver()
    await provider.ensure_ready(reference)
    spec = await provider.resolve_spec(reference)
    assert spec["agent"] == f"container:{reference}"
    assert spec["type"] == "command" and "container" not in spec
    assert not {"worktree_id", "repo"} & spec.keys()
    assert spec["spawn_command"][-1] == "pool-1"
    assert PHYSICAL_ID in spec["spawn_command"] and binding["binding_id"] in spec["spawn_command"]
    assert spec["venue"]["instance_id"] == PHYSICAL_ID
    assert spec["venue"]["target_id"] == f"container:{reference}"
    assert spec["venue"]["ready"] is True and spec["venue"]["posture_verified"] is True
    assert spec["venue"]["capabilities"] == {
        "host_credentials": False, "credential_relay": False,
        "session_host": False, "task_projection": True, "instance_pinned_exec": True,
    }
    assert dispatch_projection._read(PHYSICAL_ID)["owner_session_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["physical", "birth", "hold"])
async def test_resolution_rechecks_physical_birth_and_lifecycle(prepared, change):
    target, binding, reference = prepared
    if change == "physical":
        target.container_id = OTHER_ID
    elif change == "birth":
        dispatch_projection._path(PHYSICAL_ID).unlink()
    else:
        with lease.deploy_hold("pool-1", "remove"):
            with pytest.raises(RuntimeError, match="busy"):
                await resolver.ContainerResolver().resolve_spec(reference)
        return
    with pytest.raises(RuntimeError):
        await resolver.ContainerResolver().ensure_ready(reference)
    with pytest.raises(RuntimeError):
        await resolver.ContainerResolver().resolve_spec(reference)


@pytest.mark.asyncio
async def test_reference_does_not_prepare_an_unused_birth(target):
    reference = dispatch_target.reference("pool-1", PHYSICAL_ID, "c" * 32)
    with pytest.raises(RuntimeError, match="does not match"):
        await resolver.ContainerResolver().resolve_spec(reference)
    assert dispatch_projection._read(PHYSICAL_ID)["task_id"] is None


@pytest.mark.asyncio
async def test_replacement_between_resolution_and_launch_cannot_start_native(prepared):
    target, binding, reference = prepared
    spec = await resolver.ContainerResolver().resolve_spec(reference)
    target.container_id = OTHER_ID
    args = SimpleNamespace(
        name="pool-1", dispatch_binding_id=binding["binding_id"],
        expected_container_id=spec["venue"]["instance_id"], stdio=True,
    )
    with pytest.raises(RuntimeError, match="physical identity changed"):
        dispatch_projection.launch_task(args, lambda *args: pytest.fail("replacement must not launch"))
    assert dispatch_projection._read(PHYSICAL_ID)["owner_session_id"] is None


def test_launch_spec_contains_no_grant_and_reuses_explicit_exec(prepared):
    _, binding, reference = prepared
    bound = {**binding, "owner_session_id": "native-1", "task_token": "never-retain-this"}
    spec = dispatch_target.launch_spec("pool-1", bound)
    assert spec["agent"] == f"container:{reference}"
    assert "never-retain-this" not in str(spec)
    assert spec["spawn_command"][-7:] == [
        "exec", "--stdio", "--dispatch-binding-id", binding["binding_id"],
        "--expected-container-id", PHYSICAL_ID, "pool-1",
    ]
