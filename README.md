# Sole Research

Sole Research is a local-first, files-first research workbench. It provides
strict project contracts and deterministic commands for initializing, checking,
importing sources, recording exact-locator evidence, exporting, and rebuilding
the disposable SQLite index.

The 0.2 transition adds an installable Codex plugin with a self-contained local
MCP runtime and a private, read-only Codex Site. Research files remain the sole
authority on disk; the Site receives only a bounded versioned projection and
keeps revision history in managed D1 storage. The packaged runtime needs no
Python or Node installation on the target machine.

## Try the transition locally

The checked-in plugin currently includes the macOS arm64 standalone runtime.
Select an explicit workspace root before installing it so the MCP server never
scans outside the selected directory:

```bash
integrations/codex/soleresearch/bin/sole-research workspace select "$PWD"
integrations/codex/soleresearch/bin/sole-research doctor
```

For source development, the existing `uv` workflow remains available. Generate
a private publisher token outside every research project and configure the same
secret in the Site. An owner-only Site also needs its Sites dispatch-bypass
token stored in a separate mode-0600 file:

```bash
openssl rand -hex -out "$HOME/.config/soleresearch/sites-publisher.token" 32
chmod 600 "$HOME/.config/soleresearch/sites-publisher.token"
uv run sole-research site configure \
  --url "https://YOUR-SITE" \
  --publisher-token-file "$HOME/.config/soleresearch/sites-publisher.token" \
  --sites-auth-token-file "$HOME/.config/soleresearch/sites-auth.token"
uv run sole-research publish ./research-project
```

The configuration stores only the URL and credential-file locations; it never
copies either secret. `publish` accepts plain HTTP only for loopback development. It keeps an exact
retry outbox and uses bounded backoff; a failed publish does not modify
canonical research files. Inspect it with `sole-research publication status
PROJECT`. The Site is permanently read-only and should remain at
owner-only or the narrowest workspace access level that fits the project.

Release builders can produce the standalone artifact, checksum manifest, and
deterministic SPDX runtime inventory together and exercise them without a source
checkout or language-runtime subprocess:

```bash
uv run python scripts/build-native.py --output-dir artifacts --verify
```

The research graph is reviewable rather than conversationally implicit. Typed
nodes and links are proposed as versioned diffs, only the orchestrator applies
them, and each accepted change atomically updates the graph plus its clean
anchored Markdown projection. Raw discussions remain raw until an explicit graph
diff promotes a takeaway.

```bash
uv sync --locked --dev
uv run sole-research init ./research-project --name "Research Project"
uv run sole-research migrate ./research-project  # only for an explicit historical v1 upgrade
uv run sole-research doctor ./research-project
uv run sole-research status ./research-project
uv run sole-research rebuild-index ./research-project
uv run sole-research export ./research-project ./research-export
```

Start a graph and inspect reviewable changes:

```bash
uv run sole-research scaffold ./research-project --question "What do we need to understand?" \
  --controller-token-file "$SOLERESEARCH_CONFIG_HOME/projects/PROJECT_ID/agent.capability.json"
uv run sole-research diff ./research-project propose --operations operations.json \
  --base-revision 1 --actor-type agent --actor-id reader-1
uv run sole-research diff ./research-project apply --diff-id DIFF_ID \
  --controller-token-file "$SOLERESEARCH_CONFIG_HOME/projects/PROJECT_ID/agent.capability.json"
```

`operations.json` is an array of `add`, `update`, `merge`, `move`, `link`,
`unlink`, `retire`, or `restore` operations. Node types are `question`,
`concept`, `evidence`, `interpretation`, `conclusion`, `gap`, and `outline`.
Evidence, interpretation, conclusion, and evidentiary link records must resolve
real exact-locator evidence IDs. Opaque IDs remain stable when titles and order
change.

The human may directly edit headings, authored body text, and order in
`outline.md`, while preserving the unobtrusive `soleresearch:node` comments.
Reconcile those edits explicitly:

```bash
uv run sole-research reconcile ./research-project \
  --controller-token-file "$SOLERESEARCH_CONFIG_HOME/projects/PROJECT_ID/human.capability.json"
```

Human semantics win. Pending agent diffs that touch the same nodes become stale;
orphan, duplicate, missing, or identity-conflicting anchors are recorded in
`graph/conflicts.jsonl` and left queued instead of being silently repaired.
Each attempt also appends an `events/reconciliations.jsonl` record containing
the base/current/result hashes, semantic before/after fields, graph revisions,
proposal status changes, actor, and conflict IDs. `diff ... conflicts` and
`diff ... events` expose those ledgers.

Entity discussions preserve every raw turn and separately mark takeaways:

```bash
uv run sole-research discuss ./research-project add --entity-type node \
  --entity-id NODE_ID --content "Question this interpretation."
uv run sole-research discuss ./research-project promote --discussion-id DISCUSSION_ID \
  --entity-type node --entity-id NODE_ID --content "Candidate takeaway"
```

A promoted discussion entry does not mutate or accept graph state. Use an
explicit diff for that decision.

`init` creates separate mode-0600 agent and human capability records outside the
project under `SOLERESEARCH_CONFIG_HOME`, or the XDG config root when set, and
reports both paths without printing either token. Canonical `apply`, `reconcile`,
and `scaffold` require a record through `--controller-token-file` or a raw token
through `SOLERESEARCH_CONTROLLER_TOKEN`. Effective authority and audit subject
come from the verified record, never a task packet's claimed `actor_type`: the
agent record can produce at most `agent_accepted`, while explicit human
acceptance requires the human record. Capabilities are absent from project
mounts and exports, forbidden in worker task contracts, and mounted only into
the Compose orchestrator CLI. The run repository acts as the controller broker:
workers receive selected context and logical artifact references, never either
capability or the canonical project path. This remains an OS same-user secret
boundary, not protection from another process running as that user.

## Bounded research runs

Start a human- or Sol-controlled run with the balanced envelope (3 cycles, 10
tasks, 15 deeply processed sources, 4 agents including the orchestrator, one
nested worker level, 90 minutes, and a $5 provider ceiling):

```bash
uv run sole-research run ./research-project start --controller sol
uv run sole-research status ./research-project --run-id RUN_ID
uv run sole-research run ./research-project dispatch --run-id RUN_ID \
  --role reader --subquestion "What does this study establish?" \
  --evidence-strategy "Inspect exact passages" --context selected-context.json \
  --capability read_source --artifact-ref source:SOURCE_ID \
  --controller-token-file AGENT_CAPABILITY_FILE
uv run sole-research run ./research-project import-result --run-id RUN_ID \
  --result result.json --controller-token-file AGENT_CAPABILITY_FILE
```

Run manifests and task/result packets are immutable, versioned JSON under
`runs/RUN_ID/`. Budgets, gates, state, extensions, and append-only events are
authoritative files. A nonblocking OS lock permits one writer and one unfinished
run per project. Every descendant inherits the shared remaining envelope;
splitting work cannot reset it. Dispatch reserves bounded deep-source/provider
usage and carries an expiring lease; pending reservations cannot overcommit the
shared remainder. Context is size-bounded, capability-allowlisted, and rejects
secrets or canonical paths. Results must satisfy the task's required operations
and immutable source/evidence provenance. A hard cap durably pauses the run. Resume after
a cap requires an explicit, reasoned, bounded extension:

```bash
uv run sole-research budget ./research-project RUN_ID extend --actor sol \
  --deep-sources 5 --reason "Resolve one bounded disagreement" \
  --controller-token-file AGENT_CAPABILITY_FILE
uv run sole-research resume ./research-project RUN_ID \
  --controller-token-file AGENT_CAPABILITY_FILE
```

Human-controlled results wait as structured gate decisions. Sol-controlled
results may be applied with `agent_accepted` authority and remain listed for
later human ratification. Controller handoff and cycle advancement are accepted
only at a clean gate. Stale results stay reviewable proposals and cannot replace
newer graph state.

Git isolation is disabled by default. An explicitly enabled run requires an
explicit repository, baseline revision, and worktree parent outside that
repository. It creates `soleresearch/run/RUN_ID` and binds autonomous canonical
graph/provenance reads and writes to that linked worktree; the originating tree
retains only the run audit queue. Checkpoints commit only explicit paths locally
in that worktree. Soleresearch exposes no automatic
merge or push operation. Non-Git projects retain the full run workflow.
The worktree stores a validated controller-root binding so direct worktree
mutation still encounters the original run lock and broker. A durable Git-create
intent removes orphan worktrees/branches after interruption. Checkpoints verify
the exact top-level, common Git directory, and branch before staging and commit;
they reject pre-staged files and clean their own staged paths on failure.

All run multi-file mutations share the graph's hash-aware recovery journal. An
abrupt process exit rolls a prepared mutation back on reopen; third-state edits
remain visible recovery conflicts. The project-wide nonblocking writer lock is
shared by run, graph, source, evidence, discussion, and reconciliation writes.
While a run is unfinished, direct canonical writes outside its broker fail.
Expired worker leases are recorded durably and may be cancelled or requeued as
new immutable tasks with lineage; receipt time, not worker-supplied completion
time, controls expiry.

Graph outlines support five node levels (H2 through H6). Deeper structures and
authored node-body headings are rejected rather than clamped. Fenced or indented
code and raw HTML code blocks are parsed as body content, so anchor-like examples
inside them do not acquire identity.

Canonical graph/outline writes use a before/after-hash journal and fsync file
replacements plus containing directories where the platform supports it. On
restart, only targets proven to equal the transaction's after hash are rolled
back; divergent third-state edits are preserved and leave a visible recovery
conflict journal. This provides tested local crash recovery, not a claim of
hardware-level power-loss atomicity on every filesystem.

Import discovery metadata or inspect source content:

```bash
uv run sole-research import ./research-project doi 10.1000/example
uv run sole-research import ./research-project arxiv 2401.01234
uv run sole-research import ./research-project url https://example.org/study --inspect
uv run sole-research import ./research-project bibtex references.bib
uv run sole-research import ./research-project csl-json references.json
uv run sole-research import ./research-project pdf paper.pdf
uv run sole-research import ./research-project markdown notes.md
uv run sole-research import ./research-project outline prior-outline.md
```

URL inspection uses HTTP/HTTPS only, rejects private and reserved destinations,
pins each connection to the vetted DNS address while preserving Host/TLS SNI,
ignores proxy environment variables, and enforces 10-second connect/30-second
read timeouts, one request/second/domain, five redirects, three retryable attempts
total (success may follow at most two failures), an eight-request absolute ceiling,
a 30-second maximum `Retry-After`, and a 15 MiB decoded response ceiling. PDF
inspection also caps pages and extracted characters. Local PDF and Markdown content is inspected without
retaining a source copy unless `--retain-copy` is explicit. Private imports need
a project initialized with `--data-policy local_private` and `--private`.

The human reading queue is deterministic and exposes every quality dimension:

```bash
uv run sole-research source ./research-project read SOURCE_ID --state queued
uv run sole-research source ./research-project quality SOURCE_ID \
  --authority high --methodology-transparency medium \
  --evidence-directness high --relevance high \
  --publication-status peer_reviewed --notes "Primary study"
uv run sole-research source ./research-project queue
```

Evidence can be recorded only after content inspection. PDFs require one-based
page locators. HTML and Markdown accept section, paragraph, figure, table, or
unique captured-passage locators. Figure/table locators resolve a unique typed
caption and exact slice in its page/section context; timestamp locators resolve
parsed `HH:MM[:SS]` Markdown transcript segments and fail clearly without an
inspected timeline. Each passage preserves its immutable raw-plus-extraction source
hash/version, exact locator, bounded excerpt, retrieval time, and separate
human/agent/run/task attestations. Paraphrases and stances are attestation data,
not stable evidence identity:

```bash
uv run sole-research evidence ./research-project add \
  --source-id SOURCE_ID --locator page --page 3 \
  --excerpt "Exact text from page three." \
  --paraphrase "The authors observed the stated result." --stance supports
```

The same commands run in Docker with the current directory mounted at
`/workspace`:

```bash
docker compose run --rm cli init /workspace/research-project
```

Mount an external workspace, or match Linux host ownership, through Compose
variables:

```bash
SOLERESEARCH_WORKSPACE=/absolute/project-parent \
SOLERESEARCH_UID="$(id -u)" SOLERESEARCH_GID="$(id -g)" \
docker compose run --rm cli status /workspace/research-project
```

Canonical research state is JSON, JSONL, Markdown, BibTeX, and CSL-JSON. The
`.soleresearch/` SQLite database and `exports/` are generated and safe to delete.
`source-copies/` and extraction caches are intentionally ignored; accepted
evidence remains auditable through its exact locator, excerpt, and source hash.

## Harness tools and local integrations

Harnesses consume the same strict, packaged v1 tool catalog as the CLI. It
describes each command's authority, inputs, JSON output, side effects, limits,
and failure modes without assuming Codex. Grouped commands additionally expose
per-action parser signatures containing every positional/option type,
requiredness, repeatability, default, enum choice, and semantic constraint:

```bash
uv run sole-research tools list
uv run sole-research tools show --tool-id run.operate
uv run sole-research tools validate-result --file ./result.json
```

The published result contract is a Draft 2020-12 schema with exact operation
branches and output/completion conditionals. Harnesses must still use
`tools validate-result` as the canonical dependency-free semantic preflight
immediately before submission. Passing preflight does not admit a packet: the
run broker separately checks its task identity, provenance, lease, and budget.

The validated Codex plugin lives at
`integrations/codex/soleresearch/`. Its primary skill keeps one orchestrator as
the writer and delegates deterministic inspection, dispatch, and gate snapshots
to bundled scripts. `prepare-zotero-bundle` prepares open citation files for
human review and manual import; it never contacts or mutates Zotero:

The plugin also bundles a fail-safe Codex `Stop` hook. At turn completion it
atomically replaces a small, transcript-free signal in the operating-system
temporary directory. The UI checks `/api/v1/completion` once per second and
refreshes its file-derived state when that signal changes, preserving the
current map focus and draft position. A malformed hook payload, marker write
failure, missing marker, or contract-version mismatch is ignored; the existing
five-second state poll remains the fallback. Set `SOLERESEARCH_REFRESH_SIGNAL`
for both Codex and the UI server only when an explicit shared marker path is
needed. Codex requires the plugin hook to be reviewed and trusted before it can
run.

```bash
uv run sole-research zotero-bundle ./research-project ./zotero-review
```

The bundle contains `references.bib`, `references.csl.json`, and a deterministic
`review-manifest.json` with hashes, per-format duplicate citation-key/DOI/arXiv
diagnostics, BibTeX-to-CSL identity mismatches, and explicit review checks. The
status remains `pending_human_review`; Soleresearch performs no library import.

Generate a harness-neutral adapter only in an explicit absent or empty staging
directory. Generation and testing are deterministic and offline. Approval is a
separate deliberate record and still does not install, register, copy, or
activate anything:

```bash
uv run sole-research adapter generate ./adapter.staging --harness example-harness
uv run sole-research adapter test ./adapter.staging
uv run sole-research adapter approve ./adapter.staging \
  --project ./research-project --reason "Reviewed commands and effects" \
  --controller-token-file "$SOLERESEARCH_CONFIG_HOME/projects/PROJECT_ID/human.capability.json"
```

Generated files carry a strict manifest, per-file hashes, the packaged tool
catalog hash, harness identity, and `not_installed` status. Testing recomputes
the exact generator-owned manifest and bytes before trust and never executes
staged code. Approval requires the explicit project's verified human capability
and records its subject/identity. Unknown versions, unexpected files, hash drift,
nonempty/symlink targets, or failed shared contracts fail closed. No adapter
installation command exists in v1.

## Outline-first local UI

Serve the inspectable outline, map, sources, exact evidence, discussions,
conflicts, gaps, run gates/budgets/history, and audit ledgers directly from the
authoritative project files:

```bash
uv run sole-research serve ./research-project
```

To keep unrelated questions isolated, place each Soleresearch project in its
own immediate child directory and serve the parent as an explicit workspace:

```bash
uv run sole-research serve ./research-workspace/question-a \
  --workspace-dir ./research-workspace
```

The root map then shows one spatially separate portal per valid project
directory. Clicking a portal focuses that tree while keeping every workspace
root mounted, and scopes the outline, sources, evidence, discussions, and run
views to the selected project. Newly initialized immediate child
projects appear on refresh. Symlinked, nested, duplicate-ID, or malformed
entries fail closed; projects with inconsistent state remain visible as needing
repair instead of failing during navigation.

The browser UI has no annotation form. Human research direction comes through
the connected agent conversation: the orchestrator resolves the explicit or
URL-fragment-selected project/node, records the human wording with
`sole-research discuss PROJECT add --entity-type node --entity-id NODE_ID
--content TEXT --actor-type human --actor-id codex-user`, and replies in the
same discussion before changing canonical state. The selected context is
reflected as `#project=PROJECT_ID&topic=NODE_ID` without navigation so a host
such as Codex can pass deterministic context to the agent. Chat messages are
not automatically persisted; the orchestrator must report the returned
discussion ID. Non-research product or coding feedback must not enter the
research ledger.

The default is read-only on `127.0.0.1:8765`. Human editing is an explicit
loopback-only mode and validates the project-bound external human capability
before the server starts:

```bash
uv run sole-research serve ./research-project --edit \
  --controller-token-file "$SOLERESEARCH_CONFIG_HOME/projects/PROJECT_ID/human.capability.json"
```

The browser receives an in-memory CSRF token, never the controller capability.
Saves require both the displayed outline hash and its HTTP ETag, preserve every
active stable anchor, and atomically replace only `outline.md` plus append one
`events/human-edits.jsonl` record. They do not apply or reconcile graph state.
With an unfinished run, editing is allowed only at a clean, active,
human-controlled gate through that run's writer broker; Sol-controlled, busy,
review, paused, and hard-cap gates reject with an actionable conflict. Run
`reconcile` deliberately after reviewing the Markdown edit.

Binding a read-only service beyond loopback requires
`--unsafe-non-loopback`; edit mode remains disabled there in v1. Even then,
Host headers remain limited to loopback names unless each additional DNS name
is explicitly supplied with repeatable `--allowed-host`. The Compose service
publishes only to host loopback, accepts loopback Host values, and mounts no
controller configuration. The UI serves
only packaged CSS/JavaScript, has no CDN or remote assets, and renders project
content using inert DOM text. Its views do not depend on SQLite, so deleting and
rebuilding `.soleresearch/index.sqlite3` does not change observable research
state.

The versioned read endpoints are `GET /api/v1/workspace`, `GET /api/v1/state`,
and `GET /api/v1/outline`; project reads accept the registered `project` query.
Edit mode retains only bounded annotation/legacy-outline API compatibility for the primary project; the packaged UI exposes neither write control. No API route
can apply diffs, import sources, operate runs, change gates, or extend budgets.

Every inspector view reports “showing N of total” and whether its bounded local
display is complete. Map branches show graph parentage plus typed incoming and
outgoing edges. Source cards show authors, publication date, DOI/arXiv identity,
human reading state, every separate quality dimension, and quality notes.
Evidence cards resolve the source title and print every populated exact-locator
field, including captured character offsets and labels. Run cards expose
pending tasks/decisions, every configured/consumed/remaining budget dimension,
and the append-only run event history. The unified chronological audit combines
human edits, reconciliations, migrations, decisions, graph diffs, and run
events; it does not collapse provenance into conversational summaries.

The inspector uses keyboard-operable ARIA tabs (arrow keys plus Home/End), one
tabpanel per view, and a separate quiet live-status region for saves/errors.
The five-second file refresh does not turn the changing inspector into a live
region or repeatedly interrupt assistive-technology users.

Compose exposes the read-only UI back to host loopback:

```bash
SOLERESEARCH_WORKSPACE=/absolute/project-parent \
SOLERESEARCH_PROJECT=/workspace/research-project \
docker compose up ui
```

Portable host helpers live under `scripts/`:

```bash
scripts/sole-research-docker status /workspace/research-project
scripts/serve-ui-docker
scripts/launch-tmux.py ./research-project --session sole-research --dry-run -- codex
scripts/launch-tmux.py ./research-project --session sole-research -- codex
```

The tmux launcher requires Python 3.12+ and `tmux`, validates its session name,
binds the session to one resolved project path, refuses collisions, reuses a
matching session idempotently, passes the orchestration command without shell
evaluation, and prints SSH port-forward guidance. The Docker wrappers require a
POSIX shell and Docker Compose v2. These commands use standard macOS/Linux
facilities; no host database or JavaScript toolchain is required.

## Explicit project migration

Historical Phase-1 projects use the packaged, immutable `outline.meta.json` v1
contract (`schema_version`, `revision`, and `anchors`). Ordinary load/status/
index/export commands raise `MigrationRequired` with the exact `sole-research
migrate PROJECT` command and never rewrite v1 bytes. The migration command is
the only v1-to-v2 path: it accepts an empty Phase-1 graph, preserves the existing
outline bytes, writes strict graph metadata v2 plus missing Phase-3 ledgers in
one recoverable transaction, and appends a deterministic before/after-hash event
to `events/migrations.jsonl`. The preserved Phase-1 outline is deliberately
dirty until its unanchored headings are explicitly reconciled or replaced.

Nonempty untyped Phase-1 graphs are refused because assigning stable identities
and typed semantics requires manual review. Unknown metadata versions also fail
closed. Re-running migration after a completed v2 upgrade is an idempotent
read; interrupted migrations recover through the same canonical transaction
journal before retrying.
