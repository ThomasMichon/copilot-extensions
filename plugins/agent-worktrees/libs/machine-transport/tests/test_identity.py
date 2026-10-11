from __future__ import annotations

from types import SimpleNamespace
import pytest

from machine_transport.identity import is_local_machine
from machine_transport.registry import MachineEntry


def _entries(**kw):
    return {"box-a": MachineEntry(key="box-a", display_name="Box A", **kw)}


def _loader(entries):
    return lambda: entries


class TestIsLocalMachine:
    @pytest.mark.parametrize("configured", ["", "unregistered"])
    def test_consumer_identity_without_optional_scope_fields(self, configured):
        entry = SimpleNamespace(
            key="legacy-box", alias="", hostname="local-host", display_name="Legacy",
        )
        assert is_local_machine(
            entry.key, config_machine=configured,
            load_entries=lambda: {entry.key: entry}, real_hostname=entry.hostname,
        )

    def test_exact_match_against_config_machine(self):
        assert is_local_machine(
            "aurora-cloud2", config_machine="aurora-cloud2",
            load_entries=_loader({}),
        ) is True

    def test_case_insensitive_direct_match(self):
        assert is_local_machine(
            "AURORA-CLOUD2", config_machine="aurora-cloud2",
            load_entries=_loader({}),
        ) is True

    def test_direct_match_never_calls_load_entries(self):
        def _boom():
            raise AssertionError("load_entries must not be called")

        assert is_local_machine(
            "aurora-cloud2", config_machine="aurora-cloud2", load_entries=_boom,
        ) is True

    def test_alias_vs_key_both_resolve_to_this_machine(self):
        # config_machine is the alias; a caller passing the registry KEY for
        # the very same entry must still be recognized as local.
        entries = _entries(alias="aurora-cloud2")
        assert is_local_machine(
            "box-a", config_machine="aurora-cloud2", load_entries=_loader(entries),
        ) is True

    def test_raw_hostname_vs_canonical_alias_both_resolve_to_this_machine(self):
        # The exact bug class this helper exists to fix: a stale
        # claim/codename recorded under the raw COMPUTERNAME before the
        # hostname-field decoupling landed, compared against the current
        # canonical alias -- a bare string `==` would wrongly call this
        # remote and SSH-loopback to the very machine running the check.
        entries = _entries(alias="aurora-cloud2", hostname="CPC-FAKE-HOST1")
        assert is_local_machine(
            "CPC-FAKE-HOST1", config_machine="aurora-cloud2",
            load_entries=_loader(entries), real_hostname="CPC-FAKE-HOST1",
        ) is True

    def test_devtunnel_hostname_mismatch_still_resolves_local(self):
        """A cloud/devtunnel-provisioned box whose real OS hostname differs
        entirely from its configured alias -- the exact production bug this
        library was extracted to fix generally."""
        entries = _entries(alias="aurora-cloud2")
        assert is_local_machine(
            "aurora-cloud2", config_machine="aurora-cloud2",
            load_entries=_loader(entries), real_hostname="happy-field-devtunnel",
        ) is True

    def test_genuinely_different_machine_is_not_local(self):
        entries = {
            "box-a": MachineEntry(key="box-a", display_name="A", alias="aurora-cloud2"),
            "box-b": MachineEntry(key="box-b", display_name="B", alias="aurora-cloud1"),
        }
        assert is_local_machine(
            "aurora-cloud1", config_machine="aurora-cloud2",
            load_entries=_loader(entries),
        ) is False

    def test_unknown_name_is_not_local(self):
        assert is_local_machine(
            "nonexistent-box", config_machine="aurora-cloud2",
            load_entries=_loader({}),
        ) is False

    def test_empty_name_is_not_local(self):
        assert is_local_machine(
            "", config_machine="aurora-cloud2", load_entries=_loader({}),
        ) is False

    def test_missing_registry_falls_back_gracefully(self):
        def _boom():
            raise FileNotFoundError("no registry")

        assert is_local_machine(
            "aurora-cloud2", config_machine="aurora-cloud2", load_entries=_boom,
        ) is True  # direct match never needed the registry
        assert is_local_machine(
            "some-other-box", config_machine="aurora-cloud2", load_entries=_boom,
        ) is False

    def test_malformed_registry_degrades_to_false(self):
        def _boom():
            raise ValueError("malformed machines.yaml")

        assert is_local_machine(
            "some-other-box", config_machine="aurora-cloud2", load_entries=_boom,
        ) is False

    def test_unexpected_exception_propagates(self):
        def _boom():
            raise RuntimeError("not a degrade-safe case")

        with pytest.raises(RuntimeError):
            is_local_machine(
                "some-other-box", config_machine="aurora-cloud2", load_entries=_boom,
            )

    def test_empty_config_machine_does_not_blanket_match_empty_alias(self):
        """A legitimately empty ``config_machine`` must never blanket-match
        every roster entry's own unset/empty alias -- the entry-identity
        comparison (not a string comparison against ``config_machine``)
        is what makes this safe."""
        entries = _entries()  # alias="" by default
        assert is_local_machine(
            "box-a", config_machine="", load_entries=_loader(entries),
        ) is False

    def test_empty_config_machine_with_matching_real_hostname_is_still_local(self):
        entries = _entries(hostname="real-hostname")
        assert is_local_machine(
            "box-a", config_machine="", load_entries=_loader(entries),
            real_hostname="real-hostname",
        ) is True
