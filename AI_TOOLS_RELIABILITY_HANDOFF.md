# Agent Handoff

## Task

- Plan: `src/soleresearch/AI_TOOLS_RELIABILITY_PLAN.md`, `SOLERESEARCH-AI-TOOLS-001 r1 approved`
- Unit: AI-side orchestration reliability
- Status: complete
- Scope: central gate lifecycle derivation; authorized bounded worker bundles and bundle-aware result preflight; conservative stale pure-node-add admission. UI, exports, provider accounting, dependencies, network access, and Git operations were excluded.
- Baseline: `e351a184ccb21f60cf98a75fd79dd8394898a142`; the whole Soleresearch subtree was untracked and the parent repository contained unrelated work that was preserved.

## Changes

| File or area | Outcome | Contract or invariant affected |
| --- | --- | --- |
| `src/soleresearch/src/soleresearch/orchestration.py` | Derives gate status from lifecycle state; generates integrity-bound worker bundles; shares broker/preflight task checks; safely rebases independent node additions; stamps every new result diff with one captured broker receipt; recovers an already-applied deterministic rebase before terminal, lease, pause, elapsed-time, and cap admission paths. | Paused/finished lifecycle dominates pending work; immutable task/result identity; broker remains authoritative; durable diff time is trusted receipt time; graph/run recovery is idempotent. |
| `src/soleresearch/src/soleresearch/cli.py` | Adds authorized `run ... bundle-task` and optional `tools validate-result --bundle`; structural-only validation is unchanged. | Existing CLI remains compatible; bundle-aware receipts state `broker_admission: false`. |
| `src/soleresearch/src/soleresearch/schemas.py`, `contracts/v1/worker_bundle.schema.json` | Registers the strict v1 worker-bundle contract and minimal evidence projection. | Bundle ID, task, bounded assigned material, result schema, and self-validating template are explicit; attestations are not disclosed. |
| `contracts/v1/tool_catalog.json`, `contracts/v1/tool_action_arguments.json` | Publishes the new action and optional preflight arguments with reverse argparse parity. | Catalog/action parser signatures remain authoritative and complete. |
| `tests/test_phase4_orchestration.py`, `tests/test_phase5_integrations.py` | Covers three parallel branches, same-clock/expired-lease/minutes-cap crash recovery, advancing-clock receipt capture, late durable-diff rejection, collision/non-add safeguards, hard-cap rejection lifecycle, bounded secret-free material, path/query redaction, authorized CLI generation, self-validating templates, task-time ordering, and task-aware preflight. | Twelve new regression/product-replay tests. |

## Validation

| Command or check | Result | Evidence or reason skipped |
| --- | --- | --- |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_phase4_orchestration.py tests/test_phase5_integrations.py tests/test_schemas.py -q` | pass | `112 passed in 19.72s` after captured-receipt corrections. |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_phase4_orchestration.py::test_sol_semantically_rebases_three_independent_parallel_node_adds tests/test_phase4_orchestration.py::test_applied_semantic_rebase_recovers_after_run_commit_crash tests/test_phase4_orchestration.py::test_applied_rebase_recovery_overrides_later_lease_expiry tests/test_phase4_orchestration.py::test_applied_rebase_recovery_accounts_before_current_minutes_pause tests/test_phase4_orchestration.py::test_result_diff_uses_single_captured_receipt_when_clock_crosses_lease tests/test_phase4_orchestration.py::test_applied_prior_diff_stamped_after_lease_fails_recovery tests/test_phase4_orchestration.py::test_stale_position_collision_pauses_at_cap_and_reject_keeps_gate_paused tests/test_phase5_integrations.py::test_authorized_bundle_command_and_bundle_aware_result_preflight -q` | pass | `8 passed in 4.22s`; isolated three-branch/crash/expiry/cap/clock/lifecycle/bundle product replay. |
| `UV_CACHE_DIR=/tmp/uv-cache uv lock --check` | pass | Locked 34-package environment resolved without changes. |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q` | pass | `246 passed in 22.62s`. |
| `git diff --check` and scoped trailing-whitespace scan | pass | No errors; parent diff/stat contains unrelated pre-existing work because Soleresearch is untracked. |
| Mode check | pass | Plan, dispatch, schema, code, contracts, tests, and this handoff are `0644`. |

## Contract and safety checks

| Check | Result | Evidence or reason skipped |
| --- | --- | --- |
| Existing structural-only preflight response compatibility | pass | Existing CLI assertion remains byte-for-shape compatible. |
| Bundle secret/path isolation | pass | Regression asserts no `ctl_` value or canonical project path; bundle generation also fails closed on either. |
| Bundle material scope and bound | pass | Only canonical `source:`/`evidence:` task references are materialized; source excerpts are capped at 60,000 characters, serialized bundles at 131,072 bytes, sources at 15, and evidence projections at 64. Evidence attestations are never emitted. |
| Trusted receipt and lease checks | pass | Bundle-aware CLI preflight always uses its local invocation clock; no public receipt-time override exists. The enforced order is task creation ≤ completion ≤ trusted receipt ≤ immutable lease expiry. |
| Generated template validity | pass | The unchanged template completes every required operation with matching `no_result:<operation>` placeholders and passes its own bundle-aware preflight. |
| Path, secret, and URL isolation | pass | Both controller root and Git worktree root are scanned; obvious credential values fail closed; URL userinfo is omitted and queries/fragments are stripped before emission. |
| Safe semantic rebase | pass | Only new node IDs, active/existing parents, and unused sibling positions pass; current-graph semantic failure falls back to a stale reviewable proposal. |
| Captured receipt and crash recovery | pass | New result proposals and applications use one fixed broker receipt, even if the external clock advances. Before terminal/lease/pause/cap handling, retry verifies and reuses the deterministic already-applied diff at its admitted revision, requires `task.created_at <= result.completed_at <= diff.created_at <= lease_expires_at`, atomically restores result/budget/accepted/ratification/gate ledgers, and never reapplies or duplicates the graph node. A prior applied diff stamped after lease fails closed. |
| UI/export stability | pass | No UI or export file changed; complete suite remains green. |
| Dependency/network/Git boundary | pass | No dependency added, live network contacted, model touched, commit made, push made, or PR opened. |

## Artifact ledger

No durable dataset, model, report, export, or run artifact was produced or consumed. Test projects and bundles were confined to pytest temporary directories and removed by the test harness.

## Decisions and blockers

- Approved decisions applied: the maintainer authorized AI-side changes while preserving the human-facing output/experience; `SOLERESEARCH-AI-TOOLS-001 r1` defines the bounded unit.
- Ambiguities: none.
- Blockers: none for implementation. Bundle source material is deliberately excerpt-bounded and normalized to `bundle_excerpt`; offline exact-locator preflight supports captured passages inside included text, not full PDF page/section/table parity. `material_truncated` makes this explicit. A worker-scoped paginated read service is a future product improvement, not part of this unit.
- Next authorized step: read-only correctness and outside-perspective reviews against this handoff and approved plan.
