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
| 2026-11-15 | Codex | Review of M3 design 61e823b; pricing and token-counting documentation checked | unmeasured | unmeasured |
| 2026-11-15 | Claude | M3 offline adapter, replay, review gate, approval packet tooling and tests (04:06:25Z–04:20:06Z, to implementation commit; the approval-packet commit and handoff are reported in the handoff, because the packet binds this commit) | 13.7 min | +5 min handoff |
| 2026-11-15 | Claude | M3 approval packet, offline dry run, push and CI check (04:20:06Z–04:23:36Z; carried from Handoff 009, not committed then because the packet bound the prior commit) | 3.5 min | — |
| 2026-11-15 | Codex | M3 review at 9caa90f (exception-text injection, environments check) | unmeasured | unmeasured |
| 2026-11-15 | Claude | M3 review fixes: safe error records, local single-use ledger, local procedure, strict clean tree, client-before-claim ordering (04:27:39Z–04:33:22Z, to fix commit; packet commit and handoff reported in the handoff) | 5.7 min | +5 min handoff |
| 2026-11-15 | Claude | M3 packet regeneration, dry run, push and CI (04:33:22Z–04:36:04Z; carried from Handoff 010) | 2.7 min | — |
| 2026-11-15 | Codex | M3 review at 790dbd8 (sentinel re-check; Windows environment inspection) | unmeasured | unmeasured |
| 2026-11-15 | Claude | Hidden key prompt, pinned provider host, operator environment check, WSL procedure, estimate-threshold naming (04:38:55Z–04:43:54Z, to code commit; packet commit and handoff reported in the handoff) | 5.0 min | +5 min handoff |
| 2026-11-15 | Codex | Local Ollama adapter, local model attempts, evidence and checks (05:07–05:19 UTC) | ~12 min wall-clock, approximate | Earlier Codex review and reasoning remains unmeasured |
| 2026-11-15 | Codex | Local trusted baseline and candidate evaluation, review packet, hosting and demo preparation | ~7 min wall-clock, approximate | Follow-on to local adapter work; CI polling excluded |
| 2026-11-15 | Claude | M3 environment-check proposal, push and CI (carried from Handoff 011) | 2.9 min | +5 min handoff |
| 2026-11-15 | Claude | Independent review at 159d517 (05:35:31–05:39:30 UTC) | 4.0 min | +4 min handoff |
| 2026-11-15 | Codex | Address independent review findings | unmeasured | unmeasured |

| 2026-11-15 | Claude | Follow-up review at 9d87293 (05:50:44–05:53:19 UTC) | 2.6 min | +3 min handoff |
| 2026-11-15 | Codex | Address follow-up review | unmeasured | unmeasured |

Running totals: Claude measured ≈ 92.1 min (2.5 + 0.6 + 23.9 + 0.9 + 1.8 + 14.7 + 2.6 + 3.7 + 1.3 + 13.7 + 3.5 + 5.7 + 2.7 + 5.0 + 2.9 + 4.0 + 2.6); Claude estimated additions ≈ 61 min;
Codex ≈ 1–2 min estimated (initial audit) + ≈1 min observed (M1 review), plus ~19 min approximate local adapter/evaluation work and unmeasured review and reasoning time.
