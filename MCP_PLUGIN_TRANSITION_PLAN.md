# Sole Research Codex Plugin + Sites Transition Plan

- **Plan ID:** `SOLERESEARCH-MCP-001`
- **Revision:** `r4`
- **Status:** implemented as a macOS arm64 developer preview on `2026-07-18`; see `TRANSITION_IMPLEMENTATION_STATUS.md` for verified evidence and external general-release gates
- **Feature path:** `src/soleresearch/`
- **Primary outcome:** package the complete Sole Research workflow for Codex and present its permanently read-only dashboard as an OpenAI-hosted Site opened beside the conversation in the ChatGPT desktop app's built-in browser
- **Human approval:** the maintainer explicitly requested full implementation on `2026-07-18`; public distribution, broader Site access, signing identities, and removal of compatibility paths remain separately controlled
- **Relationship to existing vision:** proposed distribution and presentation amendment to `PLANNED_VISION.md`; the files-first model, CLI contracts, and local HTTP dashboard remain supported during migration
- **Human decisions recorded:** `2026-07-13` — the dashboard is permanently read-only; Sites is the primary dashboard surface; the built-in browser or a separately tiled browser is the intended side-by-side UX; the local MCP surface must cover the existing workflow; one active writer thread per project is the supported concurrency rule; Cloudflare and public tunnels are excluded

## Outcome

Sole Research remains conversation-controlled and files-first. Codex and its
plugin perform research, apply authorized workflow operations, and maintain the
authoritative project files. A bounded publisher projects the current project
into a Sites-hosted read model. The Site renders the existing map, draft,
evidence, notes, activity, and history experience without exposing research
mutation controls.

The Site is opened in the ChatGPT desktop app's built-in browser and may remain
visible beside the Codex task. If the desktop layout does not provide a useful
dock, the same project URL can be opened in a separately tiled browser window.
This is the same product surface, not a second implementation.

```text
Codex task
├── conversation                     research direction and human decisions
├── Sole Research plugin             skills and workflow instructions
└── packaged local MCP process        complete existing workflow
        ├── reads/writes authorized project files
        ├── builds a bounded dashboard projection
        └── publishes a revision
                    │
                    ▼
OpenAI Sites
├── authenticated snapshot-ingest boundary
├── D1 read model and revision ledger
├── project-scoped read API
└── hosted dashboard
                    │
                    ▼
Built-in browser beside Codex           read-only interactive dashboard
```

The architecture does not depend on an MCP `ui://` resource, iframe rendering,
ChatGPT app registration, a locally installed frontend toolchain, Cloudflare,
or a manually started web server. MCP remains valuable as the local workflow
and publishing boundary; Sites owns dashboard hosting and rendering.

## Why this shape

Sites is a managed hosting surface in ChatGPT for websites and full-stack web
apps. It supports persistent projects, saved versions and deployments, D1
structured storage, workspace-authenticated identity, restricted sharing, and
local-project linkage through `.openai/hosting.json`.
[Sites documentation](https://learn.chatgpt.com/docs/sites) and
[internal-app guidance](https://learn.chatgpt.com/use-cases/build-and-deploy-internal-apps).

The ChatGPT desktop app's built-in browser gives the user and ChatGPT a shared
view of public pages and local web apps inside a task. It can open from a URL,
the toolbar, or the browser shortcut, and the Browser plugin can inspect it when
the user asks Codex to do so.
[Browser documentation](https://learn.chatgpt.com/docs/browser).

Codex plugins can package skills and MCP configuration, while Codex hosts can
launch local stdio MCP servers. Those documented capabilities are sufficient
for the workflow layer without making undocumented local MCP UI rendering a
release dependency.
[Plugin overview](https://learn.chatgpt.com/docs/plugins) and
[Codex MCP support](https://learn.chatgpt.com/docs/extend/mcp).

Sites is currently a public beta. Availability can depend on plan, region, and
workspace policy. That dependency is an explicit release gate, not an assumed
universal capability.

## Current baseline

- The Python package owns the files-first research model, versioned JSON
  contracts, capability checks, transactions, orchestration, migration, and
  exports.
- `ui.py` mixes reusable dashboard projections with `http.server` routing and
  loopback-specific security behavior.
- The dashboard is dependency-light vanilla HTML, CSS, and JavaScript. It
  already implements the map, draft, evidence, notes, activity, history,
  project isolation, focus, zoom, expansion, and refresh behavior that must be
  retained.
- The Codex integration contains skills, helper scripts, and a Python `Stop`
  hook, but no complete MCP workflow surface and no Sites project.
- Existing project directories and schema versions remain authoritative and
  must stay readable throughout the transition.

## In-scope work

1. Define a stable, bounded dashboard-projection contract derived from current
   project files.
2. Extract projection logic from `ui.py` without changing research semantics.
3. Port the existing read-only dashboard to a Sites-compatible project.
4. Prove private/restricted Sites deployment and built-in-browser use.
5. Add revision-safe snapshot publication and automatic dashboard refresh.
6. Expose the complete existing workflow through bounded model-visible MCP
   tools or explicitly retained packaged compatibility commands.
7. Package the local workflow runtime without requiring target-machine Node,
   Python, `uv`, `pnpm`, or a manual server process.
8. Distribute the plugin through personal, repository, or organization
   marketplaces while keeping Sites provisioning and access explicit.

## Non-goals

- Do not redesign the research data model, graph taxonomy, draft format, or
  visual hierarchy while changing presentation and transport boundaries.
- Do not add any dashboard mutation, editor, annotation composer, approval
  control, generic command forwarding, or hidden write path.
- Do not make browser clicks authoritative research actions. Research direction
  and workflow mutations continue through Codex chat.
- Do not publish project capability records, controller metadata, credentials,
  source document bodies, arbitrary repository files, or chat transcripts.
- Do not require Cloudflare, public tunnels, public DNS controlled by the user,
  or a local dashboard server for the primary experience.
- Do not scrape Codex UI internals or depend on undocumented DOM structure,
  browser docking geometry, or private Sites APIs.
- Do not make the `Stop` hook responsible for research-state correctness.
- Do not add same-project multi-writer reconciliation in this transition.
- Do not submit the plugin or Site publicly without separate human approval and
  review.

## Product invariants

- Local project files remain the sole authoritative research state.
- The hosted Site is a rebuildable read model, never the authority for research
  content or workflow decisions.
- Dashboard visitors cannot mutate research state.
- Every hosted response is scoped to one stable project ID and one published
  revision.
- Independent research questions remain independent project directories.
- One active Codex writer thread per project is supported; concurrent writers
  against the same project are not.
- A publication failure cannot roll back, corrupt, or block local research.
- Plugin update, Site deployment, and uninstall do not delete local projects.
- Existing HTTP and CLI behavior remains available until the replacement has
  passed parity and survived an explicit deprecation decision.

## Decisions and gates

### Gate A — Sites and side-by-side UX viability

Before production refactoring, create a disposable Sites spike with inert
fixture data. It proves the actual product surface rather than the local MCP App
path from revision `r2`.

```text
spikes/codex-sites-dashboard/
├── site/                            minimal Sites-compatible dashboard
├── fixtures/snapshot.json           inert graph/draft/evidence fixture
├── .openai/hosting.json             only after Sites provisions the spike
├── README.md                        exact save/deploy/open/test procedure
└── RESULTS.md                       versions, URL scope, screenshots, verdict
```

The spike must prove:

1. Sites can deploy the fixture dashboard without a target-machine Node server.
2. The deployment can be restricted to the maintainer or workspace rather than
   made public.
3. The Site opens in the ChatGPT desktop app's built-in browser from a URL.
4. The browser view remains usable while the Codex conversation continues.
5. The full graph interactions, draft, evidence preview, tabs, source links,
   search, keyboard navigation, zoom, and reduced-motion behavior work.
6. A new fixture revision can appear without a full page reload or camera jerk.
7. Focused node, camera, expanded nodes, selected tab, evidence preview, search,
   and draft position survive a data refresh when still valid.
8. A project-scoped URL opens only that project and does not display questions
   from another project.
9. The UI shows `Live`, `Updating`, `Stale`, and `Publish failed` states with the
   last successful revision and timestamp.
10. Browser-plugin absence degrades to a normal clickable URL; it does not make
    the dashboard unusable.

The public docs do not promise a permanently docked 50/50 browser layout. Gate
A records the actual desktop layout and verifies that a separately tiled browser
is an acceptable equivalent when docking is unavailable.

### Gate B — snapshot publication path

Phase 0 must prove one supported publication path without relying on an
undocumented Sites API:

1. **Preferred — live read model:** a documented Sites full-stack handler
   accepts an authenticated, revision-protected snapshot and commits it to D1.
   The dashboard polls a bounded revision endpoint and updates in place.
2. **Fallback — saved snapshot deployment:** Codex updates the static snapshot,
   asks Sites to save a new version, and deliberately deploys it. This preserves
   complete dashboard functionality but has deployment-scale update latency.

If the preferred path succeeds, select a publisher-authentication mechanism:

- workspace-authenticated server identity, only if Sites documents and proves
  that flow for non-browser publication; or
- one revocable, site-scoped publisher secret configured outside the project
  and dashboard bundle.

Do not assume the browser's workspace session can authenticate an MCP process.
Do not place the publisher secret in `.openai/hosting.json`, project files,
tool results, logs, the dashboard DOM, or client-side JavaScript.

### Gate C — native local workflow packaging

Recommended first implementation: retain the Python domain code and produce a
self-contained executable with an approved standalone packager. Consider a Go
or Rust rewrite only if the frozen artifact is unacceptably large, slow,
unreliable, or difficult to sign.

Adding an MCP SDK or binary packager is a dependency change and requires
maintainer approval. The selected approach must support deterministic artifacts,
embedded contracts, bounded HTTPS publication, and macOS/Windows signing.

### Gate D — supported platforms

Define the first release matrix before distribution automation. Recommended:

- macOS arm64;
- macOS x86_64 while Codex supports it;
- Windows x86_64;
- Linux x86_64 for CLI/IDE and repository installs.

Add other architectures only after a real host and clean-machine test exist.
The Sites dashboard itself is browser-portable; the platform matrix applies to
the packaged local workflow process.

### Gate E — workspace and project selection

The local workflow process never scans the whole machine. It uses the Codex
workspace root when the host passes supported context or an explicitly
human-selected allowlisted root. Marketplace installs require explicit first-run
selection when no trusted repository context exists.

Project initialization is limited to a validated immediate child of that root.
Symlinks, reserved directories, duplicate project IDs, and arbitrary model-
supplied paths fail closed.

### Gate F — one writer thread per project

The plugin and dashboard must state that one active Codex writer thread owns a
project. Multiple questions may run concurrently only in separate project
directories. Read-only Site instances may coexist because they do not write the
canonical project.

## Dashboard projection contract

The Site consumes a versioned projection rather than raw project files. The
projection manifest requires:

| Field | Rule |
| --- | --- |
| `projection_schema_version` | Required semantic version; incompatible changes increment the major version |
| `project_id` | Required stable ID derived from the validated project contract, never from a display title |
| `thread_id` | Required writer-thread identity or explicit `unknown` for imported legacy projects |
| `project_revision` | Required authoritative local revision/hash used to build the projection |
| `published_revision` | Required monotonically increasing integer within the project |
| `produced_at` | Required UTC timestamp |
| `content_sha256` | Required hash of the canonical serialized projection |
| `collections` | Required bounded descriptors for graph, sources, evidence, discussions, run/audit data, and outline |
| `truncated` | Required boolean with cursor metadata whenever a collection is bounded |

The projection includes only presentation-safe fields needed by the existing
dashboard. It excludes:

- capability and controller records;
- publisher credentials and environment values;
- arbitrary local paths beyond display-safe source locators;
- raw chat transcripts or unrelated prompts;
- source document bodies not already approved for dashboard presentation;
- local caches, temporary files, and untracked repository contents.

Large collections use revision-bound pages. A page cursor binds project ID,
collection, published revision, position, and limit. The Site never mixes pages
from different revisions.

## Sites data and route contract

One Site serves one approved Sole Research workspace. Direct project routes are
the normal entry point:

```text
/projects/<project-id>
/api/v1/projects/<project-id>/summary
/api/v1/projects/<project-id>/graph?cursor=...
/api/v1/projects/<project-id>/sources?cursor=...
/api/v1/projects/<project-id>/evidence?cursor=...
/api/v1/projects/<project-id>/outline
/api/v1/projects/<project-id>/revision
```

The Site does not show a cross-project question list on a project route. Any
future workspace index is separate, access-controlled, and must not alter this
isolation rule.

The preferred live publisher uses one narrow endpoint:

```text
POST /api/v1/projects/<project-id>/snapshots
```

It accepts only the projection schema, requires publisher authentication, uses
`project_id + published_revision + content_sha256` as its idempotency identity,
rejects older or conflicting revisions, and atomically advances the latest
revision. The browser bundle contains no credential and exposes no mutation
request path.

D1 stores the current published revision plus a bounded rollback/history window.
R2 is out of scope unless a later approved dashboard requirement needs published
binary assets. Local source documents are not uploaded merely because R2 is
available.

## Refresh and failure-safety model

- After a successful local workflow checkpoint, the model-visible publish tool
  attempts to publish the new projection.
- The optional completion hook may request a best-effort retry, but disabling
  the hook cannot affect local research correctness.
- The dashboard polls only the small revision endpoint while visible. A changed
  revision triggers bounded collection refreshes, not a whole-page reload.
- Refreshes preserve valid camera and navigation state and discard only state
  referencing nodes or evidence that no longer exists.
- Publication uses a local outbox record written atomically after projection
  generation. A failed request remains retryable and cannot mark itself sent.
- Retries use bounded exponential backoff and idempotency. An older revision can
  never overwrite a newer Site revision.
- The Site displays its last successful revision, publication timestamp, and
  stale/error state. It never implies that an unpublished local change was lost.
- Local project mutation does not wait indefinitely for Sites and does not roll
  back when Sites is unavailable.

## Local storage contract

Research project directories remain in the human-selected workspace and are
never stored inside the plugin cache. Plugin configuration, workspace registry,
publisher metadata, durable state, and cache use platform-appropriate user data
locations. Publisher secrets remain separate from nonsecret workspace metadata
and use user-only permissions or ACLs.

`SOLERESEARCH_CONFIG_HOME`, `SOLERESEARCH_DATA_HOME`, and
`SOLERESEARCH_CACHE_HOME` remain explicit development/test overrides. Production
capability and publisher-secret storage never falls back to an OS temporary
directory.

Plugin update and uninstall never delete local projects or Sites deployments.
Credential revocation and Site deletion are separate explicit actions.

## Transition phases

### Phase 0 — freeze contracts and prove Sites

**Work**

- Record golden fixtures for current workspace, state, outline, sources,
  evidence, notes, activity, history, and authorized workflow responses.
- Inventory every dashboard field, interaction, and helper-script capability.
- Execute Gate A with inert fixture data.
- Execute Gate B far enough to select live D1 publication or saved-snapshot
  deployment.
- Record Sites availability, workspace policy, access scope, deployment URL,
  browser behavior, and exact ChatGPT desktop version.
- Decide Gates C–E and record Gate F as the operating rule.
- Produce a current-workflow coverage inventory from
  `contracts/v1/tool_catalog.json`.

**Exit evidence**

- A restricted Site renders the representative dashboard and opens in the
  built-in browser or documented tiled-browser fallback.
- One new fixture revision reaches the open dashboard through the selected
  publication path.
- Refresh preserves map/navigation state and exposes a correct sync indicator.
- Cross-project fixture content cannot appear on the selected project route.
- `RESULTS.md` distinguishes documented Sites behavior, observed behavior, and
  any fallback.
- No production module, existing plugin, project schema, or HTTP server is
  replaced.

### Phase 1 — extract application services and projection

**Work**

- Move dashboard projections out of `ui.py` into transport-neutral services.
- Define typed internal responses for project summary, paged graph/source/
  evidence/discussion/run/audit collections, outline, and revision.
- Add the versioned dashboard-projection contract and canonical serialization.
- Preserve schemas, capability validation, stale-hash checks, active-run gates,
  transactions, audit behavior, and HTTP compatibility.
- Generate projections through inert text/data APIs only; do not embed project
  content as executable HTML.

**Exit evidence**

- Golden projection responses match current REST behavior semantically.
- Identical project state serializes to the same content hash.
- Redaction tests prove secrets, capabilities, transcripts, and excluded file
  content cannot enter the projection.
- Existing HTTP tests and the full Sole Research suite pass unchanged.

### Phase 2 — port the read-only dashboard to Sites

**Work**

- Create a Sites-compatible project from the existing vanilla dashboard.
- Replace direct loopback REST assumptions with one small read client for the
  project-scoped Sites API or static snapshot selected by Gate B.
- Preserve the complete current read-only experience: map, draft, sources,
  evidence, notes, activity/history, inspector tabs, search, focus, zoom,
  expansion/collapse, evidence previews, source titles, keyboard access,
  reflow, and reduced motion.
- Preserve user navigation state across revision refreshes.
- Add sync status, revision, timestamp, and stale/error affordances.
- Keep the Site bundle free of publisher secrets, mutation controls, analytics,
  and unnecessary third-party assets.

**Exit evidence**

- The existing representative UI fixture passes against both HTTP and Sites
  read clients during migration.
- Browser tests cover wide, medium, mobile, keyboard, 200% zoom, reduced motion,
  large projects, and cross-project isolation.
- Static inspection and network tests prove the browser has no research or
  snapshot mutation path.
- Opening a deep link renders only its selected project.

### Phase 3 — implement secure publication and automatic refresh

**Work**

- Implement the selected Gate B publisher.
- Add projection outbox, idempotency identity, revision conflict handling,
  bounded retries, and explicit manual retry.
- For live mode, add D1 schema/migrations, atomic latest-revision advancement,
  bounded history retention, project-scoped reads, and the narrow ingest route.
- Configure the publisher secret or supported workspace identity outside
  project files and client assets.
- Poll the revision endpoint while visible and refresh changed collections
  without remounting the application.
- Return the project Site URL and publication result from the publish workflow.

**Exit evidence**

- Duplicate publication is idempotent; stale/conflicting publication fails
  closed; interruption leaves the previous Site revision readable.
- Retry after an ambiguous network failure cannot create divergent revisions.
- Browser state remains stable through successful publication.
- Unauthorized writes, project-ID substitution, oversized projections, schema
  mismatch, and secret leakage tests fail closed.
- Disabling the optional completion hook changes only publication timeliness,
  not project correctness or manual publication capability.

### Phase 4 — add complete local MCP workflow coverage

**Work**

- Map every operation in `contracts/v1/tool_catalog.json` to a strict MCP tool
  schema or an explicitly retained packaged compatibility command.
- Cover behavior currently hidden in `inspect_project.py`,
  `dispatch_worker.py`, `gate_snapshot.py`, and `prepare_bundle.py` before
  removing any helper.
- Preserve the catalog's authority, effects, limits, failure modes, and hash in
  a versioned MCP mapping artifact.
- Expose named bounded tools only; never expose unrestricted filesystem access,
  generic shell execution, or a generic `run CLI` tool.
- Keep human/controller capabilities project-bound, external, secret, and
  unavailable to the Site or model unless the existing authority contract
  explicitly grants the operation.
- Add `project_summary`, projection build, publication status, publish, and
  project Site URL tools without giving the Site a workflow write tool.

**Exit evidence**

- MCP initialization, discovery, schemas, reads, writes, retries, malformed
  inputs, stale revisions, cancellations, and typed errors pass.
- Catalog-to-MCP parity proves complete workflow coverage.
- Existing REST/CLI and MCP operations remain semantically equivalent against
  golden fixtures.
- MCP stdout contains protocol bytes only; diagnostics are bounded and
  secret-scrubbed on stderr.

### Phase 5 — package the native local workflow runtime

**Work**

- Produce one self-contained runtime artifact per approved OS/architecture.
- Embed contracts and required nonsecret assets or ship integrity-checked
  adjacent read-only files.
- Establish deterministic names, SHA-256 checksums, version metadata,
  dependency inventory/SBOM, and reproducible inputs.
- Sign/notarize macOS artifacts and sign Windows artifacts. Verify clean-machine
  permissions, quarantine, Gatekeeper, SmartScreen, and antivirus behavior.
- Separate user data and secrets from plugin code; preserve the previous runtime
  during atomic upgrade and rollback.
- Do not package the Sites frontend as a local server. The target machine needs
  no Node, Python, `uv`, `pnpm`, or manual dashboard process.

**Exit evidence**

- Clean machines launch the workflow MCP process, open an existing project,
  perform one authorized operation, build/publish a projection, and return its
  Site URL without a source checkout or language runtime.
- Offline local project operations continue when Sites is unavailable;
  publication reports a retryable stale state rather than corrupting work.

### Phase 6 — assemble the plugin, Sites project, and marketplaces

**Target layout**

```text
src/soleresearch/
├── integrations/codex/soleresearch/
│   ├── .codex-plugin/plugin.json
│   ├── .mcp.json
│   ├── skills/
│   ├── hooks/                       optional, nonessential
│   └── bin/                         generated platform artifact
└── integrations/sites/soleresearch/
    ├── .openai/hosting.json         generated linkage; no secrets
    ├── dashboard source
    ├── server/read routes
    └── D1 migrations                live mode only
```

**Work**

- Package skills and the validated local MCP server; do not add `.app.json` or
  an MCP UI resource unless a later separately approved feature needs them.
- Rewrite skills to call named MCP tools and to open or present the selected
  project Site URL.
- Treat Browser as an optional UX enhancer. When installed and authorized,
  request that it open the project URL; otherwise return a normal clickable
  link.
- Provide personal/repository/organization marketplace installation from pinned
  immutable releases.
- Document Sites provisioning, workspace access, publisher credential setup,
  Site reconnection, clean uninstall, and Site deletion as explicit operations.
- Validate installed/cache paths rather than only the source checkout.

**Exit evidence**

- A new Codex task discovers the workflow, opens or links the correct project
  Site, and does not require a local dashboard process.
- Plugin install, update, rollback, uninstall, credential revocation, and Site
  reconnection tests pass.
- Uninstall removes plugin code but preserves local projects and the Site unless
  the user explicitly deletes them.

### Phase 7 — compatibility, security, and release hardening

**Security checks**

- Local roots, paths, symlinks, capabilities, and mutations fail closed under
  existing rules.
- Site access uses the narrowest approved sharing scope.
- Publisher authentication is site-scoped and revocable; the browser never
  receives it.
- Snapshot schema, size, project identity, revision, hash, and content are
  validated before storage.
- The Site escapes or renders project content inertly and prevents cross-project
  reads.
- Logs and errors omit credentials, capability records, source bodies, and
  private local paths.
- No Cloudflare or public-tunnel path exists.
- One writer thread per project is documented consistently.

**Compatibility checks**

- Maintain a matrix of plugin version, MCP contract version, projection schema,
  Site deployment version, project schema, Codex/ChatGPT desktop version, OS,
  architecture, browser surface, and Sites availability.
- Test built-in-browser and normal-browser operation as separate display claims.
- Run a clean-install canary after material Codex or Sites changes. Treat
  failures as compatibility events, not reasons to scrape private APIs.
- Retain the current HTTP dashboard until Sites has reached parity and survived
  at least one release cycle. Removal requires separate human approval.
- Keep old project files readable; do not combine presentation migration with a
  project-schema rewrite.

Adding automated release CI remains outside repository policy and requires
separate approval.

## Test matrix

| Layer | Required evidence |
| --- | --- |
| Domain services | Current unit tests plus golden read/write fixtures |
| Projection | Canonical hash, redaction, paging, cursor/revision binding, size bounds |
| Sites server | Access scope, ingest auth, D1 transaction/migration, project isolation, rollback window |
| Publication | Outbox, idempotency, stale/conflict handling, retry/backoff, offline behavior |
| Dashboard | Existing interaction parity, deep links, polling refresh, state preservation, keyboard/reflow/zoom/reduced motion |
| MCP workflow | Initialization, schemas, catalog coverage/hash, authority, retry/idempotency, typed failures |
| Security | Paths/symlinks, capabilities, content injection, secret leakage, cross-project access, unauthorized Site writes |
| Packaging | Artifact integrity, signing, clean-machine launch, no target runtime dependency |
| Plugin | Marketplace install, new-task discovery, project URL open/link, update, rollback, uninstall |
| Compatibility | Declared Codex/Sites/browser/OS/architecture combinations and availability constraints |

## Release sequence

1. `0.2.0-dev`: Sites feasibility spike and documented verdict only.
2. `0.3.0-alpha`: projection service and private read-only Site; HTTP remains
   default.
3. `0.4.0-beta`: selected publication path and automatic revision refresh.
4. `0.5.0-beta`: complete local MCP workflow parity and initial native package.
5. `0.6.0-beta`: plugin marketplace installation plus Sites onboarding.
6. `1.0.0`: Sites is the default dashboard after parity, security, access,
   packaging, and compatibility gates pass; HTTP remains documented for one
   release cycle.

Version numbers are coordination markers, not calendar commitments.

## Definition of done

- A machine with Codex but without Node or Python can install Sole Research from
  an approved marketplace or repository clone.
- A new Codex task discovers the complete workflow and can open or link the
  correct project dashboard without Cloudflare, a tunnel, or a local server.
- The dashboard is usable beside the conversation in the built-in browser or a
  separately tiled browser.
- The Site preserves the complete intended read-only functionality: project-
  isolated map, draft, sources, evidence, notes, activity, history, search,
  focus, zoom, expansion, previews, tabs, accessibility, and refresh behavior.
- The browser cannot mutate research or snapshot state.
- Successful local checkpoints publish monotonically versioned projections;
  failures remain visible, retryable, and harmless to local work.
- The plugin covers every existing packaged workflow action except the retained
  HTTP compatibility server, which is not model-callable.
- Independent questions remain separate project directories and Site routes;
  the supported writer rule is explicit.
- Project data survives Codex restart, plugin update/rollback/uninstall, Site
  outage, and publication retry.
- The plugin works with the optional completion hook disabled.
- Existing projects load unchanged, the existing suite remains green, and all
  new projection, Sites, MCP, packaging, plugin, and security tests pass.
- No unresolved correctness, access-control, compatibility, or data-loss issue
  remains.

## Fallback hierarchy

1. **Primary:** live Sites read model with D1 and in-place browser refresh.
2. **Sites fallback:** saved static snapshot versions deliberately deployed by
   Codex; full dashboard interactions remain, with slower updates.
3. **Availability fallback:** current localhost read-only dashboard for users or
   workspaces without Sites access.
4. **Optional future enhancement:** MCP-rendered embedded UI, only after a
   separate smoke test proves the exact host/transport path. It is not on the
   critical path of this plan.

## Implementation record

The approved implementation selected the preferred live D1 path, a frozen
Python runtime, explicit persistent workspace selection, one writer thread per
project, an owner-only Site, and separate site-scoped application and Sites
dispatch credentials. The deployed API retains the existing read-client routes
(`/workspace`, `/state`, `/outline`, and `/completion`) so the same dashboard can
run against localhost and Sites during the compatibility cycle. The specified
project routes and revision-bound collection cursors are also available; the
sole browser-inaccessible write paths are the project-bound `/snapshots` route
and its `/publish` compatibility alias.

The implementation deliberately retains the HTTP dashboard and does not claim
general-release completion for unsigned or untested platform artifacts. Current
evidence and the remaining environment-dependent release checks are recorded in
`TRANSITION_IMPLEMENTATION_STATUS.md`.
