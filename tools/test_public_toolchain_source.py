from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_builder_cli as cli
import build_toolchain_lock as btl
import public_toolchain_source as public
from seasoned_public_feed import SeasonedPublicRelease

PINS = {"setuptools": "84.0.0", "wheel": "0.46.0", "packaging": "25.0"}
HASHES = {name: ("a" * 64,) for name in PINS}


@pytest.mark.parametrize(
    "workflow",
    sorted(path.name for path in (Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml")),
)
def test_workflow_root_uv_resolvers_use_canonical_seasoning(workflow):
    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((root / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    resolver = re.compile(r"(?:^|\n|&&|;)\s*uv\s+(?:run|pip\s+install|tool\s+(?:run|install))\b")
    for job_name, job in document.get("jobs", {}).items():
        for step in job.get("steps", []):
            cwd = step.get(
                "working-directory",
                job.get("defaults", {}).get("run", {}).get(
                    "working-directory", document.get("defaults", {}).get("run", {}).get("working-directory", "."),
                ),
            )
            if cwd not in (".", "./"):
                continue
            assert not resolver.search(step.get("run", "")), (
                workflow, job_name, step.get("name"), "root has no pyproject seasoning policy",
            )


@pytest.mark.parametrize("workflow", ["ci.yml", "payload-cwd-contract.yml"])
def test_workflow_host_pytest_provisioning_is_seasoned(workflow):
    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((root / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    job_names = (
        ["windows-python-artifact-builder", "test-runner-linux", "test-runner-windows",
         "installation-context-windows",
         "coverage-guided-selection-integration"]
        if workflow == "ci.yml" else ["linux", "windows"]
    )
    for job_name in job_names:
        consumers = 0
        for step in document["jobs"][job_name]["steps"]:
            run = step.get("run", "")
            if "python -m pytest" not in run:
                continue
            consumers += 1
            provision = "python tools/seasoned_uv.py --uv uv -- --system pytest"
            assert provision in run, (job_name, step.get("name"))
            assert run.index(provision) < run.index("python -m pytest")
            assert "--no-deps" not in run
            assert "--no-build-isolation" not in run
        assert consumers


def test_ci_python_floor_preserves_interpreter_and_hash_contract():
    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = document["jobs"]["checks"]["steps"]
    index = next(i for i, step in enumerate(steps)
                 if step.get("name") == "Promotion artifact compatibility (Python 3.10 floor)")
    floor = steps[index]
    assert steps[index - 1]["uses"] == steps[index + 1]["uses"] == "actions/setup-python@v5"
    assert steps[index - 1]["with"]["python-version"] == "3.10"
    assert steps[index + 1]["with"]["python-version"] == "3.12"
    assert steps[index - 1]["if"] == floor["if"] == steps[index + 1]["if"]
    assert floor["timeout-minutes"] == 2
    run = floor["run"]
    assert (
        "python tools/seasoned_uv.py --uv uv -- --system "
        "--only-binary :all: --require-hashes -r tools/artifact_validation_requirements.txt &&"
    ) in run
    assert "python tools/seasoned_uv.py --uv uv -- --system pytest pyyaml tomli &&" in run
    assert "python -m pytest -q tools/test_promotion_python_artifacts.py" in run
    assert "--no-config" not in run
    assert "--index-url" not in run


@pytest.mark.parametrize("changed", [
    "tools/seasoned_public_feed.py",
    "worktree-manager/scripts/picker-snapshot/install.py",
    "tools/seasoned_uv.py",
    "tools/dependency_age.py",
    "tools/check-dependency-age.py",
    "libs/installer-engine/installer_dependency_policy.py",
    "plugins/demo/pyproject.toml",
    ".github/actions/package-lock.json",
    "tools/artifact_validation_requirements.txt",
    ".github/dependabot.yml",
    ".github/workflows/ci.yml",
])
def test_ci_dependency_policy_changes_trigger_age_guard(changed):
    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    step = next(step for step in document["jobs"]["checks"]["steps"]
                if step.get("id") == "dependency-age")
    expression = re.search(r"grep -Eq\s*\\\n\s*'([^']+)'", step["run"])
    assert expression is not None
    assert re.search(expression.group(1), changed), changed


def _artifact_scope(job_name: str) -> tuple[dict, str]:
    root = Path(__file__).resolve().parents[1]
    jobs = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    detector = next(step for step in jobs[job_name]["steps"]
                    if step.get("id") == "python-artifact-builder")
    expression = re.search(r"grep -Eq\s*\\\n\s*'([^']+)'", detector["run"])
    assert expression is not None
    return jobs, expression.group(1)


def test_windows_source_contracts_keep_separate_budgets_without_dropping_cases():
    jobs, _ = _artifact_scope("checks")
    builder = jobs["windows-python-artifact-builder"]
    candidate = jobs["promotion-candidate-windows"]
    assert builder["timeout-minutes"] == 10
    assert candidate["timeout-minutes"] == 5
    assert candidate["needs"] == "windows-python-artifact-builder"
    composed = next(step for step in builder["steps"]
                    if step.get("name") == "Composed promotion artifact contracts (Windows)")
    source = next(step for step in candidate["steps"]
                  if step.get("name") == "Source-owned qualification and publication contracts (Windows)")
    assert source["if"] == "needs.windows-python-artifact-builder.outputs.run == 'true'"
    assert " -k " not in source["run"]
    for name in (
        "test_qualify_promotion_candidate.py", "test_promotion_source_identity.py",
        "test_reuse_promotion_artifacts.py", "test_publish_candidate_artifacts.py",
        "test_promotion_source_alias.py", "test_release_publication_boundary.py",
        "test_rollback_release.py",
    ):
        assert composed["run"].count("tools/" + name) == 0
        assert source["run"].count("tools/" + name) == 1
    for name in (
        "test_promotion_control.py", "test_promotion_python_artifacts.py",
        "test_promotion_wheel_sources.py", "test_promotion_workflow_adapter.py",
    ):
        assert composed["run"].count("tools/" + name) == 1
    for gate in ("pr-gate", "promotion-gate"):
        assert {"windows-python-artifact-builder", "promotion-candidate-windows"} <= set(jobs[gate]["needs"])


@pytest.mark.parametrize("job_name", ["checks", "windows-python-artifact-builder"])
@pytest.mark.parametrize("changed", [
    ".gitattributes", "plugins/demo/.gitattributes", "libs/library/src/.gitattributes",
    "worktree-manager/.gitattributes", "tools/.gitattributes",
    ".changefiles/source-bump.json", ".github/plugin/marketplace.json",
    "plugins/demo/src/demo/__init__.py", "plugins/demo/pyproject.toml",
    "plugins/demo/plugin.json", "plugins/demo/libs/library/README.md",
    "plugins/demo/packaged-assets.json", "plugins/demo/docs/copied.md",
    "libs/library/src/library/__init__.py", "libs/library/pyproject.toml", "libs/library/LICENSE",
    "worktree-manager/bin/launch-session.sh", "fleet-manager/pyproject.toml",
    "services/index/pyproject.toml", "services/dispatch/src/service.py",
    "docs/patterns/entity-relationship-model.md",
    *("tools/" + name + ".py" for name in (
        "materialize_main", "materialize_installer_engine", "materialize_launch_wrapper_assets",
        "nested_uv_editable_ref", "uv_editable_ref", "installer_engine_ref",
        "launch_wrapper_assets_ref", "standalone_consumers", "accumulate_bumps", "changefile",
        "seasoned_uv", "materialized_candidate_inventory", "validate_materialized_candidate",
        "test_validate_materialized_candidate", "promotion_candidate", "promotion_control",
        "build_python_artifacts", "artifact_builder_cli", "build_toolchain_lock",
        "public_toolchain_source", "seasoned_public_feed", "governed_feed_trust",
        "promotion_python_artifacts", "publish_python_artifacts", "promotion_artifact_readiness",
        "test_promotion_python_artifacts", "test_public_toolchain_source",
    )),
])
def test_ci_artifact_scope_covers_source_and_recipe_inputs(job_name: str, changed: str) -> None:
    _, expression = _artifact_scope(job_name)
    assert re.search(expression, changed), (job_name, changed)


@pytest.mark.parametrize("job_name", ["checks", "windows-python-artifact-builder"])
@pytest.mark.parametrize("changed", [
    "README.md", "TESTING.md", "docs/unrelated.md", "efforts/active/example/README.md",
    "libs-sibling/library/pyproject.toml", "services/other/pyproject.toml",
    ".gitattributes.backup", "docs/.gitattributes.backup",
    "tools/materialize_main.py.backup", "tools/unrelated.py",
])
def test_ci_artifact_scope_excludes_unrelated_paths(job_name: str, changed: str) -> None:
    _, expression = _artifact_scope(job_name)
    assert not re.search(expression, changed), (job_name, changed)


def test_ci_artifact_scope_covers_local_recipe_import_closure() -> None:
    root = Path(__file__).resolve().parents[1]
    pending = [
        "materialize_main", "build_python_artifacts", "promotion_python_artifacts",
        "publish_python_artifacts", "promotion_artifact_readiness",
        "materialized_candidate_inventory", "validate_materialized_candidate",
    ]
    visited: set[str] = set()
    _, expression = _artifact_scope("checks")
    while pending:
        module = pending.pop()
        path = root / "tools" / f"{module}.py"
        if module in visited or not path.is_file():
            continue
        visited.add(module)
        assert re.search(expression, path.relative_to(root).as_posix()), module
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                pending.append(node.module)


def test_ci_artifact_scope_covers_declared_canonical_copies() -> None:
    root = Path(__file__).resolve().parents[1]
    _, expression = _artifact_scope("checks")
    sources: set[str] = set()
    for declaration in (root / "plugins").rglob("*assets.json"):
        manifest = json.loads(declaration.read_text(encoding="utf-8"))
        sources.update(f"{manifest['canonicalDir']}/{name}" for name in manifest["files"])
    for path in (root / "plugins").rglob("*.md"):
        with path.open(encoding="utf-8") as stream:
            pointer = re.match(r"<!--\s*VENDOR_POINTER:\s*source=(\S+)\s+kind=file\s*-->", stream.readline())
        if pointer:
            sources.add(pointer.group(1))
    assert sources
    assert all(re.search(expression, source) for source in sources), sorted(sources)


def test_ci_artifact_scopes_keep_existing_bounded_platform_recipes() -> None:
    jobs, linux = _artifact_scope("checks")
    _, windows = _artifact_scope("windows-python-artifact-builder")
    assert linux == windows
    for job_name, step_name in (
        ("checks", "Governed Python artifact build/toolchain-lock guard tests"),
        ("checks", "Promotion artifact compatibility (Python 3.10 floor)"),
        ("windows-python-artifact-builder", "Composed promotion artifact contracts (Windows)"),
    ):
        step = next(step for step in jobs[job_name]["steps"] if step.get("name") == step_name)
        assert step["if"] == "steps.python-artifact-builder.outputs.run == 'true'"
        assert "tools/test_promotion_python_artifacts.py" in step["run"]
        assert "continue-on-error" not in step
        if "3.10" in step_name:
            assert "materialized_producer_verifier_roundtrip" in step["run"]
    candidate = jobs["promotion-candidate-windows"]
    assert candidate["needs"] == "windows-python-artifact-builder"
    step = next(step for step in candidate["steps"]
                if step.get("name") == "Native candidate readiness and retry contracts (Windows)")
    assert step["if"] == "needs.windows-python-artifact-builder.outputs.run == 'true'"
    assert "tools/test_promotion_artifact_readiness.py" in step["run"]
    assert "tools/test_promotion_candidate_retry.py" in step["run"]
    assert "continue-on-error" not in candidate


@pytest.mark.parametrize("workflow,job_name,step_name", [
    ("ci.yml", "checks", "Required source metadata contract tests"),
    ("source-attribution-departures.yml", "refresh-departed-source-attribution", "Install locked metadata parser without lifecycle scripts"),
    ("workflow-lockdown-guard.yml", "source-attribution", "Install locked metadata parser without lifecycle scripts"),
    ("workflow-lockdown-guard.yml", "refresh-source-attribution", "Install locked metadata parser without lifecycle scripts"),
])
def test_workflow_action_parsers_use_existing_npm_owner(workflow, job_name, step_name):
    root = Path(__file__).resolve().parents[1]
    document = yaml.safe_load((root / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    step = next(step for step in document["jobs"][job_name]["steps"] if step.get("name") == step_name)
    assert step.get("working-directory", ".") == "."
    assert step["run"].splitlines()[0] == (
        "python worktree-manager/scripts/picker-snapshot/install.py --directory .github/actions "
        "--health-module commonmark --health-module parse5"
    )
    assert not re.search(r"\bnpm\s+(?:ci|install)\b", step["run"])


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(btl, "run_uv_install", lambda uv, args, **kwargs: btl.subprocess.run(
        [uv, "pip", "install", *args], **kwargs,
    ))
    monkeypatch.setattr(
        public, "_opaque_index_identity",
        lambda value: hashlib.sha256(value.encode()).hexdigest(),
    )
    monkeypatch.setattr(btl, "_restrict_file_to_owner", lambda path: None)
    monkeypatch.setattr(btl, "_resolve_interpreter_identity", lambda python, env: "/python/3.12")
    monkeypatch.setattr(
        btl, "_validated_trusted_index_url",
        lambda env: pytest.fail("public mode must not label an ambient index governed"),
    )
    monkeypatch.setattr(
        public, "resolve_seasoned_public_release",
        lambda name, **kwargs: SeasonedPublicRelease(PINS[name], HASHES[name]),
    )
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:2] == ["uv", "venv"]:
            python = btl._venv_python_path(Path(cmd[3]))
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
        elif cmd[:3] == ["uv", "pip", "install"]:
            config = Path(kwargs["env"]["UV_CONFIG_FILE"]).read_text(encoding="utf-8")
            assert 'url = "https://pypi.org/simple"' in config
            assert cmd[3:5] == ["--only-binary", ":all:"]
            assert cmd[7:9] == ["--require-hashes", "--requirements"]
            lines = Path(cmd[9]).read_text().splitlines()
            assert set(lines) == {
                f"{name}=={version} --hash=sha256:{'a' * 64}" for name, version in PINS.items()
            }
            assert "UV_INDEX" not in kwargs["env"]
            assert "UV_DEFAULT_INDEX" not in kwargs["env"]
        else:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(PINS), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(btl.subprocess, "run", run)
    return calls


def test_macos_construction_lane_uses_stock_shell_and_required_gate():
    root = Path(__file__).resolve().parents[1]
    jobs = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    job = jobs["construction-macos"]
    assert job["runs-on"] == "macos-latest"
    assert "construction-macos" in jobs["pr-gate"]["needs"]
    assert "continue-on-error" not in job
    step = next(step for step in job["steps"] if step.get("name", "").startswith("Native Bash"))
    assert step["env"]["VERSIONED_RUNTIME_TEST_BASH"] == "/bin/bash"
    assert step["env"]["VERSIONED_RUNTIME_TEST_SOURCE"].endswith(
        "/libs/versioned-runtime/versioned_runtime.py"
    )
    assert 'test "${BASH_VERSINFO[0]}" -eq 3' in step["run"]
    assert "tools/seasoned_uv.py" in step["run"]
    assert "--noconftest" in step["run"]
    assert "test_failed_construction.py" in step["run"]


def test_public_toolchain_pins_and_reuses(tmp_path, monkeypatch, isolated):
    monkeypatch.setenv("UV_INDEX", "https://untrusted.example/simple")
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://ambient.example/simple")
    venv = tmp_path / "toolchain"
    first = btl.resolve_toolchain_lock(venv, source_policy="seasoned-public")
    second = btl.resolve_toolchain_lock(venv, source_policy="seasoned-public")
    assert first.lock_id == second.lock_id
    assert first.venv_python == second.venv_python
    assert sum(cmd[:3] == ["uv", "pip", "install"] for cmd, _ in isolated) == 1
    marker = json.loads((venv / btl._PROVENANCE_MARKER_NAME).read_text())
    assert marker == {
        **public.PublicToolchainSource(7, PINS, HASHES).provenance, "python": "/python/3.12",
    }
    assert "validated_index_identity" not in marker
    assert first.record["provenance"] == marker


@pytest.mark.parametrize("change", ["age", "pins", "python", "policy", "hashes"])
def test_distinct_source_identity_preserves_occupied_slot(tmp_path, monkeypatch, isolated, change):
    venv = tmp_path / "shared"
    first = btl.resolve_toolchain_lock(venv, source_policy="seasoned-public")
    original = (venv / btl._PROVENANCE_MARKER_NAME).read_bytes()
    age = 7
    policy = "seasoned-public"
    if change == "age":
        age = 8
    elif change == "pins":
        monkeypatch.setitem(PINS, "wheel", "0.45.0")
    elif change == "python":
        monkeypatch.setattr(btl, "_resolve_interpreter_identity", lambda python, env: "/python/3.13")
    elif change == "hashes":
        monkeypatch.setitem(HASHES, "wheel", ("b" * 64,))
        monkeypatch.setattr(btl, "_query_toolchain_versions", lambda python: dict(PINS))
        original_run = btl.subprocess.run
        def run(cmd, **kwargs):
            if cmd[:3] == ["uv", "pip", "install"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return original_run(cmd, **kwargs)
        monkeypatch.setattr(btl.subprocess, "run", run)
    else:
        policy = "governed"
        monkeypatch.setattr(btl, "_validated_trusted_index_url", lambda env: ("https://feed.example/simple", None))
        # The governed path installs bare package names from its own feed.
        monkeypatch.setattr(btl, "_query_toolchain_versions", lambda python: dict(PINS))
        original_run = btl.subprocess.run

        def run(cmd, **kwargs):
            if cmd[:3] == ["uv", "pip", "install"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return original_run(cmd, **kwargs)
        monkeypatch.setattr(btl.subprocess, "run", run)
    second = btl.resolve_toolchain_lock(
        venv, source_policy=policy, min_age_days=age if policy == "seasoned-public" else None,
    )
    assert second.venv_python != first.venv_python
    assert second.lock_id != first.lock_id
    assert (venv / btl._PROVENANCE_MARKER_NAME).read_bytes() == original


def test_wrong_installed_pin_never_published(tmp_path, monkeypatch, isolated):
    monkeypatch.setattr(btl, "_query_toolchain_versions", lambda python: {**PINS, "wheel": "0.1"})
    with pytest.raises(btl.ArtifactBuildError, match="do not match seasoned pins"):
        btl.resolve_toolchain_lock(tmp_path / "venv", source_policy="seasoned-public")
    assert list(tmp_path.iterdir()) == []


def test_reused_pin_tampering_fails_closed(tmp_path, monkeypatch, isolated):
    venv = tmp_path / "venv"
    btl.resolve_toolchain_lock(venv, source_policy="seasoned-public")
    monkeypatch.setattr(btl, "_query_toolchain_versions", lambda python: {**PINS, "wheel": "0.1"})
    with pytest.raises(btl.ArtifactBuildError, match="do not match seasoned pins"):
        btl.resolve_toolchain_lock(venv, source_policy="seasoned-public")
    assert len(list(tmp_path.iterdir())) == 1


def test_metadata_error_never_creates_venv(tmp_path, monkeypatch, isolated):
    def fail(*args, **kwargs):
        raise public.SeasonedVersionError("metadata unavailable")
    monkeypatch.setattr(public, "resolve_seasoned_public_release", fail)
    with pytest.raises(btl.ArtifactBuildError, match="metadata unavailable") as error:
        btl.resolve_toolchain_lock(tmp_path / "venv", source_policy="seasoned-public")
    assert isinstance(error.value.__cause__, public.SeasonedVersionError)
    assert not isolated
    assert list(tmp_path.iterdir()) == []


def test_default_never_uses_public_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(btl, "_validated_trusted_index_url", lambda env: None)
    monkeypatch.setattr(btl, "resolve_public_source", lambda *args: pytest.fail("implicit public fallback"))
    with pytest.raises(btl.ArtifactBuildError, match="no affirmatively trusted"):
        btl.resolve_toolchain_lock(tmp_path / "venv")


def test_unknown_policy_rejected_before_io(tmp_path, monkeypatch):
    monkeypatch.setattr(btl, "resolve_public_source", lambda *args: pytest.fail("unexpected network"))
    with pytest.raises(btl.ArtifactBuildError, match="unknown toolchain source"):
        btl.resolve_toolchain_lock(tmp_path / "venv", source_policy="typo")


@pytest.mark.parametrize("age", [0, 7, 10, False])
def test_governed_api_rejects_unused_seasoning(tmp_path, age):
    with pytest.raises(btl.ArtifactBuildError, match="only to seasoned-public"):
        btl.resolve_toolchain_lock(tmp_path / "venv", min_age_days=age)


def test_incompatible_pin_solver_failure_is_not_relaxed(tmp_path, monkeypatch, isolated):
    original_run = btl.subprocess.run
    def run(cmd, **kwargs):
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 1, "", "incompatible exact pins")
        return original_run(cmd, **kwargs)
    monkeypatch.setattr(btl.subprocess, "run", run)
    with pytest.raises(btl.ArtifactBuildError, match="incompatible exact pins"):
        btl.resolve_toolchain_lock(tmp_path / "venv", source_policy="seasoned-public")
    assert list(tmp_path.iterdir()) == []


def test_metadata_queries_share_one_clock(monkeypatch):
    clocks = []
    def resolve(name, **kwargs):
        clocks.append(kwargs["now"])
        assert kwargs["min_age_days"] == 10
        return SeasonedPublicRelease(PINS[name], HASHES[name])
    monkeypatch.setattr(public, "resolve_seasoned_public_release", resolve)
    assert public.resolve_public_source(tuple(PINS), 10).packages == PINS
    assert len(set(clocks)) == 1


@pytest.mark.parametrize("age", [-1, True, 1.5, "7", None])
def test_invalid_age_rejected_before_network(monkeypatch, age):
    from seasoned_public_feed import resolve_seasoned_public_release
    monkeypatch.setattr(public, "resolve_seasoned_public_release", resolve_seasoned_public_release)
    with pytest.raises(btl.ArtifactBuildError, match="non-negative integer"):
        public.resolve_public_source(tuple(PINS), age)


@pytest.mark.parametrize("shared", [True, False])
def test_cli_public_lifetime(tmp_path, monkeypatch, capsys, shared):
    argv = ["builder", "demo", "--out-dir", str(tmp_path / "out"),
            "--toolchain-source", "seasoned-public", "--toolchain-min-age-days", "10"]
    venv = tmp_path / "shared"
    if shared:
        argv.extend(["--toolchain-venv", str(venv)])
    monkeypatch.setattr(sys, "argv", argv)
    seen = []
    def resolve(path, **kwargs):
        assert not path.exists()
        assert kwargs == {"python": None, "source_policy": "seasoned-public", "min_age_days": 10}
        path.mkdir()
        seen.append(path)
        return btl.ToolchainLock(path / "python", dict(PINS))
    monkeypatch.setattr(cli, "resolve_toolchain_lock", resolve)
    def build(plugin, **kwargs):
        assert plugin == "demo"
        assert seen[0].exists()
        assert kwargs["toolchain"].packages == PINS
        return {"ok": True}
    assert cli.main(build, "demo") == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}
    assert seen[0].exists() == shared


def test_cli_seasoning_cannot_change_governed_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["builder", "demo", "--out-dir", "out",
                                    "--toolchain-min-age-days", "10"])
    with pytest.raises(SystemExit) as error:
        cli.main(lambda *args: pytest.fail("build must not run"), "demo")
    assert error.value.code == 2


def test_cli_default_preserves_builder_validation_order(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["builder", "demo", "--out-dir", "out"])
    monkeypatch.setattr(cli, "resolve_toolchain_lock", lambda *args, **kwargs: pytest.fail("early provisioning"))
    def build(plugin, **kwargs):
        assert kwargs["toolchain"] is None
        raise btl.ArtifactBuildError("invalid plugin before provisioning")
    assert cli.main(build, "demo") == 1
    assert "invalid plugin before provisioning" in capsys.readouterr().err


def test_cli_shared_governed_default_does_not_supply_seasoning(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["builder", "demo", "--out-dir", "out",
                                    "--toolchain-venv", str(tmp_path / "shared")])
    def resolve(path, **kwargs):
        assert kwargs == {"python": None, "source_policy": "governed", "min_age_days": None}
        return btl.ToolchainLock(path / "python", dict(PINS))
    monkeypatch.setattr(cli, "resolve_toolchain_lock", resolve)
    assert cli.main(lambda *args, **kwargs: {"ok": True}, "demo") == 0


@pytest.mark.parametrize("provenance", [
    {"python": "/python"},
    {"python": "/python", "source_policy": "unknown"},
    {"python": "/python", "source_policy": "governed"},
    {"python": "/python", "source_policy": "governed", "validated_index_identity": "opaque", "extra": True},
    {"python": "/python", "source_policy": "governed", "validated_index_identity": "opaque", "extra": object()},
    {"python": "/python", "source_policy": "seasoned-public", "packages": PINS},
    {**public.PublicToolchainSource(7, PINS, HASHES).provenance, "python": "/python", "min_age_days": -1},
    {**public.PublicToolchainSource(7, PINS, HASHES).provenance, "python": "/python", "packages": {}},
    {**public.PublicToolchainSource(7, PINS, HASHES).provenance, "python": "/python",
     "file_hashes": {**{name: list(values) for name, values in HASHES.items()},
                    "wheel": ["a" * 64, "a" * 64]}},
    {**public.PublicToolchainSource(7, PINS, HASHES).provenance, "python": "/python",
     "file_hashes": {**{name: list(values) for name, values in HASHES.items()},
                    "wheel": ["b" * 64, "a" * 64]}},
])
def test_schema_three_record_rejects_incomplete_or_mismatched_provenance(provenance):
    lock = btl.ToolchainLock(Path("/python"), dict(PINS), provenance=provenance)
    with pytest.raises(btl.ArtifactBuildError, match="provenance"):
        _ = lock.record


def test_real_uv_refuses_late_file_not_in_seasoned_snapshot(tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("real uv contract requires uv on PATH")
    env = btl.sanitize_subprocess_env()
    btl.strip_package_source_env_vars(env, strip_credentials=True)
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    venv = tmp_path / "venv"
    created = subprocess.run(
        [uv, "venv", "--offline", "--no-config", "--python", sys.executable, str(venv)],
        capture_output=True, text=True, env=env, timeout=30, **flags,
    )
    assert created.returncode == 0, created.stderr
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = wheels / "demo_toolchain-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("demo_toolchain-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: demo-toolchain\nVersion: 1.0\n")
        archive.writestr("demo_toolchain-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("demo_toolchain-1.0.dist-info/RECORD", "")
    source = public.PublicToolchainSource(7, {"demo-toolchain": "1.0"}, {"demo-toolchain": ("a" * 64,)})
    installed = subprocess.run(
        [uv, "pip", "install", "--no-config", "--no-index", "--no-cache",
         "--find-links", str(wheels), "--python", str(btl._venv_python_path(venv)),
         *source.install_args(tmp_path)],
        capture_output=True, text=True, env=env, timeout=30, **flags,
    )
    assert installed.returncode != 0
    assert "hash mismatch" in installed.stderr.lower()
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert actual in installed.stderr
