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
    def source(self, tmp_path):
        project(tmp_path, "plugins/client", "client", ["pyyaml>=6"])
        project(
            tmp_path, "services/index", "agent-index-service", ["client"],
            {"native": ["client[store]"]},
        )
        project(tmp_path, "plugins/agent-index/server", "agent-index-engine", ["client", "torch"])
        project(tmp_path, "plugins/client", "client", ["pyyaml>=6"], {"store": ["numpy>=2"]})
        return tmp_path


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
