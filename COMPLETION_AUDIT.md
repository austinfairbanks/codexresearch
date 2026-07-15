# Soleresearch V1 completion audit

- Plan: `SOLERESEARCH-001 r2 approved`
- Audited: `2026-07-11`
- Package: `src/soleresearch/`
- External demonstration: `research/soleresearch-outsole-demo/`
- Interpretation: “implemented” means the local contract and offline validation exist. It does not mean live web research, a paid provider, a Zotero mutation, a Git merge/push, a scientific outsole conclusion, or browser visual QA occurred.

## Outcome and invariant audit

| Plan clause | Status | Evidence |
| --- | --- | --- |
| Generic, local-first evidence-to-outline engine; no paper prose | implemented | Package schemas contain generic node/source/evidence/run concepts; `rg -n -i 'outsole|running shoe' src/soleresearch/src src/soleresearch/contracts src/soleresearch/integrations` returns no domain behavior. The external demo alone contains the outsole topic. |
| Files authoritative; SQLite derived | implemented and demonstrated | `project.json`, Markdown, JSON/JSONL, BibTeX, and CSL-JSON are canonical. Demo audit records equal research views with the index deleted, equal views after rebuild, and equal logical index snapshots. |
| Accepted evidence requires inspected content and exact locator | implemented and demonstrated | Four `content_inspected` local Markdown sources and four section-locator evidence records in `demo-output/project/`; tests cover locator resolution and provenance failures. |
| Discovery metadata cannot support claims | implemented | Graph validation requires evidence IDs for active evidence/interpretation/conclusion nodes; result/source provenance validation fails closed. |
| Transparent source-quality dimensions | implemented and demonstrated | Each synthetic source records authority, methodology transparency, evidence directness, relevance, publication status, and notes; no aggregate score exists. |
| Agent acceptance distinct from human acceptance | implemented and demonstrated | Controller capabilities are external and separate. The demo’s initial agent proposal and continuation result are applied through human capability/gates; capability tests prevent agent minting of human acceptance. |
| Workers return packets; orchestrator is sole canonical writer | implemented | Strict task/result schemas, broker, project-wide writer lock, graph diff apply path, and adversarial orchestration tests. Demo worker result is immutable under `runs/.../results/v1/`. |
| Human Markdown edits win and conflicts queue | implemented and demonstrated | Demo has one `human_outline_edit` event followed by a reconciliation event with `changed=true`; graph/outline tests cover stale proposals and visible conflicts. |
| Generated/cache/credential/transcript policy | implemented with operational caveat | Project and demo-root `.gitignore`, export exclusions, external mode-0600 capabilities, post-demo capability scrubbing, source-copy policy, and 30-day transcript policy are documented/tested. Operators must still avoid force-adding ignored/generated data. |
| No bypass of auth/paywall/CAPTCHA/licensing/anti-bot controls | implemented policy; live path not exercised | Retrieval rejects unsafe destinations and documents the restriction. Demo uses local synthetic files; no live retrieval ran. |
| No implicit hosted service; explicit provider degradation | implemented | Deterministic CLI/UI work offline; demo provider usage is `$0`. Optional adapter catalog remains harness-neutral. |
| No automatic merge/push/publication/paid activation/budget extension | implemented | Git isolation exposes local worktree/checkpoint only; no merge/push command. Budget extension requires active controller and reason. No external mutation occurred. |

## Contract and runtime audit

| Area | Status | Evidence |
| --- | --- | --- |
| Portable project layout and versioned schemas | implemented | Strict packaged v1 schemas; unknown versions fail closed; schema parity tests; explicit outline metadata migration. Worker result preflight is exposed as `sole-research tools validate-result --file RESULT.json` in the catalog, Codex skill, and generated adapter. |
| Atomic writes and complete JSONL records | implemented | Transaction journal, hash-aware recovery, fsync boundaries, append validation, crash/third-state tests. |
| Source states, DOI/arXiv/URL/hash dedupe, immutable extraction | implemented | Phase 2 source/evidence suites and deterministic imports. |
| Typed graph and eight operations | implemented | `question`, `concept`, `evidence`, `interpretation`, `conclusion`, `gap`, `outline`; add/update/merge/move/link/unlink/retire/restore tests. |
| Clean outline, stable anchors, qualitative maturity and authority | implemented | Markdown projection/reconciliation suites; UI edit preserves all active anchors. |
| Balanced branch envelope and recursion bound | implemented | Defaults: 3 cycles, 10 tasks, 15 deep sources, 4 agents including orchestrator, one nested level, 90 minutes, explicit provider ceiling. Central accounting/cap/lease/extension tests. |
| Human/Sol controller and clean-gate handoff | implemented | Run/gate schemas and controller broker tests; demo uses human controller and reviewed result. |
| One writable run, crash/resume, optional Git isolation | implemented/tested; Git mode not used in external demo | OS lock, recovery journal, expired lease requeue, branch/worktree/checkpoint safety tests; demo is deliberately non-Git. |
| CLI/Compose/host wrappers/tmux | implemented | Versioned CLI catalog, Compose CLI/UI services, quote-safe wrappers, collision-safe tmux launcher, macOS/Linux docs/tests. |
| Self-contained external demo broker | implemented and independently rerun | The script binds parent APIs and CLI subprocesses to `OUTPUT/controller-config`, overrides a conflicting ambient controller root, and scrubs generated capabilities after completion. A fresh absolute `/tmp` output smoke passed. |
| Outline-first UI and safe edit scope | implemented; semantic/in-memory QA passed, real browser visual QA unverified | Read views come from files; only loopback human outline editing is writable; CSRF/ETag/Host/method/path/body/anchor/accessibility tests. No interactive browser was available for this final audit. |
| Imports | implemented offline | URL, DOI, arXiv, BibTeX, CSL-JSON, PDF, Markdown, and outline paths have fixture tests. Live URL retrieval was not executed. |
| Complete deterministic export | implemented and demonstrated | Two demo export directory trees hash identically and contain outline, references, graph/events, source/evidence ledgers, audit summary, and static HTML. Licensed source copies remain opt-in. |
| Zotero boundary | implemented and demonstrated | `zotero-bundle` creates BibTeX, CSL-JSON, and `pending_human_review` manifest; no library/API mutation exists. |
| Codex plugin and generated adapters | implemented/validated; not installed | Plugin plus orchestration/Zotero skills pass official validators. Adapter generation/test/approval is offline and approval does not install. Marketplace installation remains deferred. |
| Landscape | complete as point-in-time analysis | `LANDSCAPE.md`, checked `2026-07-11`, links direct official sources and labels product facts versus Soleresearch inferences. |

## Ten-step V1 demonstration

The reproducible command is documented in `research/soleresearch-outsole-demo/README.md`; `run_offline_demo.py` drives the public CLI and the actual ephemeral loopback HTTP UI routes. The separate normalized `demo-output/harness-acceptance.json` records a real Codex plugin-skill invocation and capability-limited child run without retaining its ephemeral paths or controller records.

| # | Required demonstration | Result | Artifact/evidence |
| --- | --- | --- | --- |
| 1 | Initialize through harness/plugin | pass | Independent Codex acceptance invoked the bundled `orchestrate-research` skill inspection/dispatch scripts, started a bounded SOL run, and spawned one child with only its immutable task packet, three fixture paths, read-only ledgers, result schema, and result destination. `demo-output/harness-acceptance.json` is the normalized evidence. |
| 2 | Scaffold generic project and one subquestion | pass | root question node in `graph/nodes.jsonl`; outsole wording is external only. |
| 3 | Curate 3–5 sources with exact locators | pass | 4 synthetic local sources, 4 section locators, immutable source hashes/versions. These validate mechanics, not scientific truth. |
| 4 | Discuss an entity and accept a graph diff | pass | source discussion/promoted takeaway; initial agent-proposed interpretation diff human-applied. |
| 5 | Edit Markdown outline in UI or external editor | pass through served UI HTTP route | An ephemeral `127.0.0.1` server handled bootstrap GET, outline GET with ETag, and CSRF/`If-Match`-protected outline PUT; one durable human-edit event was emitted. JavaScript execution and visual rendering remain unverified. |
| 6 | Reconcile, then run bounded autonomous continuation | pass | The deterministic demo reconciled its human edit, then exercised a bounded human-controlled continuation without a provider. Separate real-harness acceptance exercised a SOL-controlled 1-cycle/1-task/3-source/2-agent/depth-0/10-minute/2,000-token run and a spawned child, reporting 420 consumed tokens. Its diff is correctly `agent_accepted` and remains not human-ratified. |
| 7 | Observe sources, map, gates, budgets, history in UI | pass through served state API | `GET /api/v1/state` returned map/source/evidence/discussion/run/audit views from the actual ephemeral HTTP server. Later state comparisons cover index deletion/rebuild. No claim is made that browser JavaScript rendered those views visually. |
| 8 | Stop early or at hard cap | pass | run finished early with recorded reason that synthetic fixtures cannot answer the scientific question; no recursive extension. |
| 9 | Export complete audit bundle | pass | `export-a/` and `export-b/` are tree-hash identical; Zotero bundle remains pending human review. |
| 10 | Delete SQLite index and rebuild identical observable state | pass | index-presence indicator toggled true/false/true as expected; research view equality and logical index snapshot equality are both true. |

## Automated acceptance coverage

The standalone test suite covers schema round trips/unknown versions, deterministic IDs/dedupe/imports/locators, atomic recovery, worker write/cap/depth/usage constraints, second-writer failure, Markdown reconciliation/conflicts, prompt-injection boundary behavior, UI restart/edit events, Git no-merge/no-push invariants, offline imports/exports, plugin/skill contracts, and adapter staging. Exact final test/validator/Docker results belong in `PHASE7_HANDOFF.md` after execution.

Resolved contract-layer history: independent harness acceptance exposed that the
v1 result JSON Schema admitted opaque graph-operation objects and did not express
the coupling between result fields and `completed_operations`; the broker still
failed closed, so malformed state was never admitted. The Draft 2020-12 schema
now uses eight literal `oneOf` graph-operation branches plus six bidirectional
`allOf` output/completion/no-result conditions. `sole-research tools
validate-result --file RESULT.json` exposes the same preflight through the tool
catalog, Codex skill, and generated adapter. Root confirmed the final tree with
232/232 passing tests. `harness-acceptance.json` intentionally preserves the
original rejections and corrections as useful fail-closed history.

## Explicitly deferred or unverified

- Live public-web 3–5-source smoke: **not approved and not executed**. The demo sources are synthetic, so no scientific outsole-wear conclusion is claimed.
- Live retailer access: prohibited here and not executed.
- Paid/provider/model use: not needed; recorded usage is zero.
- Interactive browser visual QA: unavailable in this final pass; API/semantic/accessibility automation is not mislabeled as visual review.
- Git initialization, worktree use in the external demo, checkpoint commit, merge, push, remote, and PR: not executed. Git safety is automated-test evidence only.
- Marketplace/plugin/adapter installation: not executed; generation/validation only.
- Zotero library mutation: deliberately absent; reviewed open-format import bundle only.
- Paper prose, a scientific hypothesis, wear thresholds, remaining-life model logic, regulatory/systematic-review claims, and modeling-workflow integration: out of V1 scope.
- Source licensing decisions for a future live project remain human responsibilities; no source copy was exported here.
