"""Explicit real Helm/chart rendering, without cluster, credentials or effects."""

import hashlib
import os
from pathlib import Path
import re
import time

import pytest

from fleet_manager.arc_owner import ArcDeployment
from fleet_manager.planning import canonical
from test_arc_owner import rig as rig
from test_runtime_identity import keys as keys


@pytest.mark.skipif(not os.environ.get("FLEET_ARC_TEST_HELM"), reason="explicit offline Helm lane")
@pytest.mark.parametrize("trailing_slash", [False, True])
def test_real_official_chart_consumes_typed_owner_values(rig, trailing_slash):
    from agent_procutil.owned_process import run_caller_owned

    helm = Path(os.environ["FLEET_ARC_TEST_HELM"])
    chart = Path(os.environ["FLEET_ARC_TEST_CHART"])
    expected = os.environ["FLEET_ARC_TEST_CHART_SHA256"]
    assert helm.is_absolute() and helm.is_file() and chart.is_absolute() and chart.is_file()
    content = chart.read_bytes()
    assert hashlib.sha256(content).hexdigest() == expected
    value = {**rig["value"], "chart_sha256": expected}
    if trailing_slash:
        value["settings"] = {**value["settings"], "github_url": value["settings"]["github_url"] + "/"}
    selected = ArcDeployment.from_dict(value, chart=content)
    values = rig["owner"]._values(selected, rig["allocation"])
    assert values["githubConfigUrl"] == "https://github.com/example/repository"
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("HELM_", "KUBE"))}
    environment["HELM_NO_PLUGINS"] = "1"
    started = time.monotonic()
    result = run_caller_owned([
        str(helm), "template", "example-runners", str(chart), "--namespace", "arc-runners",
        "--values", "-", "--kube-version", "1.31.0",
    ], cwd=rig["root"], env=environment, input_text=canonical(values).decode(),
       timeout=30, max_stdout_bytes=1024 * 1024, max_stderr_bytes=4096)
    assert result.returncode == 0, "official chart render failed"
    assert time.monotonic() - started < 30
    rendered = result.stdout
    assert "kind: AutoscalingRunnerSet" in rendered
    assert re.search(r'maxRunners:\s*2\b', rendered)
    assert re.search(r'minRunners:\s*0\b', rendered)
    assert "githubConfigSecret: existing-app" in rendered
    assert re.search(r"githubConfigUrl:\s*https://github.com/example/repository\s*\n", rendered)
    assert "kubernetes.io/hostname: node-a" in rendered
    assert "kubernetes.io/arch: amd64" in rendered
    assert "ephemeral-storage:" in rendered and "allowPrivilegeEscalation: false" in rendered
    assert re.search(r"automountServiceAccountToken:\s*false\b", rendered)
    assert "kind: Secret" not in rendered and "kind: Namespace" not in rendered
    assert "kind: CustomResourceDefinition" not in rendered
