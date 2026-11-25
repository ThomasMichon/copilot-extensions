from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from agent_ssh import gateway_authorization as authorization
from agent_ssh import gateway_provider
from agent_ssh.gateway_native import NativeGateway
from test_gateway_native import Provider, key, peer


def admitted(root: Path) -> tuple[Provider, NativeGateway, dict[str, Any]]:
    provider = Provider()
    gateway = NativeGateway(provider, provider.descriptor)
    gateway.enroll(peer())
    public = root / "identity.pub"
    public.write_text(key(1), encoding="ascii")
    return provider, gateway, {
        "descriptor": deepcopy(provider.descriptor), "machine": "receiver",
        "environment": "windows", "public_key_file": public,
    }


def test_native_authorization_catches_up_without_fleet_or_outbound_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, gateway, scope = admitted(tmp_path)
    monkeypatch.setattr(gateway_provider, "_sdk", Mock(side_effect=AssertionError("Fleet invoked")))
    unrelated = tmp_path / "authorized_keys"
    unrelated.write_text("unrelated key\n", encoding="ascii")
    root = tmp_path / "authorization"
    value = authorization.projection_from_native(gateway.inspect(), **scope)
    assert "directory_revision" not in value and "directory_digest" not in value
    target, changed = authorization.apply_projection(value, root)
    assert changed
    before = target.read_bytes(), target.stat().st_mtime_ns
    assert authorization.apply_projection(value, root) == (target, False)
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    provider.descriptor["peers"].append({"machine": "later-controller", "relay_port": 24003})
    provider.peers.append(peer("later-controller", 3))
    value = authorization.projection_from_native(gateway.inspect(), **scope)
    assert authorization.apply_projection(value, root) == (target, True)
    assert key(3) in target.read_text(encoding="utf-8")
    authorization.validate_state(root, target=value["target"], controller=value["controller"])
    assert unrelated.read_text(encoding="ascii") == "unrelated key\n"
    assert not list(root.glob("gateway-provider-*.json"))
    assert not (tmp_path / "ssh").exists()
    assert "revision" not in json.loads((root / authorization._NATIVE_RECEIPT).read_text(encoding="utf-8"))


@pytest.mark.parametrize("change", ["target", "environment", "public", "unadmitted", "pin", "raw"])
def test_native_projection_refuses_before_creating_authorization(tmp_path: Path, change: str) -> None:
    provider, gateway, scope = admitted(tmp_path)
    if change == "target":
        scope["machine"] = "elsewhere"
    elif change == "environment":
        scope["environment"] = "other"
    elif change == "public":
        scope["public_key_file"].write_text(key(9), encoding="ascii")
    elif change == "unadmitted":
        provider.peers.pop()
    elif change == "pin":
        scope["descriptor"]["host_key"] = key(98)
    observed = gateway.inspect()
    update: object = observed.directory if change == "raw" else observed
    with pytest.raises((ValueError, PermissionError)):
        authorization.projection_from_native(update, **scope)
    assert not (tmp_path / "authorization").exists()


@pytest.mark.parametrize("change", ["missing", "key", "host", "user", "port", "missing-receipt"])
def test_unversioned_observation_never_rewinds_or_rotates_accepted_membership(
    tmp_path: Path, change: str,
) -> None:
    provider, gateway, scope = admitted(tmp_path)
    root = tmp_path / "authorization"
    value = authorization.projection_from_native(gateway.inspect(), **scope)
    target, _ = authorization.apply_projection(value, root)
    if change == "missing":
        provider.peers.pop(0)
    elif change in ("key", "host", "user"):
        provider.peers[0][{"key": "public_key", "host": "host_key", "user": "user"}[change]] = (
            "different" if change == "user" else key(7)
        )
    elif change == "port":
        provider.descriptor["peers"][1]["relay_port"] = 24004
        scope["descriptor"] = deepcopy(provider.descriptor)
        gateway = NativeGateway(provider, provider.descriptor)
    else:
        (root / authorization._NATIVE_RECEIPT).unlink()
    value = authorization.projection_from_native(gateway.inspect(), **scope)
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    with pytest.raises((ValueError, PermissionError)):
        authorization.apply_projection(value, root)
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    assert key(2) in target.read_text(encoding="utf-8")


def test_interrupted_native_publication_keeps_input_and_allows_only_same_or_additive_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, gateway, scope = admitted(tmp_path)
    root = tmp_path / "authorization"
    old = authorization.projection_from_native(gateway.inspect(), **scope)
    target, _ = authorization.apply_projection(old, root)
    before = target.read_bytes()
    provider.descriptor["peers"].append({"machine": "later-controller", "relay_port": 24003})
    provider.peers.append(peer("later-controller", 3))
    new = authorization.projection_from_native(gateway.inspect(), **scope)
    publish = authorization._publish_authorization
    monkeypatch.setattr(authorization, "_publish_authorization", Mock(side_effect=OSError("interrupted")))
    with pytest.raises(OSError, match="interrupted"):
        authorization.apply_projection(new, root)
    assert target.read_bytes() == before
    monkeypatch.setattr(authorization, "_publish_authorization", publish)
    with pytest.raises(PermissionError):
        authorization.apply_projection(old, root)
    assert target.read_bytes() == before
    assert authorization.apply_projection(new, root) == (target, True)
    assert key(3) in target.read_text(encoding="utf-8")


def test_native_and_fleet_acceptance_modes_cannot_share_a_namespace(tmp_path: Path) -> None:
    _, gateway, scope = admitted(tmp_path)
    value = authorization.projection_from_native(gateway.inspect(), **scope)
    root = tmp_path / "authorization"
    target, _ = authorization.apply_projection(value, root)
    fleet = {name: item for name, item in value.items() if name != "native_membership"}
    fleet.update(directory_revision=1, directory_digest="a" * 64)
    before = target.read_bytes()
    with pytest.raises(ValueError, match="cannot become"):
        authorization.apply_projection(fleet, root)
    assert target.read_bytes() == before
