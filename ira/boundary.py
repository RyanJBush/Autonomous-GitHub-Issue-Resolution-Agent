"""Execution-boundary gate (contract item 4).

Milestone 1 has no execution stage and no verified disposable environment, so this gate always refuses.
Milestone 2 replaces it with real verification; until then every accepted patch ends at blocked_boundary_unverified.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BoundaryResult:
    verified: bool
    code: str
    reason: str


def verify_execution_boundary() -> BoundaryResult:
    return BoundaryResult(False, "execution_boundary_unverified",
                          "Milestone 1: no disposable execution environment has been verified; candidate not executed")
