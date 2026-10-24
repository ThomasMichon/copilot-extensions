from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from subprocess import CompletedProcess

import pytest

PATH = Path(__file__).resolve().parents[1] / "libs" / "installer-engine" / "installer_dependency_policy.py"
SPEC = importlib.util.spec_from_file_location("installer_dependency_policy", PATH)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)
NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


@pytest.mark.parametrize("names", [
    [], "example", ("../example",), ("example", "EXAMPLE"), (False,),
])
def test_generated_local_exclusions_refuse_invalid_names_before_uv(names):
    with pytest.raises(policy.DependencyPolicyError, match="excluded local packages"):
        with policy.retain_uv_install("unused-uv", [], excluded_local_packages=names):
            pytest.fail("invalid exclusions cannot reach UV or admit retained inputs")


def test_generated_local_exclusions_canonicalize_owned_temp_aliases(monkeypatch):
    from contextlib import contextmanager

    temporary = policy._configuration.temporary_inputs
    observed = []
    expected = object()

    @contextmanager
    def aliased(parent):
        with temporary(parent) as directory:
            path = Path(directory)
            yield str(path / ".." / path.name)

    @contextmanager
    def retain(_uv, arguments, **options):
        constraint, = options["_generated_constraints"]
        assert constraint == constraint.resolve()
        assert constraint.read_text(encoding="utf-8") == "example-local<0,>=0\n"
        assert arguments[-2:] == ["--constraint", str(constraint)]
        observed.append(constraint)
        yield expected

    monkeypatch.setattr(policy._configuration, "temporary_inputs", aliased)
    monkeypatch.setattr(policy, "_retain_uv_install", retain)
    with policy.retain_uv_install(
        "unused-uv", [], excluded_local_packages=("Example_Local",),
    ) as transaction:
        assert transaction is expected
    assert len(observed) == 1 and not observed[0].exists()


@pytest.mark.parametrize("fraction", ["1", "12", "123", "1234", "12345", "123456", "123456789"])
def test_absolute_cutoff_fraction_is_python310_compatible_and_conservative(fraction):
    value = policy._cutoff_value(f"Absolute(2026-10-06T00:00:00.{fraction}Z)", NOW)
    assert value == datetime(2026, 10, 6, microsecond=int(fraction[:6].ljust(6, "0")), tzinfo=timezone.utc)


def settings(global_value="None", packages="", index="None"):
    return f"""PipInstallSettings {{
    settings: PipSettings {{
        index_locations: IndexLocations {{ indexes: [Index {{ exclude_newer: {index}, }}], flat_index: [], }},
        exclude_newer: ExcludeNewer {{
            global: {global_value},
            package: ExcludeNewerPackage({{{packages}}}),
        }},
    }},
}}""".replace(f"Index {{ exclude_newer: {index}, }}",
               f"Index {{\n exclude_newer: {index},\n }}")


@pytest.mark.parametrize("declare_source", [False, True])
def test_native_local_dependency_requires_declared_source_attribution(tmp_path, declare_source):
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import sys

    uv = shutil.which("uv")
    assert uv, "native UV is required for first-party closure coverage"
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_", "PYTHON")) and key != "VIRTUAL_ENV"
    }
    environment["UV_CACHE_DIR"] = str(tmp_path / "cache")
    environment["UV_OFFLINE"] = "1"
    sources = []
    for name, dependency in (("fixture_local_a", ""), ("fixture_local_b", "Requires-Dist: fixture-local-a>=1\\n")):
        source = tmp_path / name
        source.mkdir()
        mapping = (
            '[tool.uv.sources]\nfixture-local-a = {path="../fixture_local_a"}\n'
            if dependency and declare_source else ""
        )
        (source / "pyproject.toml").write_text(
            '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n'
            f'[project]\nname="{name}"\nversion="1.0"\n'
            + ('dependencies=["fixture-local-a>=1"]\n' if dependency else "dependencies=[]\n")
            + mapping,
            encoding="utf-8",
        )
        (source / "fixture_backend.py").write_text(f"""
import pathlib, zipfile
def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = '{name}-1.0-py3-none-any.whl'
    with zipfile.ZipFile(pathlib.Path(wheel_directory) / name, 'w') as wheel:
        prefix = '{name}-1.0.dist-info/'
        wheel.writestr(prefix + 'METADATA', 'Metadata-Version: 2.1\\nName: {name}\\nVersion: 1.0\\n{dependency}')
        wheel.writestr(prefix + 'WHEEL', 'Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
        wheel.writestr(prefix + 'RECORD', '')
    return name
""", encoding="utf-8")
        sources.append(source)
    target = tmp_path / "venv"
    created = subprocess.run(
        [uv, "venv", "--python", sys._base_executable, str(target)],
        env=environment, capture_output=True, text=True, creationflags=policy._HEADLESS,
        timeout=30,
    )
    assert created.returncode == 0, created.stderr
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    for source in sources:
        installed = subprocess.run(
            [sys.executable, "-I", str(Path(policy.__file__)), "--uv", uv,
             "--trusted-source-root", str(tmp_path), "--",
             "--quiet", "--force-reinstall", "--no-index", "--python", str(python), str(source)],
            env=environment, capture_output=True, text=True, creationflags=policy._HEADLESS,
            timeout=30,
        )
        if source == sources[1] and not declare_source:
            assert installed.returncode != 0
            assert "No solution found" in installed.stderr
        else:
            assert installed.returncode == 0, installed.stdout + installed.stderr


def absolute(date):
    return "Absolute(" + date.isoformat().replace("+00:00", "Z") + ",)"


def package(name, value):
    return f'PackageName("{name}",): Enabled({value},),'


def test_exact_floor_and_reinstall():
    args = policy.cutoff_arguments(settings(), now=NOW)
    assert args == ["--exclude-newer", "2026-10-06T00:00:00Z", "--reinstall"]


@pytest.mark.parametrize("seconds", [604800, 604801, 1000000])
def test_stricter_global_is_honored(seconds):
    date = NOW - timedelta(seconds=seconds)
    args = policy.cutoff_arguments(settings("Some(" + absolute(date) + ",)"), now=NOW)
    assert datetime.fromisoformat(args[1].replace("Z", "+00:00")) == date


def test_weaker_global_is_strengthened():
    assert policy.cutoff_arguments(settings("Some(" + absolute(NOW) + ",)"), now=NOW)[1] == "2026-10-06T00:00:00Z"


def test_stricter_relative_is_honored():
    assert policy.cutoff_arguments(
        settings("Some(Relative(ExcludeNewerSpan(10d,),),)"), now=NOW,
    )[1] == "2026-10-03T00:00:00Z"


def projected_cutoff(timestamp, span="None"):
    return f"ExcludeNewerValue {{ timestamp: {timestamp}, span: {span}, }}"


@pytest.mark.parametrize("timestamp,span,expected", [
    ("2026-10-03T00:00:00Z", "None", "2026-10-03T00:00:00Z"),
    ("2026-10-13T00:00:00Z", "None", "2026-10-06T00:00:00Z"),
    ("2026-10-06T00:00:00.123456789Z", "None", "2026-10-06T00:00:00Z"),
    ("2026-10-03T00:00:01Z", "Some(ExcludeNewerSpan(10d,),)", "2026-10-03T00:00:00Z"),
    ("2026-10-02T23:59:59Z", "Some(ExcludeNewerSpan(10d,),)", "2026-10-02T23:59:59Z"),
    ("2026-10-12T00:00:00Z", "Some(ExcludeNewerSpan(1d,),)", "2026-10-06T00:00:00Z"),
])
def test_structured_uv_cutoff_preserves_the_strongest_boundary(timestamp, span, expected):
    value = projected_cutoff(timestamp, span)
    assert policy.cutoff_arguments(settings(f"Some({value},)"), now=NOW)[1] == expected


@pytest.mark.parametrize("timestamp,span,accepted", [
    ("2026-10-06T00:00:00Z", "None", True),
    ("2026-10-06T00:00:00Z", "Some(ExcludeNewerSpan(7d))", True),
    ("2026-10-06T00:00:01Z", "Some(ExcludeNewerSpan(7d))", True),
    ("2026-10-06T00:00:01Z", "None", False),
    ("2026-10-03T00:00:01Z", "Some(ExcludeNewerSpan(10d,),)", True),
    ("2026-10-12T00:00:00Z", "Some(ExcludeNewerSpan(10d,),)", True),
])
def test_structured_uv_package_cutoff_cannot_weaken_seasoning(timestamp, span, accepted):
    projection = settings(packages=package("demo", projected_cutoff(timestamp, span)))
    if accepted:
        assert policy.cutoff_arguments(projection, now=NOW)[1] == "2026-10-06T00:00:00Z"
    else:
        with pytest.raises(policy.DependencyPolicyError, match="weakens seasoning"):
            policy.cutoff_arguments(projection, now=NOW)


@pytest.mark.parametrize("value", [
    "ExcludeNewerValue { timestamp: 2026-10-03T00:00:00Z }",
    "ExcludeNewerValue { timestamp: 2026-10-03T00:00:00Z, span: None, extra: None }",
    projected_cutoff("invalid"),
    projected_cutoff("2026-10-03T00:00:00"),
    projected_cutoff("2026-10-03T00:00:00Z", "Some(Unknown(10d))"),
    projected_cutoff("2026-10-03T00:00:00Z", "Some(ExcludeNewerSpan(1month))"),
])
def test_structured_uv_cutoff_unknown_or_malformed_fields_refuse(value):
    with pytest.raises(policy.DependencyPolicyError):
        policy.cutoff_arguments(settings(f"Some({value},)"), now=NOW)


@pytest.mark.parametrize("timestamp,span,equivalent", [
    ("2026-10-02T00:00:01Z", "Some(ExcludeNewerSpan(11d))", True),
    ("2026-10-02T00:00:01Z", "Some(ExcludeNewerSpan(10d))", False),
    ("2026-10-02T00:00:00Z", "Some(ExcludeNewerSpan(10d))", False),
    ("2026-10-01T23:59:59Z", "Some(ExcludeNewerSpan(11d))", False),
    ("2026-10-02T00:00:01Z", "None", False),
])
def test_structured_package_comparison_preserves_stricter_timestamps(timestamp, span, equivalent):
    original = settings(packages=package("demo", projected_cutoff(
        "2026-10-02T00:00:00Z", "Some(ExcludeNewerSpan(11d))",
    )))
    retained = settings(packages=package("demo", projected_cutoff(timestamp, span)))
    assert (
        policy._resolver_configuration(original, now=NOW)
        == policy._resolver_configuration(retained, now=NOW)
    ) is equivalent


def test_stricter_package_is_preserved():
    date = NOW - timedelta(days=10)
    assert policy.cutoff_arguments(settings(packages=package("demo", absolute(date))), now=NOW)[1] == "2026-10-06T00:00:00Z"


def test_every_effective_package_override_is_frozen_to_its_admitted_absolute_cutoff():
    packages = (
        package("first", absolute(NOW - timedelta(days=10)))
        + package("second", "Relative(ExcludeNewerSpan(11d))")
    )
    assert policy.cutoff_arguments(settings(packages=packages), now=NOW) == [
        "--exclude-newer", "2026-10-06T00:00:00Z",
        "--exclude-newer-package", "first=2026-10-03T00:00:00Z",
        "--exclude-newer-package", "second=2026-10-02T00:00:00Z",
        "--reinstall",
    ]


@pytest.mark.parametrize("packages", [
    'PackageName("demo",): Disabled,',
    package("demo", absolute(NOW - timedelta(seconds=604799))),
    'PackageName("demo",): Unknown,',
])
def test_package_bypass_refused(packages):
    with pytest.raises(policy.DependencyPolicyError):
        policy.cutoff_arguments(settings(packages=packages), now=NOW)


@pytest.mark.parametrize("index", ["Some(Disabled)", "Some(Enabled(Absolute(2020-01-01T00:00:00Z,)))"])
def test_index_override_cannot_assign_unknown_upload_age(index):
    with pytest.raises(policy.DependencyPolicyError, match="index-specific"):
        policy.cutoff_arguments(settings(index=index), now=NOW)


@pytest.mark.parametrize("projection", [
    "", "unsupported output", settings().replace("global: None", "global: Disabled"),
    settings().replace("ExcludeNewerPackage", "DifferentType"), settings() + settings(),
    settings()[:-10],
])
def test_missing_unknown_or_truncated_configuration_refuses(projection):
    with pytest.raises(policy.DependencyPolicyError):
        policy.cutoff_arguments(projection, now=NOW)


def test_probe_preserves_arguments_environment_and_cwd(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, settings(), "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    args = ["--python", "candidate-python", "demo>=1"]
    (tmp_path / "configured-feed.toml").write_text("", encoding="utf-8")
    environment = {"UV_CONFIG_FILE": "configured-feed.toml"}
    result = policy.prepare_uv_install("attributed-uv", args, env=environment, cwd=tmp_path, now=NOW)
    assert result[:len(args)] == args
    assert calls[0][0] == ["attributed-uv", "pip", "install", "--show-settings", *args]
    assert calls[0][1]["env"] == environment
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[0][1]["timeout"] == 30


def test_probe_error_never_prints_credential_projection(monkeypatch):
    secret = "private-config-value"
    monkeypatch.setattr(policy.subprocess, "run",
                        lambda *args, **kw: CompletedProcess(args, 1, secret, secret))
    with pytest.raises(policy.DependencyPolicyError) as error:
        policy.prepare_uv_install("uv", [])
    assert secret not in str(error.value)


@pytest.mark.parametrize("alias", ["--reinstall", "--force-reinstall"])
def test_reinstall_alias_is_normalized_before_mandatory_resolution(monkeypatch, alias):
    monkeypatch.setattr(policy.subprocess, "run",
                        lambda command, **kwargs: CompletedProcess(command, 0, settings(), ""))
    admitted = policy.prepare_uv_install("uv", [alias, "demo>=1"], now=NOW)
    assert admitted.count("--reinstall") == 1
    assert "--force-reinstall" not in admitted


@pytest.mark.parametrize("quiet", ["--quiet", "-q", "-qq"])
def test_probe_cannot_suppress_required_settings_but_install_remains_quiet(monkeypatch, quiet):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert quiet not in command
        return CompletedProcess(command, 0, settings(), "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    admitted = policy.prepare_uv_install("uv", [quiet, "demo>=1"], now=NOW)
    assert quiet in admitted
    assert len(calls) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows application discovery")
def test_engine_uv_discovery_selects_one_application_without_acquisition(tmp_path):
    import os
    import subprocess

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell unavailable")
    directory = tmp_path / "bin"
    directory.mkdir()
    for extension in ("cmd", "bat"):
        (directory / f"uv.{extension}").write_text("@echo uv 0.12.6\n@exit /b 0\n", encoding="ascii")
    engine = PATH.with_name("installer-engine.ps1")
    script = tmp_path / "discovery.ps1"
    script.write_text(f"""
$ErrorActionPreference='Stop'
. '{engine}'
$applications = @(Get-Command uv -CommandType Application)
if ($applications.Count -lt 2) {{ throw 'Fixture did not expose multiple applications' }}
$chosen = Ensure-Uv -InstallRoot '{tmp_path / "uncreated"}' -AcquireIfMissing $false
if ($chosen -ne $applications[0].Source) {{ throw 'UV application selection was ambiguous' }}
if (Test-Path '{tmp_path / "uncreated"}') {{ throw 'Discovery unnecessarily acquired a tool' }}
""", encoding="utf-8")
    result = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(script)],
        env={**os.environ, "PATH": str(directory) + os.pathsep + os.environ.get("PATH", "")},
        capture_output=True, text=True, timeout=20, creationflags=policy._HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("argument", ["--show-settings", "--dry-run", "--dry-run=true"])
def test_success_shaped_no_install_argument_refused(argument):
    with pytest.raises(policy.DependencyPolicyError):
        policy.prepare_uv_install("uv", [argument])


@pytest.mark.parametrize("argument", ["--no-deps", "--no-build-isolation", "--no-build-isolation-package=demo"])
def test_dependency_closure_bypass_refused(argument):
    with pytest.raises(policy.DependencyPolicyError, match="cannot bypass"):
        policy.prepare_uv_install("uv", [argument])


@pytest.mark.parametrize("requirement", [
    "demo @ https://unapproved.example/demo.whl", "git+https://unapproved.example/repo",
    "https://unapproved.example/demo.whl",
    "git://unapproved.example/repo", "demo @ HTTPS://unapproved.example/demo.whl",
])
def test_direct_remote_inputs_cannot_bypass_artifact_timestamps(requirement):
    with pytest.raises(policy.DependencyPolicyError, match="remote"):
        policy.prepare_uv_install("uv", [requirement])


@pytest.mark.parametrize("option", ["--index", "--index-url", "--default-index", "--extra-index-url"])
@pytest.mark.parametrize("inline", [False, True])
def test_index_option_exempts_only_its_own_value(monkeypatch, option, inline):
    index = ["https://configured.example/simple"]
    arguments = [option + "=" + index[0]] if inline else [option, *index]
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return CompletedProcess(command, 0, settings(), "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    with pytest.raises(policy.DependencyPolicyError, match="remote"):
        policy.prepare_uv_install(
            "uv", [*arguments, "demo @ https://unapproved.example/demo.whl"], now=NOW,
        )
    assert calls == []
    admitted = policy.prepare_uv_install("uv", [*arguments, "demo>=1"], now=NOW)
    assert admitted[:len(arguments)] == arguments
    assert len(calls) == 1


def test_remote_requirement_hidden_in_an_include_refuses_before_probe(tmp_path):
    nested = tmp_path / "nested.txt"
    nested.write_text("demo @ https://unapproved.example/demo.whl\n", encoding="utf-8")
    root = tmp_path / "requirements.txt"
    root.write_text("-r nested.txt\n", encoding="utf-8")
    with pytest.raises(policy.DependencyPolicyError, match="remote"):
        policy.prepare_uv_install("uv", ["-r", str(root)])


def test_effective_remote_override_is_not_an_index_exemption():
    projection = settings().replace("settings: PipSettings", 'overrides: ["demo @ https://unapproved.example/demo.whl"],\nsettings: PipSettings')
    with pytest.raises(policy.DependencyPolicyError, match="remote"):
        policy.cutoff_arguments(projection, now=NOW)


def test_flat_index_is_not_a_registry_timestamp_proof():
    projection = settings().replace("flat_index: []", 'flat_index: ["https://unapproved.example/wheels"]')
    with pytest.raises(policy.DependencyPolicyError, match="flat-index"):
        policy.cutoff_arguments(projection, now=NOW)


def test_real_uv_settings_consumer_preserves_stricter_cutoff(tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native UV unavailable")
    arguments = [
        "--no-config", "--python", sys.executable,
        "--exclude-newer", "2020-01-01T00:00:00Z",
        "--exclude-newer-package", "demo=2019-01-01T00:00:00Z", "demo",
    ]
    result = policy.prepare_uv_install(uv, arguments, cwd=tmp_path, now=NOW)
    assert result.count("--exclude-newer") == 1
    assert result[result.index("--exclude-newer") + 1] == "2020-01-01T00:00:00Z"
    assert result.count("--exclude-newer-package") == 1
    assert result[result.index("--exclude-newer-package") + 1] == "demo=2019-01-01T00:00:00Z"
    assert result[-1] == "--reinstall"


def test_real_uv_settings_consumer_preserves_relative_cutoffs(tmp_path):
    uv = shutil.which("uv")
    assert uv, "native UV is required for relative cutoff coverage"
    now = datetime.now(timezone.utc)
    arguments = [
        "--no-config", "--python", sys.executable,
        "--exclude-newer", "10 days", "--exclude-newer-package", "demo=11 days", "demo",
    ]
    result = policy.prepare_uv_install(uv, arguments, cwd=tmp_path, now=now)
    expected = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    assert result[result.index("--exclude-newer") + 1] == expected
    expected_package = (now - timedelta(days=11)).isoformat().replace("+00:00", "Z")
    assert result[result.index("--exclude-newer-package") + 1] == f"demo={expected_package}"
    assert result[-1] == "--reinstall"


def test_real_uv_package_span_is_enforced_on_the_executed_override(tmp_path):
    uv = shutil.which("uv")
    assert uv, "native UV is required for structured package cutoff coverage"
    (tmp_path / "uv.toml").write_text(
        'exclude-newer-package={demo={timestamp="2099-01-01T00:00:00Z",span="11 days"}}\n',
        encoding="utf-8",
    )
    arguments = ["--python", sys.executable, "demo"]
    environment = offline_environment(tmp_path)
    admitted = policy.prepare_uv_install(uv, arguments, cwd=tmp_path, env=environment, now=NOW)
    assert admitted[admitted.index("--exclude-newer") + 1] == "2026-10-06T00:00:00Z"
    assert admitted[admitted.index("--exclude-newer-package") + 1] == "demo=2026-10-02T00:00:00Z"
    assert admitted[-1] == "--reinstall"


@pytest.mark.parametrize("missing_timestamp,url_dependency,exact_boundary", [
    (False, False, False), (True, False, False), (False, True, False), (False, False, True),
])
@pytest.mark.parametrize("first_party_source", [False, True])
def test_real_uv_registry_transitive_and_isolated_build_age(
    tmp_path, missing_timestamp, url_dependency, exact_boundary, first_party_source,
    mutation=None, monkeypatch=None, clock_source=None,
):
    import hashlib
    import io
    import json
    import os
    import subprocess
    import tarfile
    import threading
    import zipfile
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native UV unavailable")
    artifacts, backends, downloads = {}, {}, []
    projects = {}

    def add_wheel(name, version, module="", contents="", dependencies=""):
        filename = f"{name}-{version}-py3-none-any.whl"
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            prefix = f"{name}-{version}.dist-info"
            archive.writestr(prefix + "/METADATA",
                             f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{dependencies}")
            archive.writestr(prefix + "/WHEEL",
                             "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr(prefix + "/RECORD", "")
            if module:
                archive.writestr(module + ".py", contents)
        artifacts[filename] = content.getvalue()
        return filename

    def published(name, filename, old):
        record = {
            "filename": filename, "url": "/files/" + filename,
            "hashes": {"sha256": hashlib.sha256(artifacts[filename]).hexdigest()},
        }
        if not (missing_timestamp and name == "fixture-transitive"):
            record["upload-time"] = "2020-01-01T00:00:00Z" if old else datetime.now(timezone.utc).isoformat()
            if clock_source and name == "fixture-transitive" and not old:
                record["upload-time"] = (NOW - timedelta(days=7) + timedelta(seconds=1)).isoformat()
            if old and name == "fixture-transitive":
                adjustment = timedelta() if exact_boundary else timedelta(milliseconds=1)
                record["upload-time"] = (NOW - timedelta(seconds=604800) - adjustment).isoformat()
        projects.setdefault(name, []).append(record)

    for version, old in [("1.0", True), ("2.0", False)]:
        published("fixture-transitive", add_wheel("fixture_transitive", version), old)
        backend = f"""
import pathlib, zipfile
def get_requires_for_build_wheel(config_settings=None):
    return []
def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = 'fixture_root-1.0-py3-none-any.whl'
    with zipfile.ZipFile(pathlib.Path(wheel_directory) / name, 'w') as wheel:
        prefix = 'fixture_root-1.0.dist-info/'
        wheel.writestr(prefix + 'METADATA', 'Metadata-Version: 2.1\\nName: fixture-root\\nVersion: 1.0\\nRequires-Dist: fixture-transitive>=1\\nSummary: backend-{version}\\n')
        wheel.writestr(prefix + 'WHEEL', 'Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
        wheel.writestr(prefix + 'RECORD', '')
    return name
"""
        published("fixture-backend", add_wheel("fixture_backend", version, "fixture_backend", backend), old)
        backends[version] = backend
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as source:
        content = b'[build-system]\nrequires=["fixture-backend>=1"]\nbuild-backend="fixture_backend"\n'
        member = tarfile.TarInfo("fixture_root-1.0/pyproject.toml")
        member.size = len(content)
        source.addfile(member, io.BytesIO(content))
    artifacts["fixture_root-1.0.tar.gz"] = archive.getvalue()
    published("fixture-root", "fixture_root-1.0.tar.gz", True)

    class Index(BaseHTTPRequestHandler):
        def log_message(self, *args):
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
                content_type = "application/vnd.pypi.simple.v1+json"
            elif self.path.startswith("/files/") and name in artifacts:
                if not head:
                    downloads.append(name)
                body, content_type = artifacts[name], "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Index)
    if url_dependency:
        for version in backends:
            backend = backends[version].replace(
                "fixture-transitive>=1",
                f"fixture-transitive @ http://127.0.0.1:{server.server_port}/files/fixture_transitive-2.0-py3-none-any.whl",
            )
            filename = add_wheel("fixture_backend", version, "fixture_backend", backend)
            record = next(file for file in projects["fixture-backend"] if file["filename"] == filename)
            record["hashes"]["sha256"] = hashlib.sha256(artifacts[filename]).hexdigest()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("UV_") and key not in {"PYTHONPATH", "PYTHONHOME"}}
    config = tmp_path / "uv.toml"
    config.write_text(f'[[index]]\nurl="http://127.0.0.1:{server.server_port}/simple"\ndefault=true\n',
                      encoding="utf-8")
    environment["UV_CONFIG_FILE"] = str(config)
    environment["UV_CACHE_DIR"] = str(tmp_path / "cache")
    if clock_source == "config":
        config.write_text(
            'exclude-newer-package={fixture-transitive="7 days"}\n'
            + config.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    elif clock_source == "environment":
        environment["UV_EXCLUDE_NEWER_PACKAGE"] = "fixture-transitive=7 days"
    venv = tmp_path / "venv"
    flags = {"creationflags": policy._HEADLESS}
    try:
        subprocess.run([uv, "venv", "--offline", "--python", sys.executable, str(venv)],
                       env=environment, check=True, capture_output=True, timeout=30, **flags)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        requirement = "fixture-root"
        roots = ()
        if first_party_source:
            source = tmp_path / "first-party"
            source.mkdir()
            dependency = "fixture-transitive>=1"
            if url_dependency:
                dependency = f"fixture-transitive @ http://127.0.0.1:{server.server_port}/files/fixture_transitive-2.0-py3-none-any.whl"
            (source / "pyproject.toml").write_text(
                '[build-system]\nrequires=["fixture-backend>=1"]\nbuild-backend="fixture_backend"\n'
                f'[project]\nname="fixture-root"\nversion="1.0"\ndependencies=["{dependency}"]\n',
                encoding="utf-8",
            )
            requirement, roots = str(source), (source,)
            if url_dependency:
                with pytest.raises(policy.DependencyPolicyError, match="remote"):
                    policy.prepare_uv_install(
                        uv, ["--python", str(python), requirement], env=environment,
                        cwd=tmp_path, now=NOW, trusted_source_roots=roots,
                    )
                assert downloads == []
                return
        arguments = ["--python", str(python), requirement]
        if clock_source == "cli":
            arguments.extend(["--exclude-newer-package", "fixture-transitive=7 days"])
        elif clock_source == "cli-inline":
            arguments.append("--exclude-newer-package=fixture-transitive=7 days")
        included = tmp_path / "included.txt"
        requirements = tmp_path / "requirements.txt"
        if mutation == "requirements":
            included.write_text(requirement + "\n", encoding="utf-8")
            requirements.write_text("-r included.txt\n", encoding="utf-8")
            arguments = ["--python", str(python), "-r", str(requirements)]
        actual_run = subprocess.run
        mutations = []
        clock_steps = []

        def run(command, **kwargs):
            if clock_source and command[1:3] == ["pip", "install"]:
                clock = NOW + (timedelta(seconds=2) if clock_steps else timedelta())
                kwargs["env"] = dict(
                    kwargs["env"], UV_TEST_CURRENT_TIMESTAMP=clock.isoformat().replace("+00:00", "Z"),
                )
                clock_steps.append(clock)
            if mutation and "--show-settings" not in command and command[1:3] == ["pip", "install"]:
                mutations.append(mutation)
                remote = f"fixture-transitive @ http://127.0.0.1:{server.server_port}/files/fixture_transitive-2.0-py3-none-any.whl"
                if mutation == "requirements":
                    included.write_text(remote + "\n", encoding="utf-8")
                elif mutation == "project":
                    manifest = source / "pyproject.toml"
                    manifest.write_text(
                        manifest.read_text(encoding="utf-8").replace("fixture-transitive>=1", remote),
                        encoding="utf-8",
                    )
                else:
                    config.write_text(
                        config.read_text(encoding="utf-8") + 'exclude-newer="2020-01-01T00:00:00Z"\n',
                        encoding="utf-8",
                    )
                assert Path(kwargs["env"]["UV_CONFIG_FILE"]) != config
                assert Path(kwargs["cwd"]) != tmp_path
            return actual_run(command, **kwargs)

        if mutation or clock_source:
            monkeypatch.setattr(policy.subprocess, "run", run)
        installed = policy.run_uv_install(
            uv, arguments, env=environment, cwd=tmp_path, now=NOW, trusted_source_roots=roots,
            capture_output=True, text=True, timeout=30, **flags,
        )
        if missing_timestamp or url_dependency or exact_boundary:
            assert installed.returncode != 0
            if url_dependency:
                assert "dependencies or constraints file" in installed.stderr
                assert "fixture_transitive-2.0-py3-none-any.whl" not in downloads
            if exact_boundary:
                assert "filtered by `exclude-newer`" in installed.stderr
                assert not any(name.startswith("fixture_transitive-") for name in downloads)
        else:
            assert installed.returncode == 0, installed.stderr
            versions = subprocess.run(
                [str(python), "-I", "-c", "import importlib.metadata as m;print(m.version('fixture-transitive'));print(m.metadata('fixture-root')['Summary'])"],
                env=environment, check=True, capture_output=True, text=True, timeout=10, **flags,
            )
            assert versions.stdout.splitlines() == ["1.0", "backend-1.0"]
            if clock_source:
                assert clock_steps == [NOW, NOW + timedelta(seconds=2), NOW + timedelta(seconds=2)]
                assert "fixture_transitive-2.0-py3-none-any.whl" not in downloads
            if mutation:
                assert mutations == [mutation]
                assert "fixture_transitive-2.0-py3-none-any.whl" not in downloads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("mutation", ["requirements", "project", "config"])
def test_native_install_never_reopens_admitted_mutable_inputs(tmp_path, monkeypatch, mutation):
    test_real_uv_registry_transitive_and_isolated_build_age(
        tmp_path, False, False, False, True, mutation=mutation, monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("source", ["cli", "cli-inline", "environment", "config"])
def test_native_package_cutoffs_stay_frozen_across_probe_and_execution_clocks(tmp_path, monkeypatch, source):
    test_real_uv_registry_transitive_and_isolated_build_age(
        tmp_path, False, False, False, False, monkeypatch=monkeypatch, clock_source=source,
    )


@pytest.mark.parametrize("selection", [
    "explicit", "implicit", "env-relative", "project-env", "directory", "resource-parent",
])
def test_native_editable_retains_metadata_but_imports_live_source_after_cleanup(tmp_path, monkeypatch, selection):
    import os
    import subprocess

    source = tmp_path / "resources" / "source" if selection == "resource-parent" else tmp_path / "source"
    backend = source / "backend" / "setuptools"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("", encoding="utf-8")
    (backend / "build_meta.py").write_text(
        """
import pathlib, zipfile, json, re
def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    data = pathlib.Path('pyproject.toml').read_text()
    assert '"dependencies" = []' in data
    assert config_settings['editable_mode'] == 'compat'
    directory = json.loads(re.search(r'"package-dir" = \\{"" = ("[^"]+")\\}', data)[1])
    live = str(pathlib.Path(directory).resolve())
    name = 'fixture_editable-1.0-py3-none-any.whl'
    with zipfile.ZipFile(pathlib.Path(wheel_directory) / name, 'w') as wheel:
        prefix = 'fixture_editable-1.0.dist-info/'
        wheel.writestr(prefix + 'METADATA', 'Metadata-Version: 2.1\\nName: fixture-editable\\nVersion: 1.0\\n')
        wheel.writestr(prefix + 'WHEEL', 'Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
        wheel.writestr(prefix + 'RECORD', '')
        wheel.writestr('fixture_editable.pth', live + '\\n')
    return name
""", encoding="utf-8",
    )
    manifest = source / "pyproject.toml"
    manifest.write_text(
        '[build-system]\nrequires=[]\nbuild-backend="setuptools.build_meta"\nbackend-path=["backend"]\n'
        '[project]\nname="fixture-editable"\nversion="1.0"\ndependencies=[]\n'
        '[tool.setuptools.packages.find]\nwhere=["src"]\n', encoding="utf-8",
    )
    live = source / "src"
    live.mkdir()
    module = live / "fixture_editable.py"
    module.write_text("value = 'original'\n", encoding="utf-8")
    environment = offline_environment(tmp_path)
    uv = shutil.which("uv")
    target = tmp_path / (".venv" if selection in ("implicit", "env-relative") else "venv")
    actual = subprocess.run
    created = actual(
        [uv, "venv", "--python", sys.executable, str(target)], env=environment,
        capture_output=True, text=True, timeout=30, creationflags=policy._HEADLESS,
    )
    assert created.returncode == 0, created.stderr
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    arguments = ["--no-config", "--no-index", "--python", str(python), "-e", str(source)]
    cwd = tmp_path
    if selection in ("implicit", "env-relative"):
        arguments = ["--no-config", "--no-index", "-e", str(source)]
        if selection == "env-relative":
            environment["UV_PYTHON"] = str(python.relative_to(tmp_path))
    elif selection == "project-env":
        environment["UV_PROJECT"] = "source"
    elif selection == "directory":
        arguments = ["--directory", str(tmp_path), "--no-config", "--no-index", "--python", str(python), "-e", "source"]
        cwd = source
    retained = []

    def run(command, **kwargs):
        if "--show-settings" not in command:
            retained.append(Path(kwargs["cwd"]))
            manifest.write_text(
                manifest.read_text().replace("dependencies=[]", 'dependencies=["bad @ https://unapproved.example/bad.whl"]'),
                encoding="utf-8",
            )
        return actual(command, **kwargs)

    monkeypatch.setattr(policy.subprocess, "run", run)
    copytree = policy.shutil.copytree

    def copy_outside(source, destination, *args, **kwargs):
        assert not Path(destination).resolve().is_relative_to(Path(source).resolve())
        return copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(policy.shutil, "copytree", copy_outside)
    installed = policy.run_uv_install(
        uv, arguments,
        env=environment, cwd=cwd,
        trusted_source_roots=(source.parent,) if selection == "resource-parent" else (source,),
        retained_resource_roots=(source.parent,) if selection == "resource-parent" else (),
        capture_output=True, text=True, timeout=30,
    )
    assert installed.returncode == 0, installed.stderr
    assert retained and not retained[0].exists()
    for value in ("original", "changed"):
        module.write_text(f"value = '{value}'\n", encoding="utf-8")
        imported = actual(
            [str(python), "-I", "-c", "import fixture_editable;print(fixture_editable.value)"],
            env=environment, capture_output=True, text=True, timeout=10, creationflags=policy._HEADLESS,
        )
        assert imported.returncode == 0, imported.stderr
        assert imported.stdout.strip() == value


@pytest.mark.parametrize("packages", [["pyyaml"], ["pyyaml", "packaging"]])
def test_native_retained_configuration_preserves_package_specific_reinstall(tmp_path, monkeypatch, packages):
    import subprocess

    environment = offline_environment(tmp_path)
    actual = subprocess.run
    installs = []

    def run(command, **kwargs):
        if "--show-settings" in command:
            return actual(command, **kwargs)
        installs.append(command)
        assert command.count("--reinstall") == 1
        assert command[command.index("--exclude-newer") + 1] == "2026-10-06T00:00:00Z"
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    requested = [item for package in packages for item in ("--reinstall-package", package)]
    result = policy.run_uv_install(
        shutil.which("uv"), ["--python", sys.executable, *requested, "pyyaml"],
        env=environment, cwd=tmp_path, now=NOW,
    )
    assert result.returncode == 0
    assert len(installs) == 1
    assert all(package in installs[0] for package in packages)


def test_native_retained_configuration_preserves_all_three_precedence_tiers(tmp_path, monkeypatch):
    import os
    import subprocess

    environment = offline_environment(tmp_path)
    environment["UV_TEST_CURRENT_TIMESTAMP"] = NOW.isoformat().replace("+00:00", "Z")
    if os.name == "nt":
        environment["APPDATA"] = str(tmp_path / "user")
        environment["SYSTEMDRIVE"] = str(tmp_path / "system")
        system_path = "system/ProgramData/uv/uv.toml"
    else:
        environment["XDG_CONFIG_HOME"] = str(tmp_path / "user")
        environment["XDG_CONFIG_DIRS"] = str(tmp_path / "system")
        system_path = "system/uv/uv.toml"
    for name, body in {
        "user/uv/uv.toml": 'exclude-newer-package={demo="11 days"}\n[[index]]\nname="retained-default"\nurl="https://user.example/simple"\ndefault=true\n',
        system_path: '[[index]]\nname="system-extra"\nurl="https://system.example/simple"\nexplicit=true\n',
        "project/uv.toml": 'exclude-newer="10 days"\n[[index]]\nname="project-extra"\nurl="https://project.example/simple"\nexplicit=true\n',
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    environment["UV_INDEX_RETAINED_DEFAULT_USERNAME"] = "fixture-user"
    environment["UV_INDEX_RETAINED_DEFAULT_PASSWORD"] = "fixture-secret"
    actual = subprocess.run
    calls = []

    def run(command, **kwargs):
        if "--show-settings" in command:
            return actual(command, **kwargs)
        config = Path(kwargs["env"]["UV_CONFIG_FILE"])
        calls.append(config)
        data = policy._toml(config)
        assert [entry["name"] for entry in data["index"]] == [
            "project-extra", "retained-default", "system-extra",
        ]
        assert kwargs["env"]["UV_INDEX_RETAINED_DEFAULT_PASSWORD"] == "fixture-secret"
        assert command[command.index("--exclude-newer") + 1] == "2026-10-03T00:00:00Z"
        environment["UV_INDEX_RETAINED_DEFAULT_PASSWORD"] = "changed-secret"
        (tmp_path / "project" / "uv.toml").write_text("no-index=true\n", encoding="utf-8")
        assert data == policy._toml(config)
        assert kwargs["env"]["UV_INDEX_RETAINED_DEFAULT_PASSWORD"] == "fixture-secret"
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    result = policy.run_uv_install(
        shutil.which("uv"), ["--python", sys.executable, "demo"], env=environment,
        cwd=tmp_path / "project", now=NOW, capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert len(calls) == 1 and not calls[0].exists()


def test_native_retained_editable_build_uses_real_setuptools(tmp_path, monkeypatch):
    import configparser
    import subprocess
    import zipfile

    source = tmp_path / ("checkout-" + "x" * 35) / "project"
    live = source / "src" / "fixture_editable"
    live.mkdir(parents=True)
    (live / "__init__.py").write_text("value = 'original'\n", encoding="utf-8")
    manifest = source / "pyproject.toml"
    manifest.write_text(
        '[build-system]\nrequires=["setuptools>=83.0.0"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="fixture-editable"\nversion="1.0"\ndependencies=[]\n'
        '[tool.setuptools.packages.find]\nwhere=["src"]\n',
        encoding="utf-8",
    )
    configuration = source / "setup.cfg"
    configuration.write_text(
        "[metadata]\ndescription = 100%% retained\n"
        "[egg_info]\ntag_build = .post1\negg_base = src\n",
        encoding="utf-8",
    )
    original_files = {
        path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()
    }
    actual = subprocess.run
    retained = []

    def run(command, **kwargs):
        if "--show-settings" in command:
            return actual(command, **kwargs)
        retained.append(Path(command[command.index("-e") + 1]))
        config = configparser.ConfigParser(interpolation=None)
        config.read(retained[-1] / "setup.cfg", encoding="utf-8")
        assert config["metadata"]["description"] == "100%% retained"
        assert config["egg_info"]["tag_build"] == ".post1"
        assert config["egg_info"]["egg_base"] == "."
        result = actual(
            [sys.executable, "-I", "-c",
             "from setuptools.build_meta import build_editable,get_requires_for_build_editable;"
             "get_requires_for_build_editable({'editable_mode': 'compat'});"
             "build_editable('.', config_settings={'editable_mode': 'compat'})"],
            cwd=retained[-1], env=kwargs["env"], capture_output=True, text=True,
            timeout=30, creationflags=policy._HEADLESS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(list(retained[-1].glob("*.egg-info"))) == 1
        assert not list(source.rglob("*.egg-info"))
        wheels = list(retained[-1].glob("*.whl"))
        assert len(wheels) == 1, result.stdout + result.stderr
        with zipfile.ZipFile(wheels[0]) as wheel:
            paths = [name for name in wheel.namelist() if name.endswith(".pth")]
            assert len(paths) == 1
            assert Path(wheel.read(paths[0]).decode().strip()).resolve() == live.parent.resolve()
        return result

    monkeypatch.setattr(policy.subprocess, "run", run)
    built = policy.run_uv_install(
        shutil.which("uv"), ["--no-config", "--no-index", "--python", sys.executable, "-e", str(source)],
        env=offline_environment(tmp_path), cwd=source.parent, trusted_source_roots=(source,),
    )
    assert built.returncode == 0, built.stdout + built.stderr
    assert len(retained) == 1 and not retained[0].exists()
    assert {
        path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()
    } == original_files


@pytest.mark.parametrize("configuration", [
    None,
    "[metadata]\ndescription = 100% retained\n",
    "[metadata]\ndescription = 100% retained\n"
    "[egg_info]\ntag_build = .fixture\negg_base = src\n"
    "[options.data_files]\nshare/WidgetPkg = first.txt\nshare/widgetpkg = second.txt\n",
])
def test_retained_editable_metadata_configuration_stays_private(tmp_path, monkeypatch, configuration):
    import configparser

    source = source_project(tmp_path / "source")
    manifest = source / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("fixture_backend", "setuptools.build_meta")
        + '[tool.setuptools.packages.find]\nwhere=["src"]\n',
        encoding="utf-8",
    )
    (source / "src").mkdir()
    original = source / "setup.cfg"
    if configuration is not None:
        original.write_text(configuration, encoding="utf-8")
    retained = []

    def run(command, **kwargs):
        if "--show-settings" in command:
            return CompletedProcess(command, 0, settings(), "")
        retained.append(Path(command[command.index("-e") + 1]))
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config.read(retained[-1] / "setup.cfg", encoding="utf-8")
        assert config["egg_info"]["egg_base"] == "."
        if configuration is not None:
            assert config["metadata"]["description"] == "100% retained"
        if configuration is not None and "tag_build" in configuration:
            assert config["egg_info"]["tag_build"] == ".fixture"
            assert dict(config["options.data_files"]) == {
                "share/WidgetPkg": "first.txt", "share/widgetpkg": "second.txt",
            }
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    policy.run_uv_install(
        "uv", ["--no-config", "-e", str(source)], env=offline_environment(tmp_path),
        cwd=tmp_path, trusted_source_roots=(source,),
    )
    assert len(retained) == 1 and not retained[0].exists()
    if configuration is None:
        assert not original.exists()
    else:
        assert original.read_text(encoding="utf-8") == configuration


@pytest.mark.parametrize("failure", ["malformed", "read", "write"])
def test_retained_editable_metadata_configuration_failure_refuses(tmp_path, monkeypatch, failure):
    source = source_project(tmp_path / "source")
    manifest = source / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("fixture_backend", "setuptools.build_meta")
        + '[tool.setuptools.packages.find]\nwhere=["src"]\n',
        encoding="utf-8",
    )
    (source / "src").mkdir()
    original = source / "setup.cfg"
    configuration = "not an INI file\n" if failure == "malformed" else "[metadata]\nname = fixture\n"
    original.write_text(configuration, encoding="utf-8")
    opened = Path.open
    original_native = Path(policy._native_retained_path(original))
    retained = []
    calls = []

    def open_config(path, mode="r", *args, **kwargs):
        if path.name == "setup.cfg" and path not in (original, original_native):
            retained.append(path.parent)
            if (failure == "read" and mode == "r") or (failure == "write" and mode == "w"):
                raise OSError("fixture configuration failure")
        return opened(path, mode, *args, **kwargs)

    def probe(command, **kwargs):
        calls.append(command)
        assert "--show-settings" in command
        return CompletedProcess(command, 0, settings(), "")

    monkeypatch.setattr(Path, "open", open_config)
    monkeypatch.setattr(policy.subprocess, "run", probe)
    with pytest.raises(policy.DependencyPolicyError, match="editable.*metadata"):
        policy.run_uv_install(
            "uv", ["--no-config", "-e", str(source)], env=offline_environment(tmp_path),
            cwd=tmp_path, trusted_source_roots=(source,),
        )
    assert len(calls) == 1
    assert retained and all(not path.exists() for path in retained)
    assert original.read_text(encoding="utf-8") == configuration


def test_retained_snapshot_inside_source_refuses_before_copy(tmp_path, monkeypatch):
    source = source_project(tmp_path / "source")
    temporary_directory = policy.tempfile.TemporaryDirectory
    monkeypatch.setattr(
        policy.tempfile, "TemporaryDirectory",
        lambda **kwargs: temporary_directory(prefix=kwargs["prefix"], dir=source),
    )
    monkeypatch.setattr(
        policy.subprocess, "run", lambda command, **kwargs: CompletedProcess(command, 0, settings(), ""),
    )
    with pytest.raises(policy.DependencyPolicyError, match="outside copied inputs"):
        policy.run_uv_install(
            "uv", ["--no-config", str(source)], env=offline_environment(tmp_path),
            cwd=tmp_path, trusted_source_roots=(source,),
        )


@pytest.mark.parametrize("excluded", [(), ("unused-local",)])
def test_native_absolute_source_build_drops_unrelated_staging_ancestry(tmp_path, monkeypatch, excluded):
    source = source_project(
        tmp_path / "runtime" / ".install-stage" / ("stage-" + "x" * 40)
        / "plugin" / "libs" / "fixture-source",
    )
    backend = source / "fixture_backend.py"
    body = backend.read_text(encoding="utf-8")
    suffix = "build/bdist.win-amd64/wheel/fixture_source/deep_package/generated_metadata.py"
    body = body.replace(
        "    name = ",
        f"    output = pathlib.Path({suffix!r})\n"
        "    output.parent.mkdir(parents=True, exist_ok=True)\n"
        "    output.write_text('retained build')\n"
        "    name = ",
    )
    backend.write_text(body, encoding="utf-8")
    cwd = tmp_path / "unrelated-caller"
    cwd.mkdir()
    (cwd / "uv.toml").write_text('exclude-newer="2026-09-01T00:00:00Z"\n', encoding="utf-8")
    actual = policy.subprocess.run
    retained = []

    def run(command, **kwargs):
        if "--show-settings" not in command:
            copied = next(Path(value) for value in command if Path(value).is_dir()
                          and (Path(value) / "fixture_backend.py").is_file())
            retained.append(copied)
            assert len(str(copied / suffix)) < 260
            assert len(str(source)) - len(str(copied)) >= 60
            assert ".install-stage" not in copied.parts
            assert Path(kwargs["cwd"]).name == "cwd"
            assert command[command.index("--exclude-newer") + 1] == "2026-09-01T00:00:00Z"
            assert (copied / "fixture_backend.py").read_text(encoding="utf-8") == body
            backend.write_text("raise RuntimeError('mutable source reopened')\n", encoding="utf-8")
        result = actual(command, **kwargs)
        if "--show-settings" not in command:
            assert (retained[-1] / suffix).read_text(encoding="utf-8") == "retained build"
        return result

    monkeypatch.setattr(policy.subprocess, "run", run)
    target = tmp_path / "installed"
    installed = policy.run_uv_install(
        shutil.which("uv"), ["--no-index", "--target", str(target), "--python", sys.executable, str(source)],
        cwd=cwd, env=offline_environment(tmp_path), trusted_source_roots=(source,),
        excluded_local_packages=excluded,
        capture_output=True, text=True, timeout=30, now=NOW,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert (target / "fixture_source-1.0.dist-info" / "METADATA").is_file()
    assert len(retained) == 1 and not retained[0].exists()


@pytest.mark.parametrize("resource_parent", [False, True])
def test_absolute_sources_preserve_dependency_and_resource_layout(tmp_path, monkeypatch, resource_parent):
    stage = tmp_path / "deep" / "staging" / "plugin"
    source = source_project(stage / "libs" / "root", sources='fixture-child={path="../child"}')
    child = source_project(stage / "libs" / "child", "fixture-child")
    (stage / "resources").mkdir()
    (stage / "resources" / "data.txt").write_text("resource", encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "--show-settings" not in command:
            retained_source = Path(kwargs["cwd"])
            assert (retained_source / "fixture_backend.py").is_file()
            assert (retained_source.parent / child.name / "pyproject.toml").is_file()
            assert policy._toml(retained_source / "pyproject.toml")["tool"]["uv"]["sources"] == {
                "fixture-child": {"path": "../child"},
            }
            if resource_parent:
                assert (retained_source.parent.parent / "resources" / "data.txt").read_text() == "resource"
        return CompletedProcess(command, 0, settings() if "--show-settings" in command else "", "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    policy.run_uv_install(
        "uv", ["--no-config", str(source)], cwd=source, env=offline_environment(tmp_path),
        trusted_source_roots=(stage,), retained_resource_roots=(stage,) if resource_parent else (),
    )
    assert len(calls) == 3


@pytest.mark.parametrize("reference", ["configured-override", "source-excursion"])
def test_absolute_operands_preserve_relative_metadata_sources(tmp_path, monkeypatch, reference):
    if reference == "configured-override":
        source = source_project(tmp_path / "nested" / "source")
        (tmp_path / "uv.toml").write_text(
            'override-dependencies=["fixture-source @ file:./nested/source"]\n',
            encoding="utf-8",
        )
    else:
        source = source_project(
            tmp_path / "source", sources='fixture-child={path="../source/child"}',
        )
        source_project(source / "child", "fixture-child")
    if reference == "configured-override":
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if "--show-settings" not in command:
                retained_base = Path(kwargs["cwd"])
                assert (retained_base / "nested" / "source" / "pyproject.toml").is_file()
            return CompletedProcess(command, 0, settings() if "--show-settings" in command else "", "")

        # Native UV on Windows does not accept relative file URLs. Exercise the
        # policy's retained configuration/source audit without bypassing it.
        monkeypatch.setattr(policy.subprocess, "run", run)
        policy.run_uv_install(
            "uv", [str(source)], cwd=tmp_path, env=offline_environment(tmp_path),
            trusted_source_roots=(tmp_path,),
        )
        assert len(calls) == 3
        return
    installed = native_policy(
        tmp_path, ["--no-index", "--python", sys.executable,
                   "--target", str(tmp_path / "installed"), str(source)],
        roots=(tmp_path,),
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert (tmp_path / "installed" / "fixture_source-1.0.dist-info" / "METADATA").is_file()


@pytest.mark.parametrize("selection", [
    "relative", "requirements", "toml", "group", "project", "environment", "editable",
])
def test_context_sensitive_sources_keep_relative_retention_layout(tmp_path, monkeypatch, selection):
    cwd = tmp_path / "caller"
    cwd.mkdir()
    source = source_project(cwd / "nested" / "source")
    arguments = ["--no-config", str(source)]
    environment = offline_environment(tmp_path)
    if selection == "relative":
        arguments[-1] = str(source.relative_to(cwd))
    elif selection == "requirements":
        requirement = cwd / "requirements.txt"
        requirement.write_text(str(source) + "\n", encoding="utf-8")
        arguments = ["--no-config", "-r", str(requirement)]
    elif selection == "toml":
        arguments = ["--no-config", "-r", str(source / "pyproject.toml")]
    elif selection == "group":
        arguments.extend(["--group", str(source) + ":dev"])
    elif selection == "project":
        arguments.extend(["--project", str(source)])
    elif selection == "environment":
        environment["UV_PROJECT"] = str(source)
    else:
        manifest = source / "pyproject.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace("fixture_backend", "setuptools.build_meta")
            + '[tool.setuptools.packages.find]\nwhere=["src"]\n',
            encoding="utf-8",
        )
        (source / "src").mkdir()
        arguments.insert(-1, "-e")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "--show-settings" not in command:
            retained_base = Path(kwargs["cwd"])
            assert (retained_base / "nested" / "source" / "pyproject.toml").is_file()
        return CompletedProcess(command, 0, settings() if "--show-settings" in command else "", "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    policy.run_uv_install(
        "uv", arguments, cwd=cwd, env=environment, trusted_source_roots=(source,),
    )
    assert len(calls) == 3


@pytest.mark.skipif(sys.platform != "win32", reason="Windows editable snapshot placement")
def test_windows_volume_root_input_has_no_outside_retention_location(tmp_path, monkeypatch):
    anchor = Path(tmp_path.anchor)
    source = anchor / "fixture"
    monkeypatch.setattr(
        policy, "_source_inputs", lambda *args: (tmp_path, {anchor, source}, set(), {source}),
    )

    def prepare(*args, _projection, **kwargs):
        _projection.append(settings())
        return []

    monkeypatch.setattr(policy, "prepare_uv_install", prepare)
    with pytest.raises(policy.DependencyPolicyError, match="outside copied inputs"):
        policy.run_uv_install("uv", [], env=offline_environment(tmp_path), cwd=tmp_path)


@pytest.mark.parametrize("field,value", [
    ("dependency_mode", "Direct"), ("build_isolation", "Shared"),
])
def test_environment_resolver_closure_bypass_refused(field, value):
    projection = settings().replace("settings: PipSettings {", f"settings: PipSettings {{\n{field}: {value},")
    with pytest.raises(policy.DependencyPolicyError, match="cannot bypass"):
        policy.cutoff_arguments(projection, now=NOW)


@pytest.mark.parametrize("unsupported", ["backend", "dynamic", "layout", "package-dir", "mode"])
def test_unsupported_editable_retention_refuses_before_install(tmp_path, monkeypatch, unsupported):
    source = source_project(tmp_path / "source")
    manifest = source / "pyproject.toml"
    data = manifest.read_text(encoding="utf-8").replace("fixture_backend", "setuptools.build_meta")
    data += '[tool.setuptools.packages.find]\nwhere=["src"]\n'
    (source / "src").mkdir()
    if unsupported == "backend":
        data = data.replace("setuptools.build_meta", "fixture_backend")
    elif unsupported == "dynamic":
        data = data.replace('version="1.0"', 'version="1.0"\ndynamic=["version"]')
    elif unsupported == "layout":
        data = data.replace('where=["src"]', 'where=["generated"]')
    elif unsupported == "package-dir":
        data += '[tool.setuptools.package-dir]\n""="custom-src"\n'
    manifest.write_text(data, encoding="utf-8")
    calls = []

    def probe(command, **kwargs):
        calls.append(command)
        assert "--show-settings" in command
        projection = settings()
        if unsupported == "mode":
            projection = projection.replace("settings: PipSettings {", 'settings: PipSettings {\nconfig_setting: {"editable_mode": "strict"},')
        return CompletedProcess(command, 0, projection, "")

    monkeypatch.setattr(policy.subprocess, "run", probe)
    with pytest.raises(policy.DependencyPolicyError, match="editable"):
        policy.run_uv_install(
            "uv", ["--no-config", "-e", str(source)], cwd=tmp_path,
            env=offline_environment(tmp_path), trusted_source_roots=(source,),
        )
    assert len(calls) == 1


def test_retained_install_never_resolves_venv_python_to_its_base_interpreter(tmp_path, monkeypatch):
    target = tmp_path / "venv" / "bin" / "python"
    target.parent.mkdir(parents=True)
    target.touch()
    base_python = tmp_path / "base-python"
    base_python.touch()
    resolve = Path.resolve

    def symlink_resolution(path, *args, **kwargs):
        return base_python if path == target else resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", symlink_resolution)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return CompletedProcess(command, 0, settings() if "--show-settings" in command else "", "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    policy.run_uv_install(
        "uv", ["--no-config", "--python", str(target), "demo"],
        env=offline_environment(tmp_path), cwd=tmp_path,
    )
    assert len(calls) == 3
    assert calls[-1][calls[-1].index("--python") + 1] == str(target)


def test_retention_never_reinterprets_inline_unknown_flags_as_requirements(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return CompletedProcess(command, 0, settings() if "--show-settings" in command else "", "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    policy.run_uv_install(
        "uv", ["--no-config", "--break-system-packages=false", "demo"],
        env=offline_environment(tmp_path), cwd=tmp_path,
    )
    assert len(calls) == 3
    assert all("--break-system-packages=false" in command for command in calls)
    assert all("false" not in command for command in calls)


def test_uv_inner_end_marker_cannot_hide_the_mandatory_cutoff():
    with pytest.raises(policy.DependencyPolicyError, match="end-of-options"):
        policy.prepare_uv_install("uv", ["--", "demo"])


def test_relative_certificate_directory_list_retains_original_cwd(tmp_path, monkeypatch):
    import os

    environment = offline_environment(tmp_path)
    environment["SSL_CERT_DIR"] = os.pathsep.join(("first", "second"))
    expected = os.pathsep.join(str(tmp_path / directory) for directory in ("first", "second"))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["env"]["SSL_CERT_DIR"] == expected
        return CompletedProcess(command, 0, settings() if "--show-settings" in command else "", "")

    monkeypatch.setattr(policy.subprocess, "run", run)
    policy.run_uv_install(
        "uv", ["--no-config", "demo"], env=environment, cwd=tmp_path,
    )
    assert len(calls) == 3


def source_project(path, name="fixture-source", sources="", dependencies=""):
    import ast

    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(
        '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n'
        f'[project]\nname="{name}"\nversion="1.0"\ndependencies=[{dependencies}]\n'
        + (f"[tool.uv.sources]\n{sources}\n" if sources else ""),
        encoding="utf-8",
    )
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n" + "".join(
        f"Requires-Dist: {requirement}\n" for requirement in ast.literal_eval(f"[{dependencies}]")
    )
    (path / "fixture_backend.py").write_text(
        "import pathlib, zipfile\n"
        "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
        f"    name = '{name.replace('-', '_')}-1.0-py3-none-any.whl'\n"
        "    with zipfile.ZipFile(pathlib.Path(wheel_directory) / name, 'w') as wheel:\n"
        f"        prefix = '{name.replace('-', '_')}-1.0.dist-info/'\n"
        f"        wheel.writestr(prefix + 'METADATA', {metadata!r})\n"
        "        wheel.writestr(prefix + 'WHEEL', 'Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')\n"
        "        wheel.writestr(prefix + 'RECORD', '')\n"
        "    return name\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(params=[
    PATH,
    PATH.parents[2] / "worktree-manager" / "scripts" / "installer_dependency_policy.py",
], ids=["canonical", "manager"])
def bundled_policy(request):
    spec = importlib.util.spec_from_file_location("bundled_installer_dependency_policy", request.param)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("change", ["none", "bytes", "package", "project", "filename"])
def test_bundled_dbus_next_exception_is_exact(tmp_path, change, bundled_policy):
    name = "other" if change == "package" else "dbus-next"
    filename = "other.whl" if change == "filename" else "dbus_next-0.2.3-py3-none-any.whl"
    source = source_project(
        tmp_path / "manager",
        name="other" if change == "project" else "copilot-extensions-worktree-manager",
        sources=f'{name} = {{path="vendor/{filename}"}}',
    )
    wheel = source / "vendor" / filename
    wheel.parent.mkdir()
    bundled = PATH.parents[2] / "worktree-manager" / "vendor" / "dbus_next-0.2.3-py3-none-any.whl"
    wheel.write_bytes(b"changed" if change == "bytes" else bundled.read_bytes())
    if change == "none":
        bundled_policy._check_project(source, (tmp_path,), set())
    else:
        with pytest.raises(bundled_policy.DependencyPolicyError, match="local UV artifacts"):
            bundled_policy._check_project(source, (tmp_path,), set())


def test_native_bundled_dbus_next_installs_through_retained_policy_offline(tmp_path, bundled_policy):
    uv = shutil.which("uv")
    assert uv, "native UV is required for the bundled wheel contract"
    source = source_project(
        tmp_path / "manager", name="copilot-extensions-worktree-manager",
        sources='dbus-next = {path="vendor/dbus_next-0.2.3-py3-none-any.whl"}',
        dependencies='"dbus-next==0.2.3"',
    )
    wheel = source / "vendor" / "dbus_next-0.2.3-py3-none-any.whl"
    wheel.parent.mkdir()
    shutil.copyfile(PATH.parents[2] / "worktree-manager" / "vendor" / wheel.name, wheel)
    environment = offline_environment(tmp_path)
    target = tmp_path / "venv"
    created = subprocess.run(
        [uv, "venv", "--python", sys._base_executable, str(target)],
        env=environment, capture_output=True, text=True, timeout=30,
        creationflags=policy._HEADLESS,
    )
    assert created.returncode == 0, created.stderr
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    installed = bundled_policy.run_uv_install(
        uv, ["--no-config", "--offline", "--python", str(python), str(source)],
        env=environment, cwd=tmp_path, trusted_source_roots=(source,),
        capture_output=True, text=True, timeout=30,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    imported = subprocess.run(
        [str(python), "-c",
         "from dbus_next.aio import MessageBus; from dbus_next.auth import AuthExternal; "
         "assert isinstance(MessageBus.connected, property)"],
        env=environment, capture_output=True, text=True, timeout=10,
        creationflags=policy._HEADLESS,
    )
    assert imported.returncode == 0, imported.stderr


def test_retained_transaction_exposes_only_admitted_inputs_until_context_exit(tmp_path, monkeypatch):
    source = source_project(tmp_path / "source")
    original = (source / "pyproject.toml").read_bytes()
    calls = []

    def probe(command, **_kwargs):
        calls.append(command)
        assert "--show-settings" in command
        return CompletedProcess(command, 0, settings(), "")

    monkeypatch.setattr(policy.subprocess, "run", probe)
    with policy.retain_uv_install(
        "attributed-uv", ["--no-config", str(source)],
        env={}, cwd=tmp_path, now=NOW, trusted_source_roots=(source,),
    ) as transaction:
        retained = transaction.sources[source]
        assert len(calls) == 2
        assert str(retained) in transaction.argv and str(source) not in transaction.argv
        assert "--exclude-newer" in transaction.argv
        assert transaction.environment["UV_CONFIG_FILE"] in transaction.argv
        assert (retained / "pyproject.toml").read_bytes() == original
        assert transaction.source_hashes[source / "pyproject.toml"] == hashlib.sha256(original).hexdigest()
        assert transaction.cwd.is_dir()
    assert not retained.exists()
    assert (source / "pyproject.toml").read_bytes() == original


def test_retained_transaction_refuses_copy_mutation_before_execution(tmp_path, monkeypatch):
    source = source_project(tmp_path / "source")
    copy = policy._copy_retained_file

    def mutate(original, destination):
        result = copy(original, destination)
        if Path(original).name == "pyproject.toml":
            Path(original).write_text('[project]\nname="changed"\nversion="1"\n', encoding="utf-8")
        return result

    monkeypatch.setattr(policy, "_copy_retained_file", mutate)
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda command, **_kwargs: CompletedProcess(command, 0, settings(), ""),
    )
    with pytest.raises(policy.DependencyPolicyError, match="source content changed"):
        with policy.retain_uv_install(
            "attributed-uv", [str(source)],
            env={}, cwd=tmp_path, now=NOW, trusted_source_roots=(source,),
        ):
            pytest.fail("mutated input cannot reach the admitted execution boundary")


def test_retained_overlapping_projects_compare_each_file_pair_once(tmp_path, monkeypatch):
    parent = source_project(tmp_path / "parent", name="fixture-parent")
    child = source_project(parent / "child", name="fixture-child")
    read_bytes = Path.read_bytes
    reads = {}

    def observe(path):
        if path.name == "fixture_backend.py":
            key = Path(str(path).removeprefix("\\\\?\\"))
            reads[key] = reads.get(key, 0) + 1
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", observe)
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda command, **_kwargs: CompletedProcess(command, 0, settings(), ""),
    )
    with policy.retain_uv_install(
        "attributed-uv", ["--no-config", str(parent), str(child)],
        env={}, cwd=tmp_path, now=NOW, trusted_source_roots=(parent, child),
    ) as transaction:
        for project in (parent, child):
            original = project / "fixture_backend.py"
            retained = transaction.sources[project] / original.name
            assert reads[original] == 1
            assert reads[retained] == 1
            assert transaction.source_hashes[original] == hashlib.sha256(read_bytes(original)).hexdigest()


def test_retained_metadata_remapping_work_is_independent_of_scalar_count(tmp_path, monkeypatch):
    layout = policy._configuration.retained_source_layout
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda command, **_kwargs: CompletedProcess(command, 0, settings(), ""),
    )

    def count_mirrors(scalars):
        source = source_project(tmp_path / f"source-{scalars}")
        manifest = source / "pyproject.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "keywords=" + json.dumps([f"keyword-{index}" for index in range(scalars)]) + "\n",
            encoding="utf-8",
        )
        calls = []

        def observe(*args, **kwargs):
            anchors, mirror = layout(*args, **kwargs)

            def counted(path):
                calls.append(path)
                return mirror(path)

            return anchors, counted

        with monkeypatch.context() as patch:
            patch.setattr(policy._configuration, "retained_source_layout", observe)
            with policy.retain_uv_install(
                "attributed-uv", ["--no-config", str(source)],
                env={}, cwd=tmp_path, now=NOW, trusted_source_roots=(source,),
            ) as transaction:
                assert transaction.source_hashes[source / "fixture_backend.py"]
        return len(calls)

    assert count_mirrors(64) == count_mirrors(1)


def test_retained_remapping_preserves_nested_native_uri_and_escaped_forms(tmp_path, monkeypatch):
    parent = source_project(tmp_path / "parent", name="fixture-parent")
    child = source_project(parent / "child", name="fixture-child")
    forms = [
        str(child / "fixture_backend.py"),
        child.as_uri() + "/fixture_backend.py",
        child.as_posix() + "/fixture_backend.py",
        json.dumps(str(child))[1:-1],
    ]
    manifest = parent / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\n[tool.fixture]\npaths=" + json.dumps([forms]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        policy.subprocess, "run",
        lambda command, **_kwargs: CompletedProcess(command, 0, settings(), ""),
    )
    with policy.retain_uv_install(
        "attributed-uv", ["--no-config", str(parent), str(child)],
        env={}, cwd=tmp_path, now=NOW, trusted_source_roots=(parent, child),
    ) as transaction:
        retained = transaction.sources[child]
        data = policy._toml(transaction.sources[parent] / "pyproject.toml")
        assert data["tool"]["fixture"]["paths"] == [[
            str(retained / "fixture_backend.py"),
            retained.as_uri() + "/fixture_backend.py",
            retained.as_posix() + "/fixture_backend.py",
            json.dumps(str(retained))[1:-1],
        ]]


@pytest.mark.parametrize("outcome", ["nonzero", "checked", "success", "admission"])
def test_real_retained_graph_preserves_primary_failure_on_cleanup(tmp_path, monkeypatch, capsys, outcome):
    source = source_project(tmp_path / "source")
    temporary_directory = policy.tempfile.TemporaryDirectory
    retained_paths = []
    allocations = []
    primary = subprocess.CalledProcessError(17, ["uv"], output="output", stderr="error")

    class CleanupFailure:
        def __init__(self, **kwargs):
            self.temporary = temporary_directory(**kwargs)
            self.name = self.temporary.name
            allocations.append(Path(self.name))

        def cleanup(self):
            self.temporary.name = self.name
            self.temporary.cleanup()
            raise PermissionError("injected retained cleanup failure")

    def run(command, **kwargs):
        if "--show-settings" in command:
            if outcome == "admission" and allocations:
                raise primary
            return CompletedProcess(command, 0, settings(), "")
        retained_paths.append(Path(next(value for value in command if (Path(value) / "pyproject.toml").is_file())))
        assert retained_paths[0] != source
        if kwargs.get("check"):
            raise primary
        return CompletedProcess(command, 0 if outcome == "success" else 17, "output", "error")

    monkeypatch.setattr(policy.tempfile, "TemporaryDirectory", CleanupFailure)
    monkeypatch.setattr(policy.subprocess, "run", run)
    options = dict(env={}, cwd=tmp_path, now=NOW, trusted_source_roots=(source,))
    if outcome in {"checked", "admission"}:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            policy.run_uv_install("uv", ["--no-config", str(source)], check=True, **options)
        assert caught.value is primary
    elif outcome == "success":
        with pytest.raises(policy.RetainedInputCleanupError):
            policy.run_uv_install("uv", ["--no-config", str(source)], **options)
    else:
        result = policy.run_uv_install("uv", ["--no-config", str(source)], **options)
        assert (result.returncode, result.stdout, result.stderr) == (17, "output", "error")
    assert source.is_dir()
    assert allocations and all(not path.exists() for path in allocations)
    assert all(not path.exists() for path in retained_paths if path != source)
    diagnostic = capsys.readouterr().err
    assert diagnostic.count("retained-input-cleanup:") == (0 if outcome == "success" else 1)


def offline_environment(tmp_path):
    import os

    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_", "PYTHON")) and key != "VIRTUAL_ENV"
    }
    environment.update(UV_OFFLINE="1", UV_CACHE_DIR=str(tmp_path / "cache"))
    return environment


def native_policy(tmp_path, arguments, *, roots=(), interpreter=sys.executable):
    import subprocess

    uv = shutil.which("uv")
    assert uv, "native UV is required"
    return subprocess.run(
        [str(interpreter), "-I", str(PATH), "--uv", uv,
         *[argument for root in roots for argument in ("--trusted-source-root", str(root))],
         "--", *arguments],
        cwd=tmp_path, env=offline_environment(tmp_path),
        capture_output=True, text=True, timeout=30, creationflags=policy._HEADLESS,
    )


@pytest.mark.parametrize("editable", [None, "-e", "--editable"])
def test_native_unowned_directory_is_not_a_first_party_exemption(tmp_path, editable):
    source = source_project(tmp_path / "unowned", "agent-lookalike")
    arguments = ([editable] if editable else []) + [str(source)]
    result = native_policy(tmp_path, arguments)
    assert result.returncode != 0
    assert "trusted source roots" in result.stderr


@pytest.mark.parametrize("reference", ["path", "url", "named"])
@pytest.mark.parametrize("option", [None, "-e", "--editable", "-r", "-c"])
def test_native_local_wheel_refused_even_inside_attested_root(tmp_path, reference, option):
    import zipfile

    wheel = tmp_path / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("demo-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n")
        archive.writestr("demo-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("demo-1.0.dist-info/RECORD", "")
    requirement = str(wheel) if reference == "path" else wheel.as_uri()
    if reference == "named":
        requirement = "demo @ " + requirement
    if option in ("-r", "-c"):
        included = tmp_path / "included.txt"
        included.write_text(requirement + "\n", encoding="utf-8")
        outer = tmp_path / "requirements.txt"
        outer.write_text("-r included.txt\n", encoding="utf-8")
        arguments = [option, str(outer)]
    else:
        arguments = ([option] if option else []) + [requirement]
    result = native_policy(tmp_path, arguments, roots=(tmp_path,))
    assert result.returncode != 0
    assert "local artifacts cannot prove release age" in result.stderr


@pytest.mark.parametrize("filename", ["demo.tar.gz", "demo.zip"])
def test_local_source_archive_refused_before_uv_probe(tmp_path, monkeypatch, filename):
    artifact = tmp_path / filename
    artifact.write_bytes(b"not attributable source")
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: pytest.fail("probe ran"))
    with pytest.raises(policy.DependencyPolicyError, match="local artifacts"):
        policy.prepare_uv_install("uv", [str(artifact)], trusted_source_roots=(tmp_path,))


def test_python_api_does_not_infer_trust_from_helper_or_cwd(tmp_path, monkeypatch):
    source = source_project(tmp_path / "owned-looking")
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: pytest.fail("probe ran"))
    with pytest.raises(policy.DependencyPolicyError, match="trusted source roots"):
        policy.prepare_uv_install("uv", [str(source)], cwd=tmp_path)


@pytest.mark.parametrize("edge", ["escape", "identity", "wheel", "symlink"])
def test_native_nested_source_graph_refuses_unattributed_edges(tmp_path, edge):
    import os

    owned = tmp_path / "owned"
    root = source_project(owned / "root", sources='fixture-child={path="../child"}')
    child = source_project(owned / "child", "fixture-child", sources='fixture-leaf={path="../../outside"}')
    outside = source_project(tmp_path / "outside", "fixture-leaf")
    if edge == "identity":
        source_project(owned / "leaf", "different-identity")
        source_project(child, "fixture-child", sources='fixture-leaf={path="../leaf"}')
    elif edge == "wheel":
        (owned / "leaf.whl").write_bytes(b"artifact")
        source_project(child, "fixture-child", sources='fixture-leaf={path="../leaf.whl"}')
    elif edge == "symlink":
        try:
            os.symlink(outside, owned / "linked", target_is_directory=True)
        except OSError:
            # Directory junctions do not require Windows symlink privileges.
            import subprocess

            if sys.platform != "win32":
                raise
            result = subprocess.run(
                [os.environ["COMSPEC"], "/c", "mklink", "/J", str(owned / "linked"), str(outside)],
                capture_output=True, text=True, timeout=10, creationflags=policy._HEADLESS,
            )
            assert result.returncode == 0, result.stderr
        source_project(child, "fixture-child", sources='fixture-leaf={path="../linked"}')
    result = native_policy(tmp_path, [str(root)], roots=(owned,))
    assert result.returncode != 0
    assert "dependency-seasoning:" in result.stderr
    assert "identity" in result.stderr if edge == "identity" else "local" in result.stderr


@pytest.mark.parametrize("working_directory", ["flag", "env"])
def test_relative_source_extras_follow_uv_working_directory(tmp_path, working_directory):
    source = source_project(tmp_path / "owned" / "source")
    with (source / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write('[project.optional-dependencies]\nextra=[]\n')
    arguments = ["--no-config", "--python", sys.executable, "source[extra]"]
    environment = offline_environment(tmp_path)
    if working_directory == "flag":
        arguments[:0] = ["--directory", str(source.parent)]
    else:
        environment["UV_WORKING_DIR"] = str(source.parent)
    admitted = policy.prepare_uv_install(
        shutil.which("uv"), arguments, cwd=tmp_path, env=environment,
        trusted_source_roots=(source.parent,), now=NOW,
    )
    assert "source[extra]" in admitted
    assert admitted[-3:] == ["--exclude-newer", "2026-10-06T00:00:00Z", "--reinstall"]


@pytest.mark.parametrize("arguments", [
    ["--index", "--quiet", "demo @ https://unapproved.example/demo.whl"],
    ["--index", "demo @ https://unapproved.example/demo.whl"],
    ["--index=https://configured.example/simple", "demo @ https://unapproved.example/demo.whl"],
])
def test_pending_index_option_cannot_exempt_a_direct_requirement(arguments, monkeypatch):
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: pytest.fail("probe ran"))
    with pytest.raises(policy.DependencyPolicyError):
        policy.prepare_uv_install("uv", arguments)


def test_native_fresh_python310_no_pip_uses_bundled_proper_parser(tmp_path):
    import os
    import subprocess

    uv = shutil.which("uv")
    assert uv
    environment = offline_environment(tmp_path)
    found = subprocess.run(
        [uv, "python", "find", "--offline", "--no-python-downloads", "3.10"],
        env=environment, capture_output=True, text=True, timeout=20, creationflags=policy._HEADLESS,
    )
    if found.returncode:
        pytest.skip("Python 3.10 unavailable offline; native 3.10 CI lane exercises this")
    target = tmp_path / "cold310"
    created = subprocess.run(
        [uv, "venv", "--offline", "--python", found.stdout.strip(), str(target)],
        env=environment, capture_output=True, text=True, timeout=30, creationflags=policy._HEADLESS,
    )
    assert created.returncode == 0, created.stderr
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    proof = subprocess.run(
        [str(python), "-I", "-c",
         "import importlib.util; assert all(importlib.util.find_spec(n) is None for n in ('pip','tomli','tomllib'))"],
        env=environment, capture_output=True, text=True, timeout=10, creationflags=policy._HEADLESS,
    )
    assert proof.returncode == 0, proof.stderr
    source = source_project(tmp_path / "source310")
    result = native_policy(
        tmp_path, ["--no-config", "--no-index", "--python", str(python), str(source)],
        roots=(source,), interpreter=python,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rejected = native_policy(
        tmp_path, ["demo @ file:///unattributed/demo.whl"], roots=(source,), interpreter=python,
    )
    assert rejected.returncode != 0
    assert "trusted source roots" in rejected.stderr


def test_parserless_bootstrap_fails_explicitly_without_installing(monkeypatch):
    monkeypatch.setattr(policy, "_BUNDLED_TOML", None)
    monkeypatch.setattr(policy.importlib, "import_module", lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name)))
    monkeypatch.setattr(policy.Path, "glob", lambda *args: [])
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: pytest.fail("installed parser"))
    with pytest.raises(policy.DependencyPolicyError, match="proper TOML parser is unavailable"):
        policy._toml_parser()


@pytest.mark.parametrize("option", ["-e", "-r", "-c", "-b"])
def test_attached_short_options_cannot_hide_a_local_artifact(tmp_path, monkeypatch, option):
    artifact = tmp_path / "demo.whl"
    artifact.write_bytes(b"artifact")
    value = str(artifact)
    if option != "-e":
        requirement = tmp_path / "requirements.txt"
        requirement.write_text(artifact.as_uri() + "\n", encoding="utf-8")
        value = str(requirement)
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: pytest.fail("probe ran"))
    with pytest.raises(policy.DependencyPolicyError, match="local artifacts"):
        policy.prepare_uv_install("uv", [option + value], trusted_source_roots=(tmp_path,))


@pytest.mark.parametrize("context", ["--project", "UV_PROJECT", "--group"])
def test_explicit_project_context_does_not_trust_an_escaping_source_graph(tmp_path, context):
    project = source_project(tmp_path / "owned", sources='fixture-child={path="../outside"}')
    source_project(tmp_path / "outside", "fixture-child")
    environment = offline_environment(tmp_path)
    arguments = ["--python", sys.executable, "demo"]
    if context == "UV_PROJECT":
        environment["UV_PROJECT"] = str(project)
    elif context == "--group":
        arguments[:0] = ["--group", str(project) + ":dev"]
    else:
        arguments[:0] = [context, str(project)]
    with pytest.raises(policy.DependencyPolicyError, match="trusted source roots"):
        policy.prepare_uv_install(
            shutil.which("uv"), arguments, env=environment, cwd=tmp_path,
            trusted_source_roots=(project,),
        )


@pytest.mark.parametrize("anchor", ["canonical", "canonical-vendored", "canonical-materialized", "payload"])
def test_native_cli_uses_its_own_roster_or_payload_anchor_not_cwd(tmp_path, anchor):
    import json

    repository = tmp_path / "repository"
    canonical_libraries = anchor.startswith("canonical")
    editable = ", editable=true" if anchor == "canonical-materialized" else ""
    payload = source_project(
        repository / "plugins" / "fixture-plugin", "fixture-plugin",
        sources=f'fixture-lib={{path="../../libs/fixture-lib"{editable}}}' if canonical_libraries
        else 'fixture-lib={path="libs/fixture-lib"}',
        dependencies='"fixture-lib>=1"',
    )
    lib = source_project(
        repository / "libs" / "fixture-lib" if canonical_libraries else payload / "libs" / "fixture-lib",
        "fixture-lib", sources=f'fixture-leaf={{path="../fixture-leaf"{editable}}}',
        dependencies='"fixture-leaf>=1"',
    )
    leaf = source_project(lib.parent / "fixture-leaf", "fixture-leaf")
    manifest = {
        "name": "fixture-plugin", "version": "1.0",
        "repository": "https://github.com/ThomasMichon/copilot-extensions",
    }
    (payload / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    if anchor.startswith("canonical"):
        helper = (
            repository / "libs" / "installer-engine" / PATH.name if anchor == "canonical"
            else payload / "scripts" / PATH.name
        )
        roster = repository / ".github" / "plugin" / "marketplace.json"
        roster.parent.mkdir(parents=True)
        roster.write_text(json.dumps({
            "name": "copilot-extensions",
            "plugins": [{"name": "fixture-plugin", "version": "1.0", "source": "plugins/fixture-plugin"}],
        }), encoding="utf-8")
        if anchor == "canonical-materialized":
            import materialize_main

            for project in (lib, leaf):
                (project / "src").mkdir()
            log = materialize_main.materialize_uv_editable_ref_into(
                source_consumer_dir=payload, dest_consumer_dir=payload,
                canonical_root=repository, dest_root=repository,
            )
            assert log and not any(entry.startswith("SKIP") for entry in log), log
            lib = payload / "libs" / "fixture-lib"
            leaf = payload / "libs" / "fixture-leaf"
            assert (lib / "pyproject.toml").is_file() and (leaf / "pyproject.toml").is_file()
            shutil.rmtree(repository / "libs")
    else:
        helper = payload / "scripts" / PATH.name
    helper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(PATH, helper)
    shutil.copyfile(PATH.with_name("installer_dependency_config.py"), helper.with_name("installer_dependency_config.py"))
    import subprocess

    uv = shutil.which("uv")
    assert uv
    environment = offline_environment(tmp_path)
    target = tmp_path / "venv"
    created = subprocess.run(
        [uv, "venv", "--offline", "--python", sys.executable, str(target)],
        capture_output=True, text=True, env=environment, timeout=30, creationflags=policy._HEADLESS,
    )
    assert created.returncode == 0, created.stderr
    import os

    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run(
        [sys.executable, "-I", str(helper), "--uv", uv, "--",
         "--no-config", "--no-index", "--python", str(python), str(payload)],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        timeout=30, creationflags=policy._HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    installed = subprocess.run(
        [str(python), "-I", "-c",
         "import importlib.metadata as m, json; "
         "print(json.dumps({name: m.version(name) for name in "
         "('fixture-plugin', 'fixture-lib', 'fixture-leaf')}))"],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        timeout=30, creationflags=policy._HEADLESS,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert json.loads(installed.stdout) == {
        "fixture-plugin": "1.0", "fixture-lib": "1.0", "fixture-leaf": "1.0",
    }
    unowned = source_project(repository / "unowned", "agent-lookalike")
    rejected = subprocess.run(
        [sys.executable, "-I", str(helper), "--uv", uv, "--", str(unowned)],
        cwd=unowned, env=environment, capture_output=True, text=True,
        timeout=30, creationflags=policy._HEADLESS,
    )
    assert rejected.returncode != 0
    assert "trusted source roots" in rejected.stderr


@pytest.mark.parametrize("helper_owner", ["canonical", "payload"])
@pytest.mark.parametrize("target", ["sibling", "unowned", "foreign-payload", "wrong-identity"])
def test_canonical_materialized_graph_retains_only_its_owned_library_namespace(
    tmp_path, monkeypatch, helper_owner, target,
):
    import json

    repository = tmp_path / "repository"
    payload = source_project(
        repository / "plugins" / "fixture-plugin", "fixture-plugin",
        sources='fixture-lib={path="libs/fixture-lib"}',
    )
    library = source_project(payload / "libs" / "fixture-lib", "fixture-lib")
    target_path = {
        "sibling": payload / "libs" / "fixture-leaf",
        "unowned": payload / "unowned" / "fixture-leaf",
        "foreign-payload": repository / "plugins" / "unlisted" / "libs" / "fixture-leaf",
        "wrong-identity": payload / "libs" / "fixture-leaf",
    }[target]
    import os

    reference = os.path.relpath(target_path, library).replace("\\", "/")
    with (library / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(f'[tool.uv.sources]\nfixture-leaf={{path="{reference}"}}\n')
    leaf = source_project(target_path, "different-name" if target == "wrong-identity" else "fixture-leaf")
    (payload / "plugin.json").write_text(json.dumps({
        "name": "fixture-plugin", "version": "1.0",
        "repository": "https://github.com/ThomasMichon/copilot-extensions",
    }), encoding="utf-8")
    roster = repository / ".github" / "plugin" / "marketplace.json"
    roster.parent.mkdir(parents=True)
    roster.write_text(json.dumps({
        "name": "copilot-extensions",
        "plugins": [{"name": "fixture-plugin", "version": "1.0", "source": "plugins/fixture-plugin"}],
    }), encoding="utf-8")
    helper = (
        repository / "libs" / "installer-engine" / PATH.name if helper_owner == "canonical"
        else payload / "scripts" / PATH.name
    )
    monkeypatch.setattr(policy, "__file__", str(helper))
    if target == "sibling":
        assert set(policy._anchor_roots()) == {payload, library, leaf}
    else:
        with pytest.raises(policy.DependencyPolicyError, match="library (attribution|identity)"):
            policy._anchor_roots()


def test_payload_metadata_alone_without_owner_provenance_grants_no_roots(tmp_path, monkeypatch):
    import json

    source = source_project(tmp_path / "payload", "agent-lookalike")
    (source / "plugin.json").write_text(
        json.dumps({"name": "agent-lookalike", "version": "1.0"}), encoding="utf-8",
    )
    monkeypatch.setattr(policy, "__file__", str(source / "scripts" / PATH.name))
    assert policy._anchor_roots() == ()


def test_nested_toml_requirements_cannot_hide_local_file_artifacts(tmp_path, monkeypatch):
    wheel = tmp_path / "demo.whl"
    wheel.write_bytes(b"artifact")
    source = source_project(tmp_path / "source", dependencies=f'"demo @ {wheel.as_uri()}"')
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(f"-r {source / 'pyproject.toml'}\n", encoding="utf-8")
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: pytest.fail("probe ran"))
    with pytest.raises(policy.DependencyPolicyError, match="local artifacts"):
        policy.prepare_uv_install(
            "uv", ["-r", str(requirements)], cwd=tmp_path, trusted_source_roots=(tmp_path,),
        )


@pytest.mark.parametrize("name", ["fixture_source", "not-the-source"])
def test_native_directory_file_url_requires_exact_attributed_project_identity(tmp_path, name):
    source = source_project(tmp_path / "source")
    arguments = ["--no-config", "--python", sys.executable, name + " @ " + source.as_uri()]
    if name == "not-the-source":
        with pytest.raises(policy.DependencyPolicyError, match="identity"):
            policy.prepare_uv_install(
                shutil.which("uv"), arguments, cwd=tmp_path, trusted_source_roots=(source,), now=NOW,
            )
    else:
        admitted = policy.prepare_uv_install(
            shutil.which("uv"), arguments, cwd=tmp_path, trusted_source_roots=(source,), now=NOW,
        )
        assert arguments == admitted[:-3]


@pytest.mark.parametrize("reference", [
    "absolute", "relative", "absolute-extras", "relative-extras",
    "uri", "literal-uri", "named-uri", "named-literal-uri", "nested",
])
def test_native_owned_source_paths_containing_at_are_not_named_requirements(tmp_path, reference):
    from urllib.parse import unquote

    source = source_project(tmp_path / "owned@source" / "demo", "demo")
    with (source / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write('[project.optional-dependencies]\nextra=[]\n')
    if reference.startswith("absolute"):
        requirement = str(source)
    elif reference.startswith("relative"):
        requirement = str(source.relative_to(tmp_path))
    elif reference == "nested":
        parent = source_project(
            tmp_path / "parent", "parent", sources='demo={path="../owned@source/demo"}',
        )
        requirement = str(parent)
    else:
        requirement = source.as_uri()
        if "literal" in reference:
            requirement = unquote(requirement)
        if reference.startswith("named"):
            requirement = "demo@" + requirement
    if reference.endswith("extras"):
        requirement += "[extra]"
    arguments = ["--no-config", "--python", sys.executable, requirement]
    admitted = policy.prepare_uv_install(
        shutil.which("uv"), arguments, cwd=tmp_path, env=offline_environment(tmp_path),
        trusted_source_roots=(tmp_path,), now=NOW,
    )
    assert admitted[:-3] == arguments
    assert admitted[-3:] == ["--exclude-newer", "2026-10-06T00:00:00Z", "--reinstall"]


@pytest.mark.parametrize("reference", ["unowned-path", "unowned-uri", "artifact", "wrong-name"])
def test_at_in_paths_does_not_relax_source_attribution(tmp_path, monkeypatch, reference):
    from urllib.parse import unquote

    owned = source_project(tmp_path / "owned@source" / "demo", "demo")
    unowned = source_project(tmp_path / "unowned@source" / "demo", "demo")
    if reference == "unowned-path":
        requirement, message = str(unowned), "trusted source roots"
    elif reference == "unowned-uri":
        requirement, message = unquote(unowned.as_uri()), "trusted source roots"
    elif reference == "artifact":
        artifact = owned / "demo@artifact.whl"
        artifact.write_bytes(b"artifact")
        requirement, message = str(artifact), "local artifacts"
    else:
        requirement, message = "different@" + unquote(owned.as_uri()), "identity"
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: pytest.fail("probe ran"))
    with pytest.raises(policy.DependencyPolicyError, match=message):
        policy.prepare_uv_install(
            "uv", [requirement], cwd=tmp_path, trusted_source_roots=(owned,), now=NOW,
        )


@pytest.mark.parametrize("resource", ["relative", "outside", "missing"])
def test_retained_build_resources_require_existing_attributed_directories(tmp_path, monkeypatch, resource):
    source = source_project(tmp_path / "source")
    outside = tmp_path / "outside"
    outside.mkdir()
    selected = {"relative": Path("source"), "outside": outside, "missing": source / "missing"}[resource]
    monkeypatch.setattr(policy.subprocess, "run", lambda *args, **kwargs: pytest.fail("probe ran"))
    with pytest.raises(policy.DependencyPolicyError):
        policy.run_uv_install(
            "uv", [str(source)], trusted_source_roots=(source,), retained_resource_roots=(selected,),
        )


def test_new_private_snapshot_paths_are_canonicalized_without_trusting_source_aliases(tmp_path, monkeypatch):
    source = source_project(tmp_path / "source")
    create = policy._private_retained_root
    roots = []

    def private(parent):
        root = create(parent)
        roots.append(root)
        return root / ".." / root.name

    def run(command, **kwargs):
        if "--show-settings" in command:
            return CompletedProcess(command, 0, """PipInstallSettings { settings: PipSettings {
                index_locations: IndexLocations { indexes: [], flat_index: [], },
                exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },
            }, }""", "")
        config = Path(kwargs["env"]["UV_CONFIG_FILE"])
        assert config == config.resolve()
        assert Path(kwargs["cwd"]).is_relative_to(roots[0])
        return CompletedProcess(command, 0)

    monkeypatch.setattr(policy, "_private_retained_root", private)
    monkeypatch.setattr(policy.subprocess, "run", run)
    result = policy.run_uv_install(
        "uv", ["--no-config", str(source)], cwd=source, trusted_source_roots=(source,),
    )
    assert result.returncode == 0 and roots and not roots[0].exists()


def test_registry_only_cli_does_not_discover_product_roots(monkeypatch):
    def forbidden():
        pytest.fail("registry bootstrap attempted product-graph discovery")

    calls = []
    monkeypatch.setattr(policy, "_anchor_roots", forbidden)
    monkeypatch.setattr(
        policy, "run_uv_install",
        lambda uv, arguments, **kwargs: (
            calls.append((uv, arguments, kwargs)) or CompletedProcess([], 0)
        ),
    )
    arguments = [
        "--no-config", "--require-hashes", "--index-url", "https://registry.example/simple",
        "packaging==25.0",
    ]
    assert policy.main(["--uv", "selected-uv", "--registry-only", "--", *arguments]) == 0
    assert len(calls) == 1
    assert calls[0][:2] == ("selected-uv", arguments)
    assert calls[0][2]["trusted_source_roots"] == ()


def test_default_cli_retains_product_attribution(monkeypatch, tmp_path):
    discovered, calls = [], []
    monkeypatch.setattr(
        policy, "_anchor_roots", lambda: (discovered.append(True) or (tmp_path,)),
    )
    monkeypatch.setattr(
        policy, "run_uv_install",
        lambda uv, arguments, **kwargs: (
            calls.append(kwargs["trusted_source_roots"]) or CompletedProcess([], 0)
        ),
    )
    assert policy.main(["--uv", "selected-uv", "--", "fixture-package"]) == 0
    assert discovered == [True] and calls == [(tmp_path,)]


def test_registry_only_cannot_grant_explicit_source_roots(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        policy.main([
            "--uv", "selected-uv", "--registry-only", "--trusted-source-root", str(tmp_path),
            "--", "fixture-package",
        ])
    assert error.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize(
    "reference", ["direct", "editable", "requirements", "nested-requirements", "project", "configured"],
)
def test_registry_only_refuses_local_sources_before_uv(monkeypatch, tmp_path, capsys, reference):
    source = source_project(tmp_path / "source")
    for name in tuple(os.environ):
        if name.startswith(("UV_", "PIP_")):
            monkeypatch.delenv(name)
    for name in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.chdir(tmp_path)
    arguments = [str(source)]
    if reference == "editable":
        arguments.insert(0, "-e")
    elif reference in {"requirements", "nested-requirements"}:
        requirement = tmp_path / "requirements.txt"
        requirement.write_text(str(source) + "\n", encoding="utf-8")
        if reference == "nested-requirements":
            outer = tmp_path / "outer.txt"
            outer.write_text("-r requirements.txt\n", encoding="utf-8")
            requirement = outer
        arguments = ["-r", str(requirement)]
    elif reference == "project":
        arguments = ["--project", str(source), "fixture-package"]
    elif reference == "configured":
        config = tmp_path / "configured.toml"
        config.write_text(
            '[sources]\nfixture-source={path="source"}\n', encoding="utf-8",
        )
        monkeypatch.setenv("UV_CONFIG_FILE", str(config))
        arguments = ["fixture-package"]

    def forbidden(*args, **kwargs):
        pytest.fail("refused local-source input reached UV or dependency writes")

    monkeypatch.setattr(policy, "_anchor_roots", forbidden)
    monkeypatch.setattr(policy.subprocess, "run", forbidden)
    assert policy.main(["--uv", "selected-uv", "--registry-only", "--", *arguments]) == 1
    assert "dependency-seasoning:" in capsys.readouterr().err


def materialized_broken_helper(root):
    import materialize_main

    repository = root / "repository"
    payload = source_project(
        repository / "plugins" / "fixture-plugin", "fixture-plugin",
        sources='fixture-lib={path="../../libs/fixture-lib",editable=true}',
        dependencies='"fixture-lib>=1"',
    )
    library = source_project(
        repository / "libs" / "fixture-lib", "fixture-lib",
        sources='fixture-leaf={path="../fixture-leaf",editable=true}',
        dependencies='"fixture-leaf>=1"',
    )
    leaf = source_project(repository / "libs" / "fixture-leaf", "fixture-leaf")
    for project in (library, leaf):
        (project / "src").mkdir()
    (payload / "plugin.json").write_text(json.dumps({
        "name": "fixture-plugin", "version": "1.0",
        "repository": "https://github.com/ThomasMichon/copilot-extensions",
    }), encoding="utf-8")
    roster = repository / ".github" / "plugin" / "marketplace.json"
    roster.parent.mkdir(parents=True)
    roster.write_text(json.dumps({
        "name": "copilot-extensions",
        "plugins": [{"name": "fixture-plugin", "version": "1.0", "source": "plugins/fixture-plugin"}],
    }), encoding="utf-8")
    changes = materialize_main.materialize_uv_editable_ref_into(
        source_consumer_dir=payload, dest_consumer_dir=payload,
        canonical_root=repository, dest_root=repository,
    )
    assert changes and not any(change.startswith("SKIP") for change in changes)
    shutil.rmtree(repository / "libs")
    manifest = payload / "libs" / "fixture-leaf" / "pyproject.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'name="fixture-leaf"', 'name="wrong-identity"',
        ),
        encoding="utf-8",
    )
    helper = repository / "libs" / "installer-engine" / PATH.name
    helper.parent.mkdir(parents=True)
    shutil.copyfile(PATH, helper)
    shutil.copyfile(
        PATH.with_name("installer_dependency_config.py"),
        helper.with_name("installer_dependency_config.py"),
    )
    wrapper = repository / "tools" / "seasoned_uv.py"
    wrapper.parent.mkdir()
    shutil.copyfile(PATH.parents[2] / "tools" / wrapper.name, wrapper)
    assert not (repository / "libs" / "fixture-lib").exists()
    assert not (repository / "libs" / "fixture-leaf").exists()
    return wrapper


@pytest.mark.parametrize("case", ["accepted", "stricter-cutoff", "wrong-hash", "unseasoned"])
def test_native_registry_bootstrap_is_independent_of_materialized_product_graph(tmp_path, case):
    uv = shutil.which("uv")
    assert uv, "native UV is required for validator-bootstrap coverage"
    helper = materialized_broken_helper(tmp_path)
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as wheel:
        wheel.writestr("fixture_validator.py", "MARKER = 'installed-from-registry'\n")
        prefix = "fixture_validator-1.0.dist-info/"
        wheel.writestr(
            prefix + "METADATA", "Metadata-Version: 2.1\nName: fixture-validator\nVersion: 1.0\n",
        )
        wheel.writestr(
            prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr(prefix + "RECORD", "")
    artifact = content.getvalue()
    filename = "fixture_validator-1.0-py3-none-any.whl"
    digest = hashlib.sha256(artifact).hexdigest()
    requirement = tmp_path / "validator-requirements.txt"
    requirement.write_text(
        f"fixture-validator==1.0 --hash=sha256:{'0' * 64 if case == 'wrong-hash' else digest}\n",
        encoding="utf-8",
    )
    downloads = []

    class Index(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond(head=False)

        def do_HEAD(self):
            self.respond(head=True)

        def respond(self, *, head):
            if self.path == "/simple/fixture-validator/":
                body = json.dumps({
                    "meta": {"api-version": "1.2"}, "name": "fixture-validator",
                    "files": [{
                        "filename": filename, "url": "/files/" + filename,
                        "hashes": {"sha256": digest},
                        "upload-time": "2099-01-01T00:00:00Z" if case == "unseasoned"
                        else "2020-01-01T00:00:00Z",
                    }],
                }).encode()
                kind = "application/vnd.pypi.simple.v1+json"
            elif self.path == "/files/" + filename:
                if not head:
                    downloads.append(filename)
                body, kind = artifact, "application/octet-stream"
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
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    environment = offline_environment(tmp_path)
    environment.pop("UV_OFFLINE", None)
    target = tmp_path / "fresh-venv"
    options = dict(
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        timeout=30, creationflags=policy._HEADLESS,
    )
    try:
        created = subprocess.run(
            [uv, "venv", "--offline", "--python", sys._base_executable, str(target)], **options,
        )
        assert created.returncode == 0, created.stdout + created.stderr
        python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        arguments = [
            "--python", str(python), "--no-config", "--only-binary", ":all:", "--require-hashes",
            "--index-url", f"http://127.0.0.1:{server.server_port}/simple", "-r", str(requirement),
        ]
        if case == "stricter-cutoff":
            arguments.extend(["--exclude-newer", "2019-01-01T00:00:00Z"])
        command = [sys.executable, "-I", str(helper), "--uv", uv]
        default = subprocess.run([*command, "--", *arguments], **options)
        assert default.returncode != 0 and "library identity" in default.stderr
        assert downloads == []
        result = subprocess.run([*command, "--registry-only", "--", *arguments], **options)
        assert "unrecognized arguments" not in result.stderr
        if case == "accepted":
            assert result.returncode == 0, result.stdout + result.stderr
            installed = subprocess.run(
                [str(python), "-I", "-c", "import fixture_validator; print(fixture_validator.MARKER)"],
                **options,
            )
            assert installed.returncode == 0
            assert installed.stdout.strip() == "installed-from-registry"
            assert downloads
        else:
            assert result.returncode != 0, result.stdout + result.stderr
            if case == "wrong-hash":
                assert "hash" in result.stderr.lower(), result.stdout + result.stderr
            else:
                assert downloads == []
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
