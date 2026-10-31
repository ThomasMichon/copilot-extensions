"""Candidate-to-wheel payload binding, including self-consistent forged artifacts."""
from __future__ import annotations

import io
import importlib.machinery
import json
import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
from test_promotion_python_artifacts import (
    _STATIC_BUILD, _complete_manifest, _publish_fixture, _wheel_bytes, _write_index,
)


@pytest.mark.parametrize("case", [
    "match", "changed-code", "injected-code", "missing-code", "missing-source",
    "valid-data", "changed-data", "missing-data", "valid-entry-points",
    "forged-entry-points", "valid-top-level", "forged-top-level", "unsupported-layout",
    "undeclared-data", "undeclared-pth", "undeclared-script", "multiple-data",
    "wildcard-data", "excluded-data", "data-files",
    "missing-backend", "custom-backend", "backend-path", "setup-py", "setup-cfg",
    "cmdclass", "dynamic-config",
])
def test_wheel_source_mapping_requires_exact_candidate_bytes(tmp_path, monkeypatch, case):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    with zipfile.ZipFile(directory / entry["filename"]) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    wheel = manifest["wheels"][0]
    source = tmp_path / "plugins" / "demo"
    project = source / "pyproject.toml"
    members = {}
    payload = b"VALUE = 'first-party fixture'\n"
    if case == "changed-code":
        payload = b"INJECTED = True\n"
    elif case == "injected-code":
        members["demo/injected.py"] = b"INJECTED = True\n"
    elif case == "missing-code":
        (source / "src" / "demo" / "required.py").write_text("REQUIRED = True\n")
    elif case == "missing-source":
        (source / "src" / "demo" / "__init__.py").unlink()
    elif case in ("undeclared-data", "undeclared-pth", "undeclared-script"):
        name = {
            "undeclared-data": "demo/undeclared.json",
            "undeclared-pth": "undeclared.pth",
            "undeclared-script": "demo-1.0.data/scripts/undeclared",
        }[case]
        path = source / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"import demo\n")
        members[name] = path.read_bytes()
    elif case == "data-files":
        project.write_text(project.read_text() + '\n[tool.setuptools.data-files]\nshare = ["README.md"]\n')
    elif case.endswith("data"):
        project.write_text(project.read_text() + '\n[tool.setuptools.package-data]\ndemo = ["*.json"]\n')
        (source / "src" / "demo" / "data.json").write_bytes(b'{"candidate": true}\n')
        if case != "missing-data":
            members["demo/data.json"] = b"forged" if case == "changed-data" else b'{"candidate": true}\n'
        if case == "wildcard-data":
            project.write_text(project.read_text().replace('demo = ["*.json"]', '"*" = ["*.json"]'))
        elif case == "multiple-data":
            project.write_text(project.read_text() + '"demo.nested" = ["*.txt"]\n')
            nested = source / "src" / "demo" / "nested"
            nested.mkdir()
            (nested / "__init__.py").write_bytes(b"")
            (nested / "required.txt").write_bytes(b"declared\n")
            members.update({"demo/nested/__init__.py": b"", "demo/nested/required.txt": b"declared\n"})
        elif case == "excluded-data":
            project.write_text(project.read_text() + '\n[tool.setuptools.exclude-package-data]\ndemo = ["*.json"]\n')
    elif case.endswith("entry-points"):
        members["demo-1.0.dist-info/entry_points.txt"] = b"[console_scripts]\ndemo = demo:main\n"
        if case == "valid-entry-points":
            project.write_text(project.read_text() + '\n[project.scripts]\ndemo = "demo:main"\n')
    elif case.endswith("top-level"):
        members["demo-1.0.dist-info/top_level.txt"] = (
            b"demo\n" if case == "valid-top-level" else b"other\n"
        )
    elif case == "unsupported-layout":
        project.write_text(project.read_text() + '\n[tool.setuptools.packages.find]\nwhere = ["generated"]\n')
    elif case == "missing-backend":
        project.write_text(project.read_text().replace(_STATIC_BUILD, ""))
    elif case == "custom-backend":
        project.write_text(project.read_text().replace("setuptools.build_meta", "untrusted_backend"))
    elif case == "backend-path":
        project.write_text(project.read_text().replace('[build-system]\n', '[build-system]\nbackend-path = ["."]\n'))
    elif case in ("setup-py", "setup-cfg"):
        (source / case.replace("-", ".")).write_text("raise RuntimeError('candidate build hook')\n")
    elif case == "cmdclass":
        project.write_text(project.read_text() + '\n[tool.setuptools.cmdclass]\nbuild = "demo.CustomBuild"\n')
    elif case == "dynamic-config":
        project.write_text(project.read_text() + '\n[tool.setuptools.dynamic]\nversion = {attr = "demo.version"}\n')
    archive = io.BytesIO(_wheel_bytes("demo", "1.0", payload=payload, members=members))
    if case in ("match", "valid-data", "multiple-data", "wildcard-data", "valid-entry-points", "valid-top-level"):
        artifacts.validate_wheel(archive, wheel, source_dir=source)
    else:
        with pytest.raises(artifacts.PromotionArtifactError):
            artifacts.validate_wheel(archive, wheel, source_dir=source)


def test_self_consistent_injected_wheel_fails_before_publication(tmp_path, monkeypatch):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    original = directory / entry["filename"]
    with zipfile.ZipFile(original) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    wheel = manifest["wheels"][0]
    wheel_dir = tmp_path / "forged"
    wheel_dir.mkdir()
    path = wheel_dir / wheel["filename"]
    path.write_bytes(_wheel_bytes("demo", "1.0", payload=b"INJECTED = True\n"))
    wheel["sha256"] = "sha256:" + artifacts._digest(path)
    _complete_manifest(manifest)
    original.unlink()
    replacement = artifacts.bundle_wheels(manifest, wheel_dir, directory)
    index = json.loads((directory / artifacts.INDEX_NAME).read_text())
    index["artifacts"] = [replacement]
    _write_index(directory, index)
    calls = []
    monkeypatch.setattr(publisher, "_gh", lambda *a, **kw: calls.append(a))
    with pytest.raises(artifacts.PromotionArtifactError, match="payload differs from candidate source"):
        publisher.publish_artifacts(
            directory, repository="example/plugins", merged_commit="c" * 40,
            dev_head="a" * 40, repo=tmp_path,
        )
    assert not calls


def test_declared_flat_python_modules_bind_to_root_source(tmp_path, monkeypatch):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    with zipfile.ZipFile(directory / entry["filename"]) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    source = tmp_path / "flat"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        _STATIC_BUILD + '[project]\nname = "demo"\nversion = "1.0"\n[tool.setuptools]\npy-modules = ["demo"]\n'
    )
    (source / "demo.py").write_bytes(b"VALUE = 'first-party fixture'\n")
    artifacts.validate_wheel(
        io.BytesIO(_wheel_bytes("demo", "1.0", flat=True)), manifest["wheels"][0], source_dir=source,
    )


@pytest.mark.parametrize("hook", [
    "custom-backend", "backend-path", "setup.py", "setup.cfg", "cmdclass",
    "setuptools", "setuptools.py", "setuptools.pyc", "sitecustomize.py", "json.py",
    "setuptools" + importlib.machinery.EXTENSION_SUFFIXES[0],
])
def test_unvalidated_build_hooks_fail_before_toolchain_resolution(tmp_path, monkeypatch, hook):
    import build_toolchain_lock
    _publish_fixture(tmp_path, monkeypatch)
    source = tmp_path / "plugins" / "demo"
    project = source / "pyproject.toml"
    if hook == "custom-backend":
        project.write_text(project.read_text().replace("setuptools.build_meta", "untrusted_backend"))
    elif hook == "backend-path":
        project.write_text(project.read_text().replace('[build-system]\n', '[build-system]\nbackend-path = ["."]\n'))
    elif hook == "cmdclass":
        project.write_text(project.read_text() + '\n[tool.setuptools.cmdclass]\nbuild = "demo.CustomBuild"\n')
    elif hook == "setuptools":
        (source / hook).mkdir()
        (source / hook / "__init__.py").write_text("raise RuntimeError('candidate shadowed build tool')\n")
    else:
        (source / hook).write_text("raise RuntimeError('candidate build hook')\n")
    calls = []
    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", lambda *a, **kw: calls.append(a))
    directory = tmp_path / "unsigned"
    directory.mkdir()
    with pytest.raises(artifacts.PromotionArtifactError, match="unsupported|shadows"):
        artifacts.build_candidate_bundles(tmp_path, directory, source_policy="seasoned-public", min_age_days=7)
    assert not calls
    assert not list(directory.iterdir())


def test_current_repository_packages_satisfy_static_build_admission():
    from plugin_runtime_resources import build_inputs

    root = Path(artifacts.__file__).resolve().parent.parent
    projects = [path for tree in ("plugins", "libs") for path in (root / tree).rglob("pyproject.toml")]
    assert projects
    for project in projects:
        build_inputs(project.parent, root=root)


@pytest.mark.parametrize("case", [
    "match", "removed-dependency", "injected-dependency", "changed-marker", "extra",
    "missing-extra", "duplicate-extra", "python-floor", "missing-python", "duplicate-python",
    "unconditional-extra-dep", "direct-url", "dynamic", "ambiguous-extras",
])
def test_runtime_metadata_cannot_change_candidate_install_behavior(tmp_path, monkeypatch, case):
    directory, entry = _publish_fixture(tmp_path, monkeypatch)
    with zipfile.ZipFile(directory / entry["filename"]) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    source = tmp_path / "plugins" / "demo"
    dependencies = ["Some_Pkg[ssl] >= 1.0, <3; python_version >= '3.10'"]
    optional = {"Dev_Tools": ["pytest>=8; sys_platform == 'win32'"]}
    headers = [
        "Metadata-Version: 2.4", "Name: demo", "Version: 1.0",
        "Requires-Python: <4,>=3.10", "Provides-Extra: dev-tools",
        'Requires-Dist: Some-Pkg[ssl]<3,>=1.0; python_version >= "3.10"',
        'Requires-Dist: pytest>=8; (sys_platform == "win32") and extra == "dev-tools"',
    ]
    if case == "removed-dependency":
        headers.pop(5)
    elif case == "injected-dependency":
        headers.append("Requires-Dist: undeclared-package>=1")
    elif case == "changed-marker":
        headers[5] = headers[5].replace('>= "3.10"', '>= "3.11"')
    elif case == "extra":
        headers[4] = "Provides-Extra: other"
    elif case == "missing-extra":
        headers.pop(4)
    elif case == "duplicate-extra":
        headers.append(headers[4])
    elif case == "python-floor":
        headers[3] = "Requires-Python: >=3.11"
    elif case == "missing-python":
        headers.pop(3)
    elif case == "duplicate-python":
        headers.append(headers[3])
    elif case == "unconditional-extra-dep":
        headers[6] = "Requires-Dist: pytest>=8"
    elif case == "direct-url":
        dependencies.append("example @ https://packages.example.invalid/example.whl")
        headers.append("Requires-Dist: " + dependencies[-1])
    elif case == "ambiguous-extras":
        optional["dev-tools"] = []
    (source / "pyproject.toml").write_text(
        _STATIC_BUILD + '[project]\nname = "demo"\nversion = "1.0"\nrequires-python = ">=3.10,<4"\n'
        + ('dynamic = ["dependencies"]\n' if case == "dynamic" else "")
        + "dependencies = " + json.dumps(dependencies) + "\n[project.optional-dependencies]\n"
        + "\n".join(json.dumps(extra) + " = " + json.dumps(values) for extra, values in optional.items())
        + "\n"
    )
    archive = io.BytesIO(_wheel_bytes("demo", "1.0", members={
        "demo-1.0.dist-info/METADATA": ("\n".join(headers) + "\n").encode(),
    }))
    if case == "match":
        artifacts.validate_wheel(archive, manifest["wheels"][0], source_dir=source)
    else:
        with pytest.raises(artifacts.PromotionArtifactError):
            artifacts.validate_wheel(archive, manifest["wheels"][0], source_dir=source)


def test_validator_provisioning_never_exposes_release_authority(monkeypatch, tmp_path):
    import yaml
    from seasoned_uv import prepare_uv_install
    import installer_dependency_policy as policy
    from _promotion_workflow_test_support import source_owned

    root = Path(__file__).resolve().parent.parent
    jobs = yaml.safe_load((root / ".github" / "workflows" / "validate-and-promote.yml").read_text())["jobs"]
    probes = []
    selected_uv = str(tmp_path / "uv-fixture")
    monkeypatch.setattr(policy, "_select_uv_executable", lambda _: selected_uv)

    def settings_probe(command, **kwargs):
        probes.append(command)
        assert command[:4] == [selected_uv, "pip", "install", "--show-settings"]
        return subprocess.CompletedProcess(command, 0, """PipInstallSettings { settings: PipSettings {
            index_locations: IndexLocations { indexes: [], flat_index: [], },
            exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },
        }, }""", "")

    monkeypatch.setattr(policy.subprocess, "run", settings_probe)
    prefix = (
        "python reviewed-helpers/tools/seasoned_uv.py --registry-only --uv uv --"
        if source_owned(jobs) else "python tools/seasoned_uv.py --registry-only --uv uv --"
    )
    for job in ("build-python-artifacts", "python-artifacts", "promote", "publish-python-artifacts"):
        provision = next(
            step for step in jobs[job]["steps"]
            if prefix in step.get("run", "")
            and "artifact_validation_requirements.txt" in step["run"]
        )
        assert "--require-hashes" in provision["run"]
        assert "--trusted-source-root" not in provision["run"]
        assert "--no-deps" not in provision["run"] and "--no-build-isolation" not in provision["run"]
        assert "--only-binary :all:" in provision["run"]
        assert '--index-url "${ARTIFACT_VALIDATION_INDEX_URL:-https://pypi.org/simple}"' in provision["run"]
        invocation = provision["run"].replace("\\\n", "").split(prefix, 1)[1]
        arguments = shlex.split(invocation)
        relative = (
            "reviewed-helpers/tools/artifact_validation_requirements.txt" if source_owned(jobs)
            else "tools/artifact_validation_requirements.txt"
        )
        assert arguments[arguments.index("-r") + 1] == relative
        requirements = tmp_path / relative
        requirements.parent.mkdir(parents=True, exist_ok=True)
        requirements.write_bytes((root / "tools/artifact_validation_requirements.txt").read_bytes())
        admitted = prepare_uv_install("uv", arguments, cwd=tmp_path)
        assert "--exclude-newer" in admitted and "--reinstall" in admitted
        assert "--require-hashes" in admitted and "--only-binary" in admitted
        assert "secrets." not in json.dumps(provision)
        if job == "promote":
            assert provision["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert len(probes) == 4


def test_promotion_actions_never_receive_or_persist_the_release_pat():
    import yaml
    from _promotion_workflow_test_support import assert_source_owned_boundary, source_owned
    root = Path(__file__).resolve().parent.parent
    jobs = yaml.safe_load(
        (root / ".github" / "workflows" / "validate-and-promote.yml").read_text(),
    )["jobs"]
    job = jobs["promote"]
    assert job["env"]["GH_TOKEN"] == "${{ github.token }}"
    for scope in ("pull-requests", "checks", "statuses"):
        assert job["permissions"][scope] == "read"
    allowed = {
        "Promote (push a candidate branch; report-only if push=false)",
        "Publish coverage baselines as a GitHub Release (durable evidence for this dev commit)",
        "Publish and read back exact-candidate artifacts before enabling merge",
        "Land the candidate branch on main via PR + squash-merge",
    }
    current = source_owned(jobs)
    unit_profile = "source-units" in jobs
    if unit_profile:
        allowed.remove(
            "Publish coverage baselines as a GitHub Release (durable evidence for this dev commit)"
        )
    publishing = jobs["publish-python-artifacts"]
    late_pr = current and publishing.get("outputs", {}).get("pr_number") == "${{ steps.land.outputs.pr_number }}"
    if late_pr:
        allowed.remove("Land the candidate branch on main via PR + squash-merge")
        land = next(step for step in publishing["steps"] if step.get("id") == "land")
        assert "uses" not in land
        assert land["env"]["GH_TOKEN"] == "${{ secrets.APERTURE_RELEASE_TOKEN }}"
        assert "APERTURE_RELEASE_TOKEN" not in json.dumps(publishing.get("env", {}))
        assert all(
            "APERTURE_RELEASE_TOKEN" not in json.dumps(step)
            for step in publishing["steps"] if "uses" in step
        )
    writers = set()
    for step in job["steps"]:
        if "APERTURE_RELEASE_TOKEN" in json.dumps(step):
            assert "uses" not in step
            writers.add(step["name"])
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
            assert "token" not in step["with"]
    assert writers == allowed
    identity = next(step for step in job["steps"] if step.get("name") == "Configure git identity for the generated commit")
    assert "gh auth setup-git --hostname github.com" in identity["run"]
    merger = jobs["merge-promotion"]
    assert "secrets." not in json.dumps(merger)
    assert all("uses" not in step for step in merger["steps"])
    wait = next(step for step in merger["steps"] if step.get("id") == "merged")
    assert wait["env"]["PROMOTION_MERGE_TOKEN"] == "${{ github.token }}"
    assert 'merge_token = os.environ.pop("PROMOTION_MERGE_TOKEN")' in wait["run"]
    if current:
        assert_source_owned_boundary(jobs)
        assert 'qualified_run = json.loads(qualification.stdout)' in wait["run"]
        if unit_profile:
            assert 'unit_job.get("conclusion") != "success"' in wait["run"]
            assert 'artifact = json.loads(artifact_result.stdout)' in wait["run"]
        else:
            assert 'qualified_run.get("conclusion") != "success"' in wait["run"]
    assert wait["run"].count("env=read_env") == (6 if unit_profile else 4 if current else 3)
    assert wait["run"].count("env=merge_env") == 1
    assert '"pr", "view"' in wait["run"] and '"pr", "checks"' in wait["run"]


@pytest.mark.parametrize("ambient_helper", (False, True))
def test_git_helper_uses_each_steps_token_without_storing_it(tmp_path, monkeypatch, ambient_helper):
    if shutil.which("gh") is None:
        pytest.skip("the hosted promotion credential helper requires GitHub CLI")
    if ambient_helper:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
        monkeypatch.setenv(
            "GIT_CONFIG_VALUE_0",
            "!printf 'username=ambient-fixture\\npassword=ambient-fixture\\n'",
        )
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "unavailable-ambient-repository"))
    config = tmp_path / "gitconfig"
    home = tmp_path / "home"
    home.mkdir()
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "LANG", "LC_ALL"}
    }
    environment.update({
        "HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
        "APPDATA": str(home / "AppData/Roaming"), "LOCALAPPDATA": str(home / "AppData/Local"),
        "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary),
        "GH_TOKEN": "read-only-sentinel", "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1",
        "GH_CONFIG_DIR": str(tmp_path / "gh"), "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
    })
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    repository = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], env=environment,
        cwd=home, capture_output=True, text=True, creationflags=flags, timeout=15,
    )
    assert repository.returncode != 0, "credential fixture must not inherit a repository"
    subprocess.run(
        ["gh", "auth", "setup-git", "--hostname", "github.com"], env=environment,
        cwd=home, capture_output=True, text=True, check=True, creationflags=flags, timeout=15,
    )
    for token in ("read-only-sentinel", "release-write-sentinel"):
        result = subprocess.run(
            ["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
            cwd=home, env={**environment, "GH_TOKEN": token},
            capture_output=True, text=True, check=True, creationflags=flags, timeout=15,
        )
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines())
        matches_synthetic_token = fields.pop("password", None) == token
        result.stdout = result.stderr = ""
        assert matches_synthetic_token, "credential helper must honor the synthetic per-step token"
        assert token not in config.read_text()
