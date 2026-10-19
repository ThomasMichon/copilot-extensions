from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dependency_age as age

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)
BOUNDARY = NOW - timedelta(seconds=604800)
REPO = Path(__file__).resolve().parent.parent
REPOSITORY_NPM_FLOORS = {
    "^2.6.2": "2.6.2",
    "0.31.2": "0.31.2",
    "7.2.1": "7.2.1",
}


def repository_npm_semver(spec, versions):
    """Controlled evidence for the repository's concrete ranges, not arbitrary npm."""
    floor = REPOSITORY_NPM_FLOORS[spec]
    return [floor], [floor] if floor in versions else []


def release(timestamp=BOUNDARY, *, yanked=False):
    return [{"upload_time_iso_8601": timestamp.isoformat() if timestamp else None, "yanked": yanked}]


@pytest.mark.parametrize("seconds,expected", [(604800, True), (604799, False), (604801, True), (-1, False)])
def test_inclusive_age_boundary(seconds, expected):
    assert age.aged((NOW - timedelta(seconds=seconds)).isoformat(), NOW) is expected


@pytest.mark.parametrize("timestamp", [None, "", "unknown", "2026-10-01T00:00:00"])
def test_unknown_timestamps(timestamp):
    assert not age.aged(timestamp, NOW)


@pytest.mark.parametrize("files", [
    [], None, [{}], release(None), release(yanked=True),
    release() + release(NOW), release() + release(None),
    release() + [{"upload_time_iso_8601": BOUNDARY.isoformat()}],
])
def test_newest_file_missing_and_yanked_fail_closed(files):
    assert not age.python_release_eligible(files, NOW)


def test_python_normalized_literal_floor_and_compatible_specifier():
    age.check_python(Requirement("demo>=1.0,<2"), {"releases": {"1.0.0": release()}}, NOW)


@pytest.mark.parametrize("requirement,releases", [
    ("demo>=1.0", {"1.0": release(NOW), "1.1": release()}),
    ("demo==1.0", {"1.0": release(None)}),
    ("demo~=1.0", {"1.0": release(yanked=True), "1.1": release()}),
    ("demo>=1.0,<1.0", {"1.0": release()}),
    ("demo>=1.0,!=1.0,<2", {"1.0": release()}),
    ("demo", {"1.0rc1": release(), "1.0.dev1": release(), "1.0+local": release()}),
    ("demo>=1.0", {"1.1": release()}),
    ("demo==1.*", {"1.0": release(NOW), "1.1": release()}),
    ("demo==1.0", {"1.0": release(NOW + timedelta(seconds=1))}),
])
def test_python_literals_and_unsatisfiable_specs(requirement, releases):
    with pytest.raises(age.DeclarationError):
        age.check_python(Requirement(requirement), {"releases": releases}, NOW)


def test_unbounded_upper_and_wildcard_declarations():
    for requirement in ("demo", "demo<2", "demo==1.*"):
        age.check_python(Requirement(requirement), {"releases": {"1.0": release()}}, NOW)


def write(path, text):
    if path.name == "pyproject.toml":
        data = tomllib.loads(text)
        if "exclude-newer" not in data.get("tool", {}).get("uv", {}):
            if "[tool.uv]" in text:
                text = text.replace("[tool.uv]", '[tool.uv]\nexclude-newer = "7 days"', 1)
            else:
                text = '[tool.uv]\nexclude-newer = "7 days"\n' + text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def project(root, directory="plugins/demo", *, dependencies="", other=""):
    return write(root / directory / "pyproject.toml", f"""
[build-system]
requires = ["builder>=1"]
[project]
name = "demo"
dependencies = [{dependencies}]
{other}
""")


def test_full_inventory_requirements_npm_and_vendor_exclusion(tmp_path):
    project(tmp_path, dependencies='"runtime>=1"', other="""
[project.optional-dependencies]
feature = ["optional>=1; sys_platform == 'other'"]
[dependency-groups]
dev = ["tester>=1", {include-group = "lint"}]
lint = ["linter"]
""")
    project(tmp_path, "plugins/demo/server", dependencies='"server>=1"')
    project(tmp_path, "plugins/demo/libs/vendor", dependencies='"must-not-audit>=1"')
    write(tmp_path / "tools" / "artifact_validation_requirements.txt",
          "validator==1 --hash=sha256:" + "a" * 64 + "\n-r extra.txt\n")
    write(tmp_path / "tools" / "extra.txt", "included>=1\n")
    write(tmp_path / "worktree-manager" / "scripts" / "render" / "package.json", json.dumps({
        section: {"some-package": "^1.0.0"}
        for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
    }))
    declarations, projects = age.inventory(tmp_path)
    assert len(projects) == 2
    assert len(declarations) == 13
    assert all("must-not-audit" not in d.requirement for d in declarations)
    assert len([d for d in declarations if d.ecosystem == "npm"]) == 4


@pytest.mark.parametrize("timestamp,violations", [(BOUNDARY, 0), (NOW, 2)])
def test_github_action_manifest_inventoried_and_age_audited(tmp_path, timestamp, violations):
    manifest = write(tmp_path / ".github" / "actions" / "package.json", json.dumps({
        "name": "trusted-pr-metadata-checks",
        "private": True,
        "dependencies": {"commonmark": "0.31.2", "parse5": "7.2.1"},
    }))
    for directory in ("node_modules/vendor", ".venv/vendor", "libs/vendor"):
        write(manifest.parent / directory / "package.json",
              '{"dependencies":{"must-not-audit":"1.0.0"}}')
    for directory in ("templates", "private", "generated"):
        write(tmp_path / ".github" / directory / "package.json",
              '{"dependencies":{"must-not-audit":"1.0.0"}}')
    declarations, projects = age.inventory(tmp_path)
    assert not projects
    assert [(d.path, d.section, d.requirement, d.ecosystem) for d in declarations] == [
        (manifest, "dependencies", "commonmark 0.31.2", "npm"),
        (manifest, "dependencies", "parse5 7.2.1", "npm"),
    ]
    calls = []

    def fetch(url):
        calls.append(url)
        floor = {"commonmark": "0.31.2", "parse5": "7.2.1"}[url.rsplit("/", 1)[-1]]
        return json.dumps({"versions": {floor: {}}, "time": {floor: timestamp.isoformat()}})

    count, errors = age.audit(tmp_path, fetch=fetch, now=NOW, semver=repository_npm_semver)
    assert count == 2
    assert len(errors) == violations
    assert all(str(manifest.relative_to(tmp_path)) in error and "literal floor/pin" in error
               for error in errors)
    assert sorted(calls) == [
        "https://registry.npmjs.org/commonmark",
        "https://registry.npmjs.org/parse5",
    ]


@pytest.mark.parametrize("manifest_link", [False, True])
def test_github_action_symlink_refused_before_manifest_read(tmp_path, manifest_link):
    root = tmp_path / "repo"
    outside = write(tmp_path / "outside" / "package.json", "not valid JSON")
    actions = root / ".github" / "actions"
    actions.parent.mkdir(parents=True)
    try:
        if manifest_link:
            actions.mkdir()
            (actions / "package.json").symlink_to(outside)
        else:
            actions.symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    with pytest.raises(age.DeclarationError, match="symlink|escapes"):
        age.inventory(root)


def test_local_exemption_requires_contained_path_and_audits_closure(tmp_path):
    path = project(tmp_path, dependencies='"owned"', other="""
[tool.uv.sources]
owned = {path = "../../libs/owned", editable = true}
""")
    write(tmp_path / "libs" / "owned" / "pyproject.toml", """
[project]
name = "owned"
dependencies = ["external>=1"]
""")
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps({"releases": {"1.0": release(NOW) if "/external/" in url else release()}}).encode()

    count, errors = age.audit(tmp_path, fetch=fetch, now=NOW)
    assert count == 3
    assert len(errors) == 1 and "external" in errors[0]
    assert not any("/owned/" in url for url in calls)
    assert path.is_file()


@pytest.mark.parametrize("source", [
    '{url = "https://example.invalid/owned.whl"}',
    '{git = "https://example.invalid/owned.git"}',
    '{path = "../../outside"}',
    '{path = "../../libs/wrong"}',
])
def test_arbitrary_sources_do_not_exempt(tmp_path, source):
    project(tmp_path, dependencies='"owned"', other=f"[tool.uv.sources]\nowned = {source}\n")
    write(tmp_path / "libs" / "wrong" / "pyproject.toml", '[project]\nname = "not-owned"\n')
    _, errors = age.audit(tmp_path, fetch=lambda _: json.dumps({"releases": {"1": release()}}), now=NOW)
    assert len(errors) == 1


def test_name_alone_is_not_repository_source_attribution(tmp_path):
    project(tmp_path, dependencies='"owned"')
    write(tmp_path / "libs" / "owned" / "pyproject.toml", '[project]\nname = "owned"\n')
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps({"releases": {"1": release()}})

    assert age.audit(tmp_path, fetch=fetch, now=NOW)[1] == []
    assert any("/owned/" in url for url in calls)


def test_direct_urls_and_metadata_errors_are_sanitized(tmp_path):
    project(tmp_path, dependencies='"demo-url @ https://private.example.invalid/path"')

    def fetch(_):
        raise OSError("secret private URL")

    _, errors = age.audit(tmp_path, fetch=fetch, now=NOW)
    assert len(errors) == 2
    assert all("private" not in error and "secret" not in error for error in errors)


def test_public_metadata_cached_and_host_markers_not_filtered(tmp_path):
    project(tmp_path, dependencies='"external>=1; sys_platform == \'other\'"',
            other='[project.optional-dependencies]\ndev = ["external>=1"]')
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps({"releases": {"1": release()}})

    assert age.audit(tmp_path, fetch=fetch, now=NOW) == (3, [])
    assert len(calls) == 2


def test_legacy_uv_dev_declarations_and_npm_audit(tmp_path):
    project(tmp_path, other='[tool.uv]\ndev-dependencies = ["legacy>=1"]')
    write(tmp_path / "tools" / "render" / "package.json",
          '{"dependencies":{"@example/renderer":"^2.6.2"}}')
    calls = []

    def fetch(url):
        calls.append(url)
        if url.startswith("https://registry.npmjs.org/"):
            return json.dumps({"versions": {"2.6.2": {}},
                               "time": {"2.6.2": BOUNDARY.isoformat()}})
        return json.dumps({"releases": {"1": release()}})

    assert age.audit(tmp_path, fetch=fetch, now=NOW,
                     semver=lambda *_: (["2.6.2"], ["2.6.2"])) == (3, [])
    assert "https://registry.npmjs.org/%40example%2Frenderer" in calls


def test_vendored_path_source_requires_canonical_external_closure(tmp_path):
    project(tmp_path, dependencies='"owned"', other="""
[tool.uv.sources]
owned = {path = "libs/owned"}
""")
    canonical = '[project]\nname = "owned"\ndependencies = ["external>=1"]\n'
    write(tmp_path / "libs" / "owned" / "pyproject.toml", canonical)
    vendor = write(tmp_path / "plugins" / "demo" / "libs" / "owned" / "pyproject.toml", canonical)
    fetch = lambda _: json.dumps({"releases": {"1": release()}})
    assert age.audit(tmp_path, fetch=fetch, now=NOW)[1] == []
    vendor.write_text(canonical.replace("external>=1", "hidden>=1"), encoding="utf-8")
    _, errors = age.audit(tmp_path, fetch=fetch, now=NOW)
    assert len(errors) == 1 and "canonical dependency closure" in errors[0]


def test_includes_cycle_and_escape_are_rejected(tmp_path):
    write(tmp_path / "tools" / "validation_requirements.txt", "-r validation_requirements.txt\n")
    with pytest.raises(age.DeclarationError):
        age.inventory(tmp_path)
    write(tmp_path / "tools" / "validation_requirements.txt", "-r ../../outside.txt\n")
    with pytest.raises(age.DeclarationError):
        age.inventory(tmp_path)


def _assert_owned_source_contract_attribution(root):
    declarations, projects = age.inventory(root)
    refs = [
        (root / "plugins" / "agent-index" / "server" / "pyproject.toml", "agent-index"),
        (root / "services" / "dispatch" / "pyproject.toml", "agent-dispatch"),
        (root / "services" / "index" / "pyproject.toml", "agent-index"),
        (root / "services" / "index" / "pyproject.toml", "agent-procutil"),
        (root / "plugins" / "agent-index" / "pyproject.toml", "fleet-manager"),
    ]
    count = 0
    for path, name in refs:
        assert name not in projects[path].get("tool", {}).get("uv", {}).get("sources", {})
        target = age.contract_source(Requirement(name), path, projects, root)
        assert target is not None and target != path
        assert age.attributed_project(Requirement(name), target.parent, projects, root)
        for declaration in declarations:
            req = Requirement(declaration.requirement) if declaration.ecosystem == "python" else None
            if declaration.path == path and req.name == name:
                assert age.local_source(req, path, projects, root)
                count += 1
    assert count == 7


@pytest.mark.parametrize("materialized", [False, True])
def test_owned_references_have_existing_source_contract_attribution(materialized):
    if not materialized:
        _assert_owned_source_contract_attribution(REPO)
        return
    import io
    import os
    import tarfile
    import tempfile

    spec = importlib.util.spec_from_file_location(
        "dependency_age_materialized_provenance",
        REPO / "tools" / "clean-room" / "lib" / "materialized_provenance.py",
    )
    assert spec is not None and spec.loader is not None
    provenance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provenance)
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    archive = subprocess.run(
        ["git", "-C", str(REPO), "archive", "--format=tar", "HEAD"],
        check=True, capture_output=True, timeout=120, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="da-") as temporary:
        root = Path(temporary) / "m"
        with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
            provenance.extract_source(tree, root)
        program = (
            "import json,sys\nfrom pathlib import Path\n"
            "root=Path(sys.argv[1]);sys.path.insert(0,str(root/'tools'))\n"
            "import materialize_main\nlog=[]\n"
            "for name in ('agent-index','agent-dispatch'):\n"
            " path=root/'plugins'/name\n"
            " log.extend(materialize_main.materialize_uv_editable_ref_into("
            "source_consumer_dir=path, dest_consumer_dir=path, canonical_root=root, dest_root=root))\n"
            "print(json.dumps(log))\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-S", "-c", program, str(root)],
            check=True, capture_output=True, text=True, timeout=120, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        log = json.loads(result.stdout)
        assert not any(entry.startswith("SKIP") for entry in log), log
        projects = age.inventory(root)[1]
        selected = age.contract_source(
            Requirement("agent-procutil"), root / "services/index/pyproject.toml",
            projects, root,
        )
        assert selected == root / "plugins/agent-index/libs/agent-procutil/pyproject.toml"
        assert selected not in projects
        _assert_owned_source_contract_attribution(root)


@pytest.mark.parametrize("mode", [
    "present", "removed", "other-plugin", "unguarded", "wrong-identity", "escaped",
])
def test_plugin_source_contract_requires_exact_runner_and_canonical_identity(tmp_path, mode):
    path = project(tmp_path, dependencies='"owned[auth]>=1"')
    source = write(tmp_path / "fleet-manager" / "pyproject.toml",
                   '[project]\nname = "owned"\nversion = "1.0"\n')
    implementation = """
def _ensure_venv(name, uv, reinstall=False):
    cmd = []
    if name == "demo":
        cmd.extend(["-e", str(REPO / "fleet-manager") + "[auth]"])
"""
    if mode == "removed":
        implementation = implementation.replace('cmd.extend(["-e", str(REPO / "fleet-manager") + "[auth]"])', "pass")
    elif mode == "other-plugin":
        implementation = implementation.replace('name == "demo"', 'name == "other"')
    elif mode == "unguarded":
        implementation = implementation.replace('    if name == "demo":\n        ', "    ")
    elif mode == "wrong-identity":
        source.write_text('[project]\nname = "unrelated"\n', encoding="utf-8")
    elif mode == "escaped":
        implementation = implementation.replace('"fleet-manager"', '"../outside"')
    write(tmp_path / "tools" / "run-plugin-tests.py", implementation)
    _, projects = age.inventory(tmp_path)
    if mode == "escaped":
        with pytest.raises(age.DeclarationError, match="escapes"):
            age.contract_source(Requirement("owned"), path, projects, tmp_path)
        return
    else:
        expected = source if mode == "present" else None
        assert age.contract_source(Requirement("owned"), path, projects, tmp_path) == expected
    assert age.contract_source(Requirement("unrelated-package"), path, projects, tmp_path) is None


def test_standalone_source_contract_revoked_if_paths_or_registration_disappear(tmp_path):
    project(tmp_path, "services/demo", dependencies='"owned"')
    write(tmp_path / "libs" / "owned" / "pyproject.toml", '[project]\nname = "owned"\n')
    registry = write(tmp_path / "tools" / "standalone_consumers.py", 'STANDALONE_CONSUMERS = ("services/demo",)\n')
    runner = write(tmp_path / "tools" / "run-standalone-tests.py", """
def prepare(component, python):
    if component == "services/demo":
        sources = [str(REPO / "libs" / "owned")]
""")
    path = tmp_path / "services" / "demo" / "pyproject.toml"
    _, projects = age.inventory(tmp_path)
    assert age.contract_source(Requirement("owned"), path, projects, tmp_path) == tmp_path / "libs" / "owned" / "pyproject.toml"
    registry.write_text('STANDALONE_CONSUMERS = ()\n', encoding="utf-8")
    assert age.contract_source(Requirement("owned"), path, projects, tmp_path) is None
    registry.write_text('STANDALONE_CONSUMERS = ("services/demo",)\n', encoding="utf-8")
    runner.write_text('def prepare(component, python):\n    sources = []\n', encoding="utf-8")
    assert age.contract_source(Requirement("owned"), path, projects, tmp_path) is None


@pytest.mark.parametrize("mode", [
    "canonical", "embedded", "removed-core", "unregistered", "undeclared", "missing-mapping",
    "wrong-identity", "escaped", "conditional", "duplicate", "changed-closure",
])
def test_standalone_source_contract_follows_only_selected_core_mapping(tmp_path, mode):
    path = project(tmp_path, "services/demo", dependencies='"core", "owned"')
    core = write(
        tmp_path / "plugins" / "core" / "pyproject.toml",
        '[project]\nname = "core"\ndependencies = ["owned"]\n',
    )
    canonical = write(
        tmp_path / "libs" / "owned" / "pyproject.toml",
        '[project]\nname = "owned"\ndependencies = ["external>=1"]\n',
    )
    embedded = tmp_path / "plugins" / "core" / "libs" / "owned" / "pyproject.toml"
    write(embedded, canonical.read_text(encoding="utf-8"))
    mapping = '{"path" = "../../libs/owned"}'
    if mode in {"embedded", "changed-closure"}:
        mapping = '{"path" = "libs/owned"}'
    elif mode == "wrong-identity":
        canonical.write_text('[project]\nname = "unrelated"\n', encoding="utf-8")
    elif mode == "escaped":
        mapping = '{"path" = "../../../outside"}'
    elif mode == "conditional":
        mapping = '[{path = "../../libs/owned", marker = "sys_platform == \'win32\'"}]'
    if mode == "changed-closure":
        embedded.write_text(
            canonical.read_text(encoding="utf-8").replace("external>=1", "hidden>=1"),
            encoding="utf-8",
        )
    if mode == "undeclared":
        core.write_text('[project]\nname = "core"\n', encoding="utf-8")
    if mode != "missing-mapping":
        with core.open("a", encoding="utf-8") as stream:
            stream.write(f"\n[tool.uv.sources]\nowned = {mapping}\n")
            if mode == "duplicate":
                stream.write('Owned = {path = "../../libs/owned"}\n')
    consumers = "()" if mode == "unregistered" else '("services/demo",)'
    write(tmp_path / "tools" / "standalone_consumers.py", f"STANDALONE_CONSUMERS = {consumers}\n")
    sources = "[]" if mode == "removed-core" else '[str(REPO / "plugins" / "core")]'
    write(tmp_path / "tools" / "run-standalone-tests.py", f"""
def prepare(component, python):
    if component == "services/demo":
        sources = {sources}
""")
    _, projects = age.inventory(tmp_path)
    req = Requirement("owned")
    if mode in {"wrong-identity", "escaped", "conditional", "duplicate", "changed-closure"}:
        with pytest.raises(age.DeclarationError):
            age.contract_source(req, path, projects, tmp_path)
    else:
        expected = {
            "canonical": canonical, "embedded": embedded,
        }.get(mode)
        assert age.contract_source(req, path, projects, tmp_path) == expected


def test_engine_source_contract_requires_both_platforms_and_canonical_parent(tmp_path):
    parent = project(tmp_path, "plugins/demo")
    child = project(tmp_path, "plugins/demo/server", dependencies='"demo"')
    sh = write(tmp_path / "plugins" / "demo" / "scripts" / "install.sh",
               '_uv_pip_install --python "$ENGINE_VENV_PYTHON" "$PLUGIN_DIR"\nargs=("$PLUGIN_DIR/server")\n')
    ps = write(tmp_path / "plugins" / "demo" / "scripts" / "install.ps1",
               'Invoke-Pip --python $EngineVenvPython "$PluginDir"\n$args = "$PluginDir\\server"\n')
    _, projects = age.inventory(tmp_path)
    # Fixture helper uses the same distribution name; call the narrower source
    # evidence API directly, rather than the self-extra exemption.
    assert age.contract_source(Requirement("demo"), child, projects, tmp_path) == parent
    ps.write_text('# Invoke-Pip --python $EngineVenvPython "$PluginDir"\n', encoding="utf-8")
    assert age.contract_source(Requirement("demo"), child, projects, tmp_path) is None
    sh.unlink()
    assert age.contract_source(Requirement("demo"), child, projects, tmp_path) is None


def test_actual_repository_external_closure_with_controlled_metadata():
    declarations, projects = age.inventory(REPO)
    releases = {}
    npm_releases = {}
    for declaration in declarations:
        if declaration.ecosystem == "npm":
            name, spec = declaration.requirement.split(" ", 1)
            floor = REPOSITORY_NPM_FLOORS[spec]
            npm_releases.setdefault(name, {})[floor] = {}
            continue
        req = Requirement(declaration.requirement)
        if declaration.path in projects and age.local_source(req, declaration.path, projects, REPO):
            continue
        versions = releases.setdefault(age.canonicalize_name(req.name), {"1": release()})
        for spec in req.specifier:
            if spec.operator in {">=", ">", "~=", "==", "==="}:
                versions[str(Version(spec.version.removesuffix(".*")))] = release()
    calls = []

    def fetch(url):
        calls.append(url)
        if url.startswith("https://registry.npmjs.org/"):
            versions = npm_releases[age.unquote(url.rsplit("/", 1)[-1])]
            return json.dumps({"versions": versions,
                               "time": {v: BOUNDARY.isoformat() for v in versions}})
        return json.dumps({"releases": releases[url.split("/")[-2]]})

    count, errors = age.audit(REPO, fetch=fetch, now=NOW,
                              semver=repository_npm_semver)
    assert count == len(declarations)
    assert errors == []
    assert set(calls) == {
        *("https://pypi.org/pypi/" + name + "/json" for name in releases),
        *("https://registry.npmjs.org/" + age.quote(name, safe="") for name in npm_releases),
    }
    assert len(set(calls)) == len(calls)


@pytest.mark.parametrize("manifest_link", [False, True])
def test_symlink_escape_refused_before_project_read(tmp_path, manifest_link):
    root = tmp_path / "repo"
    outside = write(tmp_path / "outside" / "pyproject.toml", '[project]\nname = "owned"\n')
    project(root, dependencies='"owned"', other='[tool.uv.sources]\nowned = {path = "../../libs/owned"}')
    target = root / "libs" / "owned"
    target.parent.mkdir(parents=True)
    try:
        if manifest_link:
            target.mkdir()
            (target / "pyproject.toml").symlink_to(outside)
        else:
            target.symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    with pytest.raises(age.DeclarationError, match="symlink|escapes"):
        age.inventory(root)


def test_unowned_project_copy_is_not_an_attributable_vendor(tmp_path):
    project(tmp_path, dependencies='"owned"', other='[tool.uv.sources]\nowned = {path = "../../copies/owned"}')
    text = '[project]\nname = "owned"\ndependencies = ["external>=1"]\n'
    write(tmp_path / "libs" / "owned" / "pyproject.toml", text)
    write(tmp_path / "copies" / "owned" / "pyproject.toml", text)
    _, errors = age.audit(tmp_path, fetch=lambda _: json.dumps({"releases": {"1": release()}}), now=NOW)
    assert len(errors) == 1 and "canonical dependency closure" in errors[0]


def test_python_proof_independent_of_npm_metadata_failure(tmp_path):
    project(tmp_path)
    write(tmp_path / "tools" / "render" / "package.json", '{"dependencies":{"renderer":"1.0.0"}}')

    def fetch(url):
        if "registry.npmjs.org" in url:
            raise OSError("metadata unavailable")
        return json.dumps({"releases": {"1": release()}})

    assert age.audit(tmp_path, fetch=fetch, now=NOW, ecosystem="python") == (1, [])
    count, errors = age.audit(tmp_path, fetch=fetch, now=NOW)
    assert count == 2 and len(errors) == 1


@pytest.mark.parametrize("strict", [True, False])
def test_npm_cli_respects_ca_registry_and_tls(monkeypatch, strict):
    monkeypatch.setattr(age, "npm_command", lambda: ["node", "attributed/npm-cli.js"])
    monkeypatch.delenv("NODE_TLS_REJECT_UNAUTHORIZED", raising=False)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["env"]["NODE_USE_SYSTEM_CA"] == "1"
        assert argv[:2] == ["node", "attributed/npm-cli.js"]
        if argv[2:4] == ["config", "get"]:
            assert argv[4] == "strict-ssl"
            value = strict
        else:
            value = {"versions": ["1.0.0"], "time": {"1.0.0": BOUNDARY.isoformat()}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(age.subprocess, "run", run)
    if strict:
        data = json.loads(age.npm_public_metadata("@example/renderer"))
        assert data["versions"] == {"1.0.0": {}}
        assert "--registry=https://registry.npmjs.org/" in calls[-1]
        assert "--@example:registry=https://registry.npmjs.org/" in calls[-1]
        assert "--fetch-retries=0" in calls[-1]
        assert "--fetch-timeout=30000" in calls[-1]
    else:
        with pytest.raises(age.DeclarationError):
            age.npm_public_metadata("@example/renderer")
        assert not any("view" in argv for argv in calls)
    assert all("set" not in argv and "install" not in argv and "ci" not in argv for argv in calls)


def test_npm_insecure_environment_refused(monkeypatch):
    monkeypatch.setattr(age, "npm_command", lambda: pytest.fail("must reject before spawning"))
    monkeypatch.setenv("NODE_TLS_REJECT_UNAUTHORIZED", "0")
    with pytest.raises(age.DeclarationError):
        age.npm_public_metadata("renderer")


def test_npm_selector_reuses_node_and_attributable_cli_without_cmd_spawn(tmp_path, monkeypatch):
    node = write(tmp_path / "node-bin" / "node", "")
    shim = write(node.parent / "npm.cmd", "")
    cli = write(node.parent / "node_modules" / "npm" / "bin" / "npm-cli.js", "")
    monkeypatch.setattr(age.shutil, "which", lambda name: str(node if name == "node" else shim))
    assert age.npm_command() == [str(node), str(cli)]
    cli.unlink()
    with pytest.raises(age.DeclarationError):
        age.npm_command()


@pytest.mark.parametrize("failure", ["timeout", "bad-json", "missing-versions"])
def test_npm_metadata_transport_bounded_and_fail_closed(monkeypatch, failure):
    monkeypatch.setattr(age, "npm_command", lambda: ["node", "attributed/npm-cli.js"])
    monkeypatch.delenv("NODE_TLS_REJECT_UNAUTHORIZED", raising=False)

    def run(argv, **kwargs):
        if "config" in argv:
            assert kwargs["timeout"] == 15
            return subprocess.CompletedProcess(argv, 0, "true", "")
        assert kwargs["timeout"] == 40
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 40)
        return subprocess.CompletedProcess(argv, 0, "invalid" if failure == "bad-json" else "{}", "")

    monkeypatch.setattr(age.subprocess, "run", run)
    with pytest.raises(age.DeclarationError):
        age.npm_public_metadata("renderer")


def test_npm_ca_transport_skips_urllib_only_for_authoritative_public_url(monkeypatch):
    def unavailable(_):
        raise OSError("TLS")

    monkeypatch.setattr(age, "_default_fetch", unavailable)
    monkeypatch.setattr(age, "npm_public_metadata", lambda name: name.encode())
    assert age.fetch_release_metadata("https://registry.npmjs.org/%40example%2Frenderer") == b"@example/renderer"
    with pytest.raises(OSError):
        age.fetch_release_metadata("https://pypi.org/pypi/example/json")


def test_dynamic_and_unsupported_requirements_fail_closed(tmp_path):
    path = project(tmp_path, other='dynamic = ["dependencies"]')
    with pytest.raises(age.DeclarationError):
        age.inventory(tmp_path)
    path.unlink()
    write(tmp_path / "tools" / "validation_requirements.txt", "--index-url https://example.invalid\n")
    with pytest.raises(age.DeclarationError):
        age.inventory(tmp_path)


def test_npm_controlled_metadata():
    semver = lambda *_: (["2.6.2"], ["2.6.2", "2.7.0"])
    metadata = {"versions": {"2.6.2": {}, "2.7.0": {}},
                "time": {"2.6.2": BOUNDARY.isoformat(), "2.7.0": NOW.isoformat()}}
    age.check_npm("^2.6.2", metadata, NOW, semver)
    for timestamp in (NOW.isoformat(), None, (NOW + timedelta(seconds=1)).isoformat()):
        metadata["time"]["2.6.2"] = timestamp
        with pytest.raises(age.DeclarationError):
            age.check_npm("^2.6.2", metadata, NOW, semver)


def test_npm_yanked_missing_and_incompatible():
    metadata = {"versions": {"1.0.0": {"yanked": True}}, "time": {"1.0.0": BOUNDARY.isoformat()}}
    with pytest.raises(age.DeclarationError):
        age.check_npm("1.0.0", metadata, NOW, lambda *_: (["1.0.0"], ["1.0.0"]))
    with pytest.raises(age.DeclarationError):
        age.check_npm("^2.0.0", metadata, NOW, lambda *_: (["2.0.0"], []))
    with pytest.raises(age.DeclarationError):
        age.check_npm("*", {}, NOW)


@pytest.mark.skipif(not shutil.which("node") or not shutil.which("npm"), reason="requires npm toolchain")
def test_real_npm_semver_toolchain_without_network():
    assert age.npm_versions("^2.6.2", ["2.6.2", "2.7.0", "3.0.0", "2.8.0-beta.1"]) == (
        ["2.6.2"], ["2.6.2", "2.7.0"])
    assert age.npm_versions("1.0.0 || >=2.0.0 <3", ["1.0.0", "2.1.0", "3.0.0"]) == (
        ["1.0.0", "2.0.0"], ["1.0.0", "2.1.0"])
    with pytest.raises(age.DeclarationError):
        age.npm_versions("https://example.invalid/package.tgz", [])


def test_canonical_manifest_syntax_fastapi_floor_and_cooldown():
    import yaml

    declarations, projects = age.inventory(REPO)
    assert projects
    assert (REPO / "libs" / "update-telemetry" / "pyproject.toml").resolve() in projects
    expected_npm = {}
    for relative in (".github/actions/package.json",
                     "worktree-manager/scripts/picker-snapshot/package.json"):
        path = REPO / relative
        data = json.loads(path.read_text(encoding="utf-8"))
        for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            for name, spec in data.get(section, {}).items():
                expected_npm[path, section, f"{name} {spec}"] = REPOSITORY_NPM_FLOORS[spec]
    actual_npm = {(d.path, d.section, d.requirement) for d in declarations if d.ecosystem == "npm"}
    assert actual_npm == set(expected_npm)
    assert set(expected_npm.values()) == {"2.6.2", "0.31.2", "7.2.1"}
    for data in projects.values():
        age.check_uv_policy(data, NOW)
        for dependency in data.get("project", {}).get("dependencies", []):
            req = Requirement(dependency)
            if req.name == "fastapi":
                assert not any(spec.version == "0.143.0" for spec in req.specifier)
    dispatch = projects[(REPO / "plugins" / "agent-dispatch" / "pyproject.toml").resolve()]
    assert "fastapi>=0.141.1" in dispatch["project"]["dependencies"]
    config = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text())
    assert config["version"] == 2
    assert len(config["updates"]) == 11
    for update in config["updates"]:
        assert update["schedule"]["interval"] == "weekly"
        assert update["cooldown"] == {
            "default-days": 7, "semver-major-days": 7, "semver-minor-days": 7, "semver-patch-days": 7}


@pytest.mark.parametrize("violations,expected", [([], 0), (["unseasoned declaration"], 1)])
def test_cli_exit_status_without_network(monkeypatch, capsys, violations, expected):
    spec = importlib.util.spec_from_file_location("dependency_age_cli", REPO / "tools" / "check-dependency-age.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(sys, "argv", ["check-dependency-age.py"])
    monkeypatch.setattr(cli, "audit", lambda _, **kwargs: (251, violations))
    assert cli.main() == expected
    assert f"{len(violations)} violations" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["7 days", "14 days", "1 week", "168 hours", "604800 seconds", "6 days 24 hours",
                                         "2026-10-06", "2026-10-06T00:00:00Z"])
def test_native_uv_seven_day_boundary_and_stronger_settings(value):
    age.check_uv_policy({"tool": {"uv": {"exclude-newer": value}}}, NOW)


@pytest.mark.parametrize("value", [None, False, "", "0", "6 days", "604799 seconds", "tomorrow",
                                         "-7 days", "2026-10-07", "2026-10-06T00:00:00"])
def test_native_uv_missing_disabled_weaker_or_unknown_policy(value):
    with pytest.raises(age.DeclarationError):
        age.check_uv_policy({"tool": {"uv": {"exclude-newer": value}}}, NOW)


@pytest.mark.parametrize("override", [False, "", "0 days", "6 days", "2026-10-07"])
def test_native_uv_package_override_cannot_disable_or_weaken(override):
    with pytest.raises(age.DeclarationError):
        age.check_uv_policy({"tool": {"uv": {"exclude-newer": "7 days",
                                           "exclude-newer-package": {"demo": override}}}}, NOW)


def test_native_uv_stronger_global_and_pip_policy_cannot_be_weakened():
    for settings in ({"exclude-newer-package": {"demo": "7 days"}},
                     {"pip": {"exclude-newer": "7 days"}},
                     {"pip": {"exclude-newer": "21 days", "exclude-newer-package": {"demo": "14 days"}}},
                     {"pip": {"exclude-newer": "21 days"}, "exclude-newer-package": {"demo": "14 days"}}):
        with pytest.raises(age.DeclarationError):
            age.check_uv_policy({"tool": {"uv": {"exclude-newer": "14 days", **settings}}}, NOW)
    age.check_uv_policy({"tool": {"uv": {"exclude-newer": "14 days",
                                       "exclude-newer-package": {"demo": "21 days"}}}}, NOW)


@pytest.mark.parametrize("override", [False, "", "1 day", "21 days", "2026-09-01"])
def test_index_specific_cutoffs_rejected_even_if_nominally_stronger(override):
    with pytest.raises(age.DeclarationError, match="index-specific"):
        age.check_uv_policy({"tool": {"uv": {"exclude-newer": "7 days",
                                           "index": [{"name": "example", "exclude-newer": override}]}}}, NOW)


def test_native_policy_enforced_by_live_guard_without_network(tmp_path):
    path = project(tmp_path)
    data = path.read_text()
    path.write_text(data.replace('exclude-newer = "7 days"', 'exclude-newer = "1 day"'))
    _, errors = age.audit(tmp_path, now=NOW, fetch=lambda _: json.dumps({"releases": {"1": release()}}))
    assert len(errors) == 1 and "[tool.uv]" in errors[0]


def test_local_file_urls_only_exempt_attributable_canonical_sources(tmp_path):
    target = write(tmp_path / "libs" / "owned" / "pyproject.toml",
                   '[project]\nname = "owned"\ndependencies = ["external>=1"]\n')
    project(tmp_path, dependencies=f'"owned @ {target.parent.as_uri()}"')
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps({"releases": {"1": release()}})

    assert age.audit(tmp_path, now=NOW, fetch=fetch)[1] == []
    assert not any("/owned/" in url for url in calls)
    assert any("/external/" in url for url in calls)
    _, projects = age.inventory(tmp_path)
    path = tmp_path / "plugins" / "demo" / "pyproject.toml"
    for req in (Requirement(f"wrong @ {target.parent.as_uri()}"),
                Requirement(f"owned @ {(tmp_path.parent / 'outside').as_uri()}"),
                Requirement("owned @ https://example.invalid/project")):
        with pytest.raises(age.DeclarationError):
            age.local_source(req, path, projects, tmp_path)


def test_uv_resolution_inputs_inventory_actual_table_shapes(tmp_path):
    path = project(tmp_path, other="""
[tool.uv]
constraint-dependencies = ["constrained>=1.0"]
override-dependencies = ["overridden==2.0"]
build-constraint-dependencies = ["build-pin>=3.0"]
[tool.uv.extra-build-dependencies]
build-target = ["build-helper>=1.0", {requirement = "runtime-match>=2.0", match-runtime = true}]
[[tool.uv.dependency-metadata]]
name = "metadata-target"
version = "4.0"
requires-dist = ["metadata-child>=5.0; python_version < '3.11'"]
requires-python = ">=3.8"
provides-extras = ["optional"]
""")
    declarations, _ = age.inventory(tmp_path)
    by_section = {}
    for declaration in declarations:
        by_section.setdefault(declaration.section, []).append(declaration.requirement)
    assert by_section["tool.uv.constraint-dependencies"] == ["constrained>=1.0"]
    assert by_section["tool.uv.override-dependencies"] == ["overridden==2.0"]
    assert by_section["tool.uv.build-constraint-dependencies"] == ["build-pin>=3.0"]
    assert by_section["tool.uv.extra-build-dependencies.build-target"] == [
        "build-target", "build-helper>=1.0", "runtime-match>=2.0"]
    assert by_section["tool.uv.dependency-metadata.0.target"] == ["metadata-target==4.0"]
    assert by_section["tool.uv.dependency-metadata.0.requires-dist"] == [
        "metadata-child>=5.0; python_version < '3.11'"]
    count, errors = age.audit(tmp_path, now=NOW, fetch=lambda _: json.dumps({
        "releases": {str(version): release() for version in range(1, 6)}}))
    assert count == 9 and errors == []
    if shutil.which("uv"):
        result = subprocess.run(
            ["uv", "pip", "list", "--show-settings", "--python", sys.executable],
            cwd=path.parent, capture_output=True, text=True, timeout=15,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if age.os.name == "nt" else {}),
        )
        assert result.returncode == 0, "actual UV parser rejected the resolution-input fixture"


@pytest.mark.parametrize("table,section", [
    ('[tool.uv]\nconstraint-dependencies = ["new>=1"]', "constraint-dependencies"),
    ('[tool.uv]\noverride-dependencies = ["new==1"]', "override-dependencies"),
    ('[tool.uv]\nbuild-constraint-dependencies = ["new>=1"]', "build-constraint-dependencies"),
    ('[tool.uv.extra-build-dependencies]\nbuild-target = ["new>=1"]', "extra-build-dependencies"),
    ('[tool.uv.extra-build-dependencies]\nbuild-target = [{requirement = "new>=1", match-runtime = true}]',
     "extra-build-dependencies"),
    ('[[tool.uv.dependency-metadata]]\nname = "new"\nversion = "1"', "dependency-metadata"),
    ('[[tool.uv.dependency-metadata]]\nname = "metadata-target"\nrequires-dist = ["new>=1"]',
     "dependency-metadata"),
])
def test_new_uv_resolution_inputs_cannot_hide_unseasoned_literals(tmp_path, table, section):
    project(tmp_path, other=table)

    def fetch(url):
        return json.dumps({"releases": {"1": release(NOW) if "/new/" in url else release(), "2": release()}})

    _, errors = age.audit(tmp_path, now=NOW, fetch=fetch)
    assert len(errors) == 1 and section in errors[0] and ": new:" in errors[0]


@pytest.mark.parametrize("table", [
    '[tool.uv]\nconstraint-dependencies = {hidden = "external>=1"}',
    '[tool.uv.extra-build-dependencies]\nbuild-target = "external>=1"',
    '[tool.uv.extra-build-dependencies]\nbuild-target = [{unknown = "external>=1"}]',
    '[tool.uv.extra-build-dependencies]\nbuild-target = [{requirement = "external>=1", match-runtime = "yes"}]',
    '[[tool.uv.dependency-metadata]]\nname = "external"\nrequires-dist = {hidden = "external>=1"}',
    '[[tool.uv.dependency-metadata]]\nname = "external"\nversion = ">=1"',
    '[[tool.uv.dependency-metadata]]\nname = "external"\ndependencies = ["hidden>=1"]',
])
def test_unsupported_uv_resolution_input_shapes_fail_closed(tmp_path, table):
    project(tmp_path, other=table)
    with pytest.raises(age.DeclarationError):
        age.inventory(tmp_path)
