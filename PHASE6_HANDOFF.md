# Phase 6 Handoff

## Task

- Plan: `PLANNED_VISION.md`, `SOLERESEARCH-001 r2 approved`
- Unit: Phase 6 — outline-first UI and portability
- Status: complete; root-owned browser QA and final post-permission-fix Docker smoke remain
- Scope: stdlib local outline-first UI, secure human outline edit transaction/event, authoritative read views, Docker UI service, host wrappers, tmux launcher, macOS/Linux docs, and offline tests; no network research, dependency, Git action, external install, or research-state mutation beyond the outline/event pair
- Baseline: accepted Phase 5 local tree; parent `e351a184ccb21f60cf98a75fd79dd8394898a142`; unrelated parent changes preserved

## Changes

| File or area | Outcome | Contract or invariant affected |
| --- | --- | --- |
| `ui.py` and `ui/` | Loopback-first stdlib UI with linked graph branches, complete source/evidence provenance, discussions, run decisions/budgets/events, and unified chronological audit | Fixed routes only; source text remains inert; bounded views disclose total/truncation; read state comes from files, not SQLite |
| `orchestration.py` read-only opening | Full run schema, identity, worker/task/result, extension, accounting, gate, and graph-diff validation without status mutation or Git-intent recovery | UI never renders partially validated orchestration state; wildcard run paths are confined before reads |
| `human_edit_event` schema and ledger | Strict durable human-edit provenance with before/after hashes, semantic summary, capability identity, and time | Outline plus event are one recoverable transaction; no graph apply |
| run broker integration | Active-run edit allowed only at an active, clean, human-controlled gate | Sol/busy/review/paused/hard-cap runs fail with actionable conflict |
| CLI/catalog/Compose | `sole-research serve`, harness-neutral contract, host-loopback-published read-only Compose UI | Startup human capability validation for edit; non-loopback explicit and read-only |
| `scripts/` and README | Quote-safe Compose CLI/UI wrappers and project+command-bound collision-safe/idempotent tmux launcher with SSH guidance | Exact command argv retained; no shell evaluation; macOS/Linux assumptions documented |
| `tests/test_phase6_ui.py` | In-memory HTTP, semantic DOM, symlink confinement, and isolated fake-tmux lifecycle tests | CSRF/ETag/Host allowlist/method/path/body/content-type/anchor/restart/index/run-gate/a11y/create/reuse/collision behavior covered |

## Validation

| Command or check | Result | Evidence or reason skipped |
| --- | --- | --- |
| `UV_CACHE_DIR=/tmp/soleresearch-uv-cache uv run --frozen pytest -q` | pass | 206 passed |
| focused Phase 6 + Phase 4 after narrow integrity revision | partial, regression assertion fixed afterward | 49 passed; 3 malformed-ledger cases correctly rejected as `SchemaError` while tests expected only `ProjectError`; expectation broadened, not rerun at root request |
| `uv lock --check` | pass | 34 packages unchanged |
| `uv build --offline --out-dir /tmp/soleresearch-phase6-dist` | pass | sdist and wheel; UI assets and edit schema present in wheel |
| official plugin and both skill validators | pass | plugin valid; both skills valid |
| `docker compose config --quiet` | pass | UI/CLI services valid |
| local-base `docker compose build` | pass | both `soleresearch-cli` and `soleresearch-ui` built |
| first non-root Docker status smoke | fail, diagnosed | image preserved owner-only source directories from pre-build modes; host directory modes corrected to 0755 afterward; root must rebuild once and rerun |
| browser desktop/narrow visual and interaction QA | skipped | explicitly assigned to root; browser skill/tool not used by writer |
| shell syntax, tmux dry-run, package modes, `git diff --check` | pass | wrappers preserve arguments; source 0644, directories/scripts 0755 |
| Ctrl-C shutdown regression | pass | `serve()` absorbs `KeyboardInterrupt`, closes the server, and emits no traceback |

## Contract and safety checks

| Check | Result | Evidence |
| --- | --- | --- |
| Capability secrecy | pass | token used only for startup validation; UI stores identity object, returns only CSRF token; event has no token |
| Edit confinement | pass | only fixed `PUT /api/v1/outline`; transaction targets exactly `outline.md` and `events/human-edits.jsonl` |
| Optimistic concurrency | pass | matching `If-Match`, body base hash, and current file hash required |
| Anchor safety | pass | one root/title, every active node exactly once, no duplicate/orphan/missing anchors, UTF-8 body cap |
| Host/browser safety | pass | loopback default, non-loopback acknowledgement, edit disabled remotely, CSP/no CDN, fixed assets/routes, escaped bootstrap attribute, DOM `textContent`/`value` |
| DNS-rebinding and wildcard read safety | pass | Host values remain explicit allowlist even on wildcard bind; discussion/run/wildcard symlink paths reject before read |
| Run authority | pass | one-writer lock plus validated clean-human run scope; all other unfinished run states reject |
| Rebuildability | pass | state views identical before/after deleting SQLite index; human edit ledger indexed when present |
| Portability | pass | Python stdlib server/launcher, POSIX shell wrappers, no new dependency |
| Accessibility | pass | real tablist/tab/tabpanel relationships, selected/tabindex state, arrow/Home/End keyboard navigation, separate quiet status region |
| Audit transparency | pass | graph diffs and run events join edits/reconciliations/migrations/decisions in one stable chronological timeline |
| Evidence navigation | implemented | node evidence IDs resolve to evidence/source title; DOM-created buttons switch to Evidence and focus by exact dataset identity without selector/href interpolation |
| Read-only run observation | implemented | complete relational validator runs first; active elapsed/remaining minutes are computed at an injected observed UTC instant without changing budget bytes |
| Edit broker confinement | implemented | unfinished state/gate wildcard paths and identities are confined before read; symlinked broker state rejects before outline/event writes |

## Artifact ledger

| Path | Role | Identity | Version | Git-ignored verified |
| --- | --- | --- | --- | --- |
| `/tmp/soleresearch-phase6-dist/sole_research-0.1.0-py3-none-any.whl` | validation output | package 0.1.0 with UI assets | wheel | outside Git |
| `/tmp/soleresearch-phase6-dist/sole_research-0.1.0.tar.gz` | validation output | package 0.1.0 | sdist | outside Git |
| Docker images `soleresearch-cli:latest`, `soleresearch-ui:latest` | validation output | pre-final-mode-fix build | package 0.1.0 | outside Git |
| `/tmp/soleresearch-phase6-smoke.Ts8AzW/` | validation fixture | project `prj_81085ca9cdf44c9aad225a76b2ba48ad` | project v1 / outline meta v2 | outside Git |

## Decisions and blockers

- Approved decisions applied: autonomous phase acceptance, Docker use, no new dependencies, local-only implementation, root-owned visual QA
- Ambiguities: none
- Blockers: none in implementation; final narrow focused/full rerun and Docker/runtime/browser checks are root-owned
- Next authorized step: root reruns focused/full validation, rebuilds Docker, runs non-root CLI/UI smoke and desktop/narrow browser QA, then dispatches review
