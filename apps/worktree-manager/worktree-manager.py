import importlib, sys
sys.argv[0] = 'worktree-manager'
command = importlib.import_module('worktree_manager.__main__')
for part in 'console_entry'.split('.'):
    command = getattr(command, part)
raise SystemExit(command())
