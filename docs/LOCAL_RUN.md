# Local model run: evidence and limitations

This is a real local Ollama run, not a synthetic adapter fixture. The prompt and adapter are tailored
to the small `normalize_label` demonstration: the model proposes one Python return statement, which
the adapter inserts into pinned source and converts into a unified diff. The prompt explicitly asks
for `.lower()` and a whitespace-aware operation. The model was not given the trusted tests.

On 2026-11-15, `qwen3:4b` (digest
`359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`), running locally, produced
`local-qwen3-011`. The strict schema, path and patch policy, static pre-screen, and scratch diff
confirmation passed. The run stopped at `blocked_boundary_unverified`; neither the baseline nor the
candidate was executed, and no test-pass claim is made. The unmodified packet, rendered review,
manifest and recording are in [evidence/local-qwen3-011](evidence/local-qwen3-011/). The packet
records a dirty project tree because the adapter was under development when the run happened.

Before this result, separate attempts with earlier versions of the prompt and response format were
also made. Three returned malformed diffs, one returned a response in the wrong format, three
produced flawed or syntactically invalid file proposals, one omitted lowercase, and one hit the
generation limit. The larger installed coding model returned a local-service error. A prior run of
the final return-statement format produced the same patch. These are iterative development attempts,
not independent benchmark trials or a measured success rate.

## Next gate

An operator must inspect the entire resulting file and explicitly approve its exact recorded
hashes before any replay can execute. Docker synthetic checks are not proof of isolation for
unreviewed model output. A pass from tests that import candidate code is evidence for human review,
not proof against hostile code. Draft PR creation requires separate explicit approval.

## Subsequent supervised evaluation

After inspecting the whole file, Codex separately evaluated the unpatched baseline and the exact
hashed candidate using the pinned trusted tests in a restricted local Docker container. Baseline:
two expected failures, one pass (pytest exit 1). Candidate: all three expected tests passed, no
skips or errors (pytest exit 0). The container had no network, read-only inputs and root filesystem,
non-root user and resource limits. The tests import candidate code, so output is candidate-influenced.
This was a manual evaluation under the user's request to complete the project, not an approved
agent replay; the agent's replay gate remains closed. See the [combined review packet](evidence/local-qwen3-011/review-packet.md)
and the retained envelopes. The original packet's blocked status is unchanged and is not contradicted:
the testing occurred in a separate operation.
