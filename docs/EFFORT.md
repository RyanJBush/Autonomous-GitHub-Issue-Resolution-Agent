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

Running totals: Claude measured ≈ 29.7 min (2.5 + 0.6 + 23.9 + 0.9 + 1.8); Claude estimated additions ≈ 23 min;
Codex ≈ 1–2 min estimated (initial audit) + ≈1 min observed (M1 review), plus unmeasured review and reasoning time.
