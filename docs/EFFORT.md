# Effort ledger

Budget: MVP target 8–12 h; hard ceiling 30 h total actual effort. "Measured" means tool wall-clock timestamps taken
in the session; "estimated" means not measured. Assistant wall-clock time is not equivalent to human effort.

| Date (UTC) | Who | Work | Measured | Estimated (not measured) |
|---|---|---|---|---|
| 2026-11-15 | Codex | Initial environment audit | — | 1–2 min |
| 2026-11-15 | Claude | Handoff 001: environment and reuse audit (01:47:59Z–01:50:29Z) | 2.5 min | +5 min writing |
| 2026-11-15 | Claude | Handoff 002: implementation plan (01:54:02Z–01:54:39Z) | 37 s | +6 min writing |
| 2026-11-15 | Codex | Review of plan and M1 approval | — | unmeasured |
| 2026-11-15 | Claude | Milestone 1 implementation, push and hosted CI check (02:00:33Z–02:24:25Z, includes ~3 min waiting for repository creation) | 23.9 min | +5 min handoff |
| 2026-11-15 | Codex | Milestone 1 review at d46f1d5 (Linux hosted CI check, Windows run, focused suite) | ≈1 min observed command-window elapsed; excludes unmeasured reasoning; not precise total effort | — |
| 2026-11-15 | Claude | README corrections: Linux-only scope, exit-code meaning, packet contents and secrets (03:05:05Z–03:06:01Z, to commit; excludes post-commit CI polling) | 0.9 min | +3 min handoff |
| 2026-11-15 | Codex | Final Milestone 1 review and acceptance at 78ef8c7 | unmeasured | unmeasured |
| 2026-11-15 | Claude | M2 design and evidence plan, docs only (03:21:34Z–03:23:23Z, to commit) | 1.8 min | +4 min handoff |
| 2026-11-15 | Codex | Review of the M2 design and approval with amendments | unmeasured | unmeasured |
| 2026-11-15 | Claude | M2 implementation and local trusted synthetic checks (03:28:12Z–03:42:56Z, to commit; excludes hosted CI polling) | 14.7 min | +5 min handoff |
| 2026-11-15 | Claude | Hosted CI observation and BOUNDARY.md hosted section (03:42:56Z–03:45:32Z, to commit) | 2.6 min | — |
| 2026-11-15 | Codex | M2 review at a34cfa5 (allowlist diffs, Windows verdict tests, hosted run 43673783023) | unmeasured | unmeasured |
| 2026-11-15 | Claude | Cleanup-precedence fix, fault-injection tests, image-trust and fork-burst wording (03:52:06Z–03:55:48Z, to commit; includes restoring dockerd after a VM restart; excludes hosted CI polling) | 3.7 min | +3 min handoff |
| 2026-11-15 | Codex | Final M2 review and acceptance at f224d8e | unmeasured | unmeasured |
| 2026-11-15 | Claude | M3 design and verification plan, docs only (04:01:47Z–04:03:07Z, to commit) | 1.3 min | +3 min handoff |

Running totals: Claude measured ≈ 52.0 min (2.5 + 0.6 + 23.9 + 0.9 + 1.8 + 14.7 + 2.6 + 3.7 + 1.3); Claude estimated additions ≈ 34 min;
Codex ≈ 1–2 min estimated (initial audit) + ≈1 min observed (M1 review), plus unmeasured review and reasoning time.
