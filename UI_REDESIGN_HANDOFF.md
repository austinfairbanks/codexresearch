# Soleresearch UI Redesign Handoff

## Task

- Plan: `src/soleresearch/UI_REDESIGN_PLAN.md`, `SOLERESEARCH-UI-001 r20 approved`
- Unit: map-first path navigation, human-readable synthesis, and collision-free layout
- Status: complete
- Scope: UI assets/serving, Phase 6 tests, and this handoff; no dark mode, palette, schema/API, dependency, research-record, or Git action
- Baseline: parent `e351a184ccb21f60cf98a75fd79dd8394898a142`; Soleresearch subtree untracked by explicit human decision; unrelated parent changes preserved

## Changes

| File or area | Outcome | Contract or invariant affected |
| --- | --- | --- |
| `src/soleresearch/ui/layout.js` | Dependency-free deterministic circle layout, route-safe gaps, height-aware root-guide rectangles, exact/final bounds, and human route summaries | Narrow guides stay one-column while eight 36px rows fit, then switch to a compact 2×4 grid when app chrome reduces height; no canonical data writes |
| `src/soleresearch/ui/app.js` | Every parent/child line gains a keyboard/click route control and destination tooltip; root focus uses an eight-item screen-space path guide with rotated direction arrow, short cue, destination, and the same follow action | Root camera holds at `.82`; split-divider updates coalesce one guide relayout into the next animation frame after CSS width changes; camera state is untouched |
| `src/soleresearch/ui/app.js` | Authored synthesis text now leads; compact linked `Sources` line follows; coverage terms use reader-facing language | Canonical titles, bodies, evidence, and provenance remain unchanged and inspectable |
| `src/soleresearch/ui/app.css` | 36px route targets, responsive two-column/single-column root path guide, focus/hover states, hoverable/clamped tooltip, and compact Sources treatment | Existing forest/neutral palette and reduced-motion behavior preserved; guide targets remain fixed-size and screen-space readable |
| `src/soleresearch/ui.py`, `src/soleresearch/ui/index.html` | Serves and loads the local geometry asset before the application | CSP remains local-only; no remote script or dependency |
| `tests/test_phase6_ui.py` | Adds executable dense geometry, the current workflow's 57-visible/56-route topology, direct layout-to-route sweeps, compact 1/8/48/69 scenes, and eight-item guide rectangles at 648×700, 390×420, 324×350, and live-equivalent 324×210 | Determinism, compact overview, eight readable/focusable guide buttons, viewport containment, pairwise non-overlap, exact bounds, and synchronized destination state asserted |

## Validation

| Command or check | Result | Evidence or reason skipped |
| --- | --- | --- |
| `node --check src/soleresearch/ui/layout.js` | pass | no syntax errors |
| `node --check src/soleresearch/ui/app.js` | pass | no syntax errors |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_phase6_ui.py -q` | pass | `27 passed in 2.50s` |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q` | pass | `261 passed in 24.25s` |
| `git diff --check` | pass | no tracked whitespace errors; Soleresearch remains wholly untracked |
| Live Tailnet rendered QA at wide/mobile/200%-effective sizes | pass | 57 nodes, 56 route controls, and 8 root-guide items rendered; DOM rectangle audits found zero overlaps and zero out-of-bounds items at 769.5×795, 390×420, and live-equivalent 324×210 map sizes |
| Live split-resize and navigation QA | pass | keyboard split 50%→30%→50% reflowed all guide items without overlap; root path follow synchronized selected node, hash, Draft, and Inspect; Back restored the root guide and URL |
| Served geometry asset | pass | `/assets/layout.js` returned 200 after the read-only service restart and its SHA-256 matched the local file |

## Contract and safety checks

| Check | Result | Evidence or reason skipped |
| --- | --- | --- |
| Pairwise displayed circles meet `distance >= radius(a) + radius(b) + clearance` | pass | executable Node fixture audits preferred/fallback output and evidence satellites |
| Existing visible positions remain stable across depth-two-plus expansion | pass | layout always receives the complete canonical graph; fixture compares collapsed/expanded coordinates |
| Workspace project clusters remain disjoint after routes/evidence and bounds use final extents | pass | final per-project bounds include node, evidence, target, and cue circles; overlap fails closed |
| Route click, Enter, Space, collapsed-destination expansion, Back, hash, Draft, Inspect, and restored workspace focus | pass | executable activation/navigation state plus application wiring regression contract |
| Route targets and persistent root cues avoid every visible node and each other | pass | targets place first; only simultaneous root hints reserve cue circles; non-root routes expose destination text through the shared hover/focus tooltip and cannot fail on a hidden cue footprint |
| Dense overview and root navigation remain readable | pass | 1/8/48/69 route scenes stay below 10,000px width; root focus stays at 82% while its eight paths move into a fixed non-overlapping guide instead of shrinking with distant topics |
| Tree/route semantics and keyboard model remain valid | pass | node treeitems live under one dedicated tree stage; evidence and route controls live in sibling presentation overlays; Left collapses current before parent navigation |
| Tooltip remains viewport-clamped | pass | horizontal and vertical canvas clamps apply after measured tooltip layout |
| Dynamic content uses DOM creation/`textContent`; no unsafe HTML or remote asset | pass | existing Phase 6 security contract and full suite |
| API, schema, controller, outline-only write boundary, five-second refresh | pass | no API/schema changes; full suite passed |
| Canonical research state mutation | pass | none; all new summaries and layout are presentation-only |

## Artifact ledger

None. No dataset, model, report, export, run-state, source-copy, or external asset was produced or consumed.

## Decisions and blockers

- Approved decisions applied: human approval of `SOLERESEARCH-UI-001 r20` and explicit instruction to proceed uncommitted.
- Ambiguities: none.
- Blockers: none in implementation or automated validation.
- Final correctness and outside-perspective reviews: accepted; no open blocker.
- Live service note: the prior edit capability failed closed as invalid, so the restarted tmux service is intentionally read-only; no capability was minted or replaced.
- No commit, push, or PR is authorized without separate human approval.
