import pytest

from qualification import PARAMETER_COUNTS, membership, membership_digest


def original_nodes():
    return [
        family + (f"[case-{index}]" if count > 1 else "")
        for family, count in PARAMETER_COUNTS.items() for index in range(count)
    ]


def test_membership_is_exact_and_full_retains_every_parameter():
    original = original_nodes()
    mapping = membership(original)
    acceptance = {node for node, lane in mapping.items() if lane == "acceptance"}
    full_only = {node for node, lane in mapping.items() if lane == "full-only"}
    assert acceptance and full_only and acceptance.isdisjoint(full_only)
    assert acceptance | full_only == set(original)
    assert membership_digest(mapping) == membership_digest(membership(list(reversed(original))))
    changed = [*original[:-1], original[-1] + "-changed"]
    assert membership_digest(mapping) != membership_digest(membership(changed))
    assert sum(PARAMETER_COUNTS.values()) == 236 + 5


@pytest.mark.parametrize("change", ["missing", "new", "duplicate", "parameter"])
def test_membership_refuses_missing_unassigned_or_duplicate_nodes(change):
    nodes = original_nodes()
    if change == "missing":
        nodes.pop()
    elif change == "new":
        nodes.append("test_new.py::test_unassigned")
    elif change == "duplicate":
        nodes.append(nodes[0])
    else:
        nodes.remove(next(node for node in nodes if "[" in node))
    with pytest.raises(ValueError):
        membership(nodes)
