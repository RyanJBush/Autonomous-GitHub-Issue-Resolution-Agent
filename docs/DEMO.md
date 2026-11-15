# Two-minute demo: supervised issue resolution

This demo uses one intentionally synthetic Python issue. The local model run is real; the issue
and trusted tests are fixtures. It is not a benchmark of general GitHub issue resolution.

## Show the evidence

1. Read [LOCAL_RUN.md](LOCAL_RUN.md) for the model identity, constraints, failed development attempts,
   and the distinction between the original blocked proposal and the subsequent evaluation.
2. Open [the combined review packet](evidence/local-qwen3-011/review-packet.md). Show the one-line
   diff and the complete resulting file, then the baseline and candidate test table.
3. Show the [original agent packet](evidence/local-qwen3-011/review.md): it stops before executing
   the model proposal. The separate evaluation and its raw test envelopes are retained beside it.
   A passing test is candidate-influenced evidence, not proof that arbitrary code is safe.
4. Show the hosted CI results for the branch. The CI model-response fixtures are synthetic; CI does
   not make a model call or rerun the real model-generated patch.

## Run a fresh local proposal

On the documented Linux environment, with Ollama listening on `127.0.0.1:11434` and the exact
`qwen3:4b` model digest specified in `demo/local/request.json` installed:

```bash
python -m ira build-demo --dest /tmp/ira-demo-repo
python -m ira run --request demo/local/request.json --out /tmp/ira-local-output
```

Use fresh output paths or a new run ID for another attempt. The generation step does not execute
the returned code. Review the entire result before authorizing any test execution. Do not interpret
the command's exit code 0 as a patch pass: inspect `terminal_status` in the packet.

The recorded experiment used an uncommitted working tree and several development attempts; it must
not be described as a first-try success. The local adapter is purpose-built for this single function,
and the code's review and replay gate is not a sandbox. No draft PR or merge is performed.
