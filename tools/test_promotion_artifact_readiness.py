"""Pre-merge candidate publication, read-authority availability and merge ordering."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

from _promotion_workflow_test_support import assert_source_owned_boundary, source_owned

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promotion_artifact_readiness as readiness
import promote_release as promotion
import promotion_python_artifacts as artifacts
import publish_python_artifacts as publisher
from test_promotion_python_artifacts import (
    DEV, TREE, _git, _identity_fixture, _publish_fixture, _repo_fixture, _write_index,
)

repo = _repo_fixture
no_ambient_git_identity = _identity_fixture


def _materializer_git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=root, env=env,
        capture_output=True, check=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ).stdout


def _materializer_controller(root):
    workflow = yaml.safe_load((root / ".github/workflows/validate-and-promote.yml").read_text())
    if not source_owned(workflow["jobs"]):
        workflow = yaml.safe_load(_materializer_git(
            root, "show", "refs/remotes/origin/main:.github/workflows/validate-and-promote.yml",
        ))
    return workflow


def _materializer_source(root, destination):
    workflow = yaml.safe_load((root / ".github/workflows/validate-and-promote.yml").read_text())
    if not source_owned(workflow["jobs"]):
        return root
    state = json.loads((root / ".github/release-pipeline-state.json").read_text())
    dev = state["last_promotion"]["dev_head"]
    assert isinstance(dev, str) and re.fullmatch(r"[0-9a-f]{40}", dev), "invalid recorded Dev source"
    assert _materializer_git(root, "rev-parse", "--verify", f"{dev}^{{commit}}").decode().strip() == dev
    payload = _materializer_git(
        root, "archive", "--format=tar", dev, "libs", "plugins/agent-machines/pyproject.toml",
    )
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        archive.extractall(destination, filter="data")
    return destination


@pytest.fixture
def generated_materializer_checkout(tmp_path):
    root = Path(__file__).resolve().parent.parent
    source_workflow = yaml.safe_load((root / ".github/workflows/validate-and-promote.yml").read_text())
    dev = (
        json.loads((root / ".github/release-pipeline-state.json").read_text())["last_promotion"]["dev_head"]
        if source_owned(source_workflow["jobs"])
        else _materializer_git(root, "rev-parse", "HEAD").decode().strip()
    )
    generated = tmp_path / "generated-checkout"
    _materializer_git(tmp_path, "clone", "--shared", "--no-checkout", str(root), str(generated))
    control = generated / ".github"
    control.mkdir()
    (control / "workflows").mkdir()
    (control / "workflows/validate-and-promote.yml").write_text(
        yaml.safe_dump(_materializer_controller(root)), encoding="utf-8",
    )
    (control / "release-pipeline-state.json").write_text(
        json.dumps({"last_promotion": {"dev_head": dev}}), encoding="utf-8",
    )
    plugin = generated / "plugins/agent-machines"
    plugin.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        '[tool.uv.sources]\nagent-installation-context = { path = "libs/installation-context" }\n',
        encoding="utf-8",
    )
    context = generated / "libs/installation-context"
    context.mkdir(parents=True)
    (context / "installation_context.py").write_bytes(b"GENERATED_BYTES_MUST_NOT_BE_SOURCE\n")
    _materializer_git(generated, "add", "-A")
    _materializer_git(
        generated, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-qm", "generated materialized fixture",
    )
    assert _materializer_git(generated, "rev-parse", "HEAD").decode().strip() != dev
    return generated


def _assert_pinned_materializer(root, tmp_path, helper):
    workflow = _materializer_controller(root)
    source = _materializer_source(root, tmp_path / "validated-dev")
    pin = workflow["env"]["RELEASE_HELPERS_SHA"]
    assert re.fullmatch(r"[0-9a-f]{40}", pin), "release helpers require an immutable Git commit"
    if helper == "obsolete":
        pin = "c2caa135dbb5c1d6bbacfee5c48f9b6a0c17d104"
    tools = tmp_path / "reviewed-helpers" / "tools"
    tools.mkdir(parents=True)
    for name in (
        "materialize_main", "materialize_installer_engine", "materialize_launch_wrapper_assets",
        "nested_uv_editable_ref", "uv_editable_ref", "installer_engine_ref",
        "launch_wrapper_assets_ref", "standalone_consumers",
    ):
        (tools / f"{name}.py").write_bytes(_materializer_git(root, "show", f"{pin}:tools/{name}.py"))
    destination = tmp_path / "candidate" / "plugins" / "agent-machines"
    destination.mkdir(parents=True)
    shutil.copyfile(source / "plugins/agent-machines/pyproject.toml", destination / "pyproject.toml")
    script = """
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import materialize_main
root, destination = map(Path, sys.argv[2:])
report = materialize_main.materialize_uv_editable_ref_into(
    source_consumer_dir=root / "plugins/agent-machines",
    dest_consumer_dir=destination,
    canonical_root=root,
    dest_root=destination.parent.parent,
)
print(json.dumps(report))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(tools), str(source), str(destination)],
        cwd=tmp_path, capture_output=True, text=True, check=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    report = json.loads(result.stdout)
    refusals = [line for line in report if line.startswith("SKIP")]
    if helper == "obsolete":
        assert any("agent-installation-context" in line and "editable = true" in line
                   for line in refusals), report
        return
    assert report and not refusals, f"pinned release helpers cannot materialize current source: {report}"
    context = destination / "libs/installation-context"
    assert (context / "installation_context.py").read_bytes() == (
        source / "libs/installation-context/installation_context.py"
    ).read_bytes()
    assert 'path = "libs/installation-context"' in (destination / "pyproject.toml").read_text()
    nested = (destination / "libs/installer-readiness/pyproject.toml").read_text()
    assert 'path = "../installation-context"' in nested
    assert "editable" not in nested


@pytest.mark.parametrize("helper", ["workflow", "obsolete"])
def test_native_pinned_materializer_expands_current_flat_sources(tmp_path, helper):
    _assert_pinned_materializer(Path(__file__).resolve().parent.parent, tmp_path, helper)


@pytest.mark.parametrize("helper", ["workflow", "obsolete"])
def test_native_generated_checkout_replays_unmaterialized_dev(
    tmp_path, generated_materializer_checkout, helper,
):
    _assert_pinned_materializer(generated_materializer_checkout, tmp_path, helper)


@pytest.mark.parametrize("dev", [None, "", "../outside", "f" * 40])
def test_native_generated_checkout_requires_available_recorded_dev(
    tmp_path, generated_materializer_checkout, dev,
):
    state = generated_materializer_checkout / ".github/release-pipeline-state.json"
    state.write_text(json.dumps({"last_promotion": {"dev_head": dev}}))
    expected = subprocess.CalledProcessError if dev == "f" * 40 else AssertionError
    with pytest.raises(expected):
        _materializer_source(generated_materializer_checkout, tmp_path / "refused-source")
    assert not (tmp_path / "refused-source").exists()


class HostedRelease:
    def __init__(self, directory: Path):
        self.directory = directory
        self.tag = None
        self.target = None
        self.release = None
        self.assets = {}
        self.calls = []
        self.failure = None

    def gh(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        binary = kwargs.get("binary", False)

        def result(payload="", code=0, error=""):
            if not binary and not isinstance(payload, str):
                payload = json.dumps(payload)
            return subprocess.CompletedProcess(argv, code, payload, error)

        if argv[:2] == ["release", "create"]:
            if self.failure == "create":
                return result(code=1, error="publication unavailable")
            self.tag = argv[2]
            self.target = argv[argv.index("--target") + 1]
            self.release = {
                "id": 1, "draft": False, "prerelease": True,
                "tag_name": self.tag, "assets": [],
            }
            if self.failure == "tag-race":
                self.target = "f" * 40
            return result()
        if argv[:2] == ["release", "upload"]:
            name = Path(argv[-1]).name
            if self.failure == "upload" and name != artifacts.INDEX_NAME:
                return result(code=1, error="bundle upload unavailable")
            payload = (self.directory / name).read_bytes()
            self.assets[name] = {
                "id": len(self.assets) + 1, "name": name,
                "state": "uploaded", "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "payload": payload,
            }
            return result()
        if argv[0] != "api":
            raise AssertionError(argv)
        endpoint = next(value for value in argv[1:] if value.startswith("repos/"))
        if "/git/ref/tags/" in endpoint:
            if self.target is None:
                return result(code=1, error="HTTP 404: Not Found")
            return result({"object": {"type": "commit", "sha": self.target}})
        if "/releases/tags/" in endpoint:
            if self.failure == "permission":
                return result(code=1, error="HTTP 403: Forbidden")
            if self.release is None:
                return result(code=1, error="HTTP 404: Not Found")
            return result(self.release)
        if endpoint.endswith("/assets?per_page=100"):
            pages = [[{key: value for key, value in asset.items() if key != "payload"}]
                     for asset in self.assets.values()]
            return result(pages)
        if "/releases/assets/" in endpoint:
            assert kwargs.get("verification") is True
            assert kwargs.get("binary") is True
            if self.failure == "download":
                return result(code=1, error="HTTP 403: Forbidden")
            asset = next(asset for asset in self.assets.values()
                         if str(asset["id"]) == endpoint.rsplit("/", 1)[1])
            return result(b"tampered" if self.failure == "download-digest" else asset["payload"])
        raise AssertionError(argv)

    @property
    def writes(self):
        return [argv for argv, _ in self.calls if argv[0] == "release"]


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    directory, _ = _publish_fixture(tmp_path, monkeypatch)
    server = HostedRelease(directory)
    monkeypatch.setattr(publisher, "_gh", server.gh)
    kwargs = dict(
        repository="example/plugins", candidate_commit=TREE, dev_head=DEV, repo=tmp_path,
    )
    return directory, server, kwargs


def test_prepare_uploads_signed_index_and_bundles_then_reads_every_byte_with_read_authority(hosted):
    directory, server, kwargs = hosted
    tag = readiness.prepare_artifacts(directory, **kwargs)
    assert tag == "python-artifacts-" + TREE
    assert server.target == TREE
    assert set(server.assets) == {path.name for path in directory.iterdir()}
    create = server.writes[0]
    assert "--prerelease" in create and "--latest=false" in create
    assert all("--clobber" not in argv for argv in server.writes)
    reads = [argv for argv, options in server.calls if options.get("binary")]
    assert len(reads) == len(server.assets)
    writes = list(server.writes)
    assert readiness.prepare_artifacts(directory, **kwargs) == tag
    assert server.writes == writes
    assert all(options["timeout"] == 120 for _, options in server.calls if "timeout" in options)


@pytest.mark.parametrize("failure", [
    "permission", "create", "tag-race", "upload", "download", "download-digest",
])
def test_premerge_fault_never_returns_a_readiness_receipt(hosted, failure):
    directory, server, kwargs = hosted
    server.failure = failure
    with pytest.raises(artifacts.PromotionArtifactError):
        readiness.prepare_artifacts(directory, **kwargs)
    if failure in ("permission", "create", "tag-race"):
        assert not any(argv[:2] == ["release", "upload"] for argv in server.writes)
    if failure == "upload":
        assert set(server.assets) == {artifacts.INDEX_NAME}
        writes = len(server.writes)
        server.failure = None
        readiness.prepare_artifacts(directory, **kwargs)
        retried = server.writes[writes:]
        assert len(retried) == 1
        assert Path(retried[0][-1]).name != artifacts.INDEX_NAME


@pytest.mark.parametrize("mutation", [
    "tag", "draft", "not-prerelease", "missing", "digest", "state", "asset-id", "extra",
    "bytes",
])
def test_postmerge_readiness_check_is_read_only_and_rejects_conflicts(hosted, mutation):
    directory, server, kwargs = hosted
    readiness.prepare_artifacts(directory, **kwargs)
    if mutation == "tag":
        server.target = "d" * 40
    elif mutation == "draft":
        server.release["draft"] = True
    elif mutation == "not-prerelease":
        server.release["prerelease"] = False
    elif mutation == "missing":
        server.assets.pop(artifacts.INDEX_NAME)
    elif mutation in ("digest", "state", "asset-id"):
        field, value = {
            "digest": ("digest", "sha256:" + "0" * 64),
            "state": ("state", "new"), "asset-id": ("id", True),
        }[mutation]
        server.assets[artifacts.INDEX_NAME][field] = value
    elif mutation == "extra":
        server.assets["unrelated"] = dict(server.assets[artifacts.INDEX_NAME], name="unrelated")
    else:
        server.failure = "download-digest"
    writes = list(server.writes)
    with pytest.raises(artifacts.PromotionArtifactError):
        readiness.verify_ready_artifacts(directory, **kwargs)
    assert server.writes == writes


def test_source_mismatch_refuses_before_any_release_request(hosted):
    directory, server, kwargs = hosted
    (kwargs["repo"] / "plugins" / "demo" / "src" / "demo" / "__init__.py").write_bytes(b"changed")
    with pytest.raises(artifacts.PromotionArtifactError):
        readiness.prepare_artifacts(directory, **kwargs)
    assert not server.calls


def test_cli_preparation_failure_is_nonzero_and_final_publication_never_repairs_readiness(
    hosted, monkeypatch, capsys,
):
    directory, server, _ = hosted
    server.failure = "upload"
    args = [
        "--directory", str(directory), "--repository", "example/plugins",
        "--signer-digest", TREE, "--source-digest", TREE, "--source-ref", "refs/heads/main",
        "--candidate-commit", TREE, "--dev-head", DEV,
    ]
    monkeypatch.setattr(publisher, "verify_attestations", lambda *a, **kw: None)
    assert publisher.main(["prepare", *args]) == 1
    assert "artifact-ready:" not in capsys.readouterr().out
    writes = list(server.writes)
    assert publisher.main(["publish", *args, "--merged-commit", TREE]) == 1
    assert server.writes == writes


def test_publish_cli_requires_a_nonempty_premerge_identity(hosted):
    directory, server, _ = hosted
    with pytest.raises(SystemExit) as refused:
        publisher.main([
            "publish", "--directory", str(directory), "--repository", "example/plugins",
            "--signer-digest", TREE, "--source-digest", TREE, "--source-ref", "refs/heads/main",
            "--merged-commit", TREE, "--candidate-commit", "", "--dev-head", DEV,
        ])
    assert refused.value.code == 2
    assert not server.calls


def test_binary_read_uses_real_subprocess_bytes_and_read_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "synthetic-write")
    monkeypatch.setenv("ARTIFACT_VERIFY_TOKEN", "synthetic-read")
    run = subprocess.run
    observed = {}

    def invoke(argv, **kwargs):
        observed.update(kwargs)
        return run(
            [sys.executable, "-c", "import sys;sys.stdout.buffer.write(bytes(range(256)))"],
            **kwargs,
        )

    monkeypatch.setattr(publisher.subprocess, "run", invoke)
    result = publisher._gh(["api", "synthetic"], verification=True, binary=True, timeout=10)
    assert result.stdout == bytes(range(256))
    assert observed["env"]["GH_TOKEN"] == "synthetic-read"
    assert "ARTIFACT_VERIFY_TOKEN" not in observed["env"]
    assert observed["text"] is False


def test_native_git_candidate_and_nonancestor_squash_bind_the_same_ready_bytes(repo, monkeypatch):
    with monkeypatch.context() as fixture_patches:
        directory, _ = _publish_fixture(repo, fixture_patches)
    main_before = _git(["rev-parse", "HEAD"], repo)
    _git(["add", "plugins"], repo)
    _git(["commit", "-m", "candidate first-party source"], repo)
    candidate = _git(["rev-parse", "HEAD"], repo)
    tree = _git(["rev-parse", "HEAD^{tree}"], repo)
    index = json.loads((directory / artifacts.INDEX_NAME).read_text())
    index.update(
        dev_head=main_before, main_before=main_before,
        content_tree=promotion._tree_excluding_state(tree, repo=repo),
    )
    _write_index(directory, index)
    server = HostedRelease(directory)
    monkeypatch.setattr(publisher, "_gh", server.gh)
    tag = readiness.prepare_artifacts(
        directory, repository="example/plugins", candidate_commit=candidate,
        dev_head=main_before, repo=repo,
    )
    assert tag == "python-artifacts-" + candidate
    merged = _git(["commit-tree", tree, "-p", main_before, "-m", "actual squash"], repo)
    assert not promotion._is_ancestor(candidate, merged, cwd=repo)
    assert publisher.validate_commit_artifacts(
        directory, commit=merged, dev_head=main_before, repo=repo,
    ) == index
    writes = list(server.writes)
    readiness.verify_ready_artifacts(
        directory, repository="example/plugins", candidate_commit=candidate,
        dev_head=main_before, repo=repo,
    )
    assert server.writes == writes
    state = repo / promotion.PIPELINE_STATE_PATH
    state.write_text('{"unexpected": "replacement bookkeeping"}\n')
    _git(["add", promotion.PIPELINE_STATE_PATH], repo)
    changed_tree = _git(["write-tree"], repo)
    changed = _git(["commit-tree", changed_tree, "-p", main_before, "-m", "different state"], repo)
    assert publisher.validate_commit_artifacts(
        directory, commit=changed, dev_head=main_before, repo=repo,
    ) == index
    with pytest.raises(artifacts.PromotionArtifactError, match="complete ready candidate tree"):
        publisher.publish_artifacts(
            directory, repository="example/plugins", merged_commit=changed,
            candidate_commit=candidate, dev_head=main_before, repo=repo,
        )
    assert server.writes == writes


def test_workflow_readiness_precedes_pr_creation_and_pins_the_merge_head():
    root = Path(__file__).resolve().parent.parent
    jobs = yaml.safe_load(
        (root / ".github" / "workflows" / "validate-and-promote.yml").read_text(),
    )["jobs"]
    steps = jobs["promote"]["steps"]
    ready = next(step for step in steps if step.get("id") == "ready")
    land = next(step for step in steps if step.get("id") == "land")
    merger = jobs["merge-promotion"]
    wait = next(step for step in merger["steps"] if step.get("id") == "merged")
    assert steps.index(ready) < steps.index(land)
    if source_owned(jobs):
        assert_source_owned_boundary(jobs)
        assert '--repo "$GITHUB_WORKSPACE"' in ready["run"]
        assert "candidate must preserve the admitted pipeline pause state" in ready["run"]
    else:
        assert set(merger["needs"]) == {
            "promote", "materialized-candidate-plan", "materialized-candidate-consumers",
            "materialized-candidate-windows",
        }
        assert re.sub(r"\s+", " ", merger["if"]).strip() == (
            "needs.promote.outputs.pr_number != '' && "
            "needs.materialized-candidate-plan.result == 'success' && "
            "needs.materialized-candidate-consumers.result == 'success' && "
            "needs.materialized-candidate-windows.result == 'success'"
        )
    assert jobs["promote"]["outputs"]["pr_number"] == "${{ steps.land.outputs.pr_number }}"
    assert "publish_python_artifacts.py prepare" in ready["run"]
    assert "continue-on-error" not in ready
    assert ready["run"].index("publish_python_artifacts.py prepare") < ready["run"].index(
        'echo "candidate_pushed=true"',
    )
    assert land["if"] == "steps.ready.outputs.candidate_pushed == 'true'"
    assert "--auto" not in land["run"] and "--auto" not in wait["run"]
    assert '"--match-head-commit", os.environ["CANDIDATE_SHA"]' in wait["run"]
    assert 'pr["headRefOid"] != os.environ["CANDIDATE_SHA"]' in wait["run"]
    assert ready["env"]["ARTIFACT_VERIFY_TOKEN"] == "${{ github.token }}"
    assert jobs["promote"]["outputs"]["candidate_sha"] == "${{ steps.promote.outputs.candidate_sha }}"
    assert ready["env"]["CANDIDATE_SHA"] == "${{ steps.promote.outputs.candidate_sha }}"
    assert "steps.promote.outputs.candidate_sha != ''" in ready["if"]
    promoter = next(step for step in steps if step.get("id") == "promote")
    assert '--github-output "$GITHUB_OUTPUT"' in promoter["run"]
    assert '--promotion-time "$PROMOTION_TIME"' in promoter["run"]
    clock = next(step for step in steps if step.get("id") == "candidate-clock")
    assert "--jq .created_at" in clock["run"] and "run_started_at" not in clock["run"]
    assert steps.index(clock) < steps.index(promoter)
    assert "secrets." not in json.dumps(clock)
    assert 'candidate_sha=$(printf' not in ready["run"]
    final = next(step for step in jobs["publish-python-artifacts"]["steps"] if "run" in step
                 and "publish_python_artifacts.py publish" in step["run"])
    assert '--candidate-commit "$CANDIDATE_SHA"' in final["run"]
    assert 'git fetch origin "$CANDIDATE_SHA"' in final["run"]


@pytest.mark.parametrize("remote", ["d" * 40, ""])
def test_native_ready_step_refuses_moved_or_deleted_remote_before_publication(tmp_path, remote):
    bash = shutil.which("bash")
    git = shutil.which("git")
    if os.name == "nt" and git:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    if not bash:
        pytest.skip("native Bash is unavailable")
    root = Path(__file__).resolve().parent.parent
    steps = yaml.safe_load(
        (root / ".github" / "workflows" / "validate-and-promote.yml").read_text(),
    )["jobs"]["promote"]["steps"]
    ready = next(step for step in steps if step.get("id") == "ready")
    call_log, output = tmp_path / "calls", tmp_path / "output"
    script = """
    set -euo pipefail
    git() {
      printf '%s\\n' "$*" >> "$CALL_LOG"
      if [ "$1" != "ls-remote" ]; then exit 91; fi
      if [ -n "$MOVED_SHA" ]; then
        printf '%s\\t%s\\n' "$MOVED_SHA" "refs/heads/$CANDIDATE_BRANCH"
      fi
    }
    """ + ready["run"]
    env = {
        **os.environ, "CANDIDATE_SHA": TREE, "CANDIDATE_BRANCH": "release/promote-fixture",
        "MOVED_SHA": remote, "CALL_LOG": call_log.as_posix(), "GITHUB_OUTPUT": output.as_posix(),
    }
    result = subprocess.run(
        [bash, "--noprofile", "--norc"], input=script, text=True, capture_output=True,
        env=env, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Remote branch does not match the trusted promoter's candidate" in result.stdout
    assert len(call_log.read_text().splitlines()) == 1
    assert not output.exists()


@pytest.mark.parametrize("candidates,accepted", [
    ([], True),
    ([{"number": 1, "headRefOid": TREE, "isCrossRepository": False, "state": "OPEN"}], True),
    ([{"number": 1, "headRefOid": TREE, "isCrossRepository": False, "state": "MERGED"}], True),
    ([{"number": 1, "headRefOid": DEV, "isCrossRepository": False, "state": "OPEN"}], False),
    ([{"number": 1, "headRefOid": TREE, "isCrossRepository": True, "state": "OPEN"}], False),
    ([{"number": 1, "headRefOid": TREE, "isCrossRepository": False, "state": "CLOSED"}], False),
    ([{"number": True, "headRefOid": TREE, "isCrossRepository": False, "state": "OPEN"}], False),
    ([{}, {}], False), (["invalid"], False),
])
def test_retry_pr_lookup_reuses_only_the_exact_eligible_candidate(
    monkeypatch, capsys, candidates, accepted,
):
    root = Path(__file__).resolve().parent.parent
    steps = yaml.safe_load(
        (root / ".github" / "workflows" / "validate-and-promote.yml").read_text(),
    )["jobs"]["promote"]["steps"]
    land = next(step for step in steps if step.get("id") == "land")
    code = re.search(r"python - <<'PY'\s*\n(.*?)^PY\s*$", land["run"], re.M | re.S)
    assert code is not None
    monkeypatch.setenv("PR_CANDIDATES", json.dumps(candidates))
    monkeypatch.setenv("CANDIDATE_SHA", TREE)
    if accepted:
        exec(compile(code.group(1), "promotion-pr-lookup", "exec"), {})
        assert capsys.readouterr().out.strip() == ("1" if candidates else "")
    else:
        with pytest.raises(RuntimeError):
            exec(compile(code.group(1), "promotion-pr-lookup", "exec"), {})


def test_required_ci_collects_readiness_and_retry_in_their_changed_path_lanes():
    root = Path(__file__).resolve().parent.parent
    jobs = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text())["jobs"]
    paths = [
        ".github/workflows/ci.yml", "tools/promotion_artifact_readiness.py",
        "tools/test_promotion_artifact_readiness.py", "tools/test_promotion_candidate_retry.py",
        "tools/promotion_candidate.py",
        "tools/verify_promotion_workflows.py", "tools/test_verify_promotion_workflows.py",
        "tools/tag_promotion.py", "tools/test_tag_promotion.py",
        ".github/workflows/workflow-lockdown-guard.yml",
    ]
    for job_id in ("checks", "windows-python-artifact-builder"):
        steps = jobs[job_id]["steps"]
        detection = next(step for step in steps if step.get("id") == "python-artifact-builder")
        pattern = re.search(r"'(\^\(.+\)\$)'", detection["run"])
        assert pattern is not None
        assert all(re.fullmatch(pattern.group(1), path) for path in paths)
        if job_id == "checks":
            selected = next(
                step for step in steps
                if step.get("name") == "Governed Python artifact build/toolchain-lock guard tests"
            )
            assert selected["if"] == "steps.python-artifact-builder.outputs.run == 'true'"
        else:
            native = jobs["promotion-candidate-windows"]
            assert native["needs"] == job_id
            assert native["if"] == jobs[job_id]["if"]
            assert native["runs-on"] == "windows-latest"
            assert native["timeout-minutes"] == 5
            assert jobs[job_id]["timeout-minutes"] == 10
            assert "continue-on-error" not in native
            assert jobs[job_id]["outputs"]["run"] == "${{ steps.python-artifact-builder.outputs.run }}"
            assert all(
                step["if"] == "needs.windows-python-artifact-builder.outputs.run == 'true'"
                for step in native["steps"]
            )
            publication = jobs["promotion-publication-windows"]
            assert publication["needs"] == job_id
            assert publication["if"] == native["if"]
            assert publication["runs-on"] == "windows-latest"
            assert publication["timeout-minutes"] == 5
            assert "continue-on-error" not in publication
            for gate in ("pr-gate", "promotion-gate"):
                assert "promotion-candidate-windows" in jobs[gate]["needs"]
                assert "promotion-publication-windows" in jobs[gate]["needs"]
            selected = next(
                step for step in native["steps"]
                if step.get("name") == "Native candidate readiness and retry contracts (Windows)"
            )
            assert not any(
                step.get("name") == selected["name"] for step in steps
            )
            assert "-k 'native or binary_read'" in selected["run"]
            assert "tools/seasoned_uv.py" in selected["run"]
            for name in ("test_verify_promotion_workflows.py", "test_tag_promotion.py"):
                assert f"tools/{name}" in selected["run"]
        for name in ("test_promotion_artifact_readiness.py", "test_promotion_candidate_retry.py"):
            assert f"tools/{name}" in selected["run"]


@pytest.mark.parametrize("job_id", ["checks", "windows-python-artifact-builder"])
@pytest.mark.parametrize("promotion_run,changed,expected", [
    ("123-1", "tools/promotion_artifact_readiness.py", "run=true"),
    ("123-1", "plugins/demo-plugin/README.md", "run=true"),
    ("123-1", "docs/unrelated.md", "run=false"),
    ("", "tools/promotion_artifact_readiness.py", "run=false"),
    ("123-1", "tools/promotion_artifact_readiness.py", None),
])
def test_native_artifact_detector_covers_promotion_dispatch_without_changing_manual_mode(
    repo, tmp_path, job_id, promotion_run, changed, expected,
):
    bash, git = shutil.which("bash"), shutil.which("git")
    if os.name == "nt" and git:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    if not bash:
        pytest.skip("native Bash is unavailable")
    base = _git(["rev-parse", "HEAD"], repo)
    target = repo / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed input\n", encoding="utf-8")
    _git(["add", "--", changed], repo)
    _git(["commit", "-qm", "event-scoped input"], repo)
    if expected is None:
        _git(["checkout", "--orphan", "root-candidate"], repo)
        _git(["commit", "-qm", "root candidate has no diff parent"], repo)
    root = Path(__file__).resolve().parent.parent
    job = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"][job_id]
    assert next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")[
        "with"
    ]["fetch-depth"] == 0
    step = next(step for step in job["steps"] if step.get("id") == "python-artifact-builder")
    script = step["run"].replace("${{ github.event_name }}", "workflow_dispatch").replace(
        "${{ github.event.pull_request.base.sha }}", base,
    ).replace("${{ github.event.before }}", base)
    output = tmp_path / "scope-output"
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "CI_PROMOTION_RUN": promotion_run, "GITHUB_OUTPUT": output.as_posix(),
    })
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail"], cwd=repo,
        input=script, text=True, capture_output=True, env=environment, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if expected is None:
        assert result.returncode != 0
        assert not output.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert output.read_text().strip() == expected
