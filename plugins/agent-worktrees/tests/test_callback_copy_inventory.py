"""Callback requests cannot expand the Python owner's declared copy scope."""
from __future__ import annotations

from pathlib import Path

import pytest

from test_source_python_publication import (
    publication_case as shared_publication_case,
    publication_transform as default_publication_transform,
)

publication_case = shared_publication_case
publication_transform = default_publication_transform


@pytest.mark.parametrize("publication_case", ["legacy"], indirect=True)
@pytest.mark.parametrize("fault", ["target", "source", "duplicate", "alias", "relative", "policy", "incomplete"])
def test_invalid_copy_inventory_refuses_before_target_writes(publication_case, fault):
    driver, root, _slot, _owner, content, _identity = publication_case
    helper = driver.helper_publication
    bindings = helper.declared_copies(content, root, "resolvers", "sh", False)
    rows = [[item["source"], item["target"], str(int(item["executable"])),
             str(int(item["normalized_noop"]))] for item in bindings]
    if fault == "target":
        rows[0][1] = str(root.parent / "unapproved")
    elif fault == "source":
        rows[0][0] = str(root.parent / "unapproved-source")
    elif fault == "duplicate":
        rows.append(rows[0])
    elif fault == "alias":
        rows[0][1] = str(Path(rows[0][1]).parent / ".." / "bin" / Path(rows[0][1]).name)
    elif fault == "relative":
        rows[0][1] = "relative"
    elif fault == "policy":
        rows[0][2] = "unexpected"
    copies = root / "copy-inventory"
    copies.write_bytes(("\0".join(field for row in rows for field in row) + "\0").encode("utf-8"))
    copies.chmod(0o600)
    if fault == "incomplete":
        copies.write_bytes(copies.read_bytes()[:-1])
    with pytest.raises(ValueError, match="Callback copy"):
        helper.parse_copies({"copies": str(copies), "bindings": bindings})
    assert all(not Path(item["target"]).exists() for item in bindings)


@pytest.mark.parametrize("publication_case", ["legacy"], indirect=True)
def test_copy_inventory_mutation_refuses_before_first_target_write(publication_case):
    driver, root, _slot, _owner, content, _identity = publication_case
    helper = driver.helper_publication
    bindings = helper.declared_copies(content, root, "resolvers", "sh", False)
    copies = root / "copy-inventory"
    copies.write_bytes(("\0".join(
        field for item in bindings for field in
        (item["source"], item["target"], str(int(item["executable"])), str(int(item["normalized_noop"])))
    ) + "\0").encode("utf-8"))
    copies.chmod(0o600)
    directory = root / "publication"
    directory.mkdir()

    def mutate() -> None:
        copies.write_bytes(copies.read_bytes() + b"changed")

    with pytest.raises(ValueError, match="inventory changed"):
        helper.publish({"root": str(root), "copies": str(copies), "bindings": bindings},
                       directory, {}, validate_inputs=mutate)
    assert all(not Path(item["target"]).exists() for item in bindings)
