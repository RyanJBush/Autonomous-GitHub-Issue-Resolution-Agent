# Local model proposal and supervised evaluation

**Outcome:** the local `qwen3:4b` model proposed a return statement for the synthetic label-normalization issue. The adapter derived the diff; the complete file was reviewed, then evaluated separately in a restricted Docker container. The three expected trusted tests passed for the candidate. This does not establish correctness for arbitrary issues or resistance to hostile candidate code.

## Proposed patch

```diff
--- a/src/labels.py
+++ b/src/labels.py
@@ -3,7 +3,7 @@
 
 def normalize_label(text: str) -> str:
     """Return a canonical label: lower-case, trimmed, internal whitespace collapsed to one space."""
-    return text.lower()
+    return ' '.join(text.split()).lower()
 
 
 def is_valid_label(text: str) -> bool:
```

- Base commit: `eb455af5a468dc06630d0758f6989490fc2ddcfd`
- Model digest: `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`
- Patch SHA-256: `8db3a8d02d7c91c119089b30f02bc53204d3bb1b24a24ea950f760b7e76ddce0`
- Whole resulting file SHA-256: `a2530d45f8d96bc5704f4e7038ed5ccd27ba122d232fe87bdf012169883b18ac`
- Recording SHA-256: `330e0cb3e1884381a9c9045af8efea502479caf7cbce4352faae35d4e469298f`
- Source: actual local model generation; no paid model API. The model was prompted to propose a single return statement, and the adapter generated the unified diff.

## Trusted tests

| Test | Baseline | Candidate |
|---|---|---|
| trims and collapses whitespace | failed | passed |
| whitespace-only label is invalid | failed | passed |
| existing behavior kept | passed | passed |

The baseline pytest exit code was 1; the candidate pytest exit code was 0, with exactly the three expected test IDs and no failures, errors or skips. Both used the same pinned trusted test file and image, with a read-only candidate/base mount, no container network, a non-root user and resource limits. Container output and JUnit are candidate-influenced. The image label is not independent proof of build provenance.

## Trace and boundaries

1. The model generated a response locally. The adapter preserved the raw response and derived a diff.
2. Schema, path and patch policy, static pre-screen and scratch application checks passed.
3. The original agent run stopped at `blocked_boundary_unverified`; it did not execute model output. See [original review](review.md) and [packet](packet.json).
4. Codex inspected the full resulting file and, under the user's instruction to complete the project, separately ran the base and exact hashed candidate through the trusted tests. No operator replay-approval record was created and the agent's automatic gate remains closed.
5. The baseline and candidate envelopes and outputs are retained alongside this report. No GitHub pull request or merge was created.

The original model run happened while the project tree was dirty; its packet records that fact. Several earlier local-model attempts failed or produced incorrect proposals. See the repository's `docs/LOCAL_RUN.md` for the development history. The tests cover one synthetic issue and are not a general benchmark.
