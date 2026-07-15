# Soleresearch Plan

- Plan ID: `SOLERESEARCH-001`
- Revision: `r2`
- Status: `approved`
- Feature path: `src/soleresearch/`
- Scope: general-purpose, local-first evidence-to-outline research workbench
- Human approval date: `2026-07-10`
- Approval source: the design interview in this thread followed by “take the plan from this chat … implement,” permission to build locally without the Git remote for now, and explicit delegation of phase acceptance to the root orchestrator for this autonomous implementation
- Parent baseline: `e351a184ccb21f60cf98a75fd79dd8394898a142`; all pre-existing parent changes are unrelated and must be preserved

## Outcome

Build a Docker-first, standalone-ready research engine whose primary interface is an agent-harness conversation backed by deterministic CLI tools and inspectable local artifacts. A single orchestrator may launch bounded specialist workers to discover and read sources, create exact-locator evidence, discuss and promote ideas, maintain a typed research graph, and project that graph into a clean paper-shaped Markdown outline. The outline contains authored ideas, questions, evidence links, disagreements, gaps, and candidate conclusions; v1 does not generate paper prose.

The engine is generic. The first external validation project may use a bounded outsole-wear topic, but no shoe-specific schemas, tags, prompts, or templates belong in the package.

## Invariants

- Files are authoritative: versioned JSON/JSONL, Markdown, BibTeX, and CSL-JSON. SQLite is a disposable, rebuildable index only.
- Every accepted evidence item points to content actually inspected using a page, section, figure, table, paragraph, timestamp, or captured-passage locator.
- Discovery metadata alone cannot support a claim. Interpretations and conclusions trace through evidence records.
- Source quality is expressed through transparent dimensions, never one opaque score.
- Human and agent acceptance are distinct. Autonomous decisions remain `agent_accepted` until human-ratified.
- Workers return versioned result packets and proposed graph diffs; only the orchestrator writes canonical project state.
- Human Markdown edits win. Reconciliation logs a semantic diff, invalidates conflicting proposals, and queues unresolved anchor/link conflicts.
- Source copies, extraction caches, indexes, run logs, generated integrations, credentials, and raw transcripts are ignored. Raw transcripts expire after 30 days by default.
- No agent may bypass authentication, paywalls, CAPTCHAs, licensing controls, or anti-bot defenses. Browser-equivalent inspection may use a human-approved signed-in session.
- No implicit hosted service is required. Optional providers degrade explicitly when unavailable.
- No automatic merge, push, publication, paid-service activation, or budget extension.

## Project contract

Each research project is portable and contains:

```text
project.json
outline.md
outline.meta.json
graph/nodes.jsonl
graph/edges.jsonl
sources/sources.jsonl
evidence/evidence.jsonl
discussions/*.jsonl
decisions/decisions.jsonl
runs/<run-id>/{manifest.json,tasks/,results/,events.jsonl,edit-queue.jsonl}
references/references.bib
references/references.csl.json
```

Generated data lives below `.soleresearch/` and exports below `exports/`; both are rebuildable. Writes use temporary files plus atomic replacement. Append logs accept complete newline-terminated JSON records only. Schema version begins at `1`; unknown versions fail closed and migrations are explicit.

Core node types are `question`, `concept`, `evidence`, `interpretation`, `conclusion`, `gap`, and `outline`. Topic-specific meaning uses tags. Graph operations are `add`, `update`, `merge`, `move`, `link`, `unlink`, `retire`, and `restore`. Stable IDs never encode mutable titles or timestamps.

Source states distinguish `discovered`, `metadata_resolved`, `content_inspected`, and `local_copy_retained`, plus human reading state. Deduplication uses DOI/arXiv identifier, canonical URL, then content hash. Evidence stores a bounded excerpt, paraphrase, stance, locator, source version/hash, retrieval time, run/task identity, and provenance.

The clean outline uses headings, bullets, citations, and unobtrusive stable-anchor comments. Machine state and maturity live in sidecars. Qualitative maturity is `exploratory`, `developing`, `supported`, `contested`, `gap`, or `retired`; authority is separately `proposed`, `agent_accepted`, `human_accepted`, `rejected`, or `stale`.

## Agent and budget contracts

The core exposes executable tools plus versioned JSON capability specifications. Harness-specific setup may generate optimized plugins or skills in an isolated directory, run shared contract tests, and install only after human approval. Codex v1 ships as a validated plugin wrapping the CLI; the CLI and task/result schemas remain harness-neutral.

The orchestrator is the only long-lived context. Workers are dynamically selected scouts, readers, verifiers, critics, or synthesizers. The orchestrator may spawn workers, and a worker may request or launch one nested subworker; deeper nesting is rejected. All descendants share the branch envelope.

Balanced default per branch:

- 3 research cycles;
- 10 total worker tasks;
- 15 deeply processed sources;
- curated batches of 3–5 sources for one subquestion;
- 4 concurrent agents including the orchestrator;
- one nested worker level;
- 90 minutes;
- an explicit project/provider usage ceiling, suggested `$5` when dollars are supported but never treated as a target.

Any cap pauses the branch. Only the active orchestrator can extend it with a recorded reason. Human-vs-Sol control is chosen per run and may switch only at a clean gate. Research stops by orchestrator judgment within the hard envelope; coverage, novelty, unread priority sources, disagreements, and gaps are advisory signals.

Task packets include schema/run/task/parent IDs, depth, one subquestion, evidence strategy, allowed capabilities/domains, artifact references, selected context, all inherited limits, and required result operations. Results include accessed sources, exact-locator evidence, proposed graph operations, outline suggestions, disagreements/gaps, compact rationale, usage, and errors. Stale-base results remain proposals and cannot overwrite newer state.

## CLI and runtime

Host wrappers invoke versioned commands inside Docker Compose while the agent harness remains on the host:

```text
sole-research init|doctor|status|scaffold|import|source|evidence|discuss
sole-research diff|gate|reconcile|run|resume|budget|export|rebuild-index|serve
```

Autonomous sessions use a harness-specific host launcher in a named `tmux` session. One project permits one writable run; each autonomous run uses a dedicated Git branch/worktree when the project is a Git repository. Local checkpoint commits are permitted on the run branch, but merge and push remain human-gated.

The Compose stack runs the CLI service and local web service against mounted workspaces. The UI opens on the full-page outline, shows linked map branches, sources, exact evidence, discussions, budgets, run history, gates, contradictions, and gaps. It may edit only `outline.md`; research-state mutation and orchestration stay in the harness CLI. Saves create durable human-edit events. External editor adapters are optional.

## Imports, exports, and integrations

V1 imports URLs, DOI/arXiv identifiers, BibTeX, CSL-JSON, local PDFs, Markdown notes, and an existing outline. It exports a complete bundle: Markdown outline, BibTeX/CSL-JSON, graph/event JSON, source/evidence ledgers, audit summaries, and static HTML. Permitted source copies require an explicit export option.

Open formats are the integration boundary. The Zotero skill prepares a reviewed import bundle and never mutates a library. Soleresearch owns local orchestration, evidence/idea provenance, gates, budgets, graph state, and outline projection. It should integrate rather than rebuild citation managers, PDF viewers, scholarly citation graphs, systematic-review platforms, or final prose editors. Optional future adapters may target PaperQA2, Zotero, Elicit, Litmaps/ResearchRabbit, OpenAI Deep Research, Perplexity, GPT Researcher, or Open Deep Research.

## Implementation phases

1. **Bootstrap and core contracts** — standalone Python 3.12 `uv` package, Docker/Compose, plugin/skill scaffolds, CLI shell, versioned schemas, files-first project initialization, ignore rules, and tests.
2. **Sources and evidence** — common imports, bounded retrieval/extraction, canonicalization/deduplication, exact-locator evidence, reading queue, source-quality dimensions, and fixture tests.
3. **Graph, discussions, and outline reconciliation** — typed graph operations, entity threads, graph diffs, clean anchored Markdown, semantic reconciliation, maturity/authority states, and conflict queue.
4. **Bounded orchestration and Git isolation** — task/result queues, controller modes, central budget enforcement, nested-worker guard, resume, one-writer lock, run branches/worktrees, and local checkpoint policy.
5. **Codex plugin and generated adapters** — concise orchestration skill, harness-neutral tool specs, deterministic scripts, reviewed Zotero bundle skill, plugin validation, and independent forward tests.
6. **Outline-first UI and portability** — outline editor, linked map/source/audit views, live status, rebuildable index, complete exports, Docker host wrappers, tmux launcher, and macOS/Linux setup.
7. **External validation and landscape** — cited comparison of current research tools plus an external parent-repo project that exercises one bounded 3–5-source outsole-related subquestion without adding domain code to the package.

Each phase is independently reviewed. For this implementation, the human explicitly delegates phase acceptance to the root orchestrator, which may accept a phase only after reconciling a clean final review and validation evidence against this plan. New dependencies, external writes, live-source actions, commits/pushes, and unresolved product decisions still require the human. One writer operates at a time. Reviewer and outside-perspective passes are read-only and may run concurrently.

## External operations

- HTTP/HTTPS only; validate redirects and final domain.
- Defaults: 10-second connect timeout, 30-second read timeout, 5 redirects, 3 attempts, 1 request/second/domain, 15 MiB response ceiling, and configurable decoded/document limits.
- Automated tests never contact the network. Saved HTML/PDF/search fixtures cover all boundaries.
- A separately approved manual smoke may access public web content and processes one subquestion with 3–5 sources.
- Private/imported documents carry a project data policy declaring which harness/model may receive their content.

## Test and acceptance contract

Automated coverage must prove:

- schema round trips and fail-closed unknown versions;
- deterministic IDs, DOI/URL/hash deduplication, idempotent imports, and exact locator preservation;
- atomic writes, append-log boundaries, crash/resume behavior, and identical index rebuilds/exports;
- workers cannot mutate canonical state or exceed task/source/cycle/agent/time/usage/depth limits;
- a second writable run fails safely;
- graph/Markdown round trips preserve anchors, human edits win, and true conflicts queue visibly;
- source text cannot prompt-inject capabilities, budgets, or execution;
- UI edits survive restart and emit semantic-diff events;
- run worktrees/checkpoints never merge or push implicitly;
- all promised imports/exports work offline;
- plugin and skill validators pass.

V1 completion requires this bounded demonstration:

1. Initialize through the harness/plugin.
2. Scaffold a generic project and one subquestion.
3. Curate 3–5 sources with exact locators.
4. Discuss a source/entity and accept a graph diff.
5. Edit the Markdown outline in the UI or external editor.
6. Reconcile and run one bounded autonomous continuation.
7. Observe sources, map, gates, budgets, and history in the UI.
8. Stop early or at a hard cap.
9. Export the complete audit bundle.
10. Delete the SQLite index and rebuild identical observable state.

## Approved dependencies and deferred operations

Approved runtime dependencies: `httpx`, `pypdf`, `trafilatura`, and `bibtexparser`. Approved development dependency: `pytest`. Docker Compose is approved and available. Any additional dependency requires approval.

Local implementation under `src/soleresearch/` is approved. Git initialization, remote creation, submodule registration, merge, push, and PR remain deferred until the human requests them.
