"""Behavioral checks for the reader's evidence and reasoning distinctions."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


APP = Path(__file__).parents[1] / "src/soleresearch/ui/app.js"


def _function(name: str) -> str:
    source = APP.read_text(encoding="utf-8")
    match = re.search(rf"^( *)function {name}\(", source, re.MULTILINE)
    assert match is not None, name
    closing = "\n" + match.group(1) + "}"
    end = source.index(closing, match.start()) + len(closing)
    return source[match.start():end]


DOM = r"""
const assert = require('node:assert/strict');
class Element {
  constructor(tag, text = '', className = '') {
    this.tag = tag; this.ownText = String(text ?? ''); this.className = className;
    this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {};
  }
  set textContent(text) { this.ownText = String(text); this.children = []; }
  get textContent() { return this.ownText + this.children.map(item => item.textContent).join(''); }
  appendChild(item) { this.children.push(item); item.parent = this; return item; }
  append(...items) { items.forEach(item => this.appendChild(item)); }
  replaceChildren(...items) { this.ownText = ''; this.children = []; this.append(...items); }
  insertBefore(item, before) {
    const index = this.children.indexOf(before);
    if (index < 0) return this.appendChild(item);
    this.children.splice(index, 0, item); item.parent = this; return item;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  click() { if (!this.disabled) this.listeners.click?.({stopPropagation() {}}); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this); }
  matches(selector) {
    return selector.startsWith('.') ? this.className.split(' ').includes(selector.slice(1)) : this.tag === selector;
  }
  querySelectorAll(selector) {
    return this.children.flatMap(item => [...(item.matches(selector) ? [item] : []), ...item.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const document = {
  createElement: tag => new Element(tag),
  createTextNode: text => new Element('#text', text),
  getElementById: id => ({id}),
};
let snapshot, selectedNodeId, content = new Element('main'), outlinePreview = new Element('main');
let activeEvidenceMode = 'passages', evidenceSearchQuery = '', evidenceExpanded = {passages: false, sources: false};
let focusedEvidenceId = null, target, outlineGraph = null, selectedOwner = null, activeContext = null, activeTab = null;
let collectionOptions;
function evidenceModeSwitch() { return new Element('div'); }
function renderSearchableCollection(options) {
  collectionOptions = options;
  if (!options.items.length) content.appendChild(new Element('p', options.emptyMessage));
  options.items.forEach(item => content.appendChild(options.renderItem(item)));
}
function selectDraftNode(id) { selectedOwner = id; }
function activateContextTab(view) { activeContext = view; }
function activateTab(tab) { activeTab = tab.id; }
function focusEvidence(id) { focusedEvidenceId = id; }
function appendSynthesisAttribution() { return false; }
function node(id, parent, evidence = [], extra = {}) {
  return {node_id: id, parent_id: parent, title: id, node_type: parent ? 'interpretation' : 'question',
    position: 0, evidence_ids: evidence, authority: 'agent_accepted', maturity: 'exploratory', ...extra};
}
function passage(id, sourceId = 'source') {
  return {evidence_id: id, source_id: sourceId, source_title: 'A source', excerpt: 'A recorded passage.',
    locator: {type: 'section', section: 'Results'}, source_version: 'v1',
    attestations: [{stance: 'context', actor_type: 'agent', actor_id: 'researcher', method: 'manual',
      attested_at: '2026-09-30T12:00:00Z', paraphrase: 'A recorded interpretation.'}]};
}
function makeSnapshot(nodes, evidence, sources = [{source_id: 'source', title: 'A source', source_type: 'markdown'}]) {
  const view = items => ({items, total: items.length, truncated: false});
  return {views: {nodes: view(nodes), evidence: view(evidence), sources: view(sources),
    edges: view([]), proposals: view([]), conflicts: view([])}};
}
"""


FUNCTIONS = (
    "element", "humanize", "coverageLabel", "safeWebUrl", "appendInlineLinks",
    "appendRichTextParagraphs", "card", "details", "viewHeader", "locatorText",
    "activeGraph", "researchContextForNode", "selectedResearchContext", "contextScopeText",
    "contextCoverageLabel", "nodeReviewText", "nodeDepth", "nodeDisplayKind",
    "renderMap", "sourceCard", "renderEvidence", "renderDraftProvenance",
    "labeledParagraph", "appendEvidenceText", "readerParagraphs",
)


def _run(script: str) -> None:
    result = subprocess.run(
        ["node", "-e", DOM + "\n" + "\n".join(_function(name) for name in FUNCTIONS) + "\n" + script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_context_deduplicates_direct_and_child_passages_and_preserves_owners() -> None:
    _run(r"""
snapshot = makeSnapshot([
  node('root', null, ['a']), node('child', 'root', ['a', 'b']), node('sibling', 'root', ['c']),
  node('retired', 'root', ['d'], {retired: true}),
], [passage('a'), passage('b'), passage('c'), passage('d')]);
selectedNodeId = 'root';
const before = JSON.stringify(snapshot);
const context = selectedResearchContext();
assert.deepEqual([...context.directEvidenceIds], ['a']);
assert.deepEqual([...context.branchEvidenceIds], ['a', 'b', 'c']);
assert.deepEqual([...context.additionalEvidenceIds], ['b', 'c']);
assert.equal(context.evidence.length, 3);
assert.deepEqual(context.evidenceOwners.get('a').map(owner => owner.node_id), ['root', 'child']);
assert.equal(contextScopeText(context), '1 directly linked · 2 additional in child branches');
assert.equal(context.evidenceTruncated, false);
assert.equal(JSON.stringify(snapshot), before);
snapshot.views.nodes.items[0].evidence_ids = [];
assert.equal(contextCoverageLabel(selectedResearchContext()), 'Evidence linked in child branches');
assert.equal(coverageLabel('conflicting_evidence'), 'Contradicting evidence linked');
""")


def test_bounded_evidence_is_not_reported_as_complete_and_owner_links_navigate() -> None:
    _run(r"""
snapshot = makeSnapshot([node('root', null), node('child', 'root', ['a', 'unloaded'])], [passage('a')]);
snapshot.views.evidence.total = 2;
snapshot.views.evidence.truncated = true;
selectedNodeId = 'root';
renderEvidence();
assert.equal(selectedResearchContext().missingEvidenceCount, 1);
assert.match(content.textContent, /1 of 2/);
assert.match(content.textContent, /incomplete/);
assert.doesNotMatch(content.textContent, /complete set/);
assert.match(content.textContent, /Recorded stance: Context/);
assert.match(content.textContent, /Assessed by: Agent \(researcher\)/);
const owner = content.querySelectorAll('button').find(item => item.dataset.ownerNodeId === 'child');
assert.ok(owner);
owner.click();
assert.equal(selectedOwner, 'child');
snapshot.views.evidence.items = [];
content = new Element('main');
renderEvidence();
assert.match(content.textContent, /No linked passages are available in the loaded records/);
assert.doesNotMatch(content.textContent, /No .* supports/);
snapshot.views.evidence.items = [passage('a'), passage('unloaded')];
snapshot.views.sources.items = [];
activeEvidenceMode = 'sources';
content = new Element('main');
renderEvidence();
assert.equal(selectedResearchContext().sourcesTruncated, true);
assert.doesNotMatch(content.textContent, /complete set/);
assert.match(content.textContent, /No linked sources are available in the loaded records/);
""")


def test_partial_graph_is_disclosed_but_unrelated_ledger_truncation_does_not_invent_missing_links() -> None:
    _run(r"""
snapshot = makeSnapshot([node('root', null, ['a'])], [passage('a')]);
selectedNodeId = 'root';
snapshot.views.evidence.truncated = true;
snapshot.views.evidence.total = 900;
assert.equal(selectedResearchContext().evidenceTruncated, false);
snapshot.views.nodes.truncated = true;
snapshot.views.nodes.total = 700;
assert.equal(selectedResearchContext().evidenceTruncated, true);
renderEvidence();
assert.match(content.textContent, /at least 1/);
assert.match(content.textContent, /child branches are outside the loaded set/);
assert.doesNotMatch(content.textContent, /complete set/);
""")


def test_reader_preserves_authored_reasoning_without_promoting_generic_prose_to_evidence() -> None:
    _run(r"""
for (const type of ['question', 'concept', 'outline']) {
  target = {dataset: {nodeType: type}};
  const result = readerParagraphs('An authored explanation without a source claim.');
  assert.equal(result[0].textContent, 'An authored explanation without a source claim.');
}
target = {dataset: {nodeType: 'concept'}};
const parts = readerParagraphs('A working idea. Fact: The recorded result is 3. Inference: It may generalize. Interpretation: More checks are needed.');
assert.equal(parts.length, 4);
assert.equal(parts[0].textContent, 'A working idea.');
assert.equal(parts[1].textContent, 'Fact — The recorded result is 3.');
assert.equal(parts[2].textContent, 'Inference — It may generalize.');
assert.equal(parts[3].textContent, 'Interpretation — More checks are needed.');
assert.equal(parts[1].querySelectorAll('strong').length, 2);
target = {dataset: {nodeType: 'conclusion'}};
assert.equal(readerParagraphs('A tentative conclusion.')[0].textContent, 'Conclusion — A tentative conclusion.');
target = {dataset: {nodeType: 'evidence'}};
assert.equal(readerParagraphs('The observed result.')[0].textContent, 'Evidence — The observed result.');
target = {dataset: {nodeType: 'gap'}};
assert.equal(readerParagraphs('Missing evidence includes a replication.')[0].textContent, 'Missing evidence — a replication.');
""")


def test_synthesis_provenance_keeps_own_evidence_and_exposes_review_state() -> None:
    _run(r"""
snapshot = makeSnapshot([
  node('root', null, ['a'], {tags: ['terminal-synthesis'], authority: 'human_accepted'}),
  node('child', 'root', ['b']),
], [passage('a'), passage('b')]);
selectedNodeId = 'root';
const section = new Element('section');
outlinePreview.appendChild(section);
renderDraftProvenance(section, 'root');
assert.match(section.textContent, /1 directly linked · 1 additional in child branches/);
assert.match(section.textContent, /Review 2 linked passages/);
assert.match(section.textContent, /Exploratory · Accepted by a human/);
section.querySelector('button').click();
assert.equal(activeContext, 'inspect');
assert.equal(activeTab, 'tab-evidence');
snapshot.views.nodes.items[0].tags = [];
snapshot.views.evidence.items = [];
renderDraftProvenance(section, 'root');
assert.equal(section.querySelector('button').disabled, true);
assert.match(section.textContent, /outside the loaded records/);
""")


def test_overview_and_source_card_keep_acceptance_separate_from_source_assessment() -> None:
    _run(r"""
snapshot = makeSnapshot([node('root', null), node('child', 'root', ['a'])], [passage('a')]);
selectedNodeId = 'root';
renderMap();
assert.match(content.textContent, /Evidence linked in child branches/);
assert.match(content.textContent, /0 directly linked · 1 additional in child branches/);
assert.match(content.textContent, /Exploratory · Accepted by an agent/);
assert.doesNotMatch(content.textContent, /Accepted by a human|Supported by sources/);
snapshot.views.nodes.items[0].evidence_ids = ['a'];
content = new Element('main');
renderMap();
assert.match(content.textContent, /0 supporting · 0 qualifying · 0 contradicting · 1 context/);
const source = sourceCard({source_id: 's', title: 'A synthesis note', source_type: 'markdown',
  human_reading_state: 'unread', import_method: 'markdown', canonical_url: 'https://example.org/source',
  quality: {authority: 'high', evidence_directness: 'medium', relevance: 'high', publication_status: 'peer_reviewed'}});
assert.match(source.textContent, /Source type: Markdown/);
assert.match(source.textContent, /Human reading: Unread/);
assert.match(source.textContent, /Recorded source assessment: authority High/);
assert.match(source.textContent, /publication assessment: Peer reviewed/);
assert.match(source.textContent, /Imported as: Markdown/);
assert.equal(source.querySelector('a').target, '_blank');
""")


def test_unchanged_refresh_preserves_draft_evidence_control_identity() -> None:
    _run(r"""
snapshot = makeSnapshot([node('root', null, ['a'])], [passage('a')]);
selectedNodeId = 'root';
const section = new Element('section');
outlinePreview.appendChild(section);
renderDraftProvenance(section, 'root');
const originalButton = section.querySelector('button');
renderDraftProvenance(section, 'root');
assert.equal(section.querySelector('button'), originalButton);
originalButton.click();
assert.equal(focusedEvidenceId, 'a');
snapshot.views.nodes.items[0].authority = 'human_accepted';
renderDraftProvenance(section, 'root');
assert.notEqual(section.querySelector('button'), originalButton);
assert.match(section.textContent, /Accepted by a human/);
""")
