from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from machine_transport import registry
from machine_transport.registry import (
    MachineEntry,
    SSHEnvironment,
    find_machine_entry,
    machine_name,
    merge_machines_yaml,
    parse_machines_yaml_file,
    parse_machines_yaml,
    normalize_machine_entries,
)


def _write(tmp_path: Path, body: str, name: str = "machines.yaml") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_legacy_wsl_projection_preserves_native_and_exact_profiles():
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        "desktop": {
            "alias": "native-compat", "hostname": "generated-host",
            "ssh": {"environments": [
                {"name": "windows", "alias": "native-route", "shell": "pwsh"},
                {"name": "wsl", "alias": "guest-route", "user": "developer", "port": 2201},
            ]},
        },
    }}))
    assert set(entries) == {"desktop", "desktop-wsl"}
    native, guest = entries["desktop"], entries["desktop-wsl"]
    assert (native.execution_platform, guest.execution_platform) == ("windows", "wsl")
    assert native.physical_host == guest.physical_host == "desktop"
    assert machine_name(native) == "desktop"
    assert machine_name(guest) == "desktop-wsl"
    assert find_machine_entry(entries, "native-compat") is native
    assert find_machine_entry(entries, "native-route") is native
    assert find_machine_entry(entries, "guest-route") is guest
    assert guest.ssh_environments == [SSHEnvironment("wsl", "guest-route", user="developer", port=2201)]
    with pytest.raises(ValueError, match="does not select an execution space"):
        find_machine_entry(entries, "generated-host")
    assert find_machine_entry(entries, "generated-host", execution_platform="windows") is native
    assert find_machine_entry(entries, "generated-host", execution_platform="wsl") is guest
    assert find_machine_entry(entries, "desktop", execution_platform="wsl") is guest
    assert normalize_machine_entries(entries) == entries


def test_explicit_logical_entries_keep_nonconventional_keys_and_physical_association():
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        "native-custom": {
            "execution_platform": "windows", "physical_host": "desktop",
            "hostname": "os-name", "compatibility_aliases": ["old-native"],
            "ssh": {"environments": [{"name": "windows", "alias": "native-profile"}]},
        },
        "guest-custom": {
            "execution_platform": "wsl", "physical_host": "desktop",
            "ssh": {"environments": [{"name": "wsl", "alias": "guest-profile"}]},
        },
    }}))
    assert set(entries) == {"native-custom", "guest-custom"}
    assert find_machine_entry(entries, "old-native").key == "native-custom"
    assert find_machine_entry(entries, "guest-profile").key == "guest-custom"
    assert find_machine_entry(entries, "desktop", execution_platform="wsl").key == "guest-custom"
    assert find_machine_entry(entries, "os-name", execution_platform="wsl").key == "guest-custom"
    assert {entry.physical_host for entry in entries.values()} == {"desktop"}


@pytest.mark.parametrize("environments", [
    [{"name": "windows", "alias": "same"}, {"name": "wsl", "alias": "same"}],
    [{"name": "wsl", "alias": "one"}, {"name": "wsl", "alias": "two"}],
    [{"name": "linux", "alias": "native"}, {"name": "wsl", "alias": "guest"}],
])
def test_normalization_refuses_profile_collisions_or_ambiguous_platforms(environments):
    with pytest.raises(ValueError, match="ambiguous|unknown native platform"):
        normalize_machine_entries(parse_machines_yaml({
            "machines": {"desktop": {"ssh": {"environments": environments}}},
        }))


def test_normalization_refuses_generated_key_collision():
    raw = {"machines": {
        "desktop": {"ssh": {"environments": [{"name": "wsl", "alias": "guest"}]}},
        "desktop-wsl": {"execution_platform": "wsl", "physical_host": "other"},
    }}
    with pytest.raises(ValueError, match="logical machine key.*ambiguous"):
        normalize_machine_entries(parse_machines_yaml(raw))


def test_known_platform_still_refuses_multiple_independent_guests():
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        key: {"execution_platform": "wsl", "physical_host": "desktop"}
        for key in ("guest-one", "guest-two")
    }}))
    with pytest.raises(ValueError, match="ambiguous wsl entries"):
        find_machine_entry(entries, "desktop", execution_platform="wsl")
    assert find_machine_entry(entries, "guest-two", execution_platform="wsl").key == "guest-two"


def test_identity_suffixes_are_not_stripped_or_inferred():
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {"desktop-wsl": {}}}))
    assert entries["desktop-wsl"].execution_platform == ""
    assert find_machine_entry(entries, "desktop") is None


def test_known_platform_does_not_fall_back_to_wrong_logical_entry():
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        "guest": {"execution_platform": "wsl", "physical_host": "desktop"},
    }}))
    for selector in ("guest", "desktop"):
        with pytest.raises(ValueError, match="no windows logical entry"):
            find_machine_entry(entries, selector, execution_platform="windows")


def test_known_platform_can_select_hostname_without_optional_physical_host():
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        "guest": {"execution_platform": "wsl", "hostname": "os-host"},
    }}))
    assert find_machine_entry(entries, "os-host", execution_platform="wsl").key == "guest"
    with pytest.raises(ValueError, match="does not select an execution space"):
        find_machine_entry(entries, "os-host")


@pytest.mark.parametrize("selector", ["Example-Host", "native-profile"])
def test_contextual_guest_selection_derives_conventional_host_association(selector):
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        "example-host": {"execution_platform": "windows", "alias": "native-profile"},
        "example-host-wsl": {"execution_platform": "wsl"},
    }}))
    assert find_machine_entry(entries, selector, execution_platform="wsl").key == "example-host-wsl"
    assert find_machine_entry(entries, "example-host").key == "example-host"


def test_explicit_physical_association_overrides_conventional_guest_suffix():
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        "example-host": {"execution_platform": "windows"},
        "example-host-wsl": {"execution_platform": "wsl", "physical_host": "another-host"},
        "custom-guest": {"execution_platform": "wsl", "physical_host": "example-host"},
    }}))
    assert find_machine_entry(entries, "example-host", execution_platform="wsl").key == "custom-guest"


def test_legacy_guest_hostname_and_display_metadata_do_not_alias_native_identity():
    entries = normalize_machine_entries(parse_machines_yaml({"machines": {
        "example-host": {"hostname": "generated-host", "display_name": "Shared display"},
        "example-host-wsl": {"hostname": "generated-host", "display_name": "Shared display"},
    }}))
    assert find_machine_entry(entries, "generated-host").key == "example-host"
    assert find_machine_entry(entries, "generated-host-wsl").key == "example-host-wsl"
    with pytest.raises(ValueError, match="display label.*multiple machines"):
        find_machine_entry(entries, "Shared display", reject_ambiguous=True)
    assert not entries["example-host-wsl"].execution_platform


@pytest.fixture(params=["python", "preferred"])
def registry_yaml_loader(request, monkeypatch):
    loader = yaml.SafeLoader if request.param == "python" else getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    monkeypatch.setattr(registry, "_SafeLoader", loader)


def test_registry_prefers_the_available_safe_yaml_backend():
    assert registry._SafeLoader is getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def test_registry_import_falls_back_without_c_safe_loader(monkeypatch, tmp_path):
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    name = "machine_transport._registry_fallback_test"
    spec = importlib.util.spec_from_file_location(name, registry.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert module._SafeLoader is yaml.SafeLoader
    path = _write(tmp_path, "machines:\n  box:\n    alias: fallback\n")
    assert module.parse_machines_yaml_file(path)["box"].alias == "fallback"
    path.write_text("machines: !!python/object:builtins.dict {}", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        module.parse_machines_yaml_file(path)


@pytest.mark.usefixtures("registry_yaml_loader")
class TestParseMachinesYamlFile:
    def test_each_read_observes_replacement_and_parser_failures(self, tmp_path):
        path = _write(tmp_path, "machines:\n  box:\n    alias: before\n")
        assert parse_machines_yaml_file(path)["box"].alias == "before"
        path.write_text("machines:\n  box:\n    alias: after\n", encoding="utf-8")
        assert parse_machines_yaml_file(path)["box"].alias == "after"
        for body in ("machines: [", "machines: !!python/object:builtins.dict {}"):
            path.write_text(body, encoding="utf-8")
            with pytest.raises(yaml.YAMLError):
                parse_machines_yaml_file(path)
        path.unlink()
        with pytest.raises(FileNotFoundError):
            parse_machines_yaml_file(path)

    def test_safe_backends_preserve_aliases_merges_and_quoted_scalars(self, tmp_path):
        body = (
            "defaults: &defaults\n"
            "  description: |\n"
            "    line one\n"
            "    caf\u00e9\n"
            "  ssh:\n"
            "    ready: true\n"
            "    environments: [{name: windows, alias: route, port: 2201}]\n"
            "machines:\n"
            "  '01':\n"
            "    <<: *defaults\n"
            "    alias: !!str true\n"
        )
        expected = parse_machines_yaml(yaml.safe_load(body))
        assert parse_machines_yaml_file(_write(tmp_path, body)) == expected

    def test_parses_basic_entry(self, tmp_path):
        path = _write(tmp_path, (
            "machines:\n"
            "  box-a:\n"
            "    display_name: Box A\n"
            "    alias: box-a\n"
            "    environment: Windows 11\n"
            "    role: workstation\n"
            "    description: a test box\n"
            "    capabilities: [gpu, docker]\n"
            "    ssh:\n"
            "      ready: true\n"
            "      environments:\n"
            "        - name: windows\n"
            "          alias: box-a\n"
            "          shell: pwsh\n"
        ))
        entries = parse_machines_yaml_file(path)
        assert set(entries) == {"box-a"}
        entry = entries["box-a"]
        assert entry.key == "box-a"
        assert entry.display_name == "Box A"
        assert entry.alias == "box-a"
        assert entry.role == "workstation"
        assert entry.description == "a test box"
        assert entry.capabilities == ["gpu", "docker"]
        assert entry.ssh_ready is True
        assert entry.ssh_environments == [
            SSHEnvironment(name="windows", alias="box-a", shell="pwsh"),
        ]

    def test_missing_machines_key_raises(self, tmp_path):
        path = _write(tmp_path, "not_machines: {}\n")
        with pytest.raises(ValueError, match="missing 'machines' key"):
            parse_machines_yaml_file(path)

    def test_sequence_root_raises_value_error_not_attribute_error(self, tmp_path):
        """A non-empty YAML sequence root (e.g. a bare ``- a`` list) has no
        ``.get`` method -- it previously reached ``raw.get("machines")``
        directly and raised ``AttributeError`` instead of this function's
        own documented ``ValueError``, which a consumer like
        ``data_ssh._build_sources()`` catches to retain its local-only
        fallback."""
        path = _write(tmp_path, "- a\n- b\n")
        with pytest.raises(ValueError, match="missing 'machines' key"):
            parse_machines_yaml_file(path)

    def test_scalar_root_raises_value_error_not_attribute_error(self, tmp_path):
        path = _write(tmp_path, "just a plain string\n")
        with pytest.raises(ValueError, match="missing 'machines' key"):
            parse_machines_yaml_file(path)

    def test_null_machines_value_raises_value_error_not_attribute_error(self, tmp_path):
        """``machines: null`` previously passed the presence check and then
        crashed with ``AttributeError`` at ``.items()`` -- a consumer like
        ``data_ssh._build_sources()`` that catches ``(FileNotFoundError,
        ValueError)`` to fall back to a local-only source list would not
        have caught that, losing its fallback entirely."""
        path = _write(tmp_path, "machines: null\n")
        with pytest.raises(ValueError, match="missing 'machines' key"):
            parse_machines_yaml_file(path)

    def test_list_machines_value_raises_value_error(self, tmp_path):
        path = _write(tmp_path, "machines: []\n")
        with pytest.raises(ValueError, match="missing 'machines' key"):
            parse_machines_yaml_file(path)

    def test_non_dict_machine_value_is_skipped(self, tmp_path):
        path = _write(tmp_path, "machines:\n  box-a: null\n")
        assert parse_machines_yaml_file(path) == {}

    def test_integer_machine_key_is_coerced_to_string(self, tmp_path):
        """A bare ``123:`` machine key parses as an ``int`` in YAML; every
        downstream consumer does string-only matching (``key.lower()`` in
        ``data_ssh._build_sources()``) -- an uncoerced int key crashes it."""
        path = _write(tmp_path, "machines:\n  123: {}\n")
        entries = parse_machines_yaml_file(path)
        assert set(entries) == {"123"}
        assert entries["123"].key == "123"

    def test_non_string_identity_fields_are_coerced_to_string(self, tmp_path):
        path = _write(tmp_path, (
            "machines:\n"
            "  box-a:\n"
            "    display_name: 42\n"
            "    environment: 7\n"
            "    alias: 123\n"
            "    hostname: 456\n"
        ))
        entry = parse_machines_yaml_file(path)["box-a"]
        assert entry.display_name == "42"
        assert entry.environment == "7"
        assert entry.alias == "123"
        assert entry.hostname == "456"

    def test_description_must_be_a_string(self, tmp_path):
        path = _write(tmp_path, "machines:\n  box-a:\n    description: [1, 2]\n")
        with pytest.raises(ValueError, match="description must be a string"):
            parse_machines_yaml_file(path)

    def test_capabilities_must_be_a_list(self, tmp_path):
        path = _write(tmp_path, "machines:\n  box-a:\n    capabilities: nope\n")
        with pytest.raises(ValueError, match="capabilities must be a list"):
            parse_machines_yaml_file(path)

    def test_capabilities_must_contain_only_strings(self, tmp_path):
        path = _write(tmp_path, "machines:\n  box-a:\n    capabilities: [1]\n")
        with pytest.raises(ValueError, match="only strings"):
            parse_machines_yaml_file(path)

    def test_capabilities_dedupe_and_strip(self, tmp_path):
        path = _write(tmp_path, (
            "machines:\n  box-a:\n    capabilities: [' gpu ', 'gpu', 'docker']\n"
        ))
        entries = parse_machines_yaml_file(path)
        assert entries["box-a"].capabilities == ["gpu", "docker"]

    def test_ssh_block_must_be_a_mapping(self, tmp_path):
        path = _write(tmp_path, "machines:\n  box-a:\n    ssh: nope\n")
        with pytest.raises(ValueError, match="ssh must be a mapping"):
            parse_machines_yaml_file(path)

    def test_require_alias_false_keeps_alias_less_environment(self, tmp_path):
        """worktree-manager's historical behavior: keep an environment with
        a name but no alias (alias="") so a UI can render a disabled tab."""
        path = _write(tmp_path, (
            "machines:\n"
            "  box-a:\n"
            "    ssh:\n"
            "      environments:\n"
            "        - name: windows\n"
        ))
        entries = parse_machines_yaml_file(path, require_alias=False)
        assert entries["box-a"].ssh_environments == [
            SSHEnvironment(name="windows", alias="", shell=""),
        ]

    def test_require_alias_true_drops_alias_less_environment(self, tmp_path):
        """agent-worktrees' historical behavior: an environment missing
        either name or alias never becomes a dispatchable SSH target."""
        path = _write(tmp_path, (
            "machines:\n"
            "  box-a:\n"
            "    ssh:\n"
            "      environments:\n"
            "        - name: windows\n"
        ))
        entries = parse_machines_yaml_file(path, require_alias=True)
        assert entries["box-a"].ssh_environments == []

    def test_require_alias_true_keeps_environment_with_both_fields(self, tmp_path):
        path = _write(tmp_path, (
            "machines:\n"
            "  box-a:\n"
            "    ssh:\n"
            "      environments:\n"
            "        - name: windows\n"
            "          alias: box-a\n"
        ))
        entries = parse_machines_yaml_file(path, require_alias=True)
        assert entries["box-a"].ssh_environments == [
            SSHEnvironment(name="windows", alias="box-a", shell=""),
        ]

    def test_non_dict_environment_entry_is_skipped(self, tmp_path):
        path = _write(tmp_path, (
            "machines:\n"
            "  box-a:\n"
            "    ssh:\n"
            "      environments:\n"
            "        - windows\n"
        ))
        assert parse_machines_yaml_file(path).get("box-a").ssh_environments == []

    def test_defaults_applied_when_fields_absent(self, tmp_path):
        path = _write(tmp_path, "machines:\n  box-a: {}\n")
        entry = parse_machines_yaml_file(path)["box-a"]
        assert entry.display_name == "box-a"
        assert entry.environment == ""
        assert entry.alias == ""
        assert entry.hostname == ""
        assert entry.role == ""
        assert entry.description == ""
        assert entry.capabilities == []
        assert entry.ssh_environments == []
        assert entry.ssh_ready is False
        assert entry.copilot is True

    def test_copilot_false_is_respected(self, tmp_path):
        path = _write(tmp_path, "machines:\n  box-a:\n    copilot: false\n")
        assert parse_machines_yaml_file(path)["box-a"].copilot is False


class TestMergeMachinesYaml:
    @pytest.mark.parametrize(("legacy_platform", "canonical_platform"), [
        ("windows", "wsl"), ("", "wsl"), ("windows", ""),
    ])
    def test_rejects_scoped_casefold_collision_across_files(
        self, tmp_path, legacy_platform, canonical_platform,
    ):
        legacy = parse_machines_yaml_file(_write(
            tmp_path,
            f"machines:\n  box-a:\n    execution_platform: '{legacy_platform}'\n",
            "legacy.yaml",
        ))
        canonical = parse_machines_yaml_file(_write(
            tmp_path,
            f"machines:\n  BOX-A:\n    execution_platform: '{canonical_platform}'\n",
            "canonical.yaml",
        ))
        with pytest.raises(ValueError, match="unique without regard to case"):
            merge_machines_yaml(legacy, canonical)

    def test_legacy_only_casefold_collision_remains_compatible(self):
        legacy = {"box-a": MachineEntry(key="box-a", display_name="Legacy")}
        canonical = {"BOX-A": MachineEntry(key="BOX-A", display_name="Canonical")}
        assert set(merge_machines_yaml(legacy, canonical)) == {"box-a", "BOX-A"}

    def test_scoped_exact_key_overlay_remains_compatible(self):
        legacy = {"box-a": MachineEntry(
            key="box-a", display_name="Legacy", execution_platform="windows",
        )}
        canonical = {"box-a": MachineEntry(
            key="box-a", display_name="Canonical", execution_platform="windows",
        )}
        assert merge_machines_yaml(legacy, canonical) == canonical

    def test_scoped_merge_revalidates_legacy_only_collisions(self):
        legacy = {
            key: MachineEntry(key=key, display_name=key)
            for key in ("box-a", "BOX-A")
        }
        canonical = {"box-b": MachineEntry(
            key="box-b", display_name="B", execution_platform="windows",
        )}
        with pytest.raises(ValueError, match="unique without regard to case"):
            merge_machines_yaml(legacy, canonical)

    def test_validation_uses_final_overlay_execution_platforms(self):
        legacy = {"box-a": MachineEntry(
            key="box-a", display_name="Legacy", execution_platform="windows",
        )}
        canonical = {
            key: MachineEntry(key=key, display_name=key)
            for key in ("box-a", "BOX-A")
        }
        assert merge_machines_yaml(legacy, canonical) == canonical

    def test_canonical_wins_on_key_collision(self):
        legacy = {"box-a": MachineEntry(key="box-a", display_name="Legacy")}
        canonical = {"box-a": MachineEntry(key="box-a", display_name="Canonical")}
        merged = merge_machines_yaml(legacy, canonical)
        assert merged["box-a"].display_name == "Canonical"

    def test_disjoint_keys_are_additive(self):
        legacy = {"box-a": MachineEntry(key="box-a", display_name="A")}
        canonical = {"box-b": MachineEntry(key="box-b", display_name="B")}
        merged = merge_machines_yaml(legacy, canonical)
        assert set(merged) == {"box-a", "box-b"}

    def test_none_inputs_degrade_to_empty(self):
        assert merge_machines_yaml(None, None) == {}
        assert merge_machines_yaml({"a": MachineEntry(key="a", display_name="A")}, None)


class TestMachineName:
    def test_prefers_alias(self):
        entry = MachineEntry(key="box-a", display_name="A", alias="friendly-name")
        assert machine_name(entry) == "friendly-name"

    def test_falls_back_to_key(self):
        entry = MachineEntry(key="box-a", display_name="A")
        assert machine_name(entry) == "box-a"


class TestFindMachineEntry:
    def _entries(self):
        return {
            "box-a": MachineEntry(
                key="box-a", display_name="Box A", alias="aurora-cloud2",
                hostname="CPC-FAKE-HOST1",
            ),
        }

    def test_exact_key_match(self):
        entries = self._entries()
        assert find_machine_entry(entries, "box-a") is entries["box-a"]

    def test_case_insensitive_key_match(self):
        entries = self._entries()
        assert find_machine_entry(entries, "BOX-A") is entries["box-a"]

    def test_alias_match(self):
        entries = self._entries()
        assert find_machine_entry(entries, "aurora-cloud2") is entries["box-a"]

    def test_hostname_match(self):
        entries = self._entries()
        assert find_machine_entry(entries, "cpc-fake-host1") is entries["box-a"]

    def test_display_name_match(self):
        entries = self._entries()
        assert find_machine_entry(entries, "box a") is entries["box-a"]

    def test_unknown_name_returns_none(self):
        entries = self._entries()
        assert find_machine_entry(entries, "nonexistent-box") is None

    def test_empty_name_returns_none(self):
        entries = self._entries()
        assert find_machine_entry(entries, "") is None

    @pytest.mark.parametrize("field", ["alias", "hostname", "display_name"])
    @pytest.mark.parametrize("reverse", [False, True])
    def test_strict_case_insensitive_key_precedes_other_identity(self, field, reverse):
        other = MachineEntry(key="other", display_name="Other")
        other = replace(other, **{field: "shared"})
        pairs = [("other", other), ("shared", MachineEntry(key="shared", display_name="Key"))]
        entries = dict(reversed(pairs) if reverse else pairs)
        assert find_machine_entry(entries, "SHARED", reject_ambiguous=True) is entries["shared"]

    def test_strict_colliding_case_insensitive_keys_remain_ambiguous(self):
        entries = {
            key: MachineEntry(key=key, display_name=key)
            for key in ("Shared", "shared")
        }
        with pytest.raises(ValueError, match="ambiguous"):
            find_machine_entry(entries, "SHARED", reject_ambiguous=True)
        assert find_machine_entry(entries, "Shared", reject_ambiguous=True) is entries["Shared"]

    def test_default_first_match_compatibility_is_unchanged(self):
        entries = {
            "other": MachineEntry(key="other", display_name="Other", alias="shared"),
            "shared": MachineEntry(key="shared", display_name="Key"),
        }
        assert find_machine_entry(entries, "SHARED") is entries["other"]

    def test_scoped_lookup_handles_missing_physical_host_without_crashing(self):
        """``physical_host`` is optional for a consumer-specific execution-
        space record. A record declaring ``execution_platform`` but no
        ``physical_host`` must not raise ``AttributeError`` when a
        hostname/display-name match reaches the scoped rejection branch;
        it must instead raise the intended structured error."""
        entries = {
            "box-a": MachineEntry(
                key="box-a", display_name="Box A", execution_platform="windows",
            ),
        }
        with pytest.raises(ValueError, match="does not select an execution space"):
            find_machine_entry(entries, "box a")
