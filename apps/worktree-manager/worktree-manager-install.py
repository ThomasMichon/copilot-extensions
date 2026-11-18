import importlib, sys
sys.argv[0] = 'worktree-manager-install'
command = importlib.import_module('worktree_manager.application_footprints')
for part in 'main'.split('.'):
    command = getattr(command, part)
raise SystemExit(command())
