from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

import pytest

from test_installer_dependency_policy import NOW, offline_environment, policy, settings, source_project


def compile_settings(output=None, **options):
    output_value = "None," if output is None else "Some(" + json.dumps(output) + ",),"
    return settings(**options).replace("PipInstallSettings", "PipCompileSettings").replace(
        "settings: PipSettings {",
        "settings: PipSettings {\n"
        "generate_hashes: true,\nno_header: true,\nno_annotate: true,\n"
        "emit_index_url: false,\nemit_find_links: false,\nemit_index_annotation: false,\n"
        "no_emit_package: [],\noutput_file: " + output_value,
    )


def test_compile_projection_is_admitted_without_install_flags():
    assert policy.cutoff_arguments(compile_settings(), now=NOW, _operation="compile") == [
        "--exclude-newer", "2026-10-06T00:00:00Z",
    ]
    for projection, operation in [(settings(), "compile"), (compile_settings(), "install")]:
        with pytest.raises(policy.DependencyPolicyError, match="projection is unsupported"):
            policy.cutoff_arguments(projection, now=NOW, _operation=operation)


@pytest.mark.parametrize("projection", [
    compile_settings(index="Some(Absolute(2020-01-01T00:00:00Z))"),
    compile_settings().replace("flat_index: []", 'flat_index: ["fixture"]'),
    compile_settings().replace("settings: PipSettings {", "settings: PipSettings {\n dependency_mode: Direct,"),
    compile_settings().replace("settings: PipSettings {", "settings: PipSettings {\n build_isolation: Shared,"),
])
def test_compile_projection_preserves_installer_refusals(projection):
    with pytest.raises(policy.DependencyPolicyError):
        policy.cutoff_arguments(projection, now=NOW, _operation="compile")


@pytest.mark.parametrize("arguments", [
    ["-"], ["--show-settings"], ["--dry-run"], ["--no-deps"], ["--no-build-isolation"],
    ["--format", "pylock.toml"], ["--emit-index-url"], ["--no-emit-package", "fixture"],
    ["--group", "runtime"], ["--editable", "."], ["--unknown-option"],
    ["--output-file", "-"], ["--generate-hashes=false"],
])
def test_unsupported_compile_shape_refuses_before_probe(monkeypatch, arguments):
    def unexpected(*_args, **_kwargs):
        pytest.fail("unsupported compile inputs must not reach UV")

    monkeypatch.setattr(policy.subprocess, "run", unexpected)
    with pytest.raises(policy.DependencyPolicyError):
        policy.prepare_uv_compile("fixture-uv", arguments, env={}, now=NOW)


@pytest.mark.parametrize("before,after", [
    ("generate_hashes: true", "generate_hashes: false"),
    ("no_emit_package: []", 'no_emit_package: ["fixture"]'),
    ("emit_index_url: false", "emit_index_url: true"),
    ("emit_find_links: false", "emit_find_links: true"),
    ("emit_index_annotation: false", "emit_index_annotation: true"),
    ("output_file: None,", 'output_file: Some("ambient.txt",),'),
])
def test_effective_compile_output_policy_cannot_be_supplied_by_ambient_config(
    tmp_path, monkeypatch, before, after,
):
    requirements = tmp_path / "input.in"
    requirements.write_text("fixture>=1\n", encoding="utf-8")
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda command, **_options: subprocess.CompletedProcess(
            command, 0, compile_settings().replace(before, after), "",
        ),
    )
    with pytest.raises(policy.DependencyPolicyError, match="output"):
        policy.prepare_uv_compile("fixture-uv", [str(requirements)], env={}, now=NOW)


def test_compile_retained_resolver_drift_refuses_before_execution(tmp_path, monkeypatch):
    requirements = tmp_path / "input.in"
    requirements.write_text("fixture>=1\n", encoding="utf-8")
    calls = []

    def probe(command, **_options):
        assert "--show-settings" in command
        calls.append(command)
        projection = compile_settings()
        if len(calls) == 2:
            projection = projection.replace("settings: PipSettings {", "settings: PipSettings {\nstrict: true,")
        return subprocess.CompletedProcess(command, 0, projection, "")

    monkeypatch.setattr(policy.subprocess, "run", probe)
    with pytest.raises(policy.DependencyPolicyError, match="differs"):
        policy.run_uv_compile("fixture-uv", [str(requirements)], env={}, cwd=tmp_path, now=NOW)
    assert len(calls) == 2


@pytest.mark.parametrize("option", ["-r", "--requirement", "--requirements", "-c", "--constraint", "--constraints"])
def test_compile_include_paths_are_canonicalized_before_text_remapping(tmp_path, option):
    original = tmp_path / "original" / "nested.in"
    retained = tmp_path / "retained" / "nested.in"
    seen = []

    def mirror(path):
        seen.append(path.resolve())
        return retained

    text = option + " \\\n" + original.parent.as_posix() + "/./nested.in # include comment\n"
    result = policy._configuration.compile_requirement_includes(text, original.parent / "root.in", mirror)
    assert seen == [original]
    assert result == option + " " + retained.as_posix() + "\n"


def test_compile_root_anchor_include_target_is_never_lexically_remapped_again():
    original = PurePosixPath("/tmp/build/child.in")
    retained = PurePosixPath("/private/inputs/hash/tmp/build/child.in")
    lexical_inputs = []

    def remap(line):
        lexical_inputs.append(line)
        return line.replace(str(original), str(retained))

    text = "-r child.in\n# " + str(original) + "\n"
    result = policy._configuration.compile_requirement_includes(
        text, original.parent / "root.in", lambda path: retained, remap,
    )
    assert result == "-r " + str(retained) + "\n# " + str(retained) + "\n"
    assert lexical_inputs == ["# " + str(original)]


def test_compile_retains_project_and_nested_requirements_but_not_output(tmp_path, monkeypatch):
    project = source_project(tmp_path / "source")
    manifest = project / "pyproject.toml"
    included = tmp_path / "nested.in"
    included.write_text("fixture-registry>=1\n", encoding="utf-8")
    requirements = tmp_path / "requirements.in"
    requirements.write_text("-r nested.in\n", encoding="utf-8")
    output = tmp_path / "locked.txt"
    calls = []

    def probe(command, **_kwargs):
        assert command[1:3] == ["pip", "compile"]
        assert "--reinstall" not in command
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, compile_settings(output=str(output)), "")

    monkeypatch.setattr(policy.subprocess, "run", probe)
    with policy.retain_uv_compile(
        "fixture-uv", ["--directory", str(tmp_path), str(manifest), str(requirements),
                       "-olocked.txt", "--python-version", "3.12",
                       "--python-platform", "x86_64-unknown-linux-gnu"],
        env={}, cwd=tmp_path.parent, now=NOW, trusted_source_roots=(project,),
    ) as transaction:
        assert len(calls) == 2
        assert transaction.argv[1:3] == ("pip", "compile")
        assert transaction.argv[transaction.argv.index("-o") + 1] == str(output)
        assert str(transaction.sources[project] / "pyproject.toml") in transaction.argv
        assert transaction.source_hashes[manifest] == hashlib.sha256(manifest.read_bytes()).hexdigest()
        assert {"--upgrade", "--generate-hashes", "--no-header"} <= set(transaction.argv)
        retained_input = next(Path(value) for value in transaction.argv if value.endswith("requirements.in"))
        assert retained_input != requirements
        assert (retained_input.parent / "nested.in").read_bytes() == included.read_bytes()
        retained = transaction.cwd
    assert not retained.exists()
    assert not output.exists()


def test_compile_refuses_effective_editable_graph(tmp_path, monkeypatch):
    source_project(tmp_path / "child", name="fixture-child")
    project = source_project(
        tmp_path / "root", sources='fixture-child={path="../child", editable=true}',
        dependencies='"fixture-child"',
    )
    with pytest.raises(policy.DependencyPolicyError, match="directory dependencies"):
        policy.prepare_uv_compile(
            "fixture-uv", [str(project / "pyproject.toml")], env={}, cwd=tmp_path,
            trusted_source_roots=(tmp_path,), now=NOW,
        )


def source_compile_settings(names, **options):
    return compile_settings(**options).replace(
        "no_emit_package: []",
        "no_emit_package: [" + "".join(f"PackageName({json.dumps(name)},)," for name in names) + "]",
    ).replace("settings: PipSettings {", "settings: PipSettings {\nsources: None,")


def test_source_compile_retains_graph_and_derives_only_local_omissions(tmp_path, monkeypatch):
    child = source_project(tmp_path / "child", name="fixture-child", dependencies='"registry-only>=1"')
    project = source_project(
        tmp_path / "root", sources=(
            'fixture-child={path="../child", editable=true}\n'
            'registry-only={index="fixture-index"}'
        ),
        dependencies='"fixture-child"',
    )
    names = ["fixture-child"]
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda command, **options: subprocess.CompletedProcess(command, 0, source_compile_settings(names), ""),
    )
    with policy.retain_uv_source_compile(
        "fixture-uv", [str(project / "pyproject.toml")], env={}, cwd=tmp_path,
        trusted_source_roots=(tmp_path,), now=NOW,
    ) as transaction:
        assert transaction.argv[1:3] == ("pip", "compile")
        omissions = [value for option, value in policy._argument_pairs(list(transaction.argv[3:]))
                     if option == "--no-emit-package"]
        assert omissions == names
        assert transaction.sources.keys() == {project, child}
        for source, retained in transaction.sources.items():
            assert retained != source
            assert retained.is_dir()
            assert transaction.source_hashes[source / "pyproject.toml"]
        assert "editable_mode=compat" not in transaction.argv
        root = transaction.cwd
    assert not root.exists()


@pytest.mark.parametrize("omissions", [[], ["fixture-source", "registry-only"], ["wrong-source"]])
def test_source_compile_refuses_ambient_omission_drift(tmp_path, monkeypatch, omissions):
    source_project(tmp_path / "child", name="fixture-child")
    project = source_project(
        tmp_path / "root", dependencies='"fixture-child"',
        sources='fixture-child={path="../child"}',
    )
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda command, **options: subprocess.CompletedProcess(
            command, 0, source_compile_settings(omissions), "",
        ),
    )
    with pytest.raises(policy.DependencyPolicyError, match="omissions"):
        with policy.retain_uv_source_compile(
            "fixture-uv", [str(project / "pyproject.toml")], env={}, cwd=tmp_path,
            trusted_source_roots=(tmp_path,), now=NOW,
        ):
            pytest.fail("changed output policy must not reach execution")


@pytest.mark.parametrize("kind", [
    "remote", "unowned", "duplicate", "artifact", "dynamic", "caller-omission", "conditional", "alternatives",
])
def test_source_compile_refuses_unsafe_graph_before_uv(tmp_path, monkeypatch, kind):
    child = source_project(tmp_path / "child", name="fixture-child")
    sources = 'fixture-child={path="../child"}'
    if kind == "remote":
        sources = 'fixture-child={git="https://example.invalid/child"}'
    elif kind == "unowned":
        sources = 'fixture-child={path="../../foreign"}'
    elif kind == "artifact":
        (tmp_path / "child.whl").write_bytes(b"not admitted")
        sources = 'fixture-child={path="../child.whl"}'
    elif kind == "conditional":
        sources = 'fixture-child={path="../child", marker="sys_platform == \'never\'"}'
    elif kind == "alternatives":
        sources = 'fixture-child=[{path="../child"}]'
    project = source_project(
        tmp_path / "root", name="fixture-child" if kind == "duplicate" else "fixture-source",
        sources=sources, dependencies='"fixture-child"',
    )
    if kind == "dynamic":
        manifest = child / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace('version="1.0"', 'version="1.0"\ndynamic=["dependencies"]'))
    arguments = [str(project / "pyproject.toml")]
    if kind == "caller-omission":
        arguments += ["--no-emit-package", "registry-only"]
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **options: pytest.fail("unsafe graph reached UV"))
    with pytest.raises(policy.DependencyPolicyError):
        with policy.retain_uv_source_compile(
            "fixture-uv", arguments, env={}, cwd=tmp_path,
            trusted_source_roots=(tmp_path,), now=NOW,
        ):
            pytest.fail("unsafe graph reached export")


def test_source_compile_refuses_effective_disabled_source_mappings(tmp_path, monkeypatch):
    project = source_project(tmp_path / "root")
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda command, **options: subprocess.CompletedProcess(
            command, 0, source_compile_settings([]).replace("sources: None", "sources: All"), "",
        ),
    )
    with pytest.raises(policy.DependencyPolicyError, match="disable"):
        with policy.retain_uv_source_compile(
            "fixture-uv", [str(project / "pyproject.toml")], env={}, cwd=tmp_path,
            trusted_source_roots=(tmp_path,), now=NOW,
        ):
            pytest.fail("disabled source mappings reached export")


@pytest.mark.parametrize("context", ["argument", "environment", "ambient"])
@pytest.mark.parametrize("input_kind", ["registry", "recursive", "project"])
def test_source_compile_refuses_context_only_local_mappings(tmp_path, monkeypatch, context, input_kind):
    source_project(tmp_path / "child", name="fixture-child")
    project = source_project(
        tmp_path / "context", dependencies='"fixture-child"',
        sources='fixture-child={path="../child"}',
    )
    operand = tmp_path / "input.in"
    operand.write_text("fixture-child==2.0\n", encoding="utf-8")
    if input_kind == "recursive":
        root = tmp_path / "root.in"
        root.write_text("-r input.in\n", encoding="utf-8")
        operand = root
    elif input_kind == "project":
        operand = source_project(
            tmp_path / "input-project", name="fixture-root", dependencies='"fixture-child==2.0"',
        ) / "pyproject.toml"
    arguments = [str(operand)]
    environment = {}
    cwd = tmp_path
    if context == "argument":
        arguments += ["--project", str(project)]
    elif context == "environment":
        environment["UV_PROJECT"] = str(project)
    else:
        cwd = project
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **options: pytest.fail("context mappings reached UV"))
    with pytest.raises(policy.DependencyPolicyError, match="context-only"):
        with policy.retain_uv_source_compile(
            "fixture-uv", arguments, env=environment, cwd=cwd,
            trusted_source_roots=(tmp_path,), now=NOW,
        ):
            pytest.fail("context-only mapping suppressed a required registry pin")


@pytest.mark.parametrize("kind", [
    "direct-marker", "direct-extra", "recursive-marker", "recursive-extra",
    "mapped-marker", "mapped-extra", "nested-unselected",
])
def test_source_compile_refuses_inactive_local_registry_ambiguity(tmp_path, monkeypatch, kind):
    child = source_project(tmp_path / "child", name="fixture-child")
    direct = "fixture-child @ " + child.as_uri()
    registry = "fixture-child>=1"
    marker = "; sys_platform == 'never'"
    sources = 'fixture-child={path="../child"}' if kind.startswith("mapped-") else ""
    dependencies = json.dumps(("registry-only>=1" if kind == "mapped-extra" else registry) if "extra" in kind else (
        "fixture-child" + marker if kind == "mapped-marker" else direct + marker
    ))
    project = source_project(tmp_path / "root", dependencies=dependencies, sources=sources)
    if kind == "nested-unselected":
        nested = source_project(
            tmp_path / "optional", name="fixture-optional", dependencies='"fixture-child"',
            sources='fixture-child={path="../child"}',
        )
        assert nested.is_dir()
        project = source_project(
            project, dependencies=json.dumps(registry),
            sources='fixture-optional={path="../optional"}',
        )
        extra = "fixture-optional"
    else:
        extra = "fixture-child" if kind == "mapped-extra" else direct
    if "extra" in kind or kind == "nested-unselected":
        manifest = project / "pyproject.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "\n[project.optional-dependencies]\nfeature=[" + json.dumps(extra) + "]\n",
            encoding="utf-8",
        )
    operand = project / "pyproject.toml"
    if kind.startswith("recursive"):
        nested = tmp_path / "nested.in"
        nested.write_text(direct + (marker if kind == "recursive-marker" else "") + "\n", encoding="utf-8")
        operand = tmp_path / "input.in"
        operand.write_text("-r nested.in\n" + registry + "\n", encoding="utf-8")
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **options: pytest.fail("ambiguous graph reached UV"))
    with pytest.raises(policy.DependencyPolicyError):
        with policy.retain_uv_source_compile(
            "fixture-uv", [str(operand)], env={}, cwd=tmp_path,
            trusted_source_roots=(tmp_path,), now=NOW,
        ):
            pytest.fail("ambiguous local/registry identity reached export")


@pytest.mark.parametrize("reference", ["requirements", "project", "sources"])
def test_native_compile_refuses_directory_dependencies_before_export_cleanup(tmp_path, reference):
    child = source_project(tmp_path / "child", name="fixture-child")
    dependency = "fixture-child @ " + child.as_uri()
    if reference == "requirements":
        operand = tmp_path / "input.in"
        operand.write_text(dependency + "\n", encoding="utf-8")
    else:
        source = source_project(
            tmp_path / "source", dependencies='"fixture-child"' if reference == "sources" else json.dumps(dependency),
            sources='fixture-child={path="../child"}' if reference == "sources" else "",
        )
        operand = source / "pyproject.toml"
    output = tmp_path / "export.txt"
    with pytest.raises(policy.DependencyPolicyError, match="directory dependencies"):
        policy.run_uv_compile(
            "uv", [str(operand), "-o", str(output), "--python", sys.executable, "--no-index"],
            env=offline_environment(tmp_path), cwd=tmp_path, now=NOW,
            trusted_source_roots=(tmp_path,), capture_output=True, text=True, timeout=30,
        )
    assert not output.exists()


@pytest.mark.parametrize("alias", [False, True], ids=["canonical-input", "absolute-dot-input"])
@pytest.mark.parametrize("source_kind", ["project", "requirements"])
@pytest.mark.parametrize("missing_age", [False, True])
def test_native_compile_freezes_inputs_and_exports_only_seasoned_hashed_closure(
    tmp_path, monkeypatch, source_kind, missing_age, alias, nested_alias=False, root_anchor=False,
    source_graph=False, metadata_name_conflict=False, recursive_source=False,
):
    artifacts = {}
    projects = {}
    downloads = []
    for name, versions in [("fixture-root", ["1.0"]), ("fixture-child", ["1.0", "2.0"])]:
        files = []
        for version in versions:
            normalized = name.replace("-", "_")
            filename = f"{normalized}-{version}-py3-none-any.whl"
            content = io.BytesIO()
            with zipfile.ZipFile(content, "w") as wheel:
                prefix = f"{normalized}-{version}.dist-info/"
                dependency = "Requires-Dist: fixture-child>=1\n" if name == "fixture-root" else ""
                wheel.writestr(prefix + "METADATA",
                               f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{dependency}")
                wheel.writestr(prefix + "WHEEL",
                               "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                wheel.writestr(prefix + "RECORD", "")
            artifacts[filename] = content.getvalue()
            record = {
                "filename": filename, "url": "/files/" + filename,
                "hashes": {"sha256": hashlib.sha256(artifacts[filename]).hexdigest()},
                "upload-time": "2020-01-01T00:00:00Z" if version == "1.0" else "2026-10-12T00:00:00Z",
            }
            if missing_age and name == "fixture-child":
                record.pop("upload-time")
            files.append(record)
        projects[name] = files

    class Index(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond(head=False)

        def do_HEAD(self):
            self.respond(head=True)

        def respond(self, *, head):
            name = self.path.rstrip("/").rsplit("/", 1)[-1]
            if self.path.startswith("/simple/") and name in projects:
                body = json.dumps({"meta": {"api-version": "1.2"}, "name": name,
                                   "files": projects[name]}).encode()
                kind = "application/vnd.pypi.simple.v1+json"
            elif self.path.startswith("/files/") and name in artifacts:
                if not head:
                    downloads.append(name)
                body, kind = artifacts[name], "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Index)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = offline_environment(tmp_path)
    environment.pop("UV_OFFLINE", None)
    config = tmp_path / "uv.toml"
    config.write_text(
        f'[[index]]\nurl="http://127.0.0.1:{server.server_port}/simple"\ndefault=true\n',
        encoding="utf-8",
    )
    environment["UV_CONFIG_FILE"] = str(config)
    working_directory = Path(tmp_path.anchor) if root_anchor else tmp_path
    if source_kind == "project":
        project = source_project(tmp_path / "source", dependencies='"fixture-root>=1"')
        if source_graph:
            local = source_project(tmp_path / "local", name="fixture-owned", dependencies='"fixture-root>=1"')
            if recursive_source:
                source_project(tmp_path / "leaf", name="fixture-leaf", dependencies='"fixture-root>=1"')
                local = source_project(
                    local, name="fixture-owned", dependencies='"fixture-leaf"',
                    sources='fixture-leaf={path="../leaf"}',
                )
            project = source_project(
                project, name="fixture-root" if metadata_name_conflict else "fixture-source",
                dependencies='"fixture-owned"',
                sources='fixture-owned={path="../local", editable=true}',
            )
        operand = project / "pyproject.toml"
        roots = (tmp_path,) if source_graph else (project,)
    else:
        operand = tmp_path / "requirements.in"
        nested = tmp_path.as_posix() + "/./nested.in" if nested_alias else "nested.in"
        operand.write_text("-r " + nested + "\n", encoding="utf-8")
        roots = ()
    operand_argument = str(operand.parent) + os.sep + "." + os.sep + operand.name if alias else str(operand)
    included = tmp_path / "nested.in"
    included.write_text("fixture-root>=1\n", encoding="utf-8")
    output = tmp_path / "locked.txt"
    output.write_text("fixture-child==0.1\n", encoding="utf-8")
    actual_run = subprocess.run
    mutations = []
    retained_directories = []

    def run(command, **options):
        if "--show-settings" not in command and command[1:3] == ["pip", "compile"]:
            mutations.append(True)
            operand.write_text("unadmitted input\n", encoding="utf-8")
            if source_graph:
                (local / "pyproject.toml").write_text("unadmitted input\n", encoding="utf-8")
            included.write_text("unadmitted input\n", encoding="utf-8")
            config.write_text("invalid config\n", encoding="utf-8")
            assert Path(options["env"]["UV_CONFIG_FILE"]) != config
            assert Path(options["cwd"]) != tmp_path
            retained_directories.append(Path(options["cwd"]))
        return actual_run(command, **options)

    monkeypatch.setattr(policy.subprocess, "run", run)
    try:
        unguarded = actual_run(
            [policy._select_uv_executable("uv"), "pip", "compile", operand_argument,
             "--python", sys.executable, "--no-header", "--no-annotate",
             "--python-version", "3.12", "--python-platform", "x86_64-unknown-linux-gnu",
             "--only-binary", ":all:"],
            env=environment, cwd=working_directory, capture_output=True, text=True,
            timeout=30, creationflags=policy._HEADLESS,
        )
        assert unguarded.returncode == 0, unguarded.stderr
        assert "fixture-child==2.0" in unguarded.stdout
        downloads.clear()
        arguments = [operand_argument, "-o", str(output) if root_anchor else "locked.txt", "--python", sys.executable,
                     "--python-version", "3.12", "--python-platform", "x86_64-unknown-linux-gnu",
                     "--only-binary", ":all:"]
        options = dict(env=environment, cwd=working_directory, now=NOW, trusted_source_roots=roots)
        if source_graph:
            with policy.retain_uv_source_compile("uv", arguments, **options) as transaction:
                result = policy.subprocess.run(
                    list(transaction.argv), env=transaction.environment, cwd=transaction.cwd,
                    capture_output=True, text=True, timeout=30, creationflags=policy._HEADLESS,
                )
        else:
            result = policy.run_uv_compile(
                "uv", arguments, **options, capture_output=True, text=True, timeout=30,
            )
        assert mutations == [True]
        assert retained_directories and all(not path.exists() for path in retained_directories)
        if missing_age:
            assert result.returncode != 0
            assert output.read_text(encoding="utf-8") == "fixture-child==0.1\n"
        else:
            assert result.returncode == 0, result.stderr
            text = output.read_text(encoding="utf-8")
            assert "fixture-root==1.0" in text and "fixture-child==1.0" in text
            assert "fixture-child==2.0" not in text and "fixture-child==0.1" not in text
            assert "file:" not in text
            if source_graph:
                assert "fixture-owned" not in text and "fixture-source" not in text
                assert "fixture-leaf" not in text
            for filename in ("fixture_root-1.0-py3-none-any.whl", "fixture_child-1.0-py3-none-any.whl"):
                assert "--hash=sha256:" + hashlib.sha256(artifacts[filename]).hexdigest() in text
            assert not text.startswith("#")
        assert "fixture_child-2.0-py3-none-any.whl" not in downloads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("missing_age", [False, True])
def test_native_source_compile_exports_only_seasoned_third_party_closure(tmp_path, monkeypatch, missing_age):
    test_native_compile_freezes_inputs_and_exports_only_seasoned_hashed_closure(
        tmp_path, monkeypatch, source_kind="project", missing_age=missing_age, alias=False,
        source_graph=True,
    )


def test_native_source_compile_never_omits_a_root_metadata_registry_identity(tmp_path, monkeypatch):
    test_native_compile_freezes_inputs_and_exports_only_seasoned_hashed_closure(
        tmp_path, monkeypatch, source_kind="project", missing_age=False, alias=False,
        source_graph=True, metadata_name_conflict=True,
    )


def test_native_source_compile_retains_recursive_local_mappings(tmp_path, monkeypatch):
    test_native_compile_freezes_inputs_and_exports_only_seasoned_hashed_closure(
        tmp_path, monkeypatch, source_kind="project", missing_age=False, alias=False,
        source_graph=True, recursive_source=True,
    )


def test_native_compile_nested_absolute_include_cannot_reopen_original(tmp_path, monkeypatch):
    test_native_compile_freezes_inputs_and_exports_only_seasoned_hashed_closure(
        tmp_path, monkeypatch, source_kind="requirements", missing_age=False, alias=False,
        nested_alias=True,
    )


def test_native_compile_root_working_directory_keeps_canonical_includes(tmp_path, monkeypatch):
    test_native_compile_freezes_inputs_and_exports_only_seasoned_hashed_closure(
        tmp_path, monkeypatch, source_kind="requirements", missing_age=False, alias=False,
        root_anchor=True,
    )
