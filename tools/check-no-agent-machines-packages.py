#!/usr/bin/env python3
"""Hard-block guard: this repo's own tree must never carry an agent-machines
consuming-side package.

``agent-machines`` (the plugin this repo ships) is a *platform*: it defines
the ``.copilot-extensions/agent-machines/`` requirement-package convention
(and the legacy ``.agent-machines/`` / ``.github/machine-state/`` locations
it still reads) for a **consuming** repo to declare its own desired machine
state -- gated to specific machines, sometimes down to a
``machines/<machine>/`` folder keyed by the literal machine name. That
content belongs exclusively in the repo that *consumes* the plugin (e.g. the
facility's own private aperture-labs control repo), never in
copilot-extensions itself: this repo is public, a package here would
publish a real machine name/topology to the world, and -- structurally --
copilot-extensions has no machine of its own to converge; it only ships the
mechanism.

Confirmed live (2026-09-26): an agent authoring a facility fleet-update
opt-in package almost committed it straight into copilot-extensions'
``.copilot-extensions/agent-machines/machines/lambda-core/`` before the
PR-open tooling's own branch-name privacy check caught the leak and the
operator redirected the package into aperture-labs instead. This guard
makes that redirection the enforced default for every future contributor,
not a caught-by-luck save -- see the ``agent-machines-setup`` skill's own
"Author a requirement package" section, which now states the same rule.

Whole-tree, not diff-scoped: this is a structural repo-shape invariant (like
``check-module-size.py``), not a content-diff privacy scan -- there is
never a legitimate reason for any of these paths to exist here, pre-existing
or newly added, so a full-tree check costs nothing (today: zero matches)
and can never regress silently.

Exit code 0 = no consuming-side package paths present, 1 = found one.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: The exact repo-root locations agent-machines' own consuming-repo
#: convention reads packages from (see the plugin's "Author a requirement
#: package" docs): the canonical path plus both legacy fallbacks it still
#: honors when the canonical path is absent.
FORBIDDEN_ROOTS = (
    ".copilot-extensions/agent-machines",
    ".agent-machines",
    ".github/machine-state",
)


def find_violations(repo: Path = REPO) -> list[Path]:
    violations: list[Path] = []
    for rel in FORBIDDEN_ROOTS:
        root = repo / rel
        if not root.is_dir():
            continue
        violations.extend(p for p in sorted(root.rglob("*")) if p.is_file())
    return violations


def main() -> int:
    violations = find_violations()
    if not violations:
        print("check-no-agent-machines-packages: OK (no consuming-side package paths).")
        return 0
    print(
        "check-no-agent-machines-packages: FAILED -- agent-machines "
        "consuming-side package(s) found in this repo's own tree:"
    )
    for path in violations:
        print(f"  - {path.relative_to(REPO)}")
    print(
        "\nagent-machines packages declare desired state for a CONSUMING repo's "
        "own machines and belong there, never in copilot-extensions itself "
        "(this repo is public and has no machine of its own to converge -- it "
        "only ships the mechanism). Move this package into the repo that "
        "actually adopts the pattern (e.g. your facility's own private "
        "control repo) instead."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
