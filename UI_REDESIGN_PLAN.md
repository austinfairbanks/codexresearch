# Soleresearch UI Redesign Plan

- **Plan ID:** `SOLERESEARCH-UI-001`
- **Revision:** `r20`
- **Status:** approved
- **Feature path:** `src/soleresearch/`
- **Human approval:** `2026-07-14` — explicit approval to implement r20 against the recorded uncommitted baseline

## Outcome

Turn the existing secure outline workbench into a quiet, agent-first research instrument. The structural research map is the primary workspace; a selected node owns the contextual evidence inspector; orchestration activity and telemetry gaps are continuously visible; and the paper-shaped outline is an explicit draft projection rather than the default experience.

## Revision 8 independent question workspace

- Unrelated top-level questions are separate Soleresearch project directories,
  not disconnected nodes sharing one source/evidence/run context.
- An explicit workspace root discovers only immediate, non-symlinked projects.
  Each valid project becomes a spatially separate question portal at `Root {N}`.
- Clicking a portal scopes every read to that directory. Root returns to the
  constellation; `Branch {directory}` makes the file boundary visible.
- Invalid historical projects remain visible as disabled “needs repair” roots.
  The server never silently merges, migrates, or repairs their state.
- Only the authenticated primary project is writable; other registered question
  directories are read-only in that server session.

## Revision 9 miniature project graphs

- Root view includes the bounded active-node hierarchy for every question
  directory, with honest truncation metadata at the shared display limit.
- Each project cluster occupies a separate wide grid cell. Preview branches use
  an 88px base diameter; root sizing is refined by revision 10.
- Miniature parent/child edges preserve each project's internal shape. Clicking
  any available preview node opens that directory and focuses the same node.
- The grid is presentation-only; positions are deterministic from sorted project
  and node identity and never enter canonical research state.

## Revision 10 zoom-safe proportional roots

- Root content score is the recorded node, source, and evidence count. Within a
  workspace, the smallest score maps to exactly 2× the branch diameter and the
  largest maps to exactly 5×; intermediate roots interpolate linearly.
- Root/branch containers clip their visual contents. Distant zoom removes
  secondary metadata and branch prose; minimum zoom replaces root prose with a
  compact `Qn` mark. Full titles remain in accessible labels and hover text.
- Child radius expands with its root diameter, and cluster spacing/bounds include
  the largest root so variable sizing cannot overlap or clip neighboring graphs.

## Revision 7 agent-owned transparency

- Semantic meaning stays in authoritative graph, evidence, discussion, diff, and run records. The UI derives bounded projections and never asks a second model to explain another agent.
- A human annotation is one discussion thread. Its visible lifecycle is limited to observed facts: awaiting agent, agent responded, or takeaway proposed.
- One interpretation node is the claim-provenance unit. The agent plugin should keep claims reviewable and attach only directly bearing evidence; the selected draft section exposes that coverage and its evidence links.
- Proposed graph diffs appear as a collapsed review summary. Acceptance, rejection, and comments remain agent-chat/controller operations.
- Coverage labels report structure and recorded stances, not an invented quality score.
- The activity rail surfaces task strategy or result rationale authored by the agent. Search/filter/focus remain local presentation state.
- The draft is AI-authored and read-only in the browser. Human direction enters through annotations; wording/source requests enter through agent chat.

## Revision 2 correction

- Default to a connected outline/map view made of compact question, interpretation, conclusion, and gap nodes. Do not repeat full prose bodies across the canvas.
- Make selection meaningful: the inspector leads with the chosen node's body, evidence, relationships, maturity, authority, and human-review state.
- Add a persistent activity rail derived only from authoritative run and audit files. Clearly distinguish recorded events, declared/used capabilities, and telemetry the current contract does not capture.
- Keep sources, evidence, discussions, runs, and audit as scoped inspector views, but make provenance quality and missing metadata scannable before raw identifiers.
- Move the readable outline and canonical editor behind a Map/Draft workspace switch. The exact anchored Markdown remains the sole mutable payload.
- Preserve the outline-only write boundary. Human research-state decisions remain harness operations until a separately approved mutation API exists; the UI must state that limitation instead of implying unavailable controls.

## Revision 3 spatial navigation

- Clicking a branch moves the map's camera into that node while preserving the selected-node inspector.
- A breadcrumb trail exposes the full focused path and permits deterministic movement back to any ancestor or the overview.
- Zoom out, zoom in, and fit controls change only presentation scale; they never mutate graph state.
- Leaf nodes remain inspectable and can become the focused stage even when they have no descendants.
- Reduced-motion users receive the same navigation without spatial animation.

## Revision 4 linked split workspace

- Desktop presents Map and Draft simultaneously as equal research surfaces; neither is hidden behind a mode switch.
- Exact outline node anchors become safe presentation-only section wrappers. Selecting a graph node scrolls and highlights the matching anchored draft section without changing Markdown.
- The research inspector is no longer a permanent third column. It opens as a dismissible inline drawer inside the map pane when a node is selected, leaving the corresponding draft section visible beside it.
- Sources, evidence, discussion, runs, and audit remain available through the drawer tabs.
- Small screens stack Map then Draft; selection still surfaces the anchored draft section and opens the contextual drawer.

## Revision 5 continuous spatial story

- All active nodes remain on one persistent canvas. Focusing a node moves the camera; it never replaces the graph with a separately rendered subtree.
- Parent/child structure lays out deterministically as radial branches from circular nodes. Root children may use the full circle; deeper children fan outward from their parent.
- Structural and explicit graph edges remain visible across camera movement. Edge styling distinguishes hierarchy from typed cross-links.
- Camera history powers Back. Overview fits the whole graph. Breadcrumbs remain a semantic path, not a rendering boundary.
- Zoom controls and direct canvas panning transform the persistent scene only. Node IDs, graph state, and draft state are unchanged.
- Selecting a node continues to highlight its exact anchored draft section and open the inline inspector beside the map.

## Revision 6 stable right context pane

- Remove the inspector overlay entirely. It must never cover or resize the continuous graph during selection.
- The right half owns two persistent top-level tabs: `Draft` and `Inspect`.
- Node selection updates the highlighted anchored draft section and the inspect content simultaneously without forcing the active right-side tab to change.
- Switching to Draft scrolls the already-selected section into view; switching to Inspect shows the already-selected node and its evidence context.
- Preserve the inspector's project ledgers as secondary tabs inside Inspect, but keep the two-mode right context switch visually primary.

## Revision 11 content-proportional directory branches

- Size each miniature branch from its own evidence count plus immediate child structure, so visual weight reflects recorded local content rather than title length.
- Normalize branch diameters across the directory view from 147px to 196px. The minimum is exactly `1.5×` the 98px evidence diameter, the maximum remains below the prior `3×:1×` cap, and question roots retain their separate content scale.
- Use computed node diameters when placing descendants and calculating canvas bounds, preventing larger content-rich branches from colliding with their parent or siblings.
- Preserve zoom-aware disclosure: distant views show branch type only, while closer views reveal progressively more of the bounded title and evidence count.

## Revision 12 persistent multi-root focus

- Keep the complete workspace stage mounted whenever multiple independent questions exist; selecting a root or branch changes camera focus, never the set of rendered trees.
- Treat the active project as the source for Draft, Inspect, annotations, and activity data without using it to replace the multi-root map.
- Preserve every root and branch's computed size and position across project-context loads, highlight the selected workspace node, and synchronize its project-local draft anchor.
- Retain an overview camera return point so Back or Fit can reveal the surrounding independent questions immediately after a focused selection.

## Revision 13 persistent evidence focus and bounded branch content

- Render active-project evidence satellites directly on eligible leaf nodes in the persistent multi-root stage; do not require a single-project map replacement to expose evidence.
- Selecting a leaf focuses its branch and expands its evidence bubbles. Selecting an evidence bubble centers and slightly increases the map zoom before opening the exact record in Inspect.
- Label every workspace child uniformly as `Branch`; internal research node types remain available in project-local details rather than competing with the directory hierarchy.
- Place branch labels, titles, and evidence counts inside a centered inscribed content box. Clamp by size class, omit secondary metadata on the smallest bubbles, and keep all content clipped within the circular boundary.

## Revision 14 reversible map/draft navigation and shared title disclosure

- Use one map-level tooltip layer for branches and evidence bubbles. Hover and keyboard focus reveal the complete title outside the clipped circle, with placement clamped to the visible canvas.
- Treat the root question and every anchored branch as distinct Draft sections with visible separators and focus treatment.
- Clicking or keyboard-activating any Draft section focuses its corresponding question or branch bubble without replacing the persistent multi-root stage.
- Keep Draft, map selection, evidence expansion, and Inspect synchronized in both navigation directions.

## Revision 15 paper-like reader projection

- Present the root research question as the centered paper title beneath a restrained research-outline kicker.
- Render each anchored branch as a continuous serif subsection with its authored heading, prose, and a subtle academic divider rather than a card or news-feed item.
- Emphasize only explicit reasoning signposts already authored in the outline (`Fact`, `Inference`, `Interpretation`, `Missing evidence`, and `Open question`); do not synthesize or bold inferred claims in the UI layer.
- Keep section click/keyboard navigation and provenance controls intact while constraining prose to a comfortable reading measure.

## Revision 16 prose-first skimmable branches

- Remove standalone branch headings, branch kickers, card borders, and list-like separators from the reader projection.
- Convert each authored branch title into a bold inline `Claim N — …` paragraph; it remains the exact anchored title and map target, not a newly generated summary.
- Split explicit authored reasoning markers into adjacent prose paragraphs: `Evidence — …` followed by `Interpretation — …`, or `Limitation — …` for gaps and missing-evidence branches.
- Bold factual sentences only when the canonical draft explicitly marks them as `Fact:`. The bold reading path must expose the claim and recorded facts without promoting unmarked prose to evidence.
- Preserve continuous paper rhythm, section-to-map navigation, annotations, and exact provenance beneath the paragraph group.

## Revision 17 chrome-free embedded workspace

- Remove the visible product header and authoritative-files footer so the embedded Codex browser gives the research workspace the full available height.
- Keep project identity, annotation mode, save state, and refresh state as screen-reader-only live targets; removing visible chrome must not break existing state updates or accessibility announcements.
- Let the activity rail become the visual top boundary and extend the split map/context workbench to the bottom edge.

## Revision 18 agent-chat human direction

- Remove the browser annotation composer and all packaged-client annotation write code. The UI remains an inspectable reader rather than a second chat/input surface.
- Treat substantive research direction in the connected Codex/ChatGPT conversation as a human discussion turn that the orchestrator must explicitly persist against a verified project entity before acting.
- Reflect the focused project and node in a navigation-free URL fragment so host-provided browser context can resolve references such as “this section” without guessing.
- Keep annotation/discussion API compatibility and historical ledgers readable in Inspect; distinguish the human turn, agent acknowledgement, promoted takeaway, and eventual canonical change.
- Never persist general UI feedback, coding requests, or unrelated conversation as research annotations.

## Revision 19 numbered claim orientation

- Number non-question outline anchors deterministically in canonical outline order as `Claim 1`, `Claim 2`, and so on.
- Use the same stable claim numbers in workspace branch bubbles so the map and prose projection can be cross-referenced without restoring standalone section titles.
- Keep Evidence, Interpretation, and Limitation as unnumbered prose paragraphs beneath their owning claim.

## Revision 20 map-first path navigation

- Treat every rendered parent/child connection as a navigable path. Add a small
  directional control on each connection that can focus and zoom to its
  destination without requiring the user to zoom out and rediscover the map.
- When a node is focused, surface the controls for paths leaving that node.
  Root paths also show a persistent three-to-seven-word destination cue; other
  paths expose the cue on hover or keyboard focus. Cues are derived
  deterministically from canonical titles and bodies, with common structural
  headings translated into plain language such as `How it works`, `When to use
  it`, and `Tradeoffs`.
- Route controls use the existing map tooltip layer, preserve click and keyboard
  parity, meet the 24 CSS-pixel minimum target, and synchronize camera focus,
  Back history, URL context, Draft highlight, and Inspect context.
- Make the readable synthesis lead with the authored claim or explanation.
  Move source links into a concise `Sources` line instead of repeatedly
  generating phrases such as `Evidence from … shows that …`. Use human-facing
  coverage labels while keeping exact evidence, provenance, identifiers, and
  operational metadata available in Inspect.
- Do not silently rewrite canonical research state from presentation code. Any
  later editorial rewrite of stored titles or bodies must use the normal graph
  diff and human-review workflow.
- Replace fixed-radius best effort with a deterministic post-layout geometry
  check. Every simultaneously visible node must satisfy
  `distance(a, b) >= displayed_radius(a) + displayed_radius(b) + clearance`,
  including the maximum selected/hover extent. Expanded depth-two-plus branches
  must reserve stable positions rather than pushing siblings into one another.
- Compute canvas and project-cluster bounds from final circle extents. If the
  preferred radial layout fails validation, use a deterministic spaced fallback
  and never present the invalid layout.
- Keep the full right context pane, existing forest/neutral palette, read-only UI
  boundary, exact anchors, authoritative refresh, and continuous multi-root
  canvas. Dark mode, palette redesign, schema/API changes, dependencies, and a
  graph library remain out of scope.

### Revision 20 validation

- Add a dependency-free geometry fixture covering depth five, 8–12 siblings,
  unequal node diameters, expanded depth-two-plus subtrees, multiple roots, and
  evidence rings. Assert zero pairwise intersections, disjoint project bounds,
  deterministic output, and stable existing positions across expansion.
- Exercise route controls with click, Enter, and Space; verify tooltip focus and
  hover parity, full accessible destination titles, Back history, URL context,
  Draft focus, and Inspect synchronization.
- Render the current Classical Computer Vision workflow plus the dense synthetic
  fixture at `1440×900`, `1024×768`, `390×844`, and 200% browser zoom. Run a DOM
  circle-distance audit after focus, expansion, refresh, and evidence expansion.
- Run focused Phase 6 UI tests, JavaScript syntax checks, and the full
  Soleresearch test suite before handoff.
- Keep route tooltips dismissible, hoverable, and persistent as required by
  [WCAG 1.4.13](https://www.w3.org/WAI/WCAG22/Understanding/content-on-hover-or-focus.html),
  keep their targets at least 24 CSS pixels per
  [WCAG 2.5.8](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html),
  and retain the keyboard model from the
  [ARIA tree-view pattern](https://www.w3.org/WAI/ARIA/apg/patterns/treeview/).
- Use the same explicit radius-sum invariant documented by
  [D3's collision force](https://d3js.org/d3-force/collide) without adding D3
  or another graph dependency.

## Evidence-based decisions

- Use scale, hierarchy, contrast, balance, alignment, and proximity to direct attention; limit the composition to a small number of type sizes and visual signals. [NN/g visual-design principles](https://www.nngroup.com/articles/principles-visual-design/)
- Minimalism must not hide tools or increase recall and attention-switching cost. [NN/g on “zen mode”](https://www.nngroup.com/articles/zen-mode/)
- Use a constrained 4/8px spacing scale and visible key lines instead of one-off gaps. [Carbon spacing](https://carbondesignsystem.com/elements/spacing/overview/) and [2x Grid](https://carbondesignsystem.com/elements/2x-grid/overview/)
- Keep writing lines near 70–75 characters and adapt from one column on small screens to a content-plus-inspector desktop layout. [GOV.UK layout](https://design-system.service.gov.uk/styles/layout/)
- Use a compact, consistent type scale and avoid mixing typefaces in ways that obscure hierarchy. [Apple typography](https://developer.apple.com/design/human-interface-guidelines/typography)
- Keep a visible focus perimeter at least 2 CSS px with sufficient contrast, and maintain WCAG text/non-text contrast. [W3C focus appearance](https://www.w3.org/WAI/WCAG22/Understanding/focus-appearance) and [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
- Tabs remain appropriate because regular users switch quickly among related inspector views, but labels and selected state must be unmistakable. [GOV.UK tabs](https://design-system.service.gov.uk/components/tabs/)

## Visual system

- Neutral canvas and white surfaces; no gradient, glass, decorative illustration, or dark masthead.
- Forest is the sole identity/primary-action color; rust is warning-only; blue is focus-only.
- System sans-serif for product chrome; restrained serif only for outline content.
- Spacing tokens: `4, 8, 12, 16, 24, 32, 48`.
- Radius tokens: `4, 8, 12`; shadows only where they clarify the editor layer.
- Type scale: compact metadata, body, section, project title; no arbitrary sizes.

## Build units

1. **Shell and activity** — compact project identity, Map/Draft switch, honest save state, and a persistent authoritative activity summary.
2. **Structural map** — connected hierarchy, compact nodes, visible evidence/gap state, selection, and relationship cues without duplicating paper prose.
3. **Focused inspector** — selected-node context first, then source/evidence/discussion/run/audit views with transparent provenance and telemetry limits.
4. **Draft and interaction polish** — readable outline projection, explicit canonical editor, stable refresh, cross-navigation, keyboard/focus integrity, and responsive reflow.

## Contracts and invariants

- Preserve all API routes, payloads, security headers, CSRF/ETag checks, controller authority, active-run broker rules, and outline-only write boundary.
- Preserve the six inspector tab/tabpanel relationships and add two top-level Draft/Inspect relationships; both tab groups keep arrow/Home/End behavior and the first inspector tab retains the stable `map` view key.
- Render project/source content through `textContent`; no `innerHTML`, unsafe links, remote fonts, icons, scripts, or analytics.
- Keep exact-locator evidence, source quality, gates, budgets, and audit history visible; collapse only secondary technical metadata.
- Keep the five-second refresh, but skip unnecessary DOM replacement and restore active focus/scroll when data changes.

## Scope

Primary files:

- `src/soleresearch/ui/index.html`
- `src/soleresearch/ui/app.css`
- `src/soleresearch/ui/app.js`
- `src/soleresearch/ui.py`
- `tests/test_phase6_ui.py`

No schema changes, new dependencies, UI framework, rich-text editor, graph library, dark theme, or research-state mutation controls. The existing read-only state response may expose additional already-validated run/task/result detail.

## Acceptance

- The product reads visually as one intentional system at wide, medium, and mobile widths.
- Structural map remains the first and largest task surface; Draft is an explicit projection.
- A user can tell whether research is active, what the latest recorded action was, which worker tasks/capabilities are recorded, and which telemetry is absent without opening raw files.
- Information hierarchy is understandable without relying on color alone.
- Dense evidence/provenance is scannable; raw identifiers remain available without dominating.
- Keyboard, focus, target size, reduced-motion, contrast, reflow, and semantic contracts remain intact.
- Focused and full tests pass.
- Rendered QA covers `1440×900`, `1024×768`, `390×844`, keyboard interaction, 200% zoom, and populated/read-only/edit states when a browser backend is available.

## Rendered validation

The live Tailnet workspace is available to the in-app browser. Revision 6 validation must cover node selection in both right-pane modes, mode persistence during selection, synchronized Draft/Inspect content, and preservation of every mounted graph node.
