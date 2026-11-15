# Patch and response policy (Milestone 1)

All values live in `ira/caps.py`. A request may lower a limit; it can never raise one (`ira/models.py`, `Limits`).

## Trusted hard caps

| Cap | Value | Where enforced | Status when exceeded |
|---|---|---|---|
| Raw response size | 32,768 bytes | `models.load_json_strict` (adapter reads at most cap + 1 bytes) | `rejected_schema` / `response_too_large` |
| Summary | 500 characters, no control/format characters | `models.Proposal` | `rejected_schema` / `schema_violation` |
| Assumptions | 5 items, 300 characters each | `models.Proposal` | `rejected_schema` / `schema_violation` |
| cannot_propose reason | 500 characters | `models.CannotPropose` | `rejected_schema` / `schema_violation` |
| Diff size | 8,192 bytes (UTF-8) | `policy.parse` (checked first) | `rejected_policy` / `patch_too_large` |
| Hunks | 3 | `policy.parse` | `rejected_policy` / `too_many_hunks` |
| Added lines | 20 | `policy.parse` | `rejected_policy` / `too_many_added_lines` |
| Removed lines | 20 | `policy.parse` | `rejected_policy` / `too_many_removed_lines` |
| Changed lines (added + removed) | 30 | `policy.parse` | `rejected_policy` / `too_many_changed_lines` |
| Diff line length | 300 bytes | `policy.parse` | `rejected_policy` / `line_too_long` |
| Issue title / body shown to the model | 200 / 4,000 characters | `models.Issue` (operator input) | request rejected, no packet |
| Allowlisted source file | 32,768 bytes, UTF-8, LF, final newline | `base.verify` | `rejected_policy` / `source_*` |
| Raw response stored in the packet | 32,768 bytes | `runner` | stored copy truncated, `truncated: true` |
| Trusted git subprocess | 30 s, 1 MiB output | `gitcmd.run_git` | `execution_error` (trusted-code failure) |

Rationale: the demonstration file has 11 lines and the reference fix changes 1. The caps leave room for a reasonable
alternative fix (a helper plus a docstring) while keeping every proposal small enough to review line by line.

## Strict JSON

UTF-8 only (no BOM), one JSON document, duplicate keys rejected at any depth, `NaN`/`Infinity`/`-Infinity` rejected,
top level must be an object, unknown fields rejected (`extra="forbid"`, strict types), discriminated by `outcome`
(`propose` or `cannot_propose`). Validation errors keep only location and error type, never input values.

## Diff grammar

```
[diff --git a/P b/P]           optional, exact
--- a/P                        exact (no timestamp, no quotes)
+++ b/P                        exact
@@ -A[,B] +C[,D] @@[ text]     1..3 hunks, ascending, non-overlapping
' ' | '-' | '+' body lines     counts must equal the header exactly
```

`P` is the single allowlisted path (`src/labels.py`). Positions are checked against the pinned base file exactly: the
old block must sit at line `A` (no offset, no fuzz), and `C` must equal `A` plus the line delta of earlier hunks.

Rejected, each with its own reason code: extended headers (`index`, `old/new mode`, `new/deleted file mode`, `rename`,
`copy`, `similarity`), symlink modes, `Binary files` / `GIT binary patch`, `/dev/null` (creation, deletion), any other
path (classified as `path_escape`, `test_path_not_allowed`, `config_path_not_allowed`, `workflow_path_not_allowed`,
`path_not_allowed`), a second file section, CR, NUL, other control characters, Unicode format characters (bidi
controls), `\ No newline at end of file`, a missing final newline, blank context lines without the leading space,
no-op hunks, patches that leave the file unchanged or empty, and trailing text after the last hunk.

## Base verification (before any model call)

Request identity, base commit and allowlisted path must equal `demo/pinned.json`. Then, in this order:
`.git` is a real directory with no `commondir`, `alternates`, `grafts` or `shallow`; `.git/config` is read as data and
may contain only the keys `git init` writes; `HEAD` and its tree equal the pinned ids; the allowlisted path is exactly
one regular-file blob (mode `100644`) with the pinned blob id; `git status` with untracked **and ignored** files is
empty; the file is UTF-8 with LF line endings and a final newline. The config check runs before any command that
honours repository config (for example `git status` and `core.fsmonitor`).

## Controlled git

`ira/gitcmd.py` is the only module that starts a process. It builds the environment from scratch (no inherited
variables, no credentials, system/global config disabled, `GIT_CONFIG_COUNT=0`, replace refs ignored) and adds
`-c` overrides that disable hooks, fsmonitor, signing, pagers, auto-gc and every transport. Subcommands are limited
to a fixed list; candidate text is passed only on stdin to `git apply`. This is configuration control for trusted
file operations. **It is not a sandbox.**

## Scratch confirmation

A fresh repository holding only the pinned base copy of `P` receives the diff on stdin (`git apply --check`, then
`git apply`, both with `--unidiff-zero` because positions are already verified exactly). `git diff --numstat` and
`--raw` must show exactly one in-place modification of a regular file with the policy's counts, `git status` must
show nothing else, and the resulting bytes must equal the content computed by `policy.apply_exact`. Any disagreement
is `apply_failed`. The scratch tree is deleted afterwards; nothing in it is imported or executed.
