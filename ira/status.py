"""Terminal statuses (contract v1, item 7). The set is frozen; do not add values without a contract change."""
from enum import StrEnum


class Status(StrEnum):
    BLOCKED_BOUNDARY_UNVERIFIED = "blocked_boundary_unverified"
    ADAPTER_ERROR = "adapter_error"
    REJECTED_SCHEMA = "rejected_schema"
    CANNOT_PROPOSE = "cannot_propose"
    REJECTED_POLICY = "rejected_policy"
    APPLY_FAILED = "apply_failed"
    BASE_TEST_DID_NOT_FAIL = "base_test_did_not_fail"
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    TESTS_FAILED = "tests_failed"
    TESTS_PASSED_AWAITING_HUMAN_REVIEW = "tests_passed_awaiting_human_review"


# Statuses the Milestone 1 pipeline can emit. The rest need the execution stage (Milestone 2).
M1_REACHABLE = frozenset({
    Status.BLOCKED_BOUNDARY_UNVERIFIED,
    Status.ADAPTER_ERROR,
    Status.REJECTED_SCHEMA,
    Status.CANNOT_PROPOSE,
    Status.REJECTED_POLICY,
    Status.APPLY_FAILED,
    Status.EXECUTION_ERROR,  # internal error in trusted code only; no candidate execution exists in M1
})
