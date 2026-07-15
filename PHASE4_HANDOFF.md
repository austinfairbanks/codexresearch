# Phase 4 Handoff

## Task

- Plan: `PLANNED_VISION.md`, `SOLERESEARCH-001 r2 approved`
- Unit: Phase 4 — bounded orchestration and Git isolation
- Status: complete
- Scope: strict files-first run control, inherited budgets, worker task/result queues, gates, resume, one-writer locking, and optional isolated Git worktrees; no provider calls, UI, live web, merge, or push
- Baseline: parent `e351a184ccb21f60cf98a75fd79dd8394898a142`; unrelated parent changes preserved

## Changes

| Area | Outcome | Contract or invariant affected |
| --- | --- | --- |
| Run contracts and repository | Immutable manifest/task packets; strict relational state/event/gate/budget/extension/worker/result records; hash-aware transactional persistence and recovery | Unknown versions, tampered identities/accounting, and malformed packets fail closed |
| Dispatch and result broker | Dynamic roles, bounded selected context, capability allowlist, shared reservations/leases, unconditional caps, provenance/required-op enforcement, stale/idempotent graph diffs | Workers receive no controller/human/secret/canonical path; Sol authority remains ratifiable |
| Lifecycle and CLI | `run`, `resume`, `budget`, `gate`, and run-aware `status`; clean-gate controller handoff; durable pause/finish reasons | Balanced default is 3 cycles, 10 tasks, 15 deep sources, 4 total agents, depth 1, 90 minutes, and explicit $5 ceiling |
| Shared writer boundary | One symlink-safe reentrant OS lock brokers graph, source, evidence, discussion, reconciliation, and run writes | Direct canonical mutation fails while a run is unfinished; nested broker writes do not deadlock |
| Git isolation | Autonomous canonical writes bind to the external `soleresearch/run/<run-id>` linked worktree; checkpoints reject pre-staged/out-of-scope paths | Disabled by default; dirty baseline guarded; subprocess timeout; no merge/push operation |
| Derived views and integration | Run records participate in deterministic index/export; capabilities, orchestration skill, and README document the boundary | Files remain authoritative; SQLite remains disposable |

## Validation

| Command or check | Result | Evidence |
| --- | --- | --- |
| `UV_CACHE_DIR=/tmp/soleresearch-uv-cache uv sync --locked --dev` | pass | 34 packages resolved; 32 checked |
| `UV_CACHE_DIR=/tmp/soleresearch-uv-cache uv lock --check` | pass | lock resolved unchanged |
| `UV_CACHE_DIR=/tmp/soleresearch-uv-cache uv run pytest tests/test_phase4_orchestration.py -q` | pass | 36 passed |
| `UV_CACHE_DIR=/tmp/soleresearch-uv-cache uv run pytest -q` | pass | 181 passed |
| packaged schema/capability load | pass | 20 schemas; 17 capabilities |
| `docker compose config -q` | pass | Compose contract valid |
| `docker compose build && docker compose run --rm cli --help` | pass | rebuilt image exposes run/resume/budget/gate |
| `UV_CACHE_DIR=/tmp/soleresearch-uv-cache uv build --offline --out-dir /tmp/soleresearch-phase4-dist` | pass | sdist and wheel built |
| plugin JSON + capability JSON parse | pass | strict JSON load |
| `git diff --check -- src/soleresearch` | pass | no whitespace errors |

## Contract and safety checks

| Check | Result | Evidence |
| --- | --- | --- |
| Cap boundary matrix and shared descendant envelope | pass | Phase 4 focused tests |
| Malformed, stale, mismatched, and duplicate packets | pass | Phase 4 focused tests |
| Interrupt/resume and writer lock contention | pass | Phase 4 focused tests |
| Abrupt dispatch recovery and create rollback | pass | injected replacement failures and subprocess `os._exit` recovery |
| Relational ledger tamper and cumulative extension bounds | pass | reopen fails closed; repeated extensions cannot exceed one initial-envelope delta |
| Context secret/size/capability bounds, immutable worker IDs, required operations, leases/reservations, forged provenance | pass | Phase 4 focused tests |
| Artifact/capability/domain scope and completed-op output semantics | pass | out-of-task use rejected; empty completion requires explicit no-result error |
| Canonical source authorization | pass | local sources require used source artifacts; web URLs match immutable provenance and domain/capability policy; aliases rejected |
| Exact evidence locator/excerpt/canonical ID | pass | immutable extraction revalidated during import and reopen |
| Receipt-time lease expiry/cancel/requeue | pass | durable events and immutable replacement lineage |
| Terminal and held-result admission | pass | cancelled/expired/superseded tasks reject before accounting; byte-identical held receipts retry after lease expiry |
| Human/Sol clean-gate handoff, review, and ratification | pass | Phase 4 focused tests |
| Hard-cap hold, explicit bounded extension, retry | pass | Phase 4 focused tests |
| Exact source/provider ceilings | pass | exact-ceiling result applies, then the run durably pauses; only over-ceiling admission is held |
| Temp Git canonical binding/checkpoint; linked `.git`; pre-staged rejection; no merge/push | pass | temporary-repository tests with local test identity |
| Persisted worktree broker binding and interrupted Git-create recovery | pass | direct worktree bypass rejected; orphan branch/worktree removed; same ID retry succeeds |
| Verified Git-intent cleanup | pass | intent is retained unless worktree registration, path, and run branch removal are all verified |
| Checkpoint repository/worktree/common-dir/branch verification and index cleanup | pass | injected commit failure leaves empty staged index |
| Non-Git run | pass | all orchestration tests default Git policy off |
| Deterministic run index rebuild | pass | repeated index snapshots identical |

## Artifact ledger

| Path | Role | Identity | Version | Git-ignored verified |
| --- | --- | --- | --- | --- |
| `/tmp/soleresearch-phase4-dist/sole_research-0.1.0-py3-none-any.whl` | validation output | package 0.1.0 | wheel | outside Git |
| Docker image `soleresearch-cli:latest` | validation output | local image | package 0.1.0 | outside Git |

## Decisions and blockers

- Approved decisions applied: autonomous phase acceptance and local Docker use from the user; `SOLERESEARCH-001 r2` balanced envelope and controller/Git contracts
- Ambiguities: none
- Blockers: none
- Next authorized step: root orchestrator review and Phase 4 acceptance; Phase 5 remains separate
