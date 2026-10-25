"""The real TLS/native ARC bridge consumes an independently installed SDK."""

from __future__ import annotations

import os
import inspect
import json
from pathlib import Path
import shutil
import sys

import pytest

from test_client_distribution import build, run


def test_wheel_budget_does_not_change_sdist_or_native_execution(monkeypatch, tmp_path):
    calls = []

    def capture(argv, **kwargs):
        calls.append((argv, kwargs))
        return object()

    monkeypatch.setattr("test_client_distribution.subprocess.run", capture)
    build(tmp_path, tmp_path / "wheel")
    build(tmp_path, tmp_path / "sdist", operation="build_sdist")
    run(["fixed-native-consumer"])
    assert [kwargs["timeout"] for _, kwargs in calls] == [60, 30, 30]
    assert all(kwargs["capture_output"] and kwargs["check"] is False for _, kwargs in calls)


@pytest.mark.parametrize("profile", ["jwt-pop-v1", "enrolled-pop-v1"])
@pytest.mark.parametrize("lost_reply", [False, True])
def test_installed_local_gateway_uses_original_identity_and_native_owner(
    tmp_path, request, monkeypatch, profile, lost_reply,
):
    from joserfc.jwk import ECKey
    from types import SimpleNamespace

    from gateway_fixtures import pem_key
    import test_arc_owner
    from test_gateway_enrolled_identity import EnrolledRig
    from gateway_service_transport_fixtures import Rig

    artifact, _ = request.getfixturevalue("wheel")
    uv = shutil.which("uv")
    assert uv, "native Gateway proof requires the runner's package manager"
    installed = tmp_path / "installed"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"
    result = run([
        uv, "pip", "install", "--python", sys.executable, "--target", str(installed),
        "--no-index", "--no-deps", "--link-mode", "copy", str(artifact),
    ], env=environment)
    assert result.returncode == 0, result.stderr
    root = Path(__file__).parents[2]
    caller = tmp_path / "caller"
    caller.mkdir()
    probe = caller / "probe.py"
    shutil.copy2(Path(__file__).with_name("gateway_arc_installed_probe.py"), probe)
    shutil.copytree(
        root / "libs" / "agent-procutil" / "src" / "agent_procutil",
        caller / "support" / "agent_procutil",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    script = (
        "from pathlib import Path\n"
        "import runpy,sys\n"
        "installed=Path(sys.argv.pop(1)).resolve()\n"
        "support=Path(sys.argv.pop(1)).resolve()\n"
        "probe=sys.argv.pop(1)\n"
        "case=sys.argv.pop(1)\n"
        "sys.path[:0]=[str(installed),str(support)]\n"
        "sys.argv=[probe,case,str(installed)]\n"
        "runpy.run_path(probe,run_name='__main__')\n"
    )
    case = caller / "native"
    case.mkdir()
    native = inspect.unwrap(test_arc_owner.rig)(
        case, monkeypatch, tuple(ECKey.generate_key("P-256") for _ in range(2)),
        SimpleNamespace(param="wall-clock-native"))
    gateway = EnrolledRig(case) if profile == "enrolled-pop-v1" else Rig(case)
    now = native["harness"].clock.value
    gateway.h.document["manifest"]["fleet"] = "example"
    gateway.h.document["manifest"]["gateway"] = "public-gateway"
    gateway.h.document["manifest"]["slots"][0]["routes"][0]["capabilities"] = ["http"]
    trust = gateway.h.trust if profile == "enrolled-pop-v1" else gateway.h.document["identity"]
    trust["trust_valid_until"] = now + 3600
    gateway.save()
    for name, key in (("issuer", gateway.h.keys[0]), ("caller", gateway.h.keys[3])):
        (case / f"test-{name}.pem").write_bytes(pem_key(key.get_op_key("sign")))
    (case / "consumer.json").write_text(json.dumps({
        "now": now, "profile": profile, "trust": trust, "lost_reply": lost_reply,
        "proof_key": gateway.h.keys[3].thumbprint(),
    }))
    (case / "operation.json").write_bytes(native["request"]().encoded)
    (case / "selection.json").write_bytes(native["selected"][0].encoded)
    (case / "selected-chart.tgz").write_bytes(native["selected"][0].chart)
    if lost_reply:
        (case / "lost-reply").touch()
    result = run(
        [sys.executable, "-I", "-B", "-c", script, str(installed),
         str(caller / "support"), str(probe), str(case)],
        cwd=caller, env=environment, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "installed-arc-cell-passed"
