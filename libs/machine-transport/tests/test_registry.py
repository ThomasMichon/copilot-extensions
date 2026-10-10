from __future__ import annotations

from pathlib import Path

import pytest

from machine_transport.registry import (
    MachineEntry,
    SSHEnvironment,
    find_machine_entry,
    machine_name,
    merge_machines_yaml,
    parse_machines_yaml_file,
)


def _write(tmp_path: Path, body: str, name: str = "machines.yaml") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


class TestParseMachinesYamlFile:
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
