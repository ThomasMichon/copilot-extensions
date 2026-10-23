"""Immutable helper inputs publish under retained, already-admitted authority."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import venv

import pytest

from runtime_install_fixture import isolate_account_profile
from test_source_install_transactions import _function, _payload, _ps, _sh

REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / "plugins" / "agent-worktrees"
DECLARED_VERSION = "1.5.5-dev285"
NORMALIZED_VERSION = "1.5.5.dev285"
# Publication invariants, not a cold native-shell startup latency contract.
CALLBACK_TIMEOUT = 60 if os.name == "nt" else 20


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.syspath_prepend(str(PLUGIN / "scripts"))
    return load(REPO / "libs" / "installer-engine" / "installer_engine.py", "source_pin_engine")


@pytest.mark.parametrize("boundary", ["admission", "construction", "reuse", "completion"])
def test_candidate_checks_full_source_pin_at_boundaries(engine, tmp_path, monkeypatch, boundary):
    payload, receipt = _payload(tmp_path)
    pin = {"runtime_version": receipt["runtime_version"], "runtime_source": receipt}
    root = tmp_path / "runtime"
    version = receipt["runtime_version"]
    slot = root / "versions" / version
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    before = json.loads((payload / "runtime-source.json").read_text())

    def mutate():
        changed = {**before, "source_tree": "c" * 40}
        assert changed["runtime_version"] == before["runtime_version"]
        (payload / "runtime-source.json").write_text(json.dumps(changed), encoding="utf-8")

    inputs = engine.FrozenInputs(payload, (payload,), "unused")
    monkeypatch.setattr(engine, "_retained_inputs", lambda *_args, **_kwargs: context(inputs))

    @contextmanager
    def context(value):
        yield value

    def construct(*_args, **_kwargs):
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"fixture interpreter")
        return python

    monkeypatch.setattr(engine, "_construct", construct)
    monkeypatch.setattr(engine, "_verify_installed", lambda *_args, **_kwargs: (
        mutate() if boundary == "completion" else None
    ))
    if boundary == "admission":
        mutate()
    if boundary == "construction":
        original = engine.runtime.construction_lease

        @contextmanager
        def locked(*args, **kwargs):
            with original(*args, **kwargs) as descriptor:
                mutate()
                yield descriptor

        monkeypatch.setattr(engine.runtime, "construction_lease", locked)
    if boundary == "reuse":
        construct()
        digest = engine.runtime.fingerprint_source(engine._source_files((payload,)))
        engine.runtime.mark_complete(root, version, payload_hash=digest)
        original = engine.runtime.retention_custody

        @contextmanager
        def retained(*args, **kwargs):
            with original(*args, **kwargs) as value:
                mutate()
                yield value

        monkeypatch.setattr(engine.runtime, "retention_custody", retained)
    with pytest.raises((RuntimeError, engine.runtime_source.SourceIdentityError), match="identity|source"):
        with engine.numbered_candidate(
            root, version, plugin=payload, uv=tmp_path / "unused",
            base_python=Path(sys.executable), health_module="unused",
            expected_source_identity=pin,
        ):
            pytest.fail("Changed full source pin reached handover")
    if boundary != "reuse":
        assert not engine.runtime.marker_path(root, version).exists()


def native_hosts():
    values = []
    if os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
        if candidate and candidate.is_file():
            values.append(("sh", str(candidate)))
    elif executable := shutil.which("bash"):
        values.append(("sh", executable))
    for name in ("pwsh", "powershell.exe"):
        executable = shutil.which(name)
        if executable and (os.name == "nt" or not executable.lower().endswith(".exe")):
            values.append(("ps1", executable))
    return values


@pytest.fixture
def publication_transform():
    return lambda _content: None


@pytest.fixture(params=["legacy", "namespaced"])
def publication_case(tmp_path, monkeypatch, request, publication_transform):
    owner, content = tmp_path / "original-owner", tmp_path / "immutable-content"
    home = tmp_path / "home"
    home.mkdir()
    content.mkdir()
    shutil.copytree(
        PLUGIN / "scripts", content / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(PLUGIN / "bin", content / "bin")
    package = content / "src" / "agent_worktrees"
    package.mkdir(parents=True)
    for name in ("_installation_context_resolution.py", "native_source_context.py"):
        shutil.copyfile(PLUGIN / "src" / "agent_worktrees" / name, package / name)
    (content / "pyproject.toml").write_text(
        f'[project]\nname="agent-worktrees"\nversion="{DECLARED_VERSION}"\n', encoding="utf-8",
    )
    (content / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": DECLARED_VERSION}), encoding="utf-8",
    )
    isolate_account_profile(content, home)
    publication_transform(content)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    monkeypatch.syspath_prepend(str(content / "scripts"))
    driver = load(content / "scripts" / "runtime_install.py", "source_publication_driver")
    source = driver.runtime_source
    digest = source.assembly_hash(content)
    identity = source.SourceIdentity(
        "agent-worktrees", DECLARED_VERSION, "a" * 40, "b" * 40, digest,
        source.runtime_version(DECLARED_VERSION, "a" * 40, digest),
    )
    (content / source.SOURCE_FILE).write_text(json.dumps(identity.document()), encoding="utf-8")
    shutil.copytree(content, owner)
    root = home / ".agent-worktrees"
    if request.param == "namespaced":
        helper = load(content / "scripts/installation-context/installation_context.py", "publication_context")
        durable = home / ".copilot-extensions"
        context = helper.stamp_context(
            payload_version=DECLARED_VERSION, payload_origin="explicit",
            expected_namespace_generation=0, expected_install_generation=0,
            payload_root=owner, plugin_id="agent-worktrees", durable_home=durable,
            source_descriptor={"source": "github", "repo": "example/runtime"},
            marketplace_key="example", environment=dict(os.environ),
        )
        (durable / "installation-mode.json").write_text(json.dumps({
            "schema": helper.POLICY_SCHEMA, "version": 1, "installationMode": {"enabled": True},
        }), encoding="utf-8")
        helper.compare_and_swap_activation(
            context=context["installReceipt"], expected_marketplace_id=context["marketplaceId"],
            expected_plugin_id="agent-worktrees", expected_namespace_generation=context["namespaceGeneration"],
            expected_install_generation=context["generation"], expected_activation_generation=0,
            activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
            legacy_probe={"declared": True, "result": "absent", "checkedAt": None},
            durable_home=durable, legacy_root=root, environment=dict(os.environ), os_profile=home,
        )
        root = Path(context["pluginRoot"])
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context["installReceipt"])
    slot = root / "versions" / identity.runtime_version
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"retained fixture")
    driver.runtime.mark_complete(root, slot.name, payload_hash="candidate")
    monkeypatch.setattr(driver, "_installed_identity", lambda _slot: {
        "package_version": NORMALIZED_VERSION,
        "build_info": {"version": DECLARED_VERSION, "runtime_version": slot.name,
                       "runtime_generation": "f" * 32, "deployment_source": json.dumps({
            "kind": "local", "path": owner.as_posix(),
            "version": DECLARED_VERSION, "plugin": "agent-worktrees",
            "payload_fingerprint": "a" * 64,
            **source.manifest_fields(identity),
        })},
    })
    return driver, root, slot, owner, content, identity


@contextmanager
def admitted(driver, root, owner, content, monkeypatch, *, hold=True):
    governance = driver.Governance(root, owner)
    if governance.context:
        (root / "deploy-manifest.json").write_text('{"previous":"manifest"}', encoding="utf-8")
        with (governance.held(timeout=2) if hold else nullcontext()):
            yield governance, None
        return
    binding = load(content / "scripts" / "command_binding.py", "source_callback_binding")
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(
        binding._rules(), governance.helper, payload_root=owner, plugin_id="agent-worktrees",
        legacy_root=root, source_payload_root=owner,
    ) as lease:
        monkeypatch.setenv(lifecycle.TICKET_ENV, json.dumps(lease.ticket))
        with governance.held(timeout=2):
            yield governance, lease


@pytest.mark.parametrize(("shell", "executable"), native_hosts())
@pytest.mark.parametrize("fault", ["healthy", "publisher", "full-pin", "invalid-ack"])
@pytest.mark.timeout(90)
def test_actual_native_callback_preserves_owner_and_selected_runtime(
    publication_case, monkeypatch, shell, executable, fault,
):
    driver, root, slot, owner, content, identity = publication_case
    original = driver.engine.run_owned
    bootstrap_python = Path(sys._base_executable).with_name("unused-bootstrap-python")

    def invoke(argv, **kwargs):
        assert kwargs["env"].get("COPILOT_EXTENSIONS_CONTEXT") == os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
        interpreter_option = "-BootstrapPython" if shell == "ps1" else "--runtime-publication-python"
        assert argv[argv.index(interpreter_option) + 1] == sys._base_executable
        plan_option = "-RuntimePublicationPlan" if shell == "ps1" else "--runtime-publication-plan"
        plan = json.loads(Path(argv[argv.index(plan_option) + 1]).read_text())
        assert plan["bootstrap"] == str(bootstrap_python)
        if fault == "full-pin":
            changed = {**identity.document(), "source_tree": "c" * 40}
            (content / "runtime-source.json").write_text(json.dumps(changed), encoding="utf-8")
        result = original(argv, **kwargs)
        if fault == "invalid-ack":
            plan = Path(argv[argv.index("-RuntimePublicationPlan" if shell == "ps1"
                                        else "--runtime-publication-plan") + 1])
            destination = Path(json.loads(plan.read_text())["result"])
            destination.write_text('{"published":false}', encoding="utf-8")
        return result

    monkeypatch.setattr(driver.engine, "run_owned", invoke)
    with admitted(driver, root, owner, content, monkeypatch) as (governance, lease):
        (root / "current-version").write_text("previous\n")
        (root / "last-known-good").write_text("previous\n")
        if fault == "publisher":
            (root / "bin").write_text("not a directory", encoding="utf-8")
        observed = {
            path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
            for path in (root / "current-version", root / "last-known-good", root / "deploy-manifest.json")
        }
        with driver.runtime.retention_custody(root, slot.name, payload_hash="candidate") as (_slot, descriptor):
            with nullcontext():
                if fault == "healthy":
                    transaction = driver._publish_helpers(
                        governance, {"shell": shell, "executable": executable, "scope": "resolvers"},
                        content, slot, descriptor, timeout=CALLBACK_TIMEOUT,
                        base_python=bootstrap_python,
                    )
                    transaction.keep()
                else:
                    errors = (subprocess.CalledProcessError, driver.InstallationRefused)
                    if fault == "publisher":
                        errors += (OSError,)
                    with pytest.raises(errors) as caught:
                        driver._publish_helpers(
                            governance, {"shell": shell, "executable": executable, "scope": "resolvers"},
                            content, slot, descriptor, timeout=CALLBACK_TIMEOUT,
                            base_python=bootstrap_python,
                        )
                    diagnostic = str(caught.value) + str(getattr(caught.value, "stderr", ""))
                    normalized = diagnostic.replace("\\", "/").casefold()
                    if fault == "publisher":
                        assert (root / "bin").read_text() == "not a directory"
                        compact = re.sub(r"/+", "/", "".join(normalized.split()))
                        paths = ((root / "bin").as_posix(), _sh(root / "bin").strip("'"))
                        assert any("".join(path.casefold().split()) in compact for path in paths), diagnostic
                        assert ("directory" in normalized or "exists" in normalized or (
                            "could not find a part of the path" in normalized
                            and "/bin/.resolve-runtime.ps1." in normalized
                        )), diagnostic
                    else:
                        expected = "source identity" if fault == "full-pin" else "invalid acknowledgement"
                        assert expected in normalized, diagnostic
        assert {
            path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
            for path in observed
        } == observed
        if fault == "healthy":
            for name in ("resolve-runtime.sh", "resolve-runtime.ps1"):
                assert (root / "bin" / name).read_bytes() == (content / "scripts" / name).read_bytes()
            if lease is not None:
                assert lease.owner.get("payloadRoot", str(owner)) == str(owner)
            for lock in governance.publication_locks:
                lock.assert_owned()


@pytest.mark.parametrize(("shell", "executable"), native_hosts())
@pytest.mark.parametrize("fault", ["owner", "stale-owner", "governance", "token"])
def test_actual_callback_rejects_changed_authority(
    publication_case, monkeypatch, shell, executable, fault,
):
    driver, root, slot, owner, content, _identity = publication_case
    original = driver.engine.run_owned

    def alter(argv, **kwargs):
        argv = list(argv)
        flag = "-RuntimePublicationPlan" if shell == "ps1" else "--runtime-publication-plan"
        plan_path = Path(argv[argv.index(flag) + 1])
        plan = json.loads(plan_path.read_bytes())
        if fault == "owner":
            other = owner.parent / "foreign-owner"
            other.mkdir()
            plan["owner"] = str(other)
        elif fault == "governance":
            plan["governance"]["state"]["status"] = "foreign"
        elif plan["locks"]:
            if fault == "stale-owner":
                plan["pid"] = 2147483647
            else:
                plan["locks"][0]["token"] = "0" * 32
        else:
            ticket = json.loads(kwargs["env"]["COPILOT_EXTENSIONS_COMMAND_LIFECYCLE"])
            ticket["pid" if fault == "stale-owner" else "token"] = (
                2147483647 if fault == "stale-owner" else "0" * 32
            )
            kwargs["env"]["COPILOT_EXTENSIONS_COMMAND_LIFECYCLE"] = json.dumps(ticket)
        data = json.dumps(plan).encode()
        plan_path.write_bytes(data)
        flag = "-RuntimePublicationDigest" if shell == "ps1" else "--runtime-publication-digest"
        argv[argv.index(flag) + 1] = hashlib.sha256(data).hexdigest()
        return original(argv, **kwargs)

    monkeypatch.setattr(driver.engine, "run_owned", alter)
    with admitted(driver, root, owner, content, monkeypatch) as (governance, _lease):
        with driver.runtime.retention_custody(root, slot.name, payload_hash="candidate") as (_slot, descriptor):
            with pytest.raises(subprocess.CalledProcessError) as caught:
                driver._publish_helpers(
                    governance, {"shell": shell, "executable": executable, "scope": "resolvers"},
                    content, slot, descriptor, timeout=20, base_python=Path(sys._base_executable),
                )
            diagnostic = " ".join(caught.value.stderr.casefold().split())
            expected = {
                "owner": ("expected payload", "ticket names another source"),
                "stale-owner": ("borrowed namespace publication owner changed", "runtime lifecycle admission changed"),
                "governance": ("helper publication governance changed",),
                "token": ("ownership changed during mutation",),
            }[fault]
            assert any(text in diagnostic for text in expected), caught.value.stderr
        assert not (root / "bin" / "resolve-runtime.sh").exists()
        for lock in governance.publication_locks:
            lock.assert_owned()


def test_declared_dev_package_and_qualified_operational_cli(tmp_path, engine):
    scripts = tmp_path / "frozen" / "scripts"
    shutil.copytree(PLUGIN / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    frozen = scripts.parent
    (frozen / "pyproject.toml").write_text(
        f'[project]\nname="agent-worktrees"\nversion="{DECLARED_VERSION}"\n', encoding="utf-8",
    )
    source = load(scripts / "versioned_runtime_source.py", "operational_version_source")
    digest = source.assembly_hash(frozen)
    identity = source.SourceIdentity(
        "agent-worktrees", DECLARED_VERSION, "a" * 40, "b" * 40, digest,
        source.runtime_version(DECLARED_VERSION, "a" * 40, digest),
    )
    slot = tmp_path / "versions" / identity.runtime_version
    venv.EnvBuilder(with_pip=False, symlinks=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    site = Path(subprocess.check_output(
        [str(python), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
        text=True, timeout=10, **options,
    ).strip())
    dependency_paths = [
        str(package / "src" if (package / "src").is_dir() else package)
        for package in engine._package_roots(PLUGIN) if package != PLUGIN
    ]
    support = (
        "import site,sys;site.addsitedir(" + repr(sysconfig.get_path("purelib")) + ");"
        "sys.path.extend(" + repr(dependency_paths) + ")\n"
    )
    (site / "prepared-test-dependencies.pth").write_text(support, encoding="utf-8")
    shutil.copytree(
        PLUGIN / "src/agent_worktrees", site / "agent_worktrees",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    metadata = site / f"agent_worktrees-{NORMALIZED_VERSION}.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: agent-worktrees\nVersion: {NORMALIZED_VERSION}\n",
        encoding="utf-8",
    )
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for name in ("runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
                 "versioned_runtime_construction.py", "versioned_runtime_source.py",
                 "versioned_runtime_publication.py"):
        shutil.copyfile(scripts / name, bootstrap / name)
    provenance = {
        "version": DECLARED_VERSION, "source": frozen.as_posix(),
        "commit": identity.source_commit, "branch": "unknown", "frozen_plugin": str(frozen),
        "deployment_source": json.dumps({
            "version": DECLARED_VERSION, "plugin": "agent-worktrees", "path": frozen.as_posix(),
            **source.manifest_fields(identity),
        }),
    }
    environment = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), AGENT_HOME=str(tmp_path))
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    prepare = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import json,sys;from agent_worktrees.runtime_candidate import prepare_runtime_candidate;"
         "prepare_runtime_candidate(json.loads(sys.argv[1]))", json.dumps(provenance)],
        capture_output=True, text=True, timeout=20, env=environment, **options,
    )
    assert prepare.returncode == 0, prepare.stdout + prepare.stderr
    result = subprocess.run(
        [str(python), "-I", "-B", "-m", "agent_worktrees", "--version"],
        capture_output=True, text=True, timeout=20, env=environment, **options,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("agent-worktrees " + identity.runtime_version + " ")
    installed = subprocess.check_output(
        [str(python), "-I", "-B", "-c",
         "import json,importlib.metadata;from agent_worktrees._build_info import BUILD_INFO;"
         "import agent_worktrees;print(json.dumps([importlib.metadata.version('agent-worktrees'),"
         "BUILD_INFO,agent_worktrees.__file__]))"],
        text=True, timeout=10, env=environment, **options,
    )
    version, info, package_path = json.loads(installed)
    assert Path(package_path).resolve().is_relative_to(slot.resolve())
    assert version == NORMALIZED_VERSION and info["version"] == DECLARED_VERSION
    assert info["runtime_version"] == identity.runtime_version


@pytest.mark.skipif(os.name != "nt", reason="native MSYS path boundary")
def test_msys_context_normalization_is_shell_local(tmp_path):
    executable = next((exe for shell, exe in native_hosts() if shell == "sh"), None)
    if executable is None:
        pytest.skip("native Git Bash unavailable")
    source = (PLUGIN / "scripts/install.sh").read_text(encoding="utf-8")
    function = re.search(r"(?ms)^    __aw_shell_path\(\) \{.*?^    \}", source)
    assert function is not None
    context = tmp_path / "home/.copilot-extensions/marketplaces/example/plugins/agent-worktrees/install.json"
    script = tmp_path / "path.sh"
    script.write_text(f"""
set -euo pipefail
{function.group(0)}
export COPILOT_EXTENSIONS_CONTEXT={shlex.quote(str(context))}
original="$COPILOT_EXTENSIONS_CONTEXT"
converted="$(__aw_shell_path "$original")"
[[ "$converted" == /* ]]
durable="$converted"
for _ in 1 2 3 4 5; do durable="$(dirname -- "$durable")"; done
[[ "$durable" == /* && "$COPILOT_EXTENSIONS_CONTEXT" == "$original" ]]
printf '%s\\n' "$durable"
""", encoding="utf-8", newline="\n")
    result = subprocess.run(
        [executable, script.as_posix()], capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("shell", "executable"), native_hosts())
def test_committed_backend_ack_does_not_reopen_revoked_origin(
    publication_case, monkeypatch, shell, executable, tmp_path,
):
    from agent_worktrees import config as cfg

    driver, root, slot, owner, content, identity = publication_case
    monkeypatch.setattr(driver, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "different-monitor-scope")
    with admitted(driver, root, owner, content, monkeypatch, hold=False) as (governance, _lease):
        result = driver.settle_candidate(
            root, slot.name, payload=owner, payload_hash="candidate",
            expected_governance=governance.signature(), package_version=DECLARED_VERSION,
            frontend={"shell": shell, "executable": executable, "scope": "resolvers"},
            publication_source_root=content, child_timeout=20,
            base_python=Path(sys._base_executable),
        )
        assert result["helpers_published"] is True
        assert result["deployment_manifest_published"] is True
        protected = [root / "current-version", root / "last-known-good", root / "deploy-manifest.json",
                     root / "bin/resolve-runtime.sh", root / "bin/resolve-runtime.ps1"]
        if governance.context:
            protected.append(Path(os.environ["COPILOT_EXTENSIONS_CONTEXT"]))
        else:
            protected.extend(owner.parent / "home/.local/bin" / name
                             for name in ("agent-worktrees", "agent-worktrees.cmd", "agent-worktrees.ps1"))
        observed = {
            path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
            for path in protected
        }
        (owner / "runtime-source.json").write_text("revoked after commit", encoding="utf-8")
        (owner / "scripts").rename(tmp_path / "removed-origin-scripts")
        if shell == "ps1":
            script = tmp_path / "duplicate.ps1"
            script.write_text(f"""
$ErrorActionPreference='Stop'
$script:PythonRuntimeHelpersPublished=$true
$script:PythonRuntimeManifestPublished=$true
$SourceRuntimeIdentity={_ps(json.dumps({"runtime_version":identity.runtime_version,"runtime_source":identity.document()}))}
$PluginDir={_ps(owner)}
$InstallDir={_ps(root)}
function Write-ServiceOk {{ param($Message) }}
function Assert-SourceRuntime {{ throw 'postcommit source read' }}
{_function("Deploy-GlobalBinstub", "ps1")}
{_function("Write-V3Manifest", "ps1")}
Deploy-GlobalBinstub
Write-V3Manifest
$script:PythonRuntimeHelpersPublished=$false
$refused=$false
try {{ Deploy-GlobalBinstub }} catch {{ $refused=$true }}
if (-not $refused) {{ throw 'Manifest acknowledgement bypassed an unpublished helper' }}
""", encoding="utf-8")
            argv = [executable, "-NoProfile", "-File", str(script)]
        else:
            script = tmp_path / "duplicate.sh"
            script.write_text(f"""
set -euo pipefail
PYTHON_RUNTIME_HELPERS_PUBLISHED=1; PYTHON_RUNTIME_MANIFEST_PUBLISHED=1
PLUGIN_DIR={_sh(owner)}; INSTALL_DIR={_sh(root)}
ok() {{ :; }}; _assert_source_runtime() {{ printf 'postcommit source read\\n' >&2; return 97; }}
{_function("deploy_tool_binstub", "sh")}
{_function("write_deploy_manifest", "sh")}
deploy_tool_binstub
write_deploy_manifest
PYTHON_RUNTIME_HELPERS_PUBLISHED=0
if deploy_tool_binstub; then exit 91; fi
""", encoding="utf-8", newline="\n")
            argv = [executable, script.as_posix()]
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert {
            path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino) for path in observed
        } == observed
