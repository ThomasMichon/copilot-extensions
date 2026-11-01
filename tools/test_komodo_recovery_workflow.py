"""Native recovery is an isolated portfolio with caller-owned cleanup and no skip credit."""

import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load(
    (ROOT / ".github/workflows/installation-context-full.yml").read_text("utf-8")
)
JOB = WORKFLOW["jobs"]["komodo-recovery-linux"]
STEP = next(step for step in JOB["steps"] if "run" in step)
BODY = STEP["run"].split("python - <<'PY'\n", 1)[1].removesuffix("PY\n")


def _enabled(expression, portfolio, event="workflow_dispatch"):
    for field, value in (("github.event_name", event), ("inputs.portfolio", portfolio)):
        expression = re.sub(
            re.escape(field) + r" (==|!=) '([^']+)'",
            lambda match: str((value == match[2]) == (match[1] == "==")), expression,
        )
    expression = expression.replace("&&", "and").replace("||", "or")
    assert re.fullmatch(r"(True|False|and|or|\s|\(|\))+", expression)
    return eval(expression, {"__builtins__": {}})


def test_recovery_portfolio_is_manual_and_does_not_expand_other_routes():
    trigger = WORKFLOW.get("on", WORKFLOW.get(True))
    portfolio = trigger["workflow_dispatch"]["inputs"]["portfolio"]
    assert portfolio["default"] == "context"
    assert "komodo-recovery" in portfolio["options"]
    assert {name for name, job in WORKFLOW["jobs"].items()
            if _enabled(job["if"], "komodo-recovery")} == {"komodo-recovery-linux"}
    assert not _enabled(JOB["if"], "komodo-recovery", "schedule")
    assert all(not _enabled(JOB["if"], value)
               for value in portfolio["options"] if value != "komodo-recovery")
    assert WORKFLOW["permissions"] == {"contents": "read"}
    assert not JOB.get("continue-on-error") and "strategy" not in JOB


@pytest.mark.parametrize("mode", ["pass", "prepared", "nonzero", "skipped", "empty", "unreaped"])
def test_actual_workflow_body_preserves_exit_cleanup_and_native_execution_credit(tmp_path, monkeypatch, mode):
    monkeypatch.chdir(tmp_path)
    calls = []
    python = tmp_path / ".test-venvs/linux/fleet-manager/bin/python"
    if mode == "prepared":
        python.parent.mkdir(parents=True)
        python.touch()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    temporary = SimpleNamespace(name=str(sandbox), retained=False)

    def retain():
        calls.append("retain")
        temporary.retained = True

    temporary.retain = retain

    class Unreaped(RuntimeError):
        pass

    def run(command, **options):
        calls.append("run")
        assert command[:6] == [str(python), "-I", "-m", "pytest", "-q", "--tb=short"]
        assert options["cwd"] == tmp_path / "fleet-manager"
        assert options["sandbox"] == sandbox and options["limits"].wall_seconds == 900
        assert options["env"] == {"isolated": str(sandbox)}
        selector = command[command.index("-k") + 1]
        for name in ("failed-retained-health", "lost-failure-publication", "lost-terminal-receipt",
                     "native-failure-to-repair", "repair-failure-to-native", "physical_state",
                     "komodo_incumbent"):
            assert name in selector
        assert str(tmp_path / "fleet-manager/tests/test_komodo_incumbent.py") in command
        if mode == "unreaped":
            raise Unreaped("original process remains owned")
        report = Path(command[command.index("--junitxml") + 1])
        report.write_text(
            '<testsuites><testsuite tests="' + ("0" if mode == "empty" else "21") +
            '" skipped="' + ("1" if mode == "skipped" else "0") + '"/></testsuites>',
            encoding="utf-8",
        )
        return 7 if mode == "nonzero" else 0

    runner = SimpleNamespace(
        default_python=lambda component: python,
        acquire=lambda wait, environment: SimpleNamespace(release=lambda: calls.append("release")),
        TestSandbox=lambda **options: temporary,
        prepare=lambda *arguments: calls.append("prepare"),
        isolated_environment=lambda environment, root: {"isolated": str(root)},
        Limits=lambda **options: SimpleNamespace(**options),
        run_contained=run, UnreapedProcessError=Unreaped,
        cleanup_sandbox=lambda value: calls.append("cleanup"),
    )
    monkeypatch.setattr(importlib.util, "spec_from_file_location",
                        lambda *arguments: SimpleNamespace(loader=SimpleNamespace(exec_module=lambda value: None)))
    monkeypatch.setattr(importlib.util, "module_from_spec", lambda spec: runner)
    expected = Unreaped if mode == "unreaped" else RuntimeError if mode in ("skipped", "empty") else SystemExit
    with pytest.raises(expected) as caught:
        exec(compile(BODY, "<komodo-recovery-workflow>", "exec"), {})
    if expected is SystemExit:
        assert caught.value.code == (7 if mode == "nonzero" else 0)
    assert ("prepare" in calls) == (mode != "prepared")
    assert calls[-2:] == (["retain", "release"] if mode == "unreaped" else ["cleanup", "release"])
