# Contract v1 (frozen)

Owner: Codex (scope, contracts, acceptance criteria, evaluation design, independent review).
Implementation and file writing: Claude. Changes to this file require a Codex decision relayed by the operator.

## Contract v1

1. One reproducible, explicitly synthetic demonstration repository, one pinned base commit, one issue, one existing allowlisted Python file, one model proposal per run.
2. The model sees only the bounded issue text and allowlisted source file; it supplies no commands or policy. Its response is strict JSON: proposed diff plus summary, or cannot_propose. Unknown fields are rejected.
3. Verify the base independently. Permit only modifications to that one file. Reject creation, deletion, rename, copy, mode changes, symlinks, binaries, test/config/workflow changes, malformed diffs, offsets, and patches over explicit byte, hunk, and line limits. Validate exact base-file context and confirm the applied diff.
4. Candidate code is untrusted. Do not execute it until a disposable environment with no valuable credentials, restricted network, bounded resources, fixed trusted commands, and effective timeout cleanup is verified. Otherwise stop with blocked_boundary_unverified.
5. A trusted issue-reproducing test must fail on the base and pass on the candidate in the same setup. Tests and runner configuration come from a pinned source outside candidate control. Record exit codes, expected test IDs, skips, timeout, and bounded output. JUnit evidence is not tamper-proof against candidate code running inside pytest; a pass is evidence for human review.
6. The packet records request and patch hashes, base commit, model/adapter identity, prompt-template version or hash, bounded raw response, provenance, policy decisions, diff, trace, test identity, baseline/candidate outcomes, and terminal status. Never store secrets.
7. Statuses: blocked_boundary_unverified, adapter_error, rejected_schema, cannot_propose, rejected_policy, apply_failed, base_test_did_not_fail, timeout, execution_error, tests_failed, tests_passed_awaiting_human_review.
8. Synthetic fixtures, actual live model runs, and replayed recordings must be labeled separately. Hosted CI uses fixtures and no model credentials. Verify actual hosted results before claiming success.
9. Draft PR creation requires my explicit approval. No automatic merge.

Evaluate malformed output, unauthorized paths and operations, offsets, size limits, known-good and known-bad patches, baseline behavior, timeout, and packet completeness. Keep implementation minimal; do not build a general agent framework.

## Milestone 1 acceptance amendments

- Verify repository identity, pinned commit and a clean tree including untracked files.
- Choose and document concrete trusted byte, hunk and changed-line caps.
- Keep the unified-diff grammar narrow; reject malformed headers, offsets, disallowed file operations, path escapes, duplicate JSON keys and non-finite numbers.
- Control scratch Git configuration and hooks. Confirm the applied diff and counts against policy.
- For unavailable packet fields, use null with an explicit reason. Bound and sanitize all model-derived content.
- Test that the candidate-handling path invokes only its fixed Git operations and does not execute candidate content.
- Keep all fixture outputs labeled synthetic. Do not claim Milestone 1 verifies a live adapter, sandbox, or hosted CI.

## Decisions

- Detected policy violations use rejected_policy; unexpected scratch apply or confirmation failures use apply_failed.
- Scratch application of validated text is permitted as a file operation with controlled Git configuration; it is not a sandbox.
- Personally inspected, hand-written synthetic code with understood effects may run in local Docker for runner development, with no credentials in the container and no unnecessary host mounts. Report this as a trusted synthetic check, not proof that the boundary is safe for model output.
- A hosted GitHub Actions runner is an M2 boundary candidate. It is not yet verified or approved for untrusted candidate execution.
