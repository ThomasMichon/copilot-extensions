"""Contract tests for deterministic aggregate policy resolution."""

from __future__ import annotations

import json
from pathlib import Path

from agent_logger.aggregate import (
    Admission,
    AggregateInputs,
    CheckoutDescriptor,
    Claim,
    DiscoveredDeclaration,
    DiscoveryFailure,
    ExecutionMode,
    FileSystemAggregateInputProvider,
    MachineIdentity,
    MachineSelector,
    OwnershipDimension,
    Provenance,
    RepositoryDeclaration,
    RepositoryPolicy,
    ResourceBinding,
    SourceSet,
    canonical_repository_identity,
    compile_aggregate,
)

MACHINE = MachineIdentity(name="worker-1", platform="windows", role="developer")


def _declaration(
    tmp_path: Path,
    repository: str,
    *claims: Claim,
    checkout_name: str | None = None,
    machine_policies: tuple[RepositoryPolicy, ...] = (),
) -> tuple[Admission, DiscoveredDeclaration]:
    identity = canonical_repository_identity(repository)
    checkout = (tmp_path / (checkout_name or identity.rsplit("/", 1)[-1])).resolve()
    path = checkout / ".copilot-extensions" / "agent-logger" / "config.yaml"
    declaration = RepositoryDeclaration(
        schema_version=1,
        repository=identity,
        provenance=Provenance(path=str(path), repository=identity),
        default_policy=RepositoryPolicy(policy_id="default", claims=tuple(claims)),
        machine_policies=machine_policies,
    )
    admission = Admission(
        repository=identity,
        authoritative_checkout=checkout,
        collection_targets={
            "corpus": ResourceBinding("filesystem", f"/stores/{identity}/sessions")
        },
        rendered_sinks={"logs": ResourceBinding("repository", f"{identity}:logs")},
    )
    return admission, DiscoveredDeclaration(checkout, identity, declaration)


def _compile(
    admissions: list[Admission],
    discoveries: list[DiscoveredDeclaration],
):
    return compile_aggregate(
        AggregateInputs(
            machine=MACHINE,
            mode=ExecutionMode.OBSERVE,
            admissions=tuple(admissions),
            discoveries=tuple(discoveries),
        )
    )


def _exact(
    claim_id: str,
    repository: str,
    *,
    collection: str | None = "corpus",
    rendering: str | None = None,
    profile: str | None = None,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        sources=SourceSet(repositories=frozenset({repository})),
        collection_target=collection,
        rendered_sink=rendering,
        profile=profile,
    )


def test_plan_is_deterministic_independent_of_discovery_order(tmp_path: Path) -> None:
    first = _declaration(
        tmp_path,
        "github.com/example/alpha",
        _exact("alpha", "github.com/example/alpha"),
    )
    second = _declaration(
        tmp_path,
        "github.com/example/bravo",
        _exact("bravo", "github.com/example/bravo"),
    )

    forward = _compile([first[0], second[0]], [first[1], second[1]])
    reverse = _compile([second[0], first[0]], [second[1], first[1]])

    assert forward.authorized is True
    assert forward.canonical_json() == reverse.canonical_json()
    assert json.loads(forward.canonical_json())["schema_version"] == 1
    assert str(tmp_path).replace("\\", "/") not in forward.canonical_json()


def test_exact_claim_collision_has_actionable_witness(tmp_path: Path) -> None:
    source = "github.com/example/source"
    first = _declaration(
        tmp_path,
        "github.com/example/alpha",
        _exact("alpha", source),
    )
    second = _declaration(
        tmp_path,
        "github.com/example/bravo",
        _exact("bravo", source),
    )

    plan = _compile([first[0], second[0]], [first[1], second[1]])

    finding = next(
        item for item in plan.findings if item.code == "cross-repository-ownership-collision"
    )
    assert plan.authorized is False
    assert finding.dimension is OwnershipDimension.COLLECTION
    assert finding.witness == source
    assert finding.repositories == (
        "github.com/example/alpha",
        "github.com/example/bravo",
    )


def test_wildcard_exclusion_makes_exact_claim_disjoint(tmp_path: Path) -> None:
    exact_source = "github.com/example/source"
    wildcard = Claim(
        claim_id="fallback",
        sources=SourceSet(
            wildcard=True,
            exclusions=frozenset({exact_source}),
            include_unclassified=True,
        ),
        collection_target="corpus",
    )
    first = _declaration(tmp_path, "github.com/example/alpha", wildcard)
    second = _declaration(
        tmp_path,
        "github.com/example/bravo",
        _exact("specific", exact_source),
    )

    plan = _compile([first[0], second[0]], [first[1], second[1]])

    assert plan.authorized is True
    assert not plan.findings


def test_collection_and_rendering_ownership_are_independent(tmp_path: Path) -> None:
    source = "github.com/example/source"
    collector = _declaration(
        tmp_path,
        "github.com/example/collector",
        _exact("collect", source),
    )
    renderer = _declaration(
        tmp_path,
        "github.com/example/renderer",
        _exact("render", source, collection=None, rendering="logs"),
    )

    plan = _compile([collector[0], renderer[0]], [collector[1], renderer[1]])

    assert plan.authorized is True
    assert {claim.dimension for claim in plan.claims} == {
        OwnershipDimension.COLLECTION,
        OwnershipDimension.RENDERING,
    }


def test_equal_specificity_machine_policies_fail_closed(tmp_path: Path) -> None:
    policies = (
        RepositoryPolicy(
            policy_id="by-name",
            selector=MachineSelector(name=MACHINE.name),
            claims=(_exact("name", "github.com/example/name"),),
        ),
        RepositoryPolicy(
            policy_id="by-role",
            selector=MachineSelector(role=MACHINE.role),
            claims=(_exact("role", "github.com/example/role"),),
        ),
    )
    admission, discovery = _declaration(
        tmp_path,
        "github.com/example/owner",
        machine_policies=policies,
    )

    plan = _compile([admission], [discovery])

    assert plan.authorized is False
    assert plan.claims == []
    assert {finding.code for finding in plan.findings} == {"ambiguous-machine-policy"}


def test_nonmatching_machine_policies_are_reported_as_shadowed(
    tmp_path: Path,
) -> None:
    policy = RepositoryPolicy(
        policy_id="other-machine",
        selector=MachineSelector(name="worker-2"),
        claims=(_exact("other", "github.com/example/other"),),
    )
    admission, discovery = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("default", "github.com/example/default"),
        machine_policies=(policy,),
    )

    plan = _compile([admission], [discovery])

    assert plan.authorized is True
    assert plan.shadowed_policies == [
        {
            "repository": "github.com/example/owner",
            "policy_id": "other-machine",
            "reason": "selector-mismatch",
        }
    ]


def test_machine_policy_can_disable_a_default_claim(tmp_path: Path) -> None:
    policy = RepositoryPolicy(
        policy_id="worker-policy",
        selector=MachineSelector(name=MACHINE.name),
        disabled_claims=frozenset({"default"}),
    )
    admission, discovery = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("default", "github.com/example/default"),
        machine_policies=(policy,),
    )

    plan = _compile([admission], [discovery])

    assert plan.authorized is True
    assert plan.passive is True
    assert plan.claims == []


def test_unadmitted_and_secondary_checkouts_are_inert(tmp_path: Path) -> None:
    admitted, authoritative = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("owner", "github.com/example/owner"),
    )
    _, secondary = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("changed", "github.com/example/changed"),
        checkout_name="owner-feature-worktree",
    )
    _, unadmitted = _declaration(
        tmp_path,
        "github.com/example/random",
        _exact("random", "github.com/example/random"),
    )

    plan = _compile([admitted], [secondary, unadmitted, authoritative])

    assert plan.authorized is True
    assert [claim.claim_id for claim in plan.claims] == ["owner"]
    statuses = {item["repository"] + ":" + item["status"] for item in plan.discoveries}
    assert "github.com/example/owner:secondary-checkout" in statuses
    assert "github.com/example/random:unadmitted" in statuses


def test_repository_identity_is_exact_not_basename_or_prefix_based() -> None:
    assert (
        canonical_repository_identity("git@github.com:Example/Tool.git")
        == "github.com/example/tool"
    )
    assert (
        canonical_repository_identity("https://github.com/example/tool-harness.git")
        == "github.com/example/tool-harness"
    )
    assert canonical_repository_identity("github.com/another/tool") == "github.com/another/tool"


def test_repository_identity_rejects_filesystem_paths() -> None:
    import pytest

    with pytest.raises(ValueError, match="filesystem paths"):
        canonical_repository_identity("C:\\src\\owner")


def test_duplicate_admission_output_is_deterministic(tmp_path: Path) -> None:
    first, discovery = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("owner", "github.com/example/owner"),
        checkout_name="first",
    )
    second = Admission(
        repository=first.repository,
        authoritative_checkout=(tmp_path / "second").resolve(),
    )

    forward = _compile([first, second], [discovery])
    reverse = _compile([second, first], [discovery])

    assert forward.authorized is False
    assert forward.canonical_json() == reverse.canonical_json()
    assert {finding.code for finding in forward.findings} == {"duplicate-admission"}


def test_checkout_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    admission, discovery = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("owner", "github.com/example/owner"),
    )
    mismatched = DiscoveredDeclaration(
        checkout=discovery.checkout,
        checkout_repository="github.com/example/impostor",
        declaration=discovery.declaration,
    )

    plan = _compile([admission], [mismatched])

    assert plan.authorized is False
    assert {finding.code for finding in plan.findings} == {
        "checkout-identity-mismatch",
        "missing-authoritative-declaration",
    }


def test_compile_is_independent_of_cwd_and_ordinary_environment(
    tmp_path: Path, monkeypatch
) -> None:
    admission, discovery = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("owner", "github.com/example/owner"),
    )
    first = _compile([admission], [discovery]).canonical_json()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setenv("AGENT_LOGGER_SYNC_TARGET", "unexpected")
    monkeypatch.setenv("AGENT_LOGGER_REPO_CONFIG", "unexpected")

    second = _compile([admission], [discovery]).canonical_json()

    assert first == second


def test_invalid_discovery_is_preserved_as_a_fail_closed_diagnostic(
    tmp_path: Path,
) -> None:
    checkout = (tmp_path / "owner").resolve()
    admission = Admission(
        repository="github.com/example/owner",
        authoritative_checkout=checkout,
    )

    plan = compile_aggregate(
        AggregateInputs(
            machine=MACHINE,
            mode=ExecutionMode.OBSERVE,
            admissions=(admission,),
            discoveries=(),
            discovery_failures=(
                DiscoveryFailure(
                    repository=admission.repository,
                    path=str(checkout / ".copilot-extensions" / "agent-logger" / "config.yaml"),
                    reason="unsupported field",
                ),
            ),
        )
    )

    assert plan.authorized is False
    assert "invalid-declaration" in {finding.code for finding in plan.findings}
    assert ".copilot-extensions/agent-logger/config.yaml" in plan.canonical_json()
    assert str(tmp_path).replace("\\", "/") not in plan.canonical_json()


def test_filesystem_provider_loads_admission_declaration_and_override(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    checkout = tmp_path / "owner"
    declaration_path = checkout / ".copilot-extensions" / "agent-logger" / "config.yaml"
    override_path = home / "owner-override.yaml"
    declaration_path.parent.mkdir(parents=True)
    home.mkdir()
    (home / "config.yaml").write_text(
        "\n".join(
            [
                "aggregate:",
                "  mode: observe",
                "  admissions:",
                "    - repository: github.com/example/owner",
                f"      checkout: {checkout.as_posix()}",
                f"      override: {override_path.as_posix()}",
                "      collection_targets:",
                "        corpus:",
                "          kind: filesystem",
                "          identity: /stores/sessions",
            ]
        ),
        encoding="utf-8",
    )
    declaration_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "repository: github.com/example/owner",
                "default:",
                "  claims:",
                "    - id: collect",
                "      sources:",
                "        repositories: [github.com/example/owner]",
                "      collection_target: corpus",
            ]
        ),
        encoding="utf-8",
    )
    override_path.write_text(
        "id: local\ndisabled_claims: [collect]\n",
        encoding="utf-8",
    )
    provider = FileSystemAggregateInputProvider(
        machine=MACHINE,
        home=home,
        checkouts=(
            CheckoutDescriptor(
                checkout=checkout,
                repository="git@github.com:example/owner.git",
                declaration_path=declaration_path,
            ),
        ),
    )

    plan = compile_aggregate(provider.load())

    assert plan.authorized is True
    assert plan.passive is True
    assert plan.claims == []
    assert plan.selected_policies[-1]["policy_id"] == "local"
    assert str(tmp_path).replace("\\", "/") not in plan.canonical_json()


def test_filesystem_provider_preserves_invalid_override_diagnostic(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    checkout = tmp_path / "owner"
    declaration_path = checkout / ".copilot-extensions" / "agent-logger" / "config.yaml"
    override_path = home / "invalid-override.yaml"
    declaration_path.parent.mkdir(parents=True)
    home.mkdir()
    (home / "config.yaml").write_text(
        "\n".join(
            [
                "aggregate:",
                "  admissions:",
                "    - repository: github.com/example/owner",
                f"      checkout: {checkout.as_posix()}",
                f"      override: {override_path.as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    declaration_path.write_text(
        "schema_version: 1\nrepository: github.com/example/owner\n",
        encoding="utf-8",
    )
    override_path.write_text("claims: not-a-list\n", encoding="utf-8")
    provider = FileSystemAggregateInputProvider(
        machine=MACHINE,
        home=home,
        checkouts=(
            CheckoutDescriptor(
                checkout=checkout,
                repository="github.com/example/owner",
                declaration_path=declaration_path,
            ),
        ),
    )

    plan = compile_aggregate(provider.load())

    assert plan.authorized is False
    assert "invalid-override" in {finding.code for finding in plan.findings}
    assert plan.claims == []


def test_malformed_enabled_admission_fails_closed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    checkout = tmp_path / "owner"
    home.mkdir()
    (home / "config.yaml").write_text(
        "\n".join(
            [
                "aggregate:",
                "  mode: enforce",
                "  admissions:",
                "    - repository: github.com/example/owner",
                f"      checkout: {checkout.as_posix()}",
                "      collection_targets:",
                "        corpus:",
                "          kind: repository",
                "          identity: github.com/example/owner:..",
            ]
        ),
        encoding="utf-8",
    )

    plan = compile_aggregate(
        FileSystemAggregateInputProvider(
            machine=MACHINE,
            home=home,
            checkouts=(),
        ).load()
    )

    assert plan.authorized is False
    assert {finding.code for finding in plan.findings} == {"invalid-admission"}
    assert plan.admissions[0]["state"] == "enabled"


def test_unadmitted_invalid_declaration_is_inert(tmp_path: Path) -> None:
    plan = compile_aggregate(
        AggregateInputs(
            machine=MACHINE,
            mode=ExecutionMode.OBSERVE,
            admissions=(),
            discoveries=(),
            discovery_failures=(
                DiscoveryFailure(
                    repository="github.com/example/random",
                    path=str(tmp_path / "random" / "config.yaml"),
                    reason="malformed YAML",
                ),
            ),
        )
    )

    assert plan.authorized is True
    assert plan.findings == []
    assert plan.discoveries[0]["status"] == "unadmitted-invalid"


def test_quarantine_suppresses_invalid_declaration(tmp_path: Path) -> None:
    checkout = (tmp_path / "owner").resolve()
    admission = Admission(
        repository="github.com/example/owner",
        authoritative_checkout=checkout,
        quarantine_reason="schema migration pending",
    )
    plan = compile_aggregate(
        AggregateInputs(
            machine=MACHINE,
            mode=ExecutionMode.OBSERVE,
            admissions=(admission,),
            discoveries=(),
            discovery_failures=(
                DiscoveryFailure(
                    repository=admission.repository,
                    path=str(checkout / "config.yaml"),
                    reason="unsupported schema",
                ),
            ),
        )
    )

    assert plan.authorized is True
    assert plan.findings == []
    assert plan.discoveries[0]["status"] == "quarantined-invalid"


def test_quarantine_reason_is_bounded_in_diagnostics(tmp_path: Path) -> None:
    admission = Admission(
        repository="github.com/example/owner",
        authoritative_checkout=(tmp_path / "owner").resolve(),
        quarantine_reason=("blocked by C:/Users/alice/.ssh/id_rsa token=abc123"),
    )

    output = _compile([admission], []).canonical_json()

    assert "alice" not in output
    assert "abc123" not in output
    assert '"quarantine_reason":"configured"' in output


def test_secondary_checkout_identity_mismatch_is_inert(tmp_path: Path) -> None:
    admission, authoritative = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("owner", "github.com/example/owner"),
    )
    secondary_checkout = (tmp_path / "secondary").resolve()
    secondary = DiscoveredDeclaration(
        checkout=secondary_checkout,
        checkout_repository="github.com/example/impostor",
        declaration=authoritative.declaration,
    )

    plan = _compile([admission], [secondary, authoritative])

    assert plan.authorized is True
    assert plan.findings == []
    assert {item["status"] for item in plan.discoveries} == {"authoritative", "secondary-checkout"}


def test_secondary_checkout_parse_failure_is_inert(tmp_path: Path) -> None:
    admission, authoritative = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("owner", "github.com/example/owner"),
    )
    secondary_checkout = (tmp_path / "secondary").resolve()
    plan = compile_aggregate(
        AggregateInputs(
            machine=MACHINE,
            mode=ExecutionMode.OBSERVE,
            admissions=(admission,),
            discoveries=(authoritative,),
            discovery_failures=(
                DiscoveryFailure(
                    repository=admission.repository,
                    path=str(secondary_checkout / "config.yaml"),
                    reason="malformed YAML",
                    checkout=secondary_checkout,
                ),
            ),
        )
    )

    assert plan.authorized is True
    assert plan.findings == []
    assert plan.discoveries[0]["status"] == "secondary-invalid"


def test_failure_reason_does_not_leak_absolute_path(tmp_path: Path) -> None:
    checkout = (tmp_path / "owner").resolve()
    admission = Admission(
        repository="github.com/example/owner",
        authoritative_checkout=checkout,
    )
    secret_path = tmp_path / "TOP-SECRET-DIRECTORY" / "bad.yaml"
    plan = compile_aggregate(
        AggregateInputs(
            machine=MACHINE,
            mode=ExecutionMode.OBSERVE,
            admissions=(admission,),
            discoveries=(),
            discovery_failures=(
                DiscoveryFailure(
                    repository=admission.repository,
                    path=str(secret_path),
                    reason=f"{secret_path}: malformed YAML",
                    checkout=checkout,
                ),
            ),
        )
    )

    output = plan.canonical_json()
    assert plan.authorized is False
    assert "TOP-SECRET-DIRECTORY" not in output
    assert str(tmp_path).replace("\\", "/") not in output


def test_resolved_output_contains_no_binding_secrets(tmp_path: Path) -> None:
    admission, discovery = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("owner", "github.com/example/owner"),
    )
    admission = Admission(
        repository=admission.repository,
        authoritative_checkout=admission.authoritative_checkout,
        collection_targets={
            "corpus": ResourceBinding(
                kind="remote",
                identity="example-endpoint",
                detail="credential configured",
            )
        },
        rendered_sinks=admission.rendered_sinks,
    )

    output = _compile([admission], [discovery]).canonical_json()

    assert "example-endpoint" in output
    assert "credential configured" not in output
    assert "token" not in output.lower()
    assert "password" not in output.lower()
    assert "secret" not in output.lower()


def test_remote_binding_rejects_credential_bearing_identity() -> None:
    import pytest

    with pytest.raises(ValueError, match="must not contain credentials"):
        ResourceBinding(
            kind="remote",
            identity="https://user:ghp_0123456789abcdef@example.com/ingest",
        )
    with pytest.raises(ValueError, match="must not contain credentials"):
        ResourceBinding(kind="remote", identity="svc:s3cr3t@example.com/ingest")
    with pytest.raises(ValueError, match="unsupported resource binding kind"):
        ResourceBinding(
            kind="webhook",
            identity="https://example.com/ingest",
        )


def test_invalid_repository_binding_fails_during_input_construction() -> None:
    import pytest

    with pytest.raises(ValueError, match="repository-relative"):
        ResourceBinding(
            kind="repository",
            identity="github.com/example/owner:../escape",
        )
    with pytest.raises(ValueError, match="repository-relative"):
        ResourceBinding(
            kind="repository",
            identity="github.com/example/owner:..",
        )
    with pytest.raises(ValueError, match="must be absolute"):
        ResourceBinding(
            kind="filesystem",
            identity="relative/sessions",
        )


def test_internal_ambiguity_rejects_all_repository_claims(tmp_path: Path) -> None:
    source = "github.com/example/source"
    admission, discovery = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("first", source),
        Claim(
            claim_id="second",
            sources=SourceSet(repositories=frozenset({source})),
            collection_target="corpus",
            profile="different",
        ),
    )

    plan = _compile([admission], [discovery])

    assert plan.authorized is False
    assert plan.claims == []
    assert {finding.code for finding in plan.findings} == {"internal-claim-ambiguity"}
    assert plan.rejected_repositories == [
        {
            "repository": "github.com/example/owner",
            "reason": "internal claim ambiguity",
        }
    ]


def test_duplicate_authoritative_declarations_reject_repository_deterministically(
    tmp_path: Path,
) -> None:
    admission, first = _declaration(
        tmp_path,
        "github.com/example/owner",
        _exact("first", "github.com/example/first"),
    )
    second_declaration = RepositoryDeclaration(
        schema_version=1,
        repository=admission.repository,
        provenance=first.declaration.provenance,
        default_policy=RepositoryPolicy(
            policy_id="default",
            claims=(_exact("second", "github.com/example/second"),),
        ),
    )
    second = DiscoveredDeclaration(
        checkout=first.checkout,
        checkout_repository=admission.repository,
        declaration=second_declaration,
    )

    forward = _compile([admission], [first, second])
    reverse = _compile([admission], [second, first])

    assert forward.authorized is False
    assert forward.claims == []
    assert forward.canonical_json() == reverse.canonical_json()
    assert {finding.code for finding in forward.findings} == {
        "duplicate-authoritative-declaration"
    }


def test_canonical_destination_conflict_is_detected_for_disjoint_sources(
    tmp_path: Path,
) -> None:
    first = _declaration(
        tmp_path,
        "github.com/example/alpha",
        _exact(
            "alpha",
            "github.com/example/source-a",
            rendering="logs",
            profile="brief",
        ),
    )
    second = _declaration(
        tmp_path,
        "github.com/example/bravo",
        _exact(
            "bravo",
            "github.com/example/source-b",
            rendering="logs",
            profile="detailed",
        ),
    )
    shared = ResourceBinding("repository", "github.com/example/logs:logs")
    admissions = [
        Admission(
            repository=item.repository,
            authoritative_checkout=item.authoritative_checkout,
            collection_targets=item.collection_targets,
            rendered_sinks={"logs": shared},
        )
        for item in (first[0], second[0])
    ]

    plan = _compile(admissions, [first[1], second[1]])

    assert plan.authorized is False
    assert "destination-policy-conflict" in {finding.code for finding in plan.findings}


def test_canonically_equivalent_destination_spellings_collide(tmp_path: Path) -> None:
    first = _declaration(
        tmp_path,
        "github.com/example/alpha",
        _exact("alpha", "github.com/example/source-a", profile="brief"),
    )
    second = _declaration(
        tmp_path,
        "github.com/example/bravo",
        _exact("bravo", "github.com/example/source-b", profile="detailed"),
    )
    admissions = [
        Admission(
            repository=first[0].repository,
            authoritative_checkout=first[0].authoritative_checkout,
            collection_targets={
                "corpus": ResourceBinding("filesystem", "C:\\Stores\\Shared\\Sessions")
            },
        ),
        Admission(
            repository=second[0].repository,
            authoritative_checkout=second[0].authoritative_checkout,
            collection_targets={
                "corpus": ResourceBinding("filesystem", "c:/stores/shared/other/../sessions")
            },
        ),
    ]

    plan = _compile(admissions, [first[1], second[1]])

    assert plan.authorized is False
    assert "destination-policy-conflict" in {finding.code for finding in plan.findings}


def test_default_remote_ports_canonicalize_to_same_destination(
    tmp_path: Path,
) -> None:
    first = _declaration(
        tmp_path,
        "github.com/example/alpha",
        _exact("alpha", "github.com/example/source-a", profile="brief"),
    )
    second = _declaration(
        tmp_path,
        "github.com/example/bravo",
        _exact("bravo", "github.com/example/source-b", profile="detailed"),
    )
    admissions = [
        Admission(
            repository=first[0].repository,
            authoritative_checkout=first[0].authoritative_checkout,
            collection_targets={"corpus": ResourceBinding("remote", "https://example.com/ingest")},
        ),
        Admission(
            repository=second[0].repository,
            authoritative_checkout=second[0].authoritative_checkout,
            collection_targets={
                "corpus": ResourceBinding("remote", "https://example.com:443/ingest")
            },
        ),
    ]

    plan = _compile(admissions, [first[1], second[1]])

    assert plan.authorized is False
    assert "destination-policy-conflict" in {finding.code for finding in plan.findings}
