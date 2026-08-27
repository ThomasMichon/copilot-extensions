from __future__ import annotations

import copy
import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft7Validator

from dropin_registry import Finding, ScanAuthority, WarningTracker

from agent_ssh import fragment_registry, ssh_profile
from agent_ssh.__main__ import main


def _sources(
    root: Path,
    transport: str,
    aliases: tuple[str, ...] = ("host-a",),
) -> tuple[Path, Path, dict, dict]:
    source_dir = root / f"source-{transport}"
    source_dir.mkdir()
    registry = source_dir / "registry.yaml"
    module_path = source_dir / "module.yaml"
    registry_data = {
        "transport": transport,
        "topology": "per-machine",
        "machines": [
            {
                "name": alias,
                "hostname": f"{alias}.example.com",
                "user": "example",
            }
            for alias in aliases
        ],
    }
    module_data = {
        "module": transport,
        "kind": "transport",
        "produces": {"ssh_profile": True, "keyed_by": "machine-name"},
        "inbound_ports": "none",
    }
    import yaml

    registry.write_text(yaml.safe_dump(registry_data), encoding="utf-8")
    module_path.write_text(yaml.safe_dump(module_data), encoding="utf-8")
    return registry, module_path, registry_data, module_data


def _managed_fragment(
    config_d: Path,
    registry: Path,
    module_path: Path,
    registry_data: dict,
    module_data: dict,
    *,
    newline: str = "\n",
) -> Path:
    config_d.mkdir(parents=True, exist_ok=True)
    path = config_d / ssh_profile.fragment_name(module_data["module"])
    content = ssh_profile.render_fragment(
        registry_data,
        module_data,
        registry_path=registry.resolve(strict=True),
        module_path=module_path.resolve(strict=True),
    )
    path.write_text(content.replace("\n", newline), encoding="utf-8", newline="")
    return path


def _no_syntax_error(_path: Path, _aliases: tuple[str, ...]) -> None:
    return None


def test_valid_managed_fragment_survives_malformed_peer_and_ignores_unrelated(
    tmp_path: Path,
) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    valid = _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    malformed = config_d / "50-agent-ssh-broken.conf"
    malformed.write_text("Host missing-managed-header\n", encoding="utf-8")
    unrelated = config_d / "90-operator.conf"
    unrelated.write_text("this is intentionally not parsed\n", encoding="utf-8")

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert list(report.entries) == [str(valid)]
    assert [finding.entry for finding in report.findings] == [str(malformed)]
    assert unrelated.read_text(encoding="utf-8") == "this is intentionally not parsed\n"


def test_missing_source_withdraws_fragment_and_blocks_its_alias(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    fragment = _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    registry.unlink()

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.blocked_aliases == frozenset({"host-a"})
    assert [(finding.entry, finding.reason) for finding in report.findings] == [
        (str(fragment), "missing-target")
    ]


def test_changed_topology_withdraws_stale_fragment(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    fragment = _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    registry.write_text(
        "transport: direct\nmachines:\n  - name: host-b\n    hostname: host-b.example.com\n",
        encoding="utf-8",
    )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.blocked_aliases == frozenset({"host-a"})
    assert report.findings[0].entry == str(fragment)
    assert report.findings[0].reason == "identity-mismatch"


def test_malformed_nested_source_does_not_abort_valid_peer(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    valid_registry, valid_module, valid_data, valid_module_data = _sources(
        tmp_path,
        "direct",
    )
    valid = _managed_fragment(
        config_d,
        valid_registry,
        valid_module,
        valid_data,
        valid_module_data,
    )
    bad_registry, bad_module, bad_data, bad_module_data = _sources(
        tmp_path,
        "tunnel",
        aliases=("host-b",),
    )
    bad = _managed_fragment(
        config_d,
        bad_registry,
        bad_module,
        bad_data,
        bad_module_data,
    )
    bad_registry.write_text(
        "transport: tunnel\nmachines:\n  - null\n",
        encoding="utf-8",
    )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert list(report.entries) == [str(valid)]
    assert [(finding.entry, finding.reason) for finding in report.findings] == [
        (str(bad), "invalid-entry")
    ]


def test_source_uncertainty_retains_only_unchanged_last_known_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    fragment = _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    state = fragment_registry.FragmentRegistry(
        config_d,
        syntax_check=_no_syntax_error,
    )
    first = state.refresh(emit_warnings=False)
    assert str(fragment) in first.entries

    original_read = fragment_registry._read_text

    def unreadable_source(path: Path) -> str:
        if path == registry.resolve(strict=True):
            raise PermissionError("temporarily unreadable")
        return original_read(path)

    monkeypatch.setattr(fragment_registry, "_read_text", unreadable_source)
    retained = state.refresh(emit_warnings=False)
    assert str(fragment) in retained.entries
    assert retained.findings[0].status == "indeterminate"

    fragment.write_text(
        fragment.read_text(encoding="utf-8") + "# changed while source is unreadable\n",
        encoding="utf-8",
    )
    rejected = state.refresh(emit_warnings=False)
    assert rejected.entries == {}
    assert rejected.blocked_aliases == frozenset({"host-a"})


def test_registry_enumeration_uncertainty_retains_last_known_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    fragment = _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    state = fragment_registry.FragmentRegistry(
        config_d,
        syntax_check=_no_syntax_error,
    )
    state.refresh(emit_warnings=False)
    original_iterdir = Path.iterdir

    def unreadable_registry(path: Path):
        if path == config_d:
            raise PermissionError("temporarily unreadable")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unreadable_registry)
    report = state.refresh(emit_warnings=False)
    assert report.snapshot.authority is ScanAuthority.INDETERMINATE
    assert list(report.entries) == [str(fragment)]
    assert report.findings[0].reason == "registry-indeterminate"


def test_filename_content_identity_mismatch_is_inactive(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    fragment = _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    mismatch = config_d / "50-agent-ssh-other.conf"
    fragment.replace(mismatch)

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.findings[0].reason == "identity-mismatch"


def test_host_claim_before_malformed_content_is_blocked(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    fragment = config_d / "50-agent-ssh-direct.conf"
    fragment.write_text(
        "# agent-ssh :: transport=direct\n\n"
        "Host host-a\n"
        "    HostName host-a.example.com\n"
        "Match all\n",
        encoding="utf-8",
    )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.blocked_aliases == frozenset({"host-a"})
    assert report.findings[0].reason == "invalid-entry"


def test_malformed_prefix_with_later_host_blocks_fresh_probes(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    fragment = config_d / "50-agent-ssh-direct.conf"
    fragment.write_text(
        "# agent-ssh :: transport=direct\n"
        "CanonicalizeHostname no\n"
        "Host host-a\n"
        "    HostName host-a.example.com\n",
        encoding="utf-8",
    )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.unscoped_blocking is True
    assert report.permits_probe("host-a") is False
    assert report.permits_probe("unrelated-alias") is False


def test_indented_global_option_before_first_host_blocks_all_probes(
    tmp_path: Path,
) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    fragment = config_d / "50-agent-ssh-direct.conf"
    fragment.write_text(
        "# agent-ssh :: transport=direct\n"
        "    ProxyCommand helper --target %h\n"
        "Host host-a\n"
        "    HostName host-a.example.com\n",
        encoding="utf-8",
    )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.unscoped_blocking is True
    assert report.permits_probe("host-a") is False
    assert report.findings[0].reason == "invalid-entry"


def test_incomplete_claims_block_alias_from_other_active_fragment(
    tmp_path: Path,
) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    active = _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    malformed = config_d / "50-agent-ssh-tunnel.conf"
    malformed.write_text(
        "# agent-ssh :: transport=tunnel\n"
        "CanonicalizeHostname no\n"
        "Host host-a\n"
        "    HostName unintended.example.com\n",
        encoding="utf-8",
    )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert list(report.entries) == [str(active)]
    assert report.active_aliases == frozenset({"host-a"})
    assert report.unscoped_blocking is True
    assert report.permits_probe("host-a") is False


def test_duplicate_host_inside_one_fragment_is_blocked(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    fragment = config_d / "50-agent-ssh-direct.conf"
    fragment.write_text(
        "# agent-ssh :: transport=direct\n\n"
        "Host host-a\n"
        "    HostName first.example.com\n"
        "Host host-a\n"
        "    HostName second.example.com\n",
        encoding="utf-8",
    )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.blocked_aliases == frozenset({"host-a"})
    assert report.findings[0].reason == "invalid-entry"


def test_symlink_managed_fragment_is_rejected(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    target = tmp_path / "target.conf"
    target.write_text("# agent-ssh :: transport=direct\n", encoding="utf-8")
    link = config_d / "50-agent-ssh-direct.conf"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.findings[0].reason == "invalid-entry"


def test_render_rejects_path_escape_include_and_host_injection() -> None:
    with pytest.raises(ValueError):
        ssh_profile.fragment_name("../outside")
    with pytest.raises(ValueError):
        ssh_profile.fragment_name("Bad_Name")
    with pytest.raises(ValueError):
        ssh_profile.render_fragment(
            {
                "machines": [
                    {
                        "name": "host-a",
                        "options": {"Include": "~/.ssh/other.conf"},
                    }
                ]
            },
            {"module": "direct"},
        )
    with pytest.raises(ValueError):
        ssh_profile.render_fragment(
            {"machines": [{"name": "host-a\nHost injected"}]},
            {"module": "direct"},
        )


def test_boolean_options_render_as_openssh_yes_no() -> None:
    rendered = ssh_profile.render_fragment(
        {
            "transport": "direct",
            "machines": [
                {
                    "name": "host-a",
                    "options": {
                        "IdentitiesOnly": True,
                        "Compression": False,
                    },
                }
            ],
        },
        {"module": "direct"},
    )
    assert "IdentitiesOnly yes" in rendered
    assert "Compression no" in rendered
    assert "True" not in rendered
    assert "False" not in rendered


def test_profile_validation_rejects_duplicate_aliases() -> None:
    with pytest.raises(ValueError, match="duplicates another Host alias"):
        ssh_profile.validate_profile_inputs(
            {
                "transport": "direct",
                "machines": [
                    {"name": "host-a"},
                    {"name": "HOST-A"},
                ],
            },
            {"module": "direct"},
            require_transport_match=True,
        )
    with pytest.raises(ValueError, match="duplicates another Host alias"):
        ssh_profile.validate_profile_inputs(
            {
                "transport": "direct",
                "topology": "jumpbox",
                "gate": {"name": "host-a"},
                "machines": [{"name": "HOST-A"}],
            },
            {"module": "direct"},
            require_transport_match=True,
        )


def test_published_schema_matches_runtime_constraints() -> None:
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "contract" / "registry-record.schema.json")
        .read_text(encoding="utf-8")
    )
    validator = Draft7Validator(schema)
    module = {"module": "direct"}
    valid = {
        "transport": "direct",
        "topology": "jumpbox",
        "gate": {
            "name": "gate-a",
            "options": {"StrictHostKeyChecking": "accept-new"},
        },
        "machines": [
            {
                "name": "host-a",
                "via": "jumpbox",
                "options": {"ServerAliveInterval": 30},
            }
        ],
    }
    assert list(validator.iter_errors(valid)) == []
    ssh_profile.validate_profile_inputs(
        valid,
        module,
        require_transport_match=True,
    )

    invalid_records = []
    wildcard_gate = copy.deepcopy(valid)
    wildcard_gate["gate"]["name"] = "*"
    invalid_records.append(wildcard_gate)
    include_option = copy.deepcopy(valid)
    include_option["machines"][0]["options"] = {"Include": "~/.ssh/other.conf"}
    invalid_records.append(include_option)
    nonscalar_option = copy.deepcopy(valid)
    nonscalar_option["machines"][0]["options"] = {"ProxyJump": ["gate-a"]}
    invalid_records.append(nonscalar_option)
    bad_transport = copy.deepcopy(valid)
    bad_transport["transport"] = "Direct"
    invalid_records.append(bad_transport)
    bad_gate_setting = copy.deepcopy(valid)
    bad_gate_setting["gate"]["strict_host_key_checking"] = 123
    invalid_records.append(bad_gate_setting)
    missing_machines = {"transport": "direct"}
    invalid_records.append(missing_machines)
    boolean_port = copy.deepcopy(valid)
    boolean_port["machines"][0]["port"] = True
    invalid_records.append(boolean_port)

    for record in invalid_records:
        assert list(validator.iter_errors(record))
        with pytest.raises(ValueError):
            ssh_profile.validate_profile_inputs(
                record,
                module,
                require_transport_match=True,
            )


def test_duplicate_host_identity_quarantines_both_fragments(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    for transport in ("direct", "tunnel"):
        registry, module_path, registry_data, module_data = _sources(
            tmp_path,
            transport,
            aliases=("shared-host",),
        )
        _managed_fragment(
            config_d,
            registry,
            module_path,
            registry_data,
            module_data,
        )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.blocked_aliases == frozenset({"shared-host"})
    assert [finding.reason for finding in report.findings] == [
        "duplicate",
        "duplicate",
    ]
    assert report.snapshot.findings == report.findings


def test_current_and_stale_host_collision_quarantines_current_alias(
    tmp_path: Path,
) -> None:
    config_d = tmp_path / "config.d"
    paths: list[Path] = []
    sources: list[Path] = []
    for transport in ("direct", "tunnel"):
        registry, module_path, registry_data, module_data = _sources(
            tmp_path,
            transport,
            aliases=("shared-host",),
        )
        paths.append(
            _managed_fragment(
                config_d,
                registry,
                module_path,
                registry_data,
                module_data,
            )
        )
        sources.append(registry)
    sources[1].unlink()

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.blocked_aliases == frozenset({"shared-host"})
    by_entry = {
        entry: {finding.reason for finding in report.findings if finding.entry == entry}
        for entry in map(str, paths)
    }
    assert by_entry[str(paths[0])] == {"duplicate"}
    assert by_entry[str(paths[1])] == {"duplicate", "missing-target"}


def test_legacy_fragment_is_active_with_advisory(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    legacy = config_d / "50-agent-ssh-direct.conf"
    legacy.write_text(
        "# agent-ssh :: transport=direct\n\n"
        "Host host-a\n"
        "    HostName host-a.example.com\n",
        encoding="utf-8",
    )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert str(legacy) in report.entries
    assert report.findings[0].reason == "legacy-unattributed"
    assert "original sources" in (report.findings[0].remedy or "")


def test_displaced_schema_metadata_is_not_downgraded_to_legacy(
    tmp_path: Path,
) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    fragment = _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    lines = fragment.read_text(encoding="utf-8").splitlines()
    fragment.write_text(
        "\n".join([lines[0], "# inserted comment", *lines[1:]]) + "\n",
        encoding="utf-8",
    )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.blocked_aliases == frozenset({"host-a"})
    assert report.findings[0].reason == "invalid-entry"
    assert "immediately follow" in (report.findings[0].detail or "")


def test_crlf_fragment_matches_posix_rendered_sources(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    fragment = _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
        newline="\r\n",
    )

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert list(report.entries) == [str(fragment)]
    assert report.findings == ()


@pytest.mark.skipif(shutil.which("ssh") is None, reason="OpenSSH client unavailable")
def test_openssh_syntax_failure_is_inactive(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    fragment = config_d / "50-agent-ssh-direct.conf"
    fragment.write_text(
        "# agent-ssh :: transport=direct\n\n"
        "Host host-a\n"
        "    DefinitelyNotAnSshOption yes\n",
        encoding="utf-8",
    )

    report = fragment_registry.scan_fragment_registry(config_d)

    assert report.entries == {}
    assert report.findings[0].reason == "invalid-entry"
    assert "OpenSSH rejected" in (report.findings[0].detail or "")


def test_replaced_source_symlink_is_rejected(tmp_path: Path) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    replacement = tmp_path / "replacement.yaml"
    replacement.write_text(registry.read_text(encoding="utf-8"), encoding="utf-8")
    registry.unlink()
    try:
        registry.symlink_to(replacement)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    report = fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )

    assert report.entries == {}
    assert report.findings[0].reason == "target-unusable"


@pytest.mark.skipif(shutil.which("ssh") is None, reason="OpenSSH client unavailable")
def test_write_fragment_validates_before_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    fragment = config_d / "50-agent-ssh-direct.conf"
    original = b"# existing valid profile\n"
    fragment.write_bytes(original)
    monkeypatch.setattr(ssh_profile, "_chmod", lambda *_args: None)

    with pytest.raises(ValueError, match="OpenSSH rejected"):
        ssh_profile.write_fragment(
            {
                "transport": "direct",
                "machines": [
                    {
                        "name": "host-a",
                        "options": {"DefinitelyNotAnSshOption": "yes"},
                    }
                ],
            },
            {"module": "direct"},
            config_d=config_d,
            ssh_config=tmp_path / "config",
        )

    assert fragment.read_bytes() == original
    assert not (config_d / "50-agent-ssh-direct.conf.tmp").exists()


def test_write_fragment_uses_unique_temporary_outside_included_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_d = tmp_path / "config.d"
    captured: list[Path] = []
    monkeypatch.setattr(ssh_profile, "_chmod", lambda *_args: None)

    def accept_temporary(path: Path, _aliases: tuple[str, ...]) -> None:
        captured.append(path)
        return None

    monkeypatch.setattr(ssh_profile, "openssh_syntax_error", accept_temporary)
    fragment = ssh_profile.write_fragment(
        {
            "transport": "direct",
            "machines": [{"name": "host-a", "hostname": "host-a.example.com"}],
        },
        {"module": "direct"},
        config_d=config_d,
        ssh_config=tmp_path / "config",
    )

    assert fragment == config_d / "50-agent-ssh-direct.conf"
    assert len(captured) == 1
    assert captured[0].parent == config_d.parent
    assert captured[0].parent != config_d
    assert captured[0].name.startswith(".agent-ssh-fragment-")
    assert not captured[0].exists()
    include = (tmp_path / "config").read_text(encoding="utf-8").splitlines()[0]
    assert include == ssh_profile._include_line(config_d)


def test_root_include_update_is_serialized_and_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ssh_config = tmp_path / "config"
    ssh_config.write_text(
        "Host existing\n    HostName existing.example.com\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ssh_profile, "_chmod", lambda *_args: None)
    barrier = threading.Barrier(2)

    def update() -> bool:
        barrier.wait()
        return ssh_profile.ensure_root_include(ssh_config)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: update(), range(2)))

    content = ssh_config.read_text(encoding="utf-8")
    assert sorted(results) == [False, True]
    assert content.count(ssh_profile.ROOT_INCLUDE) == 1
    assert "Host existing" in content
    assert "existing.example.com" in content
    assert not list(tmp_path.glob(".agent-ssh-root-config-*.tmp"))


def test_root_include_preserves_symlinked_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "managed" / "ssh-config"
    target.parent.mkdir()
    target.write_text(
        "Host existing\n    HostName existing.example.com\n",
        encoding="utf-8",
    )
    link = tmp_path / "config"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    monkeypatch.setattr(ssh_profile, "_chmod", lambda *_args: None)

    assert ssh_profile.ensure_root_include(link) is True

    assert link.is_symlink()
    assert target.read_text(encoding="utf-8").count(ssh_profile.ROOT_INCLUDE) == 1
    assert "Host existing" in target.read_text(encoding="utf-8")


def test_emit_profile_reports_broken_root_config_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, module_path, _, _ = _sources(tmp_path, "direct")
    broken = tmp_path / "config"
    try:
        broken.symlink_to(tmp_path / "missing-config")
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    monkeypatch.setattr(ssh_profile, "_chmod", lambda *_args: None)
    monkeypatch.setattr(ssh_profile, "openssh_syntax_error", _no_syntax_error)

    rc = main(
        [
            "emit-profile",
            str(registry),
            "--module",
            str(module_path),
            "--config-d",
            str(tmp_path / "config.d"),
            "--ssh-config",
            str(broken),
        ]
    )

    assert rc == 2
    assert "symlink target does not exist" in capsys.readouterr().err


def test_emit_profile_rejects_linked_config_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, module_path, _, _ = _sources(tmp_path, "direct")
    target = tmp_path / "actual-config.d"
    target.mkdir()
    linked = tmp_path / "config.d"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    monkeypatch.setattr(ssh_profile, "_chmod", lambda *_args: None)

    rc = main(
        [
            "emit-profile",
            str(registry),
            "--module",
            str(module_path),
            "--config-d",
            str(linked),
            "--ssh-config",
            str(tmp_path / "config"),
        ]
    )

    assert rc == 2
    assert "regular non-reparse directory" in capsys.readouterr().err
    assert list(target.iterdir()) == []


def test_compatibility_emitter_rejects_transport_mismatch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import yaml

    registry = tmp_path / "registry.yaml"
    module_path = tmp_path / "module.yaml"
    registry.write_text(
        yaml.safe_dump({"transport": "other", "machines": []}),
        encoding="utf-8",
    )
    module_path.write_text(
        yaml.safe_dump({"module": "direct"}),
        encoding="utf-8",
    )

    rc = ssh_profile.main(
        [
            str(registry),
            "--module",
            str(module_path),
            "--config-d",
            str(tmp_path / "config.d"),
            "--ssh-config",
            str(tmp_path / "config"),
        ]
    )

    assert rc == 2
    assert "must match" in capsys.readouterr().err
    assert not (tmp_path / "config.d").exists()


def test_warning_cap_dedup_and_recovery(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    for index in range(3):
        (config_d / f"50-agent-ssh-bad-{index}.conf").write_text(
            "not managed content\n",
            encoding="utf-8",
        )
    state = fragment_registry.FragmentRegistry(
        config_d,
        warning_tracker=WarningTracker(limit=2, repeat_after_seconds=3600),
        syntax_check=_no_syntax_error,
    )

    state.refresh()
    first = capsys.readouterr().err
    assert first.count("[WARN] ssh-config.d:") == 2
    assert "1 additional managed-fragment finding(s) suppressed" in first

    state.refresh()
    assert capsys.readouterr().err == ""

    for path in config_d.iterdir():
        path.unlink()
    state.refresh()
    assert "3 managed-fragment finding(s) recovered" in capsys.readouterr().err


def test_warning_dedup_and_recovery_persist_across_registry_instances(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    fragment = config_d / "50-agent-ssh-broken.conf"
    fragment.write_text("broken\n", encoding="utf-8")
    state_path = tmp_path / "warning-state.json"

    fragment_registry.FragmentRegistry(
        config_d,
        warning_state_path=state_path,
        syntax_check=_no_syntax_error,
    ).refresh()
    assert "invalid-entry" in capsys.readouterr().err

    fragment_registry.FragmentRegistry(
        config_d,
        warning_state_path=state_path,
        syntax_check=_no_syntax_error,
    ).refresh()
    assert capsys.readouterr().err == ""

    fragment.unlink()
    fragment_registry.FragmentRegistry(
        config_d,
        warning_state_path=state_path,
        syntax_check=_no_syntax_error,
    ).refresh()
    assert "1 managed-fragment finding(s) recovered" in capsys.readouterr().err


def test_persistent_warning_recovery_is_fingerprint_based(tmp_path: Path) -> None:
    tracker = fragment_registry.PersistentWarningTracker(
        tmp_path / "warning-state.json",
        repeat_after_seconds=3600,
    )
    first = Finding(
        registry="ssh-config.d",
        entry="entry.conf",
        status="inactive",
        reason="invalid-entry",
    )
    remaining = Finding(
        registry="ssh-config.d",
        entry="entry.conf",
        status="advisory",
        reason="legacy-unattributed",
    )
    tracker.select([first, remaining], now=1)
    batch = tracker.select([remaining], now=2)
    assert batch.recovered == 1
    assert batch.emitted == ()


def test_warning_state_lock_failure_falls_back_in_memory(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block parent creation", encoding="utf-8")
    tracker = fragment_registry.PersistentWarningTracker(
        blocker / "warning-state.json",
        repeat_after_seconds=3600,
    )
    finding = Finding(
        registry="ssh-config.d",
        entry="entry.conf",
        status="inactive",
        reason="invalid-entry",
    )

    first = tracker.select([finding], now=1)
    second = tracker.select([finding], now=2)

    assert first.emitted == (finding,)
    assert second.emitted == ()


def test_doctor_human_json_parity_and_report_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    active = _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    fragment = config_d / "50-agent-ssh-broken.conf"
    fragment.write_text("broken\n", encoding="utf-8")
    before = fragment.read_bytes()
    monkeypatch.setattr(ssh_profile, "openssh_syntax_error", _no_syntax_error)

    assert main(["doctor", "--config-d", str(config_d), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["fix_available"] is False
    assert payload["authority"] == "complete"
    assert payload["active"][0]["entry"] == str(active)
    assert payload["active"][0]["transport"] == "direct"
    assert payload["active"][0]["aliases"] == ["host-a"]
    assert payload["active"][0]["source"]["registry"] == str(registry.resolve())
    assert payload["active"][0]["source"]["module"] == str(module_path.resolve())
    assert payload["findings"][0]["reason"] == "invalid-entry"

    assert main(["doctor", "--config-d", str(config_d)]) == 1
    human = capsys.readouterr().out
    assert "ssh-config.d is complete" in human
    assert str(active) in human
    assert "direct" in human
    assert "host-a" in human
    assert str(registry.resolve()) in human
    assert str(module_path.resolve()) in human
    assert "invalid-entry" in human
    assert "no --fix operation is available" in human
    assert fragment.read_bytes() == before


def test_verify_network_failure_does_not_reclassify_active_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_d = tmp_path / "config.d"
    registry, module_path, registry_data, module_data = _sources(tmp_path, "direct")
    _managed_fragment(
        config_d,
        registry,
        module_path,
        registry_data,
        module_data,
    )
    monkeypatch.setattr(ssh_profile, "openssh_syntax_error", _no_syntax_error)
    calls: list[list[str]] = []

    def unreachable(command: list[str], **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("agent_ssh.__main__.subprocess.run", unreachable)
    assert main(["verify", "--config-d", str(config_d), "host-a"]) == 1
    output = capsys.readouterr().out
    assert "host-a unreachable" in output
    assert "inactive managed SSH profile" not in output
    assert calls and calls[0][-2:] == ["host-a", "true"]


def test_verify_fails_closed_on_unscoped_registry_uncertainty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_d = tmp_path / "not-a-directory"
    config_d.write_text("indeterminate registry path\n", encoding="utf-8")
    monkeypatch.setenv(
        fragment_registry.WARNING_STATE_ENV,
        str(tmp_path / "warning-state.json"),
    )

    def must_not_probe(*_args, **_kwargs):
        raise AssertionError("SSH must not run under unscoped registry uncertainty")

    monkeypatch.setattr("agent_ssh.__main__.subprocess.run", must_not_probe)
    assert main(["verify", "--config-d", str(config_d), "fresh-alias"]) == 1
    captured = capsys.readouterr()
    assert "not permitted by current managed-profile evidence" in captured.out
    assert "registry-indeterminate" in captured.err


def test_absent_registry_is_authoritative_empty(tmp_path: Path) -> None:
    report = fragment_registry.scan_fragment_registry(
        tmp_path / "missing",
        syntax_check=_no_syntax_error,
    )
    assert report.snapshot.authority is ScanAuthority.ABSENT
    assert report.entries == {}
    assert report.findings == ()


def test_only_managed_namespace_is_inspected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    managed = config_d / "50-agent-ssh-broken.conf"
    managed.write_text("broken\n", encoding="utf-8")
    unrelated = config_d / "10-operator.conf"
    unrelated.write_text("Host operator\n", encoding="utf-8")
    original_lstat = Path.lstat
    inspected: list[Path] = []

    def recording_lstat(path: Path):
        inspected.append(path)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", recording_lstat)
    fragment_registry.scan_fragment_registry(
        config_d,
        syntax_check=_no_syntax_error,
    )
    assert managed in inspected
    assert unrelated not in inspected
