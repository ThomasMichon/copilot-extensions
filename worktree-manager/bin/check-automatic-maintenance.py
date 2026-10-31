"""Read the Manager policy with the launcher's already-available Python."""

from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from worktree_manager.manager_config import load_config, ManagerConfigError

try:
    enabled = load_config().maintenance.automatic
except ManagerConfigError as exc:
    print(f"Automatic maintenance blocked: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc
if not enabled:
    print("Automatic maintenance disabled by maintenance.automatic = false", file=sys.stderr)
raise SystemExit(0 if enabled else 1)
