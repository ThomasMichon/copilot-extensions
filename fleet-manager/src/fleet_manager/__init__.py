"""Non-mutating fleet planning; no provider, server or credential side effects."""

__version__ = "0.1.4-dev1"

from .planning import PlanError, plan, verify_plan

__all__ = ["PlanError", "plan", "verify_plan"]
