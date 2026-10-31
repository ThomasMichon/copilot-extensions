import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest
from agent_procutil import no_window_kwargs

from agent_ssh import gateway_authorization as authorization
from agent_ssh.__main__ import main
from test_gateway_provider import fixture_files, key


@pytest.mark.parametrize("owner", ["S-1-5-21-123-456-789-1001", "BA", "SY"])
def test_native_privileged_file_owner_keeps_the_same_closed_access_grants(owner):
    sid = "S-1-5-21-123-456-789-1001"
    grants = f"(A;ID;FA;;;{sid})(A;ID;FA;;;SY)"
    authorization._validate_windows_descriptor(f"O:{owner}D:AI{grants}", sid, directory=False)
    if owner != sid:
        with pytest.raises(PermissionError, match="owner-private"):
            authorization._validate_windows_descriptor(
                f"O:{owner}D:P{grants}", sid, directory=True,
            )
    with pytest.raises(PermissionError, match="unexpected access grant"):
        authorization._validate_windows_descriptor(
            f"O:{owner}D:AI{grants}(A;;FA;;;BA)", sid, directory=False,
        )


def test_foreign_file_owner_is_not_a_privileged_native_default():
    sid = "S-1-5-21-123-456-789-1001"
    with pytest.raises(PermissionError, match="owner-private"):
        authorization._validate_windows_descriptor(
            f"O:S-1-5-21-987-654-321-1001D:AI(A;ID;FA;;;{sid})", sid, directory=False,
        )


def test_private_descriptor_failure_reports_shape_without_account_identifiers():
    sid = "S-1-5-21-123-456-789-1001"
    foreign = "S-1-5-21-987-654-321-1001"
    with pytest.raises(PermissionError) as caught:
        authorization._validate_windows_descriptor(
            f"O:{foreign}D:AI(A;ID;FA;;;{sid})(A;ID;FA;;;SY)", sid, directory=True,
        )
    message = str(caught.value)
    assert "directory descriptor=O:OTHERD:AI" in message
    assert "(A;ID;FA;;;CURRENT)" in message
    assert sid not in message and foreign not in message


def test_canonical_current_account_alias_is_not_a_foreign_owner_or_grant():
    grants = "(A;OICI;FA;;;LA)(A;OICI;FA;;;SY)"
    authorization._validate_windows_descriptor("O:LAD:P" + grants, "LA", directory=True)
    authorization._validate_windows_descriptor("O:BAD:AI" + grants, "LA", directory=False)
    with pytest.raises(PermissionError, match="owner-private"):
        authorization._validate_windows_descriptor("O:LGD:P" + grants, "LA", directory=True)
    with pytest.raises(PermissionError, match="unexpected access grant"):
        authorization._validate_windows_descriptor(
            "O:LAD:P" + grants + "(A;;FA;;;BA)", "LA", directory=True,
        )


@pytest.mark.skipif(os.name != "nt", reason="native SID spelling and protected Windows ACLs")
def test_native_identity_spelling_round_trip_keeps_authorization_scope_private(tmp_path):
    assert authorization._windows_sddl_sid("S-1-5-18") == "SY"
    assert authorization._windows_sddl_sid("S-1-5-32-544") == "BA"
    root = tmp_path / "private-authorization"
    authorization._windows_create_directory(root)
    authorization._windows_private(root, directory=True)
    target = root / "native-file"
    from dropin_registry import atomic_write_text

    atomic_write_text(target, "fixture")
    authorization._windows_private(target, directory=False)


def arguments(root: Path, **changes):
    _, directory = fixture_files(root, **changes)
    return [
        "gateway-authorization", "--policy", str(root / "policy.json"),
        "--directory", str(root / "directory.json"),
        "--machine", "worker-a", "--environment", "linux",
        "--public-key-file", str(root / "identity.pub"),
        "--state-dir", str(root / "native-authorization"), "--json",
    ], directory


def test_preview_never_writes_or_reads_private_identity(tmp_path, capsys):
    argv, _ = arguments(tmp_path)
    (tmp_path / "approved identity").unlink()
    assert main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["authorized_peers"]) == 2 and not result["applied"]
    assert not result["service_started"] and not result["live_readiness_verified"]
    assert not (tmp_path / "native-authorization").exists()


def test_native_file_repeat_public_keys_and_unrelated_state(tmp_path, capsys):
    argv, directory = arguments(tmp_path)
    unrelated = tmp_path / "authorized_keys"
    unrelated.write_text("existing unrelated bootstrap identity\n", encoding="ascii")
    assert main(argv + ["--apply"]) == 0
    result = json.loads(capsys.readouterr().out)
    target = Path(result["authorization_file"])
    assert result["applied"] and result["changed"]
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    assert target.read_text(encoding="utf-8").splitlines()[1:] == [
        "restrict,pty " + row["public_key"] for row in directory["machines"]
    ]
    assert main(argv + ["--apply"]) == 0
    assert not json.loads(capsys.readouterr().out)["changed"]
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    assert unrelated.read_text(encoding="ascii") == "existing unrelated bootstrap identity\n"
    executable = shutil.which("ssh-keygen")
    if not executable:
        pytest.skip("Native OpenSSH public-key consumer unavailable")
    result = subprocess.run(
        [executable, "-l", "-f", str(target)], capture_output=True,
        text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0 and result.stdout.count("ED25519") == 2


@pytest.mark.parametrize("change", [
    "older", "equal-different", "wrong-key", "foreign-file", "missing-owner", "different-user",
])
def test_refusal_preserves_existing_authorization(tmp_path, capsys, change):
    argv, directory = arguments(tmp_path, revision=2)
    assert main(argv + ["--apply"]) == 0
    target = Path(json.loads(capsys.readouterr().out)["authorization_file"])
    if change == "older":
        directory["revision"] = 1
    elif change == "equal-different":
        directory["machines"][1]["public_key"] = key(3)
    elif change == "wrong-key":
        directory["machines"][0]["public_key"] = key(3)
    elif change == "different-user":
        directory["revision"] += 1
        directory["machines"][0]["user"] = "other-user"
    elif change == "missing-owner":
        (target.parent / "owner.json").unlink()
    else:
        target.write_text("foreign unrelated keys\n", encoding="utf-8")
    (tmp_path / "directory.json").write_text(json.dumps(directory), encoding="utf-8")
    before = target.read_bytes()
    assert main(argv + ["--apply"]) == 2
    assert "[FAIL]" in capsys.readouterr().err and target.read_bytes() == before


def test_new_admitted_keys_and_withdrawal_converge_without_restart(tmp_path, capsys):
    argv, directory = arguments(tmp_path)
    assert main(argv + ["--apply"]) == 0
    target = Path(json.loads(capsys.readouterr().out)["authorization_file"])
    directory["revision"] += 1
    directory["machines"][1]["public_key"] = key(3)
    (tmp_path / "directory.json").write_text(json.dumps(directory), encoding="utf-8")
    assert main(argv + ["--apply"]) == 0
    capsys.readouterr()
    assert key(3) in target.read_text(encoding="utf-8")
    assert key(2) not in target.read_text(encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["machines"][0]["inbound"] = False
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy

    directory["policy_digest"] = GatewaySSHPolicy.from_dict(policy).policy_digest
    directory["revision"] += 1
    (tmp_path / "directory.json").write_text(json.dumps(directory), encoding="utf-8")
    assert main(argv + ["--apply"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["authorized_peers"] == [] and not result["service_started"]
    assert target.read_text(encoding="utf-8").splitlines()[1:] == []


def test_interrupted_publication_keeps_revision_floor_for_exact_retry(tmp_path, monkeypatch, capsys):
    argv, directory = arguments(tmp_path)
    assert main(argv + ["--apply"]) == 0
    target = Path(json.loads(capsys.readouterr().out)["authorization_file"])
    before = target.read_bytes()
    directory["revision"] = 2
    directory["machines"].pop()
    directory_path = tmp_path / "directory.json"
    directory_path.write_text(json.dumps(directory), encoding="utf-8")
    original = authorization.atomic_write_text
    monkeypatch.setattr(authorization, "atomic_write_text", Mock(side_effect=OSError("fixture interrupted")))
    assert main(argv + ["--apply"]) == 2
    assert "fixture interrupted" in capsys.readouterr().err
    assert target.read_bytes() == before
    floor = next(target.parent.glob("gateway-provider-*.json"))
    assert json.loads(floor.read_text(encoding="utf-8"))["directory_revision"] == 2
    monkeypatch.setattr(authorization, "atomic_write_text", original)
    assert main(argv + ["--apply"]) == 0
    assert len(json.loads(capsys.readouterr().out)["authorized_peers"]) == 1


def test_wrong_owner_and_modified_body_are_not_repaired(tmp_path, capsys):
    argv, _ = arguments(tmp_path)
    assert main(argv + ["--apply"]) == 0
    target = Path(json.loads(capsys.readouterr().out)["authorization_file"])
    before = target.read_bytes()
    target.write_bytes(before + ("restrict,pty " + key(9) + "\n").encode())
    assert main(argv + ["--apply"]) == 2
    assert "changed outside its owner" in capsys.readouterr().err
    assert target.read_bytes() == before + ("restrict,pty " + key(9) + "\n").encode()
    target.write_bytes(before)
    owner = target.parent / "owner.json"
    owner.write_text(json.dumps({"target": {}, "controller": "different"}), encoding="utf-8")
    assert main(argv + ["--apply"]) == 2
    assert "another target/controller" in capsys.readouterr().err
    assert target.read_bytes() == before


def test_hardlinked_authorization_preserves_external_file(tmp_path, capsys):
    argv, _ = arguments(tmp_path)
    assert main(argv + ["--apply"]) == 0
    target = Path(json.loads(capsys.readouterr().out)["authorization_file"])
    other = tmp_path / "other"
    os.link(target, other)
    before = other.read_bytes()
    assert main(argv + ["--apply"]) == 2
    assert "unlinked" in capsys.readouterr().err
    assert other.read_bytes() == before and target.read_bytes() == before


def test_existing_service_directory_is_not_adopted(tmp_path, capsys):
    argv, _ = arguments(tmp_path)
    root = tmp_path / "native-authorization"
    root.mkdir(mode=0o700)
    # Use the same native creation path, then add unrelated service state.
    if os.name == "nt":
        root.rmdir()
        authorization._root(root)
    foreign = root / "unrelated-service.json"
    foreign.write_text("retain", encoding="ascii")
    assert main(argv + ["--apply"]) == 2
    assert "dedicated authorization directory" in capsys.readouterr().err
    assert foreign.read_text(encoding="ascii") == "retain"
    assert not (root / "owner.json").exists()


def test_permission_drift_is_not_silently_repaired(tmp_path, capsys):
    argv, _ = arguments(tmp_path)
    assert main(argv + ["--apply"]) == 0
    target = Path(json.loads(capsys.readouterr().out)["authorization_file"])
    before = target.read_bytes()
    if os.name == "nt":
        icacls = Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"
        subprocess.run(
            [str(icacls), str(target), "/grant", "*S-1-1-0:F"],
            capture_output=True, check=True, timeout=10, **no_window_kwargs(),
        )
    else:
        target.chmod(0o644)
    assert main(argv + ["--apply"]) == 2
    assert "[FAIL]" in capsys.readouterr().err and target.read_bytes() == before
