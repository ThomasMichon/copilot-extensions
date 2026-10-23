"""Exact candidate admission and real, unprivileged consumer execution contracts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath

import pytest
import yaml

import validate_materialized_candidate as candidate

ROOT = Path(__file__).resolve().parents[1]
RUNNER_POLICY = (
    "_PLUGIN_TIMEOUT_OVERRIDES: dict[str, float] = {}\n"
    "def fixture_budget(name):\n return _PLUGIN_TIMEOUT_OVERRIDES.get(name, 900.0)\n"
)


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        check=True, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ).stdout.strip()


@pytest.fixture
def subject(tmp_path, monkeypatch):
    for key in candidate.FORBIDDEN_AUTHORITY:
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "candidate"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "core.hooksPath", str(root / "no-hooks"))
    files = {
        "owned.txt": "unchanged\n",
        ".github/workflows/ci.yml": (ROOT / ".github/workflows/ci.yml").read_text(),
        "plugins/agent-worktrees/plugin.json": "{}",
        "plugins/agent-worktrees/tests/test_owned.py": "def test_owned(): pass\n",
        "plugins/agent-bridge/plugin.json": "{}",
        "plugins/agent-bridge/tests/test_owned.py": "def test_owned(): pass\n",
        "tools/run-plugin-tests.py": RUNNER_POLICY + (
            "import json, sys\nfrom pathlib import Path\nfrom fixture_sibling import LABEL\n"
            "with Path('executions.jsonl').open('a') as stream:\n"
            " stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        ),
        "tools/fixture_sibling.py": 'LABEL = "supported recipe"\n',
        "tools/run-standalone-tests.py": (
            "import os\nfrom pathlib import Path\n"
            "Path('fleet-selector.txt').write_text(os.environ.get('PYTEST_ADDOPTS', ''))\n"
            "print('fixture standalone executed')\n"
        ),
        "tools/standalone_consumers.py": (
            "STANDALONE_CONSUMERS = "
            "('worktree-manager', 'services/index', 'fleet-manager', 'services/dispatch')\n"
        ),
        "tools/seasoned_uv.py": "print('fixture preparation executed')\n",
        "tools/artifact_validation_requirements.txt": "packaging==25.0 --hash=sha256:" + "0" * 64 + "\n",
        "tools/test_fleet_ci_scope.py": (
            "import os\nfrom pathlib import Path\n"
            "def test_fleet_scope():\n"
            " value = os.environ['PYTEST_ADDOPTS']\n"
            " assert value == '-m \"not client_distribution_full and not observation_retention_full\"'\n"
            " Path('fleet-exclusions.txt').write_text(value)\n"
        ),
        "services/dispatch/pyproject.toml": '[project]\nname="fixture-dispatch"\n',
        "services/index/pyproject.toml": '[project]\nname="fixture-index"\n',
        "fleet-manager/pyproject.toml": '[project]\nname="fixture-fleet"\n',
        "fleet-manager/setup.py": "# fixture build entry\n",
        "fleet-manager/README.md": "Fixture Fleet consumer\n",
        "worktree-manager/pyproject.toml": '[project]\nname="fixture-manager"\n',
    }
    for name, contents in files.items():
        path = root.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "main")
    main = git(root, "rev-parse", "HEAD")
    git(root, "clone", "-q", "--no-hardlinks", str(root), str(root.parent / "validated-dev"))
    state = {
        "schema": candidate.SCHEMA, "version": 1,
        "last_promotion": {"dev_head": main, "main_before": main},
    }
    (root / ".github").mkdir(exist_ok=True)
    (root / candidate.STATE).write_text(json.dumps(state), encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "candidate")
    head = git(root, "rev-parse", "HEAD")
    return root, head, main, main


def commit(root):
    git(root, "add", ".")
    previous = git(root, "rev-parse", "HEAD")
    parent = git(root, "rev-parse", "HEAD^")
    head = git(
        root, "commit-tree", git(root, "write-tree"), "-p", parent, "-m", "revised fixture candidate",
    )
    git(root, "update-ref", "HEAD", head, previous)
    return head


@pytest.fixture
def chat_subject(subject):
    root, _, _, main = subject
    dev_root = root.parent / "validated-dev"
    for checkout in (root, dev_root):
        roster = checkout / "tools/standalone_consumers.py"
        original = roster.read_text("utf-8")
        assert original.count("'services/dispatch')") == 1
        roster.write_text(
            original.replace("'services/dispatch')", "'services/dispatch', 'services/chat')"),
            encoding="utf-8",
        )
        package = checkout / "services/chat/pyproject.toml"
        package.parent.mkdir()
        package.write_text('[project]\nname="fixture-chat"\n', encoding="utf-8")
    git(dev_root, "add", ".")
    git(dev_root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "fixture chat registration")
    dev = git(dev_root, "rev-parse", "HEAD")
    state = json.loads((root / candidate.STATE).read_text("utf-8"))
    state["last_promotion"]["dev_head"] = dev
    (root / candidate.STATE).write_text(json.dumps(state), encoding="utf-8")
    return root, commit(root), dev, main


def test_pr_service_has_native_candidate_recipes_without_fleet_partition_work(subject):
    root, _, _, main = subject
    dev_root = root.parent / "validated-dev"
    for checkout in (root, dev_root):
        roster = checkout / "tools/standalone_consumers.py"
        roster.write_text(
            roster.read_text("utf-8").replace(
                "'services/dispatch')", "'services/dispatch', 'services/pr')",
            ), encoding="utf-8",
        )
        package = checkout / "services/pr/pyproject.toml"
        package.parent.mkdir()
        package.write_text('[project]\nname="fixture-pr"\n', encoding="utf-8")
        # This explicit native inventory is not today's source-unit CI profile.
        (checkout / ".github/workflows/ci.yml").write_text(
            "jobs:\n"
            "  fleet-native-linux:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        partition: &fleet-native-partitions\n"
            "          - name: fixture\n"
            "            selector: 'fixture'\n"
            "    steps: &fleet-native-steps\n"
            "      - run: fixture\n"
            "  fleet-native-windows:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        partition: *fleet-native-partitions\n"
            "    steps: *fleet-native-steps\n", encoding="utf-8",
        )
    git(dev_root, "add", ".")
    git(dev_root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "fixture pr registration")
    dev = git(dev_root, "rev-parse", "HEAD")
    state = json.loads((root / candidate.STATE).read_text("utf-8"))
    state["last_promotion"]["dev_head"] = dev
    (root / candidate.STATE).write_text(json.dumps(state), encoding="utf-8")
    head = commit(root)
    rows = candidate.matrix(root, dev_root, dev)["include"]
    assert {row["os"] for row in rows if row["component"] == "pr"} == {
        "ubuntu-latest", "windows-latest",
    }
    recipe = candidate.recipes(root, "pr")
    assert len(recipe) == 1
    assert recipe[0][0][2:] == [
        "services/pr", "--prepare", "--admission-wait", "60", "--timeout", "180",
    ]
    assert candidate.run(root, head, dev, main, "pr", dev_root=dev_root)["outcome"] == "success"


def test_exact_candidate_and_real_consumer_preserve_tracked_inputs(subject):
    root, head, dev, main = subject
    identity = candidate.validate(root, head, dev, main)
    assert identity["tree"] == git(root, "rev-parse", "HEAD^{tree}")
    result = candidate.run(
        root, head, dev, main, "plugin:agent-worktrees", dev_root=root.parent / "validated-dev",
    )
    assert result["outcome"] == "success"
    assert [
        json.loads(line) for line in (root / "executions.jsonl").read_text().splitlines()
    ] == [
        ["agent-worktrees", "--collect-only"], ["agent-worktrees", "--guards"],
    ]
    assert candidate.validate(root, head, dev, main) == identity


@pytest.mark.parametrize("position", [1, 2, 3])
def test_wrong_immutable_input_refuses_before_any_consumer(subject, monkeypatch, position):
    arguments = list(subject)
    arguments[position] = "f" * 40
    monkeypatch.setattr(candidate, "recipes", lambda *args: pytest.fail("consumer was reached"))
    with pytest.raises(candidate.CandidateValidationError):
        candidate.run(*arguments, "plugin:agent-bridge", dev_root=subject[0].parent / "validated-dev")


@pytest.mark.parametrize("key,value", [
    ("schema", "other"), ("version", True), ("last_promotion", None),
])
def test_malformed_composition_refuses(subject, key, value):
    root, _, dev, main = subject
    state = json.loads((root / candidate.STATE).read_text())
    state[key] = value
    (root / candidate.STATE).write_text(json.dumps(state))
    head = commit(root)
    with pytest.raises(candidate.CandidateValidationError, match="composition"):
        candidate.validate(root, head, dev, main)


def test_modified_checkout_and_foreign_git_selectors_do_not_change_admission(subject, monkeypatch):
    root, head, dev, main = subject
    monkeypatch.setenv("GIT_DIR", str(root / "not-the-repo"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root.parent))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "fixture-unwanted-command")
    assert candidate.validate(root, head, dev, main)["candidate_sha"] == head
    (root / "owned.txt").write_text("changed\n")
    with pytest.raises(candidate.CandidateValidationError, match="tracked inputs"):
        candidate.validate(root, head, dev, main)


def test_real_failed_and_mutating_consumers_cannot_authorize_merge(subject):
    root, _, dev, main = subject
    runner = root / "tools" / "run-plugin-tests.py"
    runner.write_text(RUNNER_POLICY + "raise SystemExit(7)\n")
    head = commit(root)
    with pytest.raises(candidate.CandidateValidationError, match="exit 7"):
        candidate.run(
            root, head, dev, main, "plugin:agent-bridge", dev_root=root.parent / "validated-dev",
        )
    runner.write_text(RUNNER_POLICY +
        "from pathlib import Path\nPath('owned.txt').write_text('mutated')\n",
    )
    head = commit(root)
    with pytest.raises(candidate.CandidateValidationError, match="tracked inputs"):
        candidate.run(
            root, head, dev, main, "plugin:agent-bridge", dev_root=root.parent / "validated-dev",
        )


def test_timed_out_consumer_still_checks_tracked_immutability(subject, monkeypatch):
    root, head, dev, main = subject
    real_run = subprocess.run

    def run(argv, **kwargs):
        if argv[0] == "git":
            return real_run(argv, **kwargs)
        (root / "owned.txt").write_text("modified by timed-out fixture")
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(candidate.subprocess, "run", run)
    with pytest.raises(candidate.CandidateValidationError, match="tracked inputs"):
        candidate.run(
            root, head, dev, main, "plugin:agent-bridge", dev_root=root.parent / "validated-dev",
        )


@pytest.mark.parametrize("name", candidate.FORBIDDEN_AUTHORITY)
def test_release_or_api_authority_refuses_before_test_execution(subject, monkeypatch, name):
    monkeypatch.setenv(name, "fixture-not-an-actual-credential")
    monkeypatch.setattr(candidate, "recipes", lambda *args: pytest.fail("consumer was reached"))
    with pytest.raises(candidate.CandidateValidationError, match="authority"):
        candidate.run(*subject, "plugin:agent-bridge", dev_root=subject[0].parent / "validated-dev")


def test_complete_matrix_and_exact_existing_recipes(subject):
    root, _, dev, _ = subject
    rows = candidate.matrix(root, root.parent / "validated-dev", dev)["include"]
    assert {(row["component"], row["os"]) for row in rows} == {
        ("plugin:agent-worktrees", "ubuntu-latest"),
        ("plugin:agent-bridge", "ubuntu-latest"),
        ("manager", "ubuntu-latest"), ("dispatch", "ubuntu-latest"),
        ("index", "ubuntu-latest"), ("index", "windows-latest"),
        ("validator-bootstrap", "ubuntu-latest"), ("validator-bootstrap", "windows-latest"),
        *(
            (f"fleet-manager:{name}", platform)
            for name in candidate.fleet_partitions(root)
            for platform in ("ubuntu-latest", "windows-latest")
        ),
    }
    commands = candidate.recipes(root, "plugin:agent-worktrees")
    assert [command[-1] for command, _ in commands] == ["--collect-only", "--guards"]
    assert candidate.recipes(root, "manager") == [
        (["uv", "run", "--extra", "dev", "pytest", "-q"], root / "worktree-manager"),
    ]
    standalone, directory = candidate.recipes(root, "dispatch")[0]
    assert standalone[:2] == [sys.executable, str(root / "tools" / "run-standalone-tests.py")]
    assert standalone[2:] == [
        "services/dispatch", "--prepare", "--admission-wait", "60",
        "--timeout", "180",
    ]
    assert directory == root
    assert candidate.recipes(root, "index")[0][0][-1] == "--smoke"
    fleet = candidate.recipes(root, "fleet-manager:planner")
    assert fleet[0][0][2:] == ["--uv", "uv", "--", "--system", "pytest"]
    assert fleet[1][0][1:4] == ["-m", "pytest", "-q"]
    assert fleet[2][0][2:] == [
        "fleet-manager", "--prepare", "--admission-wait", "60",
        "--timeout", "120",
    ]


def test_chat_registration_has_both_platform_rows_and_the_actual_ci_recipe(chat_subject):
    root, _, dev, _ = chat_subject
    rows = candidate.matrix(root, root.parent / "validated-dev", dev)["include"]
    assert {(row["component"], row["os"], row["minutes"], row["job_minutes"])
            for row in rows if row["component"] == "chat"} == {
        ("chat", "ubuntu-latest", 6, 11), ("chat", "windows-latest", 6, 11),
    }
    commands = candidate.recipes(root, "chat")
    assert commands == [(
        [sys.executable, str(root / "tools/run-standalone-tests.py"), "services/chat",
         "--prepare", "--admission-wait", "60", "--timeout", "120"], root,
    )]
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text("utf-8"))["jobs"]
    for platform in ("linux", "windows"):
        job = jobs[f"agent-chat-service-{platform}"]
        invocation = next(step["run"] for step in job["steps"] if step.get("run"))
        assert invocation.split()[2:] == commands[0][0][2:]


def test_registered_chat_recipe_executes_and_preserves_candidate_identity(chat_subject):
    root, head, dev, main = chat_subject
    result = candidate.run(
        root, head, dev, main, "chat", dev_root=root.parent / "validated-dev",
    )
    assert result["component"] == "chat" and result["outcome"] == "success"
    assert (root / "fleet-selector.txt").is_file()
    assert candidate.validate(root, head, dev, main)["candidate_sha"] == head


def test_missing_registered_chat_package_refuses(chat_subject):
    root, _, dev, _ = chat_subject
    (root / "services/chat/pyproject.toml").unlink()
    with pytest.raises(candidate.CandidateValidationError, match="absent"):
        candidate.matrix(root, root.parent / "validated-dev", dev)


def test_actual_canonical_roster_has_executable_chat_rows():
    rows = candidate.inventory_matrix(
        ROOT, candidate.python_suites(ROOT), candidate.standalone_roster(ROOT),
    )["include"]
    assert {row["os"] for row in rows if row["component"] == "chat"} == {
        "ubuntu-latest", "windows-latest",
    }
    assert candidate.recipes(ROOT, "chat")[0][0][2] == "services/chat"


def test_missing_inventory_or_unknown_consumer_cannot_silently_skip(subject):
    root, head, dev, main = subject
    with pytest.raises(candidate.CandidateValidationError, match="absent"):
        candidate.run(
            root, head, dev, main, "unrecognized", dev_root=root.parent / "validated-dev",
        )
    (root / "plugins" / "agent-worktrees" / "plugin.json").unlink()
    with pytest.raises(candidate.CandidateValidationError, match="absent"):
        candidate.matrix(root, root.parent / "validated-dev", dev)


def test_empty_required_recipe_is_not_a_success_receipt(subject, monkeypatch):
    monkeypatch.setattr(candidate, "recipes", lambda *args: [])
    with pytest.raises(candidate.CandidateValidationError, match="no executable recipe"):
        candidate.run(*subject, "plugin:agent-bridge", dev_root=subject[0].parent / "validated-dev")


def test_matrix_does_not_invent_python_suites_for_other_test_languages(subject):
    root, _, dev, _ = subject
    tests = root / "plugins" / "fixture-node" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_contract.cjs").write_text("// fixture\n")
    assert not any(
        row["component"] == "plugin:fixture-node"
        for row in candidate.matrix(root, root.parent / "validated-dev", dev)["include"]
    )


def test_symlinked_consumer_is_refused(subject, tmp_path):
    root, _, dev, _ = subject
    path = root / "plugins" / "agent-bridge" / "tests" / "foreign"
    try:
        path.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("native symlink creation is unavailable")
    (root / "plugins" / "alias").symlink_to(root / "plugins" / "agent-bridge", target_is_directory=True)
    with pytest.raises(candidate.CandidateValidationError, match="symlink"):
        candidate.matrix(root, root.parent / "validated-dev", dev)


@pytest.mark.parametrize("missing", ["directory", "empty"])
def test_missing_non_worktrees_suite_cannot_escape_inventory(subject, missing):
    root, _, dev, _ = subject
    tests = root / "plugins" / "agent-bridge" / "tests"
    if missing == "directory":
        shutil.rmtree(tests)
    else:
        (tests / "test_owned.py").unlink()
    with pytest.raises(candidate.CandidateValidationError, match="missing=.*agent-bridge"):
        candidate.matrix(root, root.parent / "validated-dev", dev)


@pytest.mark.parametrize("invalid", ["wrong-head", "dirty"])
def test_development_inventory_must_be_the_validated_input(subject, invalid):
    root, head, dev, _ = subject
    source = root.parent / "validated-dev"
    if invalid == "wrong-head":
        git(source, "fetch", "-q", str(root), head)
        git(source, "checkout", "-q", "--detach", head)
    else:
        (source / "owned.txt").write_text("changed")
    with pytest.raises(candidate.CandidateValidationError, match="validated immutable input"):
        candidate.matrix(root, source, dev)


@pytest.mark.parametrize("component", ["index", "chat"])
def test_windows_standalone_recipe_uses_canonical_component_id(subject, monkeypatch, component):
    monkeypatch.setitem(candidate.STANDALONE, component, PureWindowsPath("services", component))
    argv, _ = candidate.recipes(subject[0], component)[0]
    assert argv[2] == f"services/{component}"


def test_omitted_canonical_standalone_consumer_cannot_authorize_inventory(subject, monkeypatch):
    monkeypatch.delitem(candidate.STANDALONE, "fleet-manager")
    with pytest.raises(candidate.CandidateValidationError, match="canonical roster"):
        candidate.matrix(subject[0], subject[0].parent / "validated-dev", subject[2])


@pytest.mark.parametrize("relative", [
    "fleet-manager/pyproject.toml", "fleet-manager/setup.py",
    "fleet-manager/README.md", "tools/test_fleet_ci_scope.py",
])
def test_missing_required_fleet_consumer_files_refuse(subject, relative):
    root, _, dev, _ = subject
    root.joinpath(*relative.split("/")).unlink()
    with pytest.raises(candidate.CandidateValidationError, match="absent"):
        candidate.matrix(root, root.parent / "validated-dev", dev)


@pytest.mark.parametrize("relative", [
    "tools/seasoned_uv.py", "tools/artifact_validation_requirements.txt",
])
def test_missing_validator_bootstrap_inputs_refuse_inventory(subject, relative):
    root, _, dev, _ = subject
    root.joinpath(*relative.split("/")).unlink()
    with pytest.raises(candidate.CandidateValidationError, match="absent"):
        candidate.matrix(root, root.parent / "validated-dev", dev)


def test_validator_bootstrap_recipe_keeps_pinned_deny_local_contract(subject, tmp_path, monkeypatch):
    from _promotion_workflow_test_support import source_owned
    root = subject[0]
    workspace = tmp_path / "fresh-bootstrap"
    workspace.mkdir()
    monkeypatch.setenv("ARTIFACT_VALIDATION_INDEX_URL", "https://packages.example.invalid/simple")
    commands = candidate.recipes(root, "validator-bootstrap", bootstrap_root=workspace)
    target = workspace / "validator"
    interpreter = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert commands == [
        (["uv", "venv", "--python", sys.executable, str(target)], root),
        ([sys.executable, str(root / "tools/seasoned_uv.py"), "--registry-only", "--uv", "uv",
          "--", "--python", str(interpreter), "--no-config", "--only-binary", ":all:",
          "--require-hashes", "--index-url", "https://packages.example.invalid/simple",
          "-r", str(root / "tools/artifact_validation_requirements.txt")], root),
        ([str(interpreter), "-I", "-B", "-c",
          "import packaging; print(packaging.__version__)"], root),
    ]
    jobs = yaml.safe_load((ROOT / ".github/workflows/validate-and-promote.yml").read_text())["jobs"]
    helper_root = "reviewed-helpers/" if source_owned(jobs) else ""
    prefix = f"python {helper_root}tools/seasoned_uv.py --registry-only --uv uv --"
    for job in ("build-python-artifacts", "python-artifacts", "promote", "publish-python-artifacts"):
        body = next(step["run"] for step in jobs[job]["steps"] if prefix in step.get("run", ""))
        line = next(line.strip() for line in body.replace("\\\n", "").splitlines() if prefix in line)
        actual = shlex.split(line)
        assert actual[actual.index("--python") + 1] == "$RUNNER_TEMP/artifact-validator/bin/python"
        assert actual[actual.index("--index-url") + 1] == (
            "${ARTIFACT_VALIDATION_INDEX_URL:-https://pypi.org/simple}"
        )
        assert actual[actual.index("-r") + 1] == helper_root + "tools/artifact_validation_requirements.txt"
        actual[:2] = [sys.executable, str(root / "tools/seasoned_uv.py")]
        actual[actual.index("--python") + 1] = str(interpreter)
        actual[actual.index("--index-url") + 1] = "https://packages.example.invalid/simple"
        actual[actual.index("-r") + 1] = str(root / "tools/artifact_validation_requirements.txt")
        assert actual == commands[1][0]
    monkeypatch.setenv("ARTIFACT_VALIDATION_INDEX_URL", "")
    fallback = candidate.recipes(root, "validator-bootstrap", bootstrap_root=workspace)[1][0]
    assert fallback[fallback.index("--index-url") + 1] == "https://pypi.org/simple"
    (workspace / "retained-cache").mkdir()
    with pytest.raises(candidate.CandidateValidationError, match="fresh isolated"):
        candidate.recipes(root, "validator-bootstrap", bootstrap_root=workspace)


@pytest.mark.parametrize("mode", ["accepted", "wrong-hash", "local-source", "unsupported-helper"])
def test_real_candidate_bootstrap_is_fresh_and_refuses_bad_inputs(subject, tmp_path, monkeypatch, mode):
    from test_installer_dependency_policy import materialized_broken_helper

    root, _, dev, main = subject
    helper = materialized_broken_helper(tmp_path / "broken-source")
    shutil.copytree(helper.parent.parent, root, dirs_exist_ok=True)
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as wheel:
        wheel.writestr("packaging/__init__.py", "__version__ = '25.0'\n")
        prefix = "packaging-25.0.dist-info/"
        wheel.writestr(prefix + "METADATA", "Metadata-Version: 2.1\nName: packaging\nVersion: 25.0\n")
        wheel.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(prefix + "RECORD", "")
    artifact = content.getvalue()
    digest = hashlib.sha256(artifact).hexdigest()
    filename = "packaging-25.0-py3-none-any.whl"
    downloads = []

    class Index(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond(head=False)

        def do_HEAD(self):
            self.respond(head=True)

        def respond(self, *, head):
            if self.path == "/simple/packaging/":
                body = json.dumps({
                    "meta": {"api-version": "1.2"}, "name": "packaging",
                    "files": [{
                        "filename": filename, "url": "/files/" + filename,
                        "hashes": {"sha256": digest}, "upload-time": "2020-01-01T00:00:00Z",
                    }],
                }).encode()
                kind = "application/vnd.pypi.simple.v1+json"
            elif self.path == "/files/" + filename:
                body, kind = artifact, "application/octet-stream"
                if not head:
                    downloads.append(filename)
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

    for key in list(os.environ):
        if key.startswith(("UV_", "PIP_", "PYTHON")) or key == "VIRTUAL_ENV":
            monkeypatch.delenv(key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Index)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("ARTIFACT_VALIDATION_INDEX_URL", f"http://127.0.0.1:{server.server_port}/simple")
    requirements = root / "tools" / "artifact_validation_requirements.txt"
    requirements.write_text(
        f"packaging==25.0 --hash=sha256:{'0' * 64 if mode == 'wrong-hash' else digest}\n",
        encoding="utf-8",
    )
    if mode == "local-source":
        requirements.write_text(str(root / "plugins" / "fixture-plugin") + "\n", encoding="utf-8")
    if mode == "unsupported-helper":
        (root / "tools" / "seasoned_uv.py").write_text(
            "import argparse\nargparse.ArgumentParser().parse_args()\n", encoding="utf-8",
        )
    head = commit(root)
    original = candidate.recipes
    workspaces = []

    def recipes(path, component, **kwargs):
        workspace = kwargs["bootstrap_root"]
        assert not any(workspace.iterdir())
        workspaces.append(workspace)
        return original(path, component, **kwargs)

    monkeypatch.setattr(candidate, "recipes", recipes)
    try:
        if mode == "accepted":
            assert candidate.run(
                root, head, dev, main, "validator-bootstrap", dev_root=root.parent / "validated-dev",
            )["outcome"] == "success"
            assert downloads
        else:
            with pytest.raises(candidate.CandidateValidationError, match="consumer failed"):
                candidate.run(
                    root, head, dev, main, "validator-bootstrap", dev_root=root.parent / "validated-dev",
                )
            if mode in {"local-source", "unsupported-helper"}:
                assert not downloads
        assert workspaces and all(not workspace.exists() for workspace in workspaces)
        assert candidate.validate(root, head, dev, main)["candidate_sha"] == head
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


@pytest.mark.parametrize("name,seconds", [
    ("agent-dispatch", 1800), ("agent-index", 2700), ("agent-machines", 2700),
    ("agent-bridge", 900), ("agent-worktrees", 900),
])
def test_outer_budget_preserves_real_runner_policy_and_setup_allowance(name, seconds):
    from materialized_candidate_inventory import plugin_minutes

    assert plugin_minutes(ROOT, name) * 60 >= seconds + 180


@pytest.mark.parametrize("policy", [
    "", RUNNER_POLICY.replace("900.0", "0"), RUNNER_POLICY.replace("900.0", "True"),
    RUNNER_POLICY.replace("= {}", '= {"agent-bridge": -1}'),
])
def test_unknown_or_invalid_recipe_budget_is_not_replaced_by_a_short_default(tmp_path, policy):
    from materialized_candidate_inventory import plugin_minutes

    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "run-plugin-tests.py").write_text(policy, encoding="utf-8")
    with pytest.raises(candidate.CandidateValidationError, match="budgets are unreadable"):
        plugin_minutes(tmp_path, "agent-bridge")


def test_enclosing_jobs_allow_all_recipe_calls_and_host_setup(subject, tmp_path):
    root, _, dev, _ = subject
    workspace = tmp_path / "bootstrap"
    workspace.mkdir()
    for row in candidate.matrix(root, root.parent / "validated-dev", dev)["include"]:
        commands = (
            candidate.recipes(root, row["component"], bootstrap_root=workspace)
            if row["component"] == "validator-bootstrap" else candidate.recipes(root, row["component"])
        )
        assert row["job_minutes"] >= row["minutes"] * len(commands) + 5


def test_real_fleet_recipe_preserves_existing_bounded_exclusions(subject):
    root, head, dev, main = subject
    assert candidate.run(
        root, head, dev, main, "fleet-manager:gateway", dev_root=root.parent / "validated-dev",
    )["outcome"] == "success"
    assert (root / "fleet-exclusions.txt").read_text() == (
        '-m "not client_distribution_full and not observation_retention_full"'
    )
    assert (root / "fleet-selector.txt").read_text() == (
        '-m "not client_distribution_full and not observation_retention_full" '
        '-k "gateway_ and not gateway_processes"'
    )


def test_fleet_recipes_preserve_each_canonical_partition_and_inner_deadline(subject):
    root = subject[0]
    jobs = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]
    canonical = jobs["fleet-native-linux"]["strategy"]["matrix"]["partition"]
    assert jobs["fleet-native-windows"]["strategy"]["matrix"]["partition"] == canonical
    assert candidate.fleet_partitions(root) == {
        entry["name"]: entry["selector"] for entry in canonical
    }
    for partition in canonical:
        commands = candidate.recipes(root, f"fleet-manager:{partition['name']}")
        assert len(commands) == 3
        assert commands[-1][0][-2:] == ["--timeout", "120"]
    with pytest.raises(candidate.CandidateValidationError, match="unknown materialized"):
        candidate.recipes(root, "fleet-manager")
    with pytest.raises(candidate.CandidateValidationError, match="unknown canonical Fleet"):
        candidate.recipes(root, "fleet-manager:unrecognized")


@pytest.mark.parametrize("mutation", ["duplicate", "nonliteral", "platform-drift"])
def test_unknown_fleet_partition_shape_cannot_silently_reduce_coverage(tmp_path, mutation):
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    if mutation == "duplicate":
        text = text.replace("- name: planner\n", "- name: storage\n", 1)
    elif mutation == "nonliteral":
        text = text.replace("selector: 'not gateway_", "selector: '${{ unsafe }} not gateway_", 1)
    else:
        text = text.replace("partition: *fleet-native-partitions", "partition: []")
    path = tmp_path / ".github/workflows/ci.yml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(candidate.CandidateValidationError, match="Fleet"):
        candidate.fleet_partitions(tmp_path)


def test_isolated_trusted_cli_loads_inventory_and_executes_actual_consumer(subject):
    root, head, dev, main = subject
    result = subprocess.run(
        [
            sys.executable, "-I", "-B", str(ROOT / "tools" / "validate_materialized_candidate.py"),
            "--root", str(root), "--candidate-sha", head,
            "--dev-root", str(root.parent / "validated-dev"), "--dev-sha", dev,
            "--main-before", main, "--component", "plugin:agent-worktrees",
        ],
        cwd=root, capture_output=True, text=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outcome"] == "success"
    assert len((root / "executions.jsonl").read_text().splitlines()) == 2


def test_pipeline_requires_successful_exact_candidate_jobs_and_preserves_old_gates():
    from _promotion_workflow_test_support import assert_source_owned_boundary, source_owned
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "validate-and-promote.yml").read_text(),
    )
    jobs = workflow["jobs"]
    merger = jobs["merge-promotion"]
    if source_owned(jobs):
        assert_source_owned_boundary(jobs)
        qualifier = jobs["qualify-candidate"]
        assert qualifier["permissions"] == {"contents": "read", "actions": "write"}
        assert "environment" not in qualifier and "secrets." not in json.dumps(qualifier)
        checkouts = [step for step in qualifier["steps"] if step.get("uses") == "actions/checkout@v4"]
        assert len(checkouts) == 3
        assert all(step["with"]["persist-credentials"] is False for step in checkouts)
        assert checkouts[0]["with"]["ref"] == "${{ env.RELEASE_HELPERS_SHA }}"
        assert checkouts[1]["with"]["ref"] == "${{ needs.promote.outputs.candidate_sha }}"
        assert checkouts[2]["with"]["ref"] == "${{ needs.gate.outputs.sha }}"
        execute = next(step for step in qualifier["steps"] if step.get("id") == "qualify")
        for argument in (
            "--candidate-root candidate", "--dev-root validated-dev", "--candidate-sha",
            "--dev-sha", "--main-before", "--control-sha", "--receipt", "--github-output",
        ):
            assert argument in execute["run"]
        assert not any("checkout" in step.get("uses", "") for step in merger["steps"])
        assert "python-artifacts" in jobs
        return
    assert set(merger["needs"]) == {
        "promote", "materialized-candidate-plan", "materialized-candidate-consumers",
        "materialized-candidate-windows",
    }
    assert "needs.materialized-candidate-plan.result == 'success'" in merger["if"]
    assert "needs.materialized-candidate-consumers.result == 'success'" in merger["if"]
    assert re.sub(r"\s+", " ", merger["if"]).strip() == (
        "needs.promote.outputs.pr_number != '' && "
        "needs.materialized-candidate-plan.result == 'success' && "
        "needs.materialized-candidate-consumers.result == 'success' && "
        "needs.materialized-candidate-windows.result == 'success'"
    )
    assert not any("checkout" in step.get("uses", "") for step in merger["steps"])
    assert "postmerge-workflows" in jobs and "python-artifacts" in jobs
    for name in (
        "materialized-candidate-plan", "materialized-candidate-consumers",
        "materialized-candidate-windows",
    ):
        job = jobs[name]
        assert job["permissions"] == {"contents": "read"} and "environment" not in job
        assert name in jobs["report-failure"]["needs"]
        assert f"needs.{name}.result == 'failure'" in jobs["report-failure"]["if"]
        checkouts = [step for step in job["steps"] if step.get("uses") == "actions/checkout@v4"]
        assert len(checkouts) == 3
        assert all(step["with"]["persist-credentials"] is False for step in checkouts)
        assert checkouts[0]["with"]["ref"] == "${{ github.workflow_sha }}"
        assert checkouts[1]["with"]["ref"] == "${{ needs.promote.outputs.candidate_sha }}"
        assert checkouts[2]["with"]["ref"] == "${{ needs.gate.outputs.sha }}"
        assert "APERTURE_RELEASE_TOKEN" not in json.dumps(job)
        assert "secrets." not in json.dumps(job)
    assert jobs["materialized-candidate-plan"]["runs-on"] == "ubuntu-latest"
    assert jobs["materialized-candidate-consumers"]["runs-on"] == "ubuntu-latest"
    assert jobs["materialized-candidate-windows"]["runs-on"] == "windows-latest"
    assert jobs["materialized-candidate-consumers"]["timeout-minutes"] == "${{ matrix.job_minutes }}"
    assert jobs["materialized-candidate-windows"]["timeout-minutes"] == "${{ matrix.job_minutes }}"
    assert jobs["materialized-candidate-windows"]["strategy"]["matrix"] == (
        "${{ fromJson(needs.materialized-candidate-plan.outputs.windows_matrix) }}"
    )
    assert jobs["materialized-candidate-plan"]["outputs"]["windows_matrix"] == (
        "${{ steps.inventory.outputs.windows_matrix }}"
    )
    planner = jobs["materialized-candidate-plan"]["steps"][-1]["run"]
    assert 'select(.os == "ubuntu-latest")' in planner
    assert 'select(.os == "windows-latest")' in planner
    assert jobs["materialized-candidate-windows"]["strategy"]["fail-fast"] is False
    assert jobs["materialized-candidate-consumers"]["strategy"]["fail-fast"] is False
    assert "needs.materialized-candidate-plan.result == 'success'" in (
        jobs["materialized-candidate-consumers"]["if"]
    )


def test_candidate_recipes_preserve_current_main_consumer_contracts(subject):
    root, _, _, _ = subject
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    jobs = workflow["jobs"]
    manager = next(
        step["run"] for step in jobs["worktree-manager"]["steps"]
        if step.get("name") == "Test worktree-manager"
    )
    assert candidate.recipes(root, "manager")[0][0] == manager.split()
    dispatch = next(
        step["run"] for step in jobs["agent-dispatch-service"]["steps"]
        if step.get("name", "").startswith("Test agent-dispatch-service")
    )
    assert dispatch.split()[3:] == candidate.recipes(root, "dispatch")[0][0][3:]
    for name in ("agent-index-service-linux", "agent-index-service-windows"):
        assert any('MODE="--smoke"' in step.get("run", "") for step in jobs[name]["steps"])
    assert candidate.recipes(root, "index")[0][0][-1] == "--smoke"


def test_cli_reports_failure_not_success_for_an_unbound_checkout(subject, capsys):
    root, _, dev, main = subject
    assert candidate.main([
        "--root", str(root), "--candidate-sha", "f" * 40,
        "--dev-root", str(root.parent / "validated-dev"),
        "--dev-sha", dev, "--main-before", main, "--matrix",
    ]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "does not match" in captured.err
