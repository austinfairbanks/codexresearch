# Soleresearch Dispatch

- **Plan:** `src/soleresearch/UI_REDESIGN_PLAN.md`, `SOLERESEARCH-UI-001 r20 approved`
- **Unit:** map-first path navigation, human-readable synthesis, and collision-free layout
- **Goal:** make every map connection directly navigable, make the reader lead with authored meaning instead of generated attribution filler, and guarantee that visible nodes do not overlap after expansion or focus.
- **Non-goals:** dark mode; palette redesign; canonical research-content rewrites; schema/API changes; dependencies; graph libraries; mutation controls; live-source access; Git actions.
- **Ownership:** `src/soleresearch/src/soleresearch/ui/{index.html,app.css,app.js}`; an optional dependency-free local layout asset and its serving/package wiring; `src/soleresearch/tests/test_phase6_ui.py`; this unit's plan/dispatch/handoff docs.
- **Acceptance:** directional route controls focus their destination and synchronize Back/URL/Draft/Inspect; root routes show concise deterministic cues; synthesis prose precedes a compact Sources line; pairwise displayed-circle clearance is validated for dense and expanded layouts; existing UI/security contracts remain intact.
- **Contract checks:** presentation-only deterministic layout; stable node/evidence IDs and exact anchors; `textContent` rendering; CSP/local assets; five-second refresh; outline-only write boundary; 24px route targets; hover/focus tooltip parity; no canonical project mutation.
- **Validation:** `UV_CACHE_DIR=/tmp/soleresearch-uv-cache uv run --frozen pytest tests/test_phase6_ui.py -q`; JavaScript syntax checks; full `UV_CACHE_DIR=/tmp/soleresearch-uv-cache uv run --frozen pytest -q`; live Tailnet rendered QA at wide, medium, mobile, and 200% zoom with programmatic overlap audits.
- **Prohibited:** changing research records or active run state; adding dependencies; modifying unrelated parent work; committing, pushing, or opening a PR.
- **Stop when:** focused/full tests pass, rendered path and collision checks pass, and final correctness/outside reviews report no unresolved must-fix finding; escalate any contract ambiguity instead of improvising.
- **Baseline:** parent `e351a184ccb21f60cf98a75fd79dd8394898a142`; Soleresearch subtree untracked; unrelated parent changes preserved.
