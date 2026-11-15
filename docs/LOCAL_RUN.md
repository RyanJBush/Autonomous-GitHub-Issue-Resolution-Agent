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
