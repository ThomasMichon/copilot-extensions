"""The native Bash removal path delegates code selection to the finite owner."""
from pathlib import Path


def test_uninstall_branch_has_no_unowned_default_deletion():
    source = (Path(__file__).resolve().parents[1] / "scripts/install.sh").read_text(
        encoding="utf-8",
    )
    body = source.split("\n    uninstall)\n", 1)[1].split("\n        ;;", 1)[0]
    assert body.index("--check --check-consumers") < body.index("remove_tabby_profile")
    assert body.index("--check --check-consumers") < body.index("--remove-runtime")
    default = body.split("if $REMOVE_CONFIG; then", 1)[0]
    assert "rm -f" not in default
    assert "rm -rf" not in default
    assert "remove_legacy_binstubs" not in default
