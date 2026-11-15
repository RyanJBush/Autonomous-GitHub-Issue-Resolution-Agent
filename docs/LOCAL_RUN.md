# Local model run: evidence and limitations

This is a real local Ollama run, not a synthetic adapter fixture. The prompt and adapter are tailored
to the small `normalize_label` demonstration: the model proposes one Python return statement, which
the adapter inserts into pinned source and converts into a unified diff. The prompt explicitly asks
for `.lower()` and explicitly says: “collapse all runs of whitespace including tabs and three or
more consecutive spaces.” These hints mirror trusted-test inputs and were informed by inspection of
the tests and earlier failed proposals; the test code itself was not sent to the model. This is a
guided demonstration, not a blind or held-out evaluation. The [reconstructed exact prompt](evidence/local-qwen3-011/actual-prompt-reconstructed.txt)
matches the prompt hash recorded during the original run. It was reconstructed afterward; the
original packet stores the hash, not the actual prompt text. Its `prompt` field describes the
intermediate template, not the final prompt sent to Ollama. The adapter copied the base commit from
the trusted prompt into the normalized response, so its base-echo check did not test the model.

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
skips or errors (pytest exit 0). Codex reported no container network, read-only inputs and root filesystem, a non-root user and
resource limits. Full Docker inspection and the probe output were not retained. The retained result
records network and read-only flags, but does not independently prove the container configuration or
bind the test results cryptographically to the candidate file. The tests import candidate code, so output is candidate-influenced.
This was a manual evaluation under the user's request to complete the project, not an approved
agent replay; the agent's replay gate remains closed. See the [combined review packet](evidence/local-qwen3-011/review-packet.md)
and the retained envelopes. The original packet's blocked status is unchanged and is not contradicted:
the testing occurred in a separate operation.

The original recording uses a legacy “live model response” label, although its provenance identifies
the local model. Its `raw_text` is the adapter-derived response containing the unified diff; the
actual raw model output is under `live.raw_model_response`. The original evidence is unchanged.
