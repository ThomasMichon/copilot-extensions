"""Collected by the already-wired install-contract suite."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bundle_lock as bundle


def project(
    root, relative, name, dependencies=(), extras=None, sources=None, dynamic=(),
    requires_python=">=3.10",
):
    path = root / relative / "pyproject.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[project]", f"name = {json.dumps(name)}", 'version = "1.0.0"',
        f"requires-python = {json.dumps(requires_python)}",
        f"dependencies = {json.dumps(list(dependencies))}",
        f"dynamic = {json.dumps(list(dynamic))}",
    ]
    if extras is not None:
        lines.append("[project.optional-dependencies]")
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in extras.items())
    if sources is not None:
        lines.append("[tool.uv.sources]")
        for key, source in sources.items():
            fields = ", ".join(f"{k} = {json.dumps(v)}" for k, v in source.items())
            lines.append(f"{key} = {{ {fields} }}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestBundleInputs:
    @pytest.fixture
    def windows_target(self):
        return {
            "implementation_name": "cpython",
            "implementation_version": "3.11.9",
            "os_name": "nt",
            "platform_machine": "AMD64",
            "platform_release": "10",
            "platform_system": "Windows",
            "platform_version": "10.0.19045",
            "python_full_version": "3.11.9",
            "platform_python_implementation": "CPython",
            "python_version": "3.11",
            "sys_platform": "win32",
        }

    @pytest.fixture
    def source(self, tmp_path):
        project(tmp_path, "plugins/client", "client", ["pyyaml>=6"])
        project(
            tmp_path, "services/index", "agent-index-service", ["client"],
            {"native": ["client[store]"]},
        )
        project(tmp_path, "plugins/agent-index/server", "agent-index-engine", ["client", "torch"])
        project(tmp_path, "plugins/client", "client", ["pyyaml>=6"], {"store": ["numpy>=2"]})
        return tmp_path


    def test_root_selection_uses_the_shared_canonical_owner(self, source, monkeypatch):
        from python_project_inventory import runtime_roots

        catalog = bundle._catalog(source)
        expected = runtime_roots(source, catalog)
        calls = []

        def select(root, selected):
            assert root == source and selected is catalog
            calls.append(True)
            return expected

        monkeypatch.setattr(bundle, "runtime_roots", select, raising=False)
        assert bundle._roots(source, catalog) == expected
        assert calls == [True]


    def test_missing_environment_roots_still_refuse_after_shared_selection(self, source, monkeypatch):
        monkeypatch.setattr(
            bundle, "runtime_roots",
            lambda root, catalog: {"core": [("client", "", frozenset())], "index-service": [], "index-engine": []},
            raising=False,
        )
        with pytest.raises(bundle.InputError, match="all three runtime environment roots"):
            bundle._roots(source, bundle._catalog(source))


    def test_generation_is_deterministic_and_third_party_only(self, source, monkeypatch):
        first = bundle.render(source)
        inventory = bundle.manifests(source)
        monkeypatch.setattr(bundle, "manifests", lambda _: list(reversed(inventory)))
        assert bundle.render(source) == first
        data = tomllib.loads(first)
        assert data["tool"]["uv"]["package"] is False
        assert data["tool"]["uv"]["exclude-newer"] == f"{bundle.DEFAULT_MIN_AGE_DAYS} days"
        from datetime import datetime, timezone
        from dependency_age import check_uv_policy

        check_uv_policy(data, datetime(2026, 10, 16, tzinfo=timezone.utc))
        assert data["dependency-groups"] == {
            "core": ["pyyaml>=6"],
            "index-service": ["numpy>=2", "pyyaml>=6"],
            "index-engine": ["pyyaml>=6", "torch"],
        }
        assert "sources" not in data["tool"]["uv"]
        assert all(
            Requirement(value).name not in {"client", "agent-index-service", "agent-index-engine"}
            for values in data["dependency-groups"].values() for value in values
        )


    def test_transitive_extras_retain_all_platform_conditions(self, source):
        project(
            source, "plugins/client", "client",
            ['shared[http]>=1; sys_platform == "linux"'], {"store": ["numpy>=2"]},
            {"shared": {"path": "../../libs/shared", "editable": True}},
        )
        project(
            source, "libs/shared", "shared", ['attrs>=20; python_version < "3.12"'],
            {"http": ["httpx[http2]>=0.27", "shared[http]"]},
        )
        requirements = [Requirement(value) for value in bundle.collect_inputs(source)[0]["core"]]
        assert {requirement.name for requirement in requirements} == {"attrs", "httpx"}
        for requirement in requirements:
            assert requirement.marker is not None
            assert not requirement.marker.evaluate({"sys_platform": "win32", "python_version": "3.11"})
            assert requirement.marker.evaluate({"sys_platform": "linux", "python_version": "3.11"})
        attrs = next(requirement for requirement in requirements if requirement.name == "attrs")
        assert not attrs.marker.evaluate({"sys_platform": "linux", "python_version": "3.12"})
        assert next(r for r in requirements if r.name == "httpx").extras == {"http2"}


    def test_explicit_optional_core_dependencies_keep_arm64_degradation(self, source):
        project(
            source, "plugins/agent-containers", "agent-containers", [],
            {"provider-exec": ["paramiko>=4"]},
        )
        project(source, "plugins/agent-dispatch", "agent-dispatch", [], {"mcp": ["mcp>=2,<3"]})
        requirements = [Requirement(value) for value in bundle.collect_inputs(source)[0]["core"]]
        for requirement in requirements:
            if requirement.name not in {"mcp", "paramiko"}:
                continue
            assert not requirement.marker.evaluate({"sys_platform": "win32", "platform_machine": "ARM64"})
            assert requirement.marker.evaluate({"sys_platform": "win32", "platform_machine": "AMD64"})
            assert requirement.marker.evaluate({"sys_platform": "linux", "platform_machine": "aarch64"})
        assert {r.name for r in requirements} >= {"mcp", "paramiko"}


    @pytest.mark.parametrize("dependency", [
        "httpx @ https://example.test/pkg.whl",
        "httpx @ git+https://example.test/pkg",
        "httpx @ file:///tmp/pkg.whl",
        'httpx; extra == "http"',
    ])
    def test_unsupported_dependency_never_becomes_a_partial_input(self, source, dependency):
        project(source, "plugins/client", "client", [dependency], {"store": []})
        with pytest.raises(bundle.InputError):
            bundle.render(source)


    def test_unknown_first_party_extra_is_not_silently_stripped(self, source):
        project(source, "plugins/consumer", "consumer", ["client[missing]"])
        with pytest.raises(bundle.InputError, match="unknown or ambiguous extra missing"):
            bundle.render(source)


    def test_duplicate_normalized_first_party_identity_refuses(self, source):
        project(source, "libs/duplicate", "CLIENT")
        with pytest.raises(bundle.InputError, match="ambiguous first-party distribution"):
            bundle.render(source)


    @pytest.mark.parametrize("dynamic", ["dependencies", "optional-dependencies", "requires-python"])
    def test_dynamic_runtime_metadata_refuses(self, source, dynamic):
        project(source, "plugins/other", "other")
        path = project(
            source, "plugins/client", "client", [], {"store": []}, dynamic=[dynamic],
        )
        if dynamic == "requires-python":
            path.write_text(
                path.read_text(encoding="utf-8").replace('requires-python = ">=3.10"\n', ""),
                encoding="utf-8",
            )
        with pytest.raises(bundle.InputError, match="dynamic runtime"):
            bundle.render(source)


    @pytest.mark.parametrize("malformed", [
        "missing-project", "missing-name", "invalid-name", "dynamic-string",
    ])
    def test_malformed_metadata_cannot_turn_first_party_into_registry_dependencies(
        self, source, malformed,
    ):
        project(source, "plugins/other", "other")
        path = source / "plugins/client/pyproject.toml"
        text = path.read_text(encoding="utf-8")
        if malformed == "missing-project":
            text = "[tool.uv]\npackage = false\n"
        elif malformed == "missing-name":
            text = text.replace('name = "client"\n', "")
        elif malformed == "invalid-name":
            text = text.replace('name = "client"', 'name = "client-"')
        else:
            text = text.replace("dynamic = []", 'dynamic = "dependencies"')
        path.write_text(text, encoding="utf-8")
        with pytest.raises(bundle.InputError):
            bundle.render(source)


    def test_malformed_editable_flag_is_not_silently_stripped(self, source):
        project(source, "libs/shared", "shared")
        project(
            source, "plugins/client", "client", ["shared"], {"store": []},
            {"shared": {"path": "../../libs/shared", "editable": "true"}},
        )
        with pytest.raises(bundle.InputError):
            bundle.render(source)


    def test_selected_vendored_metadata_must_match_canonical_before_stripping(self, source):
        project(source, "libs/shared", "shared", ["attrs>=1"])
        project(source, "plugins/client/libs/shared", "shared", ["attrs>=2"])
        project(
            source, "plugins/client", "client", ["shared"], {"store": []},
            {"shared": {"path": "libs/shared"}},
        )
        with pytest.raises(bundle.InputError, match="runtime metadata differs"):
            bundle.render(source)


    @pytest.mark.parametrize("nested", ["different-leaf", "remote-source"])
    def test_selected_nested_source_graph_is_validated(self, source, nested):
        project(source, "libs/shared", "shared", ["leaf"])
        project(source, "libs/leaf", "leaf", ["attrs>=1"])
        mapping = (
            {"leaf": {"path": "../leaf"}} if nested == "different-leaf"
            else {"leaf": {"url": "https://example.test/leaf.whl"}}
        )
        project(source, "plugins/client/libs/shared", "shared", ["leaf"], sources=mapping)
        project(source, "plugins/client/libs/leaf", "leaf", ["attrs>=2"])
        project(
            source, "plugins/client", "client", ["shared"], {"store": []},
            {"shared": {"path": "libs/shared"}},
        )
        with pytest.raises(bundle.InputError):
            bundle.render(source)


    def test_selected_source_cycles_terminate_without_losing_dependencies(self, source):
        for prefix in ("libs", "plugins/client/libs"):
            project(
                source, f"{prefix}/shared", "shared", ["leaf"],
                sources={"leaf": {"path": "../leaf"}},
            )
            project(
                source, f"{prefix}/leaf", "leaf", ["shared", "attrs>=1"],
                sources={"shared": {"path": "../shared"}},
            )
        project(
            source, "plugins/client", "client", ["shared"], {"store": []},
            {"shared": {"path": "libs/shared"}},
        )
        assert bundle.collect_inputs(source)[0]["core"] == ["attrs>=1"]


    def test_runtime_target_ranges_are_separate_from_the_tooling_floor(self, source):
        project(source, "plugins/new-core", "new-core", requires_python=">=3.11")
        data = tomllib.loads(bundle.render(source))
        from packaging.specifiers import SpecifierSet

        assert SpecifierSet(data["project"]["requires-python"]).contains("3.10")
        groups = data["tool"]["uv"]["dependency-groups"]
        assert not SpecifierSet(groups["core"]["requires-python"]).contains("3.10")
        assert SpecifierSet(groups["index-service"]["requires-python"]).contains("3.10")
        assert SpecifierSet(groups["index-engine"]["requires-python"]).contains("3.10")


    def test_selected_source_python_range_cannot_be_replaced_silently(self, source):
        project(source, "libs/shared", "shared", ["attrs>=1"], requires_python=">=3.11")
        project(source, "plugins/client/libs/shared", "shared", ["attrs>=1"])
        project(
            source, "plugins/client", "client", ["shared"], {"store": []},
            {"shared": {"path": "libs/shared"}},
        )
        with pytest.raises(bundle.InputError, match="runtime metadata differs"):
            bundle.render(source)


    @pytest.mark.parametrize("condition", [
        'python_version >= "3.11"', 'sys_platform == "linux"',
    ])
    def test_conditional_first_party_python_range_cannot_narrow_the_whole_group(
        self, source, condition,
    ):
        project(
            source, "plugins/client", "client", [f"shared; {condition}"], {"store": []},
        )
        project(source, "libs/shared", "shared", ["attrs>=1"], requires_python=">=3.11")
        with pytest.raises(bundle.InputError, match="conditional.*Python range"):
            bundle.render(source)


    def test_conditional_redundant_python_range_preserves_group_targets(self, source):
        project(
            source, "plugins/client", "client", ['shared; python_version >= "3.11"'],
            {"store": []},
        )
        project(source, "libs/shared", "shared", ["attrs>=1"])
        groups, ranges = bundle.collect_inputs(source)
        from packaging.specifiers import SpecifierSet

        assert SpecifierSet(ranges["core"]).contains("3.10")
        attrs = Requirement(groups["core"][0])
        assert attrs.name == "attrs"
        assert not attrs.marker.evaluate({"python_version": "3.10"})
        assert attrs.marker.evaluate({"python_version": "3.11"})


    @pytest.mark.parametrize("mapping", [
        {"git": "https://example.test/shared"},
        {"url": "https://example.test/shared.whl"},
        {"index": "private"},
    ])
    def test_source_choices_cannot_silently_lose_identity(self, source, mapping):
        project(source, "plugins/client", "client", ["shared"], {"store": []}, {"shared": mapping})
        with pytest.raises(bundle.InputError, match="attributed first-party path"):
            bundle.render(source)


    def test_wrong_local_source_name_refuses(self, source):
        project(source, "libs/shared", "different")
        project(
            source, "plugins/client", "client", ["shared"], {"store": []},
            {"shared": {"path": "../../libs/shared"}},
        )
        with pytest.raises(bundle.InputError, match="source distribution identity differs"):
            bundle.render(source)


    def test_uninventoried_local_source_cannot_become_external(self, source):
        project(source, "unregistered/nested", "shared")
        project(
            source, "plugins/client", "client", ["shared"], {"store": []},
            {"shared": {"path": "../../unregistered/nested"}},
        )
        with pytest.raises(bundle.InputError, match="absent from canonical inventory"):
            bundle.render(source)


    def test_runtime_root_disappearance_refuses_incomplete_groups(self, source):
        (source / "plugins/agent-index/server/pyproject.toml").unlink()
        with pytest.raises(bundle.InputError, match="all three runtime environment roots"):
            bundle.render(source)


    def test_cli_generate_check_and_drift_are_real_consumer_contracts(self, source):
        script = Path(bundle.__file__).resolve()
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

        def run(operation):
            return subprocess.run(
                [sys.executable, str(script), operation, "--root", str(source)],
                capture_output=True, text=True, timeout=15, **kwargs,
            )

        generated = run("generate")
        assert generated.returncode == 0, generated.stderr
        output = source / bundle.OUTPUT
        before = output.read_bytes()
        assert run("check").returncode == 0
        project(source, "plugins/client", "client", ["pyyaml>=7"], {"store": ["numpy>=2"]})
        stale = run("check")
        assert stale.returncode == 1
        assert "inputs are stale" in stale.stderr
        assert output.read_bytes() == before
        assert run("generate").returncode == 0
        assert output.read_bytes() != before
        assert run("check").returncode == 0


    def test_generation_never_invokes_a_resolver_or_subprocess(self, source, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda *_a, **_k: pytest.fail("input generation reached a subprocess"),
        )
        assert bundle.render(source)


    def test_selected_core_does_not_require_unselected_environment_roots(self, source):
        (source / "services/index/pyproject.toml").unlink()
        (source / "plugins/agent-index/server/pyproject.toml").unlink()
        assert bundle.collect_inputs(source, groups=("core",)) == (
            {"core": ["pyyaml>=6"]}, {"core": ">=3.10"},
        )
        with pytest.raises(bundle.InputError, match="all three runtime environment roots"):
            bundle.render(source)


    @pytest.mark.parametrize("groups", [(), ("missing",), ("core", "core"), "core"])
    def test_selected_groups_are_nonempty_known_and_unambiguous(self, source, groups):
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=groups)


    def test_selected_group_itself_still_needs_canonical_roots(self, source):
        (source / "plugins/agent-index/server/pyproject.toml").unlink()
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("index-engine",))


    def test_selected_groups_without_target_preserve_markers(self, source):
        project(
            source, "plugins/client", "client",
            ['attrs>=20; sys_platform == "linux"'], {"store": []},
        )
        groups, _ = bundle.collect_inputs(source, groups=("core",))
        requirement = Requirement(groups["core"][0])
        assert requirement.name == "attrs"
        assert requirement.marker is not None
        assert not requirement.marker.evaluate({"sys_platform": "win32"})
        assert requirement.marker.evaluate({"sys_platform": "linux"})


    def test_default_and_cli_still_refuse_linux_local_wheel(self, source):
        project(
            source, "plugins/client", "client",
            ['dbus-next==0.2.3; sys_platform == "linux"'], {"store": []},
            {"dbus-next": {"path": "vendor/dbus_next-0.2.3-py3-none-any.whl"}},
        )
        with pytest.raises((bundle.InputError, OSError)):
            bundle.collect_inputs(source)
        with pytest.raises((bundle.InputError, OSError)):
            bundle.render(source)
        output = source / bundle.OUTPUT
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("unchanged candidate\n", encoding="utf-8")
        before = output.read_bytes()
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        for operation in ("generate", "check"):
            result = subprocess.run(
                [sys.executable, str(Path(bundle.__file__).resolve()), operation, "--root", str(source)],
                capture_output=True, text=True, timeout=15, **kwargs,
            )
            assert result.returncode == 1
            assert "Bundle inputs refused:" in result.stderr
            assert output.read_bytes() == before


    def test_target_omits_only_inactive_external_source_edges(self, source, windows_target):
        project(
            source, "plugins/client", "client",
            ["pyyaml>=6", 'dbus-next==0.2.3; sys_platform == "linux"'],
            {"store": []},
            {"dbus-next": {"path": "vendor/dbus_next-0.2.3-py3-none-any.whl"}},
        )
        assert bundle.collect_inputs(
            source, groups=("core",), target_environment=windows_target,
        ) == ({"core": ["pyyaml>=6"]}, {"core": ">=3.10"})
        linux = {
            **windows_target, "os_name": "posix", "sys_platform": "linux",
            "platform_system": "Linux",
        }
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=linux)


    def test_selected_core_without_target_cannot_discard_linux_source(self, source):
        project(
            source, "plugins/client", "client",
            ['dbus-next==0.2.3; sys_platform == "linux"'], {"store": []},
            {"dbus-next": {"path": "vendor/dbus_next-0.2.3-py3-none-any.whl"}},
        )
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",))


    def test_target_alone_retains_default_all_three_group_selection(self, source, windows_target):
        groups, _ = bundle.collect_inputs(source, target_environment=windows_target)
        assert set(groups) == set(bundle.GROUPS)
        assert groups["index-service"] == ["numpy>=2", "pyyaml>=6"]
        assert groups["index-engine"] == ["pyyaml>=6", "torch"]


    @pytest.mark.parametrize("change", [
        {"python_version": None},
        {"python_version": "3.12"},
        {"python_full_version": "not-a-version"},
        {"implementation_version": "not-a-version"},
        {"platform_machine": ""},
        {"os_name": "posix"},
        {"platform_system": "Linux"},
        {"sys_platform": "unknown"},
        {"implementation_name": "pypy"},
        {"extra": "store"},
    ])
    def test_target_malformed_or_inconsistent_context_refuses(
        self, source, windows_target, change,
    ):
        with pytest.raises(bundle.InputError):
            bundle.render(
                source, groups=("core",),
                target_environment={**windows_target, **change},
            )


    def test_target_must_be_complete_without_host_defaults(self, source, windows_target, monkeypatch):
        import packaging.markers

        monkeypatch.setattr(
            packaging.markers, "default_environment",
            lambda: pytest.fail("projection implicitly read host marker values"),
        )
        for missing in windows_target:
            incomplete = {key: value for key, value in windows_target.items() if key != missing}
            with pytest.raises(bundle.InputError):
                bundle.render(source, groups=("core",), target_environment=incomplete)


    def test_target_closes_first_party_extras_and_inherited_markers(self, source, windows_target):
        project(
            source, "plugins/client", "client",
            [
                'shared[http]; sys_platform == "win32"',
                'unavailable; sys_platform == "linux"',
            ],
            {"store": []},
            {
                "shared": {"path": "../../libs/shared"},
                "unavailable": {"git": "https://example.test/unavailable"},
            },
        )
        project(
            source, "libs/shared", "shared",
            ['attrs>=20; python_version < "3.12"'],
            {"http": ['httpx[http2]>=0.27; os_name == "nt"', 'unused; os_name == "posix"']},
        )
        groups, _ = bundle.collect_inputs(
            source, groups=("core",), target_environment=windows_target,
        )
        requirements = [Requirement(value) for value in groups["core"]]
        assert {requirement.name for requirement in requirements} == {"attrs", "httpx"}
        for requirement in requirements:
            assert requirement.marker is not None
            assert requirement.marker.evaluate(windows_target)
            assert not requirement.marker.evaluate({**windows_target, "sys_platform": "linux"})
        assert next(requirement for requirement in requirements if requirement.name == "httpx").extras == {"http2"}


    def test_target_applies_canonical_optional_root_conditions(self, source, windows_target):
        project(
            source, "plugins/agent-containers", "agent-containers", [],
            {"provider-exec": ["paramiko>=4"]},
        )
        project(source, "plugins/agent-dispatch", "agent-dispatch", [], {"mcp": ["mcp>=2,<3"]})
        for machine, expected in (("AMD64", {"pyyaml", "paramiko", "mcp"}), ("ARM64", {"pyyaml"})):
            groups, _ = bundle.collect_inputs(
                source, groups=("core",),
                target_environment={**windows_target, "platform_machine": machine},
            )
            assert {Requirement(value).name for value in groups["core"]} == expected


    def test_target_python_compatibility_checks_only_active_closure(self, source, windows_target):
        project(
            source, "plugins/client", "client",
            ['shared; sys_platform == "linux"'], {"store": []},
        )
        project(source, "libs/shared", "shared", ["attrs>=1"], requires_python=">=3.12")
        groups, _ = bundle.collect_inputs(
            source, groups=("core",), target_environment=windows_target,
        )
        assert groups == {"core": []}
        project(
            source, "plugins/client", "client",
            ['shared; sys_platform == "win32"'], {"store": []},
        )
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    def test_target_allows_compatible_conditional_python_intersection(self, source, windows_target):
        project(
            source, "plugins/client", "client",
            ['shared; sys_platform == "win32"'], {"store": []},
        )
        project(source, "libs/shared", "shared", ["attrs>=1"], requires_python=">=3.11")
        groups, ranges = bundle.collect_inputs(
            source, groups=("core",), target_environment=windows_target,
        )
        assert {Requirement(value).name for value in groups["core"]} == {"attrs"}
        from packaging.specifiers import SpecifierSet

        assert SpecifierSet(ranges["core"]).contains(windows_target["python_full_version"])
        assert not SpecifierSet(ranges["core"]).contains("3.10")


    def test_target_prerelease_python_uses_requires_python_compatibility(
        self, source, windows_target, monkeypatch,
    ):
        from packaging.specifiers import SpecifierSet

        contains = SpecifierSet.contains

        def explicit_compatibility(specifiers, item, prereleases=None, **kwargs):
            assert prereleases is True
            return contains(specifiers, item, prereleases=prereleases, **kwargs)

        monkeypatch.setattr(SpecifierSet, "contains", explicit_compatibility)
        target = {
            **windows_target, "python_version": "3.12",
            "python_full_version": "3.12.0rc1", "implementation_version": "3.12.0rc1",
        }
        assert bundle.collect_inputs(
            source, groups=("core",), target_environment=target,
        ) == ({"core": ["pyyaml>=6"]}, {"core": ">=3.10"})
        project(
            source, "plugins/client", "client", ["pyyaml>=6"], {"store": []},
            requires_python=">=3.13",
        )
        with pytest.raises(bundle.InputError, match="outside the runtime range"):
            bundle.render(source, groups=("core",), target_environment=target)


    @pytest.mark.parametrize("dependency", [
        "httpx @ https://example.test/pkg.whl",
        "httpx @ git+https://example.test/pkg",
        'httpx; extra == "http"',
        'httpx; "http" in extras',
        'httpx; "core" in dependency_groups',
        "client[missing]",
    ])
    def test_target_preserves_active_reference_and_context_refusals(
        self, source, windows_target, dependency,
    ):
        project(source, "plugins/client", "client", [dependency], {"store": []})
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    @pytest.mark.parametrize("mapping", [
        {"path": "vendor/shared.whl"},
        {"git": "https://example.test/shared"},
        {"url": "https://example.test/shared.whl"},
        {"index": "private"},
    ])
    def test_target_active_external_sources_never_become_registry_requirements(
        self, source, windows_target, mapping,
    ):
        project(source, "plugins/client", "client", ["shared"], {"store": []}, {"shared": mapping})
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    def test_target_selected_first_party_source_metadata_still_must_match(
        self, source, windows_target,
    ):
        project(source, "libs/shared", "shared", ["attrs>=1"])
        project(source, "plugins/client/libs/shared", "shared", ["attrs>=2"])
        project(
            source, "plugins/client", "client", ["shared"], {"store": []},
            {"shared": {"path": "libs/shared"}},
        )
        with pytest.raises(bundle.InputError, match="runtime metadata differs"):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    @pytest.mark.parametrize("settings", [
        '[tool.uv]\nexclude-newer = "14 days"\n',
        '[tool.uv]\nexclude-newer = "1 day"\n',
        '[tool.uv]\nconstraint-dependencies = ["attrs<25"]\n',
        '[tool.uv]\noverride-dependencies = ["attrs==24.0"]\n',
        '[tool.uv]\nindex-url = "https://example.test/simple"\n',
        '[[tool.uv.index]]\nname = "private"\nurl = "https://example.test/simple"\n',
    ])
    def test_target_unrepresentable_project_resolver_settings_refuse(
        self, source, windows_target, settings,
    ):
        path = source / "plugins/client/pyproject.toml"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(settings)
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    def test_target_unrepresentable_repository_uv_configuration_refuses(
        self, source, windows_target,
    ):
        (source / "uv.toml").write_text(
            'override-dependencies = ["pyyaml==6.0"]\n', encoding="utf-8",
        )
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    def test_target_unrepresentable_ancestor_pyproject_settings_refuse(
        self, source, windows_target,
    ):
        (source / "pyproject.toml").write_text(
            '[tool.uv]\noverride-dependencies = ["pyyaml==6.0"]\n', encoding="utf-8",
        )
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    def test_target_selected_source_resolver_drift_cannot_be_stripped(self, source, windows_target):
        project(source, "libs/shared", "shared", ["attrs>=1"])
        selected = project(source, "plugins/client/libs/shared", "shared", ["attrs>=1"])
        with selected.open("a", encoding="utf-8") as stream:
            stream.write('[tool.uv]\noverride-dependencies = ["attrs==24.0"]\n')
        project(
            source, "plugins/client", "client", ["shared"], {"store": []},
            {"shared": {"path": "libs/shared"}},
        )
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    def test_target_transitive_external_source_override_cannot_be_stripped(
        self, source, windows_target,
    ):
        project(source, "libs/shared", "shared", ["attrs>=1"])
        project(
            source, "plugins/client", "client", ["shared"], {"store": []},
            {
                "shared": {"path": "../../libs/shared"},
                "attrs": {"index": "private"},
            },
        )
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    @pytest.mark.parametrize("mapping", [
        {"index": "private"},
        {"url": "https://example.test/urllib3.whl"},
        {"git": "https://example.test/urllib3"},
        {"path": "vendor/urllib3.whl"},
    ])
    def test_target_undeclared_external_source_is_not_proven_inactive(
        self, source, windows_target, mapping,
    ):
        project(
            source, "plugins/client", "client", ["requests>=2"], {"store": []},
            {"urllib3": mapping},
        )
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    def test_target_inactive_source_proof_covers_every_declared_extra(
        self, source, windows_target,
    ):
        project(
            source, "plugins/client", "client",
            ['dbus-next==0.2.3; sys_platform == "linux"'],
            {"store": ["dbus-next==0.2.3"]},
            {"dbus-next": {"path": "vendor/dbus_next-0.2.3-py3-none-any.whl"}},
        )
        with pytest.raises(bundle.InputError):
            bundle.render(source, groups=("core",), target_environment=windows_target)


    def test_target_render_attributes_complete_context_without_approval(
        self, source, windows_target, monkeypatch,
    ):
        monkeypatch.setattr(
            subprocess, "run", lambda *_a, **_k: pytest.fail("projection reached a subprocess"),
        )
        before = dict(windows_target)
        text = bundle.render(source, groups=("core",), target_environment=windows_target)
        data = tomllib.loads(text)
        assert "candidate inputs, not an approved lock" in text
        assert data["tool"]["bundle-inputs"]["target-environment"] == before
        assert set(data["dependency-groups"]) == {"core"}
        assert windows_target == before
        assert bundle.render(
            source, groups=("core",),
            target_environment=dict(reversed(list(windows_target.items()))),
        ) == text
        assert not (source / bundle.OUTPUT).exists()


    def test_target_real_tree_windows_core_is_an_inert_candidate(self, windows_target):
        root = Path(bundle.__file__).resolve().parents[1]
        text = bundle.render(root, groups=("core",), target_environment=windows_target)
        data = tomllib.loads(text)
        assert set(data["dependency-groups"]) == {"core"}
        assert data["tool"]["bundle-inputs"]["target-environment"] == windows_target
        requirements = [Requirement(value) for value in data["dependency-groups"]["core"]]
        names = {requirement.name for requirement in requirements}
        assert "pyyaml" in names
        assert "dbus-next" not in names
        assert "torch" not in names
        assert not names & bundle._catalog(root).keys()
