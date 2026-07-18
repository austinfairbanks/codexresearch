# Sole Research transition requirement audit

Audit date: 2026-07-18  
Plan: `MCP_PLUGIN_TRANSITION_PLAN.md`, revision r4  
Audited implementation: macOS arm64 developer preview

Status meanings:

- **Verified** — implemented and exercised in this repository or the private Site.
- **Partial** — the locally actionable implementation exists, but a specified UX,
  lifecycle, or release check still needs direct evidence.
- **External gate** — completion requires a signing identity, another platform,
  an authenticated ChatGPT desktop surface, or explicit authorization that is
  not available to this implementation session.

## Outcome and invariants

| Requirement | Status | Evidence or remaining gate |
| --- | --- | --- |
| Local files remain authoritative and Sites is a rebuildable read model | Verified | `src/soleresearch/projection.py`; D1 receives bounded projections only. |
| Permanently read-only browser dashboard | Verified | Site browser routes expose GET reads; authenticated snapshot routes are server-only. `integrations/sites/soleresearch/tests/rendered-html.test.mjs` rejects browser mutation controls. |
| Stable project and revision isolation | Verified | Project-bound snapshot/read routes, canonical hashes, revision-bound cursors, substitution rejection, and cross-project tests. |
| One writer thread per project; separate directories for separate questions | Verified | Project contract and plugin instructions enforce the operating rule. |
| Publication failure cannot corrupt or roll back local research | Verified | Atomic local outbox, bounded retry, idempotency, and stale/conflict rejection in `src/soleresearch/projection.py`. |
| Existing CLI and localhost dashboard remain available | Verified | Compatibility code and regression suite remain intact. |
| Update or uninstall does not delete projects or the Site | Verified on macOS arm64 | The personal-plugin remove/reinstall canary preserved the plugin source, workspace, deployed Site, and byte-identical Application Support registries. A disposable local marketplace also verified update and rollback while preserving the demo project byte-for-byte. See `TRANSITION_LIFECYCLE_CANARY.md`. |

## Gates A–F

| Gate | Status | Evidence or remaining gate |
| --- | --- | --- |
| A — Sites and side-by-side viability | Partial | Owner-only Site deployed at `https://sole-research.general992066.chatgpt.site`; local Chrome QA covers wide, medium, responsive, and 200% rendering; dashboard tests cover interaction/state behavior. The Browser integration initialized successfully in the implementation task, but discovery returned no available Browser instance. Direct authenticated ChatGPT built-in-browser and side-by-side testing is therefore an evidenced external gate. The originally proposed disposable `spikes/` directory was superseded by the production-compatible private Site and was not retained as a separate artifact. |
| B — snapshot publication | Verified | Preferred authenticated live D1 path selected. Project-bound `POST /api/v1/projects/:projectId/snapshots`, atomic latest-revision advancement, bounded history, live revision polling, and successful production publication are verified. |
| C — native workflow packaging | Verified for macOS arm64 | Frozen Python one-file runtime, embedded contracts, checksum, build metadata, SPDX SBOM, and isolated no-target-runtime verifier. Signing is tracked under Phase 5. |
| D — supported platforms | External gate | Matrix is declared in `integrations/codex/soleresearch/release-platforms.json`. Only macOS arm64 is built and cleanly verified here; macOS x86_64, Windows x86_64, and Linux x86_64 need native hosts. |
| E — workspace/project selection | Verified | Explicit persisted workspace selection, immediate-child confinement, reserved-directory, symlink, duplicate-ID, and arbitrary-path fail-closed behavior. |
| F — one writer thread | Verified | Documented in the plan, plugin skill, and project workflow contract. |

## Projection, Site, refresh, and local storage contracts

| Contract | Status | Evidence or remaining gate |
| --- | --- | --- |
| Versioned bounded projection with canonical hash | Verified | `src/soleresearch/projection.py` and versioned contract schemas; 2 MiB maximum; deterministic canonical serialization. |
| Required manifest fields and bounded collection descriptors | Verified | Schema/tests require schema version, stable project/thread IDs, local and published revisions, UTC production time, SHA-256, collection metadata, truncation, and cursor descriptors. |
| Redaction/exclusion rules | Verified | Tests exclude credentials, capability/controller records, transcripts, unsafe paths, source bodies, caches, and unrelated files. |
| Exact project-scoped route contract | Verified | Summary, graph, sources, evidence, outline, revision, and snapshots routes are implemented; compatibility routes remain during migration. |
| Revision-bound paging | Verified | `integrations/sites/soleresearch/db/read-api.ts`; cursors bind project, collection, revision, position, and limit, with a maximum page size of 200. |
| D1 revision ledger and rollback window | Verified | D1 migrations and snapshot transaction retain a bounded 20-revision history. |
| In-place refresh and state preservation | Verified in implementation/tests | Small revision polling, selective reload, camera/focus/tab/search/draft preservation, and stale/error status logic are in the shared dashboard client. Direct built-in-browser observation remains part of Gate A. |
| Durable outbox and harmless offline behavior | Verified | A failed publication remains retryable and cannot mark itself sent or block local mutation. |
| Platform-appropriate nonsecret workspace/Site configuration | Verified | macOS Application Support registry contains the workspace root, Site URL, and credential paths only. |
| Production credentials outside temporary storage | External gate | Current developer credentials are mode-0600 files under `/private/tmp`. Moving sign-in/publisher credentials into durable user storage requires explicit user authorization; the implementation does not silently persist them. |

## Phases 0–7

| Phase | Status | Evidence or remaining gate |
| --- | --- | --- |
| 0 — contracts and Sites proof | Partial | Golden fixtures, workflow inventory, owner-only Site, live revision publication, project isolation, Gate decisions, and coverage inventory are complete. Exact ChatGPT desktop version/layout observation and the proposed separate spike `RESULTS.md` are not available; production evidence supersedes the spike technically but not documentary parity. |
| 1 — services and projection | Verified | Transport-neutral projection service, stable hash, redaction, stale/capability behavior, and HTTP compatibility pass. |
| 2 — read-only Sites dashboard | Partial | Full shared dashboard is packaged, deep links and read clients work, browser mutation is absent, responsive overflow fix is included, and automated UI/layout assertions cover keyboard, refresh, large layouts, and reduced-motion CSS. Authenticated built-in-browser interaction remains external. |
| 3 — secure publication and refresh | Verified | Authenticated D1 ingest, exact retry, conflicts, outbox, bounded backoff, manual retry, history, polling, and project URL result are implemented and production-tested. |
| 4 — complete MCP coverage | Verified | `src/soleresearch/contracts/v1/mcp_mapping.json` maps the authoritative catalog to strict named tools/compatibility commands; protocol, schemas, authority, errors, and stdout discipline are tested. |
| 5 — native runtime | Partial / external gate | macOS arm64 standalone runtime, checksum, metadata, deterministic SPDX SBOM, and isolated init/write/projection/MCP verification pass without target Node or Python. Other platform artifacts, signing/notarization, Gatekeeper/SmartScreen/AV, and clean machines are external gates. |
| 6 — plugin, Site, marketplaces | Partial | Personal marketplace install, cached native path, update, rollback, uninstall/data preservation, reinstall/reconnection, and revoked-credential rejection are verified; skills, named MCP configuration, optional hook, normal Site link, provisioning, recovery, and explicit deletion docs exist. Publishing two real immutable releases and organization/repository distribution remain release checks. |
| 7 — security and compatibility | Partial | Local/security boundaries, narrow Site access, inert rendering, project isolation, credential scrubbing, no tunnels, old projects, and localhost compatibility are verified. Cross-platform and built-in-browser compatibility rows remain external; automated release CI is intentionally out of scope. |

## Test matrix

| Layer | Status |
| --- | --- |
| Domain services, projection, publication, MCP, security, compatibility UI | Verified by the Python suite (272 tests after the responsive assertion update). |
| Sites build/server contract | Verified by production build and four rendered/route/security tests. |
| Live Site | Verified owner-only deployment, D1 publication, summary/revision reads, paged evidence, and project deep link. |
| Dashboard visual QA | Verified locally at wide, medium, and high-density/200% layouts; narrow-toolbar overflow corrected. Built-in-browser manual QA remains external. |
| Packaging | Verified on macOS arm64 with the isolated native verifier. Signing and remaining OS/architecture rows are external. |
| Plugin lifecycle | Install, update, rollback, uninstall preservation, reinstall/reconnection, cached-native launch, and revoked-credential rejection are verified on macOS arm64. The rollback canary used two disposable local cachebuster versions; rollback between two published releases remains a distribution check. |

## Definition-of-done disposition

The developer-preview definition is satisfied on this macOS arm64 workstation:
the installed native plugin needs no target Node/Python runtime or local web
server, covers the workflow, publishes safely to the owner-only Site, and keeps
the localhost fallback and project files intact.

The plan's unrestricted/general-release definition is **not yet satisfied**.
The remaining items are explicit and non-local: authenticated ChatGPT desktop
built-in-browser QA; signed/notarized macOS and signed Windows artifacts;
native builds and clean-machine tests for macOS x86_64, Windows x86_64, and
Linux x86_64; authenticated built-in-browser QA; and publishing/testing two
real immutable distribution releases. The current temporary credential files
also do not survive a reboot unless the user explicitly authorizes durable
secret storage.
