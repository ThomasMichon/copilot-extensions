"""Execute the temporary workflow adapter against legacy and repaired promoters."""
from __future__ import annotations

import inspect
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from _promotion_artifact_fixture import fixture_yaml
from _promotion_workflow_test_support import heredoc, source_owned
from test_promotion_python_artifacts import (
    _commit, _fake_builder, _git, artifacts, promotion,
    no_ambient_git_identity as _identity_fixture, repo as _repo_fixture,
)

repo = _repo_fixture
no_ambient_git_identity = _identity_fixture
_ROOT = Path(__file__).resolve().parent.parent


def _adapter() -> str:
    workflow = fixture_yaml((_ROOT / ".github/workflows/validate-and-promote.yml").read_text())
    if source_owned(workflow["jobs"]):
        step = next(step for step in workflow["jobs"]["promote"]["steps"]
                    if step.get("id") == "promote")
        return heredoc(step["run"])
    step = next(
        step for step in workflow["jobs"]["promote"]["steps"]
        if step.get("name") == "Prepare temporary trusted-main promoter adapter"
    )
    return step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def _legacy_promoter(monkeypatch):
    # Recreate the pre-shared-reader call boundary, keeping the real CLI and
    # promotion implementation rather than replacing either with a passing fake.
    source = inspect.getsource(promotion.promote)
    old_import = "PromotionArtifactError, export_artifacts, validate_composed_artifacts,"
    new_import = "PromotionArtifactError, candidate_projects, export_artifacts, validate_artifacts,"
    old_call = """validate_composed_artifacts(
                    python_artifacts_dir, scratch=scratch, content_tree=content_tree,
                    dev_head=dev_head, main_before=main_head,
                )"""
    new_call = """validate_artifacts(
                    python_artifacts_dir, content_tree=content_tree,
                    dev_head=dev_head, main_before=main_head,
                    projects=candidate_projects(scratch), candidate_root=scratch,
                )"""
    assert source.count(old_import) == source.count(old_call) == 1
    namespace = dict(promotion.__dict__)
    exec(compile(source.replace(old_import, new_import).replace(old_call, new_call),
                 "<legacy-promoter-call>", "exec"), namespace)
    monkeypatch.setattr(promotion, "promote", namespace["promote"])
    monkeypatch.delattr(artifacts, "validate_composed_artifacts")


def _run_adapter(repo, directory, dev, main, monkeypatch):
    current = source_owned(fixture_yaml(
        (_ROOT / ".github/workflows/validate-and-promote.yml").read_text(),
    )["jobs"])
    monkeypatch.setattr(sys, "argv", [
        "trusted-promoter.py", str(_ROOT / "tools" if current else _ROOT), "--repo", str(repo),
        "--dev-ref", dev, "--main-ref", main, "--python-artifacts-dir", str(directory),
    ])
    if current:
        def dispatch(path: str, *, run_name: str) -> None:
            # Keep the real CLI's monkeypatched Git/build fixtures; the native
            # isolation test below separately executes the unmocked runpy loader.
            assert Path(path) == _ROOT / "tools/promote_release.py"
            assert run_name == "__main__"
            raise SystemExit(promotion.main(sys.argv[1:]))
        monkeypatch.setattr(runpy, "run_path", dispatch)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(artifacts, "validate_artifacts", artifacts.validate_artifacts)
    with pytest.raises(SystemExit) as exited:
        exec(compile(_adapter(), "<workflow-promoter-adapter>", "exec"), {})
    return exited.value.code


@pytest.mark.parametrize("legacy", [False, True], ids=["repaired", "legacy"])
@pytest.mark.parametrize("failure", [None, "bundle", "checkout", "identity"])
def test_adapter_uses_real_cli_validation_and_failure_boundary(
    repo, tmp_path, monkeypatch, capsys, legacy, failure,
):
    _git(["checkout", "-q", "dev"], repo)
    _fake_builder(repo, monkeypatch)
    (repo / ".gitattributes").write_text("*.ps1 text eol=crlf\n", encoding="utf-8")
    relative = Path("plugins/demo-plugin/generated.ps1")
    payload = b"first line\nsecond line\n"
    (repo / relative).write_bytes(payload)
    dev = _commit(repo, "generated checkout attributes")
    main = _git(["rev-parse", "main"], repo)
    consume = promotion.consume_pending_changes

    def materialize(scratch, **kwargs):
        summary = consume(scratch, **kwargs)
        (scratch / relative).write_bytes(payload)
        return summary

    monkeypatch.setattr(promotion, "consume_pending_changes", materialize)
    directory = tmp_path / "artifacts"
    promotion.promote(
        repo=repo, dev_ref=dev, main_ref=main, build_python_artifacts_dir=directory,
    )
    if legacy:
        _legacy_promoter(monkeypatch)
        with pytest.raises(promotion.PromotionError, match="payload hash differs"):
            promotion.promote(
                repo=repo, dev_ref=dev, main_ref=main, python_artifacts_dir=directory,
            )
    if failure == "bundle":
        bundle = next(directory.glob("*.zip"))
        bundle.write_bytes(b"corrupt")
    elif failure == "identity":
        import json
        index = directory / artifacts.INDEX_NAME
        data = json.loads(index.read_text())
        data["dev_head"] = "0" * 40
        index.write_text(json.dumps(data), encoding="utf-8")

    run = artifacts._run
    subprocess_run = subprocess.run
    checkouts = []

    def fail_checkout(argv, *args, **kwargs):
        if argv[0] == "git" and "checkout-index" in argv:
            return subprocess.CompletedProcess(argv, 128, "", "forced canonical checkout failure")
        return subprocess_run(argv, *args, **kwargs)

    if failure == "checkout":
        monkeypatch.setattr(subprocess, "run", fail_checkout)

    def checkout(argv, **kwargs):
        if argv[0] == "git" and "checkout-index" in argv:
            checkouts.append(Path(argv[-1].removeprefix("--prefix=")))
        return run(argv, **kwargs)

    monkeypatch.setattr(artifacts, "_run", checkout)
    capsys.readouterr()
    current = source_owned(fixture_yaml(
        (_ROOT / ".github/workflows/validate-and-promote.yml").read_text(),
    )["jobs"])
    unsupported_legacy = current and legacy
    assert _run_adapter(repo, directory, dev, main, monkeypatch) == (
        1 if failure or unsupported_legacy else 0
    )
    output = capsys.readouterr()
    assert "Traceback" not in output.out + output.err
    if failure or unsupported_legacy:
        expected = {
            "bundle": "artifact digest mismatch", "identity": "dev_head",
            "checkout": "forced canonical checkout failure",
        }.get(failure, "payload hash differs")
        if unsupported_legacy and failure not in ("bundle", "identity"):
            expected = "payload hash differs"
        assert expected in output.err
        assert "promoted:" not in output.out
    assert len(checkouts) == (0 if unsupported_legacy else 1)
    assert all(not path.exists() for path in checkouts)
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1


def test_adapter_isolates_trusted_imports_and_preserves_arguments(tmp_path):
    trusted = tmp_path / "trusted"
    tools = trusted / "tools"
    tools.mkdir(parents=True)
    (tools / "promote_release.py").write_text(
        "import sys\n"
        "def main(argv):\n"
        "    assert argv == ['--push', '--candidate-branch', 'release/example']\n"
        "    assert sys.flags.isolated and sys.dont_write_bytecode\n"
        "    return 23\n"
        "if __name__ == '__main__': raise SystemExit(main(sys.argv[1:]))\n", encoding="utf-8",
    )
    (tools / "promotion_python_artifacts.py").write_text(
        "def validate_composed_artifacts(): pass\n", encoding="utf-8",
    )
    hostile = tmp_path / "candidate"
    hostile.mkdir()
    for name in ("promote_release.py", "promotion_python_artifacts.py", "sitecustomize.py"):
        (hostile / name).write_text("raise RuntimeError('candidate code executed')\n", encoding="utf-8")
    driver = tmp_path / "trusted-promoter.py"
    driver.write_text(_adapter(), encoding="utf-8")
    current = source_owned(fixture_yaml(
        (_ROOT / ".github/workflows/validate-and-promote.yml").read_text(),
    )["jobs"])
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(driver), str(tools if current else trusted),
         "--push", "--candidate-branch", "release/example"],
        cwd=hostile, env={**os.environ, "PYTHONPATH": str(hostile)},
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 23, result.stdout + result.stderr


def test_both_workflow_promotion_branches_use_isolated_adapter():
    workflow = fixture_yaml((_ROOT / ".github/workflows/validate-and-promote.yml").read_text())
    step = next(
        step for step in workflow["jobs"]["promote"]["steps"]
        if step.get("id") == "promote"
    )
    if source_owned(workflow["jobs"]):
        assert '-I -B - "$GITHUB_WORKSPACE/reviewed-helpers/tools" "${args[@]}"' in step["run"]
        assert 'args=(--repo "$GITHUB_WORKSPACE"' in step["run"]
        assert 'args+=(--push --candidate-branch "$CANDIDATE_BRANCH"' in step["run"]
        assert "runpy.run_path" in _adapter()
    else:
        assert step["run"].count(
            '-I -B "$RUNNER_TEMP/trusted-promoter.py" "$GITHUB_WORKSPACE"'
        ) == 2
    assert " tools/promote_release.py " not in step["run"]
