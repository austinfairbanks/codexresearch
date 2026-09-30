# Usability and reasoning review — September 30, 2026

This pass updates the read-only dashboard in `src/soleresearch/ui` and its
generated Site assets. It keeps the circular research map, forest/neutral
palette, serif draft, linked selection, and local files as the source of truth.
The preview uses a temporary copy of the existing peptide project. No research
records were edited, published, or medically reverified.

## What changed

| Problem observed | Result |
| --- | --- |
| A narrow embedded window squeezed the draft beside an unreadable map. | Explicit Side by side, Map, and Read controls. Wide windows start split; smaller windows start with a full-width reader. Below 600px the full-width modes remain available. |
| Generic breadcrumbs and a zoomed-out graph made sections hard to find. | Named breadcrumbs, a numbered Contents menu, and readable search results with their parent topic. Search covers loaded descendants even when collapsed and reveals the selected path. |
| Reloading a selected topic lost context, and overlapping project reads could restore the wrong camera. | URL selection is restored on load. Superseded reads and project navigation cannot replace a later selection. |
| The root said “Needs evidence” beside a count of 20 passages from its branches. | Direct passages and additional passages in child branches are counted separately. Shared passages are deduplicated, and synthesis sections keep their own evidence as well as descendants’ evidence. |
| Recorded stances were presented as broad judgments such as “Sources disagree.” | Labels describe supporting, qualifying, and contradicting records without certifying truth. Claim acceptance, maturity, source type, and recorded source assessments are distinct. |
| Ordinary authored text could gain an “Evidence” label during rendering. | Generic prose retains its wording without that inferred label. Explicit reasoning markers and typed evidence, interpretations, and conclusions retain their meaning. |
| Scoped views could report a “complete set” after the server truncated the underlying records. | Partial views disclose their limits. Missing loaded records are not treated as proof that evidence is absent. |
| Refresh failures were invisible to sighted readers. | A visible connection state and Retry control preserve the last successfully loaded view. Unchanged refreshes preserve draft evidence controls instead of replacing the focused button. |
| A short window left a tiny evidence panel beneath fixed controls. | Short-height windows scroll the document around a usable workbench. At 720×450 the evidence panel measured about 309px high after the fix. |

Keyboard navigation, Escape dismissal, reduced-motion behavior, source links,
and focus after contents/search selection remain part of the interaction model.
The interface also names the selected parent topic so repeated headings such as
“Main effects” remain understandable.

## Writing approach

The referenced chats identified the
[Humanizer skill](https://github.com/softaworks/agent-toolkit/blob/main/skills/humanizer/SKILL.md).
The interface copy uses concrete labels and ordinary sentences, while retaining
research uncertainty and recorded attribution. Stored research prose remains
unchanged; presentation code does not rewrite scientific conclusions.

## Validation

- Python/JavaScript behavioral tests cover evidence deduplication and ownership,
  partial records, reasoning markers, acceptance, stale views, collapsed-node
  search, competing reads, and superseded project navigation.
- Existing UI, geometry, read-only API, publisher, and project tests remain in
  the suite. The Site build and packaged-dashboard checks run against the same
  synchronized assets.
- Browser checks covered 1440×900, 390×844, and a 720×450 short-window layout:
  mode selection, contents, search and empty results, claim/evidence navigation,
  URL context, and horizontal overflow. The checked browser tab reported no
  JavaScript errors. Temporary viewport overrides were reset.
- The preview project passed `doctor`.

## Follow-up worth separating from interface work

The current example's source records include imported Markdown summaries. The
dashboard now makes that container type visible alongside its recorded quality
assessment. Those summaries and their underlying references would need their
own source-verification pass before revising the research conclusions. This
interface review is not evidence that the underlying medical claims are current
or independently verified.

The public Site has not been deployed. Its generated dashboard assets and build
remain aligned with the local preview.
