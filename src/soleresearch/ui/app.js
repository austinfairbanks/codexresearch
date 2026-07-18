"use strict";

const geometry = window.SoleResearchLayout;
const boot = JSON.parse(document.body.dataset.bootstrap);
const editor = document.getElementById("outline-editor");
const outlinePreview = document.getElementById("outline-preview");
const mapCanvas = document.getElementById("map-canvas");
const mapBreadcrumb = document.getElementById("map-breadcrumb");
const mapZoomOut = document.getElementById("map-zoom-out");
const mapZoomIn = document.getElementById("map-zoom-in");
const mapFit = document.getElementById("map-fit");
const mapBack = document.getElementById("map-back");
const researchSearch = document.getElementById("research-search");
const coverageFilter = document.getElementById("coverage-filter");
const researchMatchCount = document.getElementById("research-match-count");
const mapFind = document.querySelector(".map-find");
const splitWorkspace = document.querySelector(".split-workspace");
const splitDivider = document.getElementById("split-divider");
const researchInspector = document.getElementById("research-inspector");
const draftWorkspace = document.getElementById("draft-workspace");
const saveState = document.getElementById("save-state");
const liveStatus = document.getElementById("live-status");
const refreshState = document.getElementById("refresh-state");
const tabs = Array.from(document.querySelectorAll('.inspector [role="tab"][data-view]'));
const panels = Object.fromEntries(tabs.map((tab) => [tab.dataset.view, document.getElementById(tab.getAttribute("aria-controls"))]));
const inspectorMore = document.getElementById("inspector-more");
const contextTabs = Array.from(document.querySelectorAll('[role="tab"][data-context-view]'));
const contextPanels = {draft: draftWorkspace, inspect: researchInspector};
const renderedSignatures = {};
let content = null;
let previewContent = null;
let snapshot = null;
let workspace = null;
let activeProjectId = null;
let workspaceOverview = false;
let activeView = "map";
let activeEvidenceMode = "passages";
let evidenceSearchQuery = "";
let evidenceExpanded = {passages: false, sources: false};
let focusedEvidenceId = null;
let historySearchQuery = "";
let historyExpanded = false;
let activeContextView = "draft";
let selectedNodeId = null;
let focusRootId = null;
let cameraScale = 1;
let cameraX = 0;
let cameraY = 0;
let cameraHistory = [];
let graphPositions = new Map();
let graphBounds = null;
let dragState = null;
let splitDragState = null;
let splitRatio = .5;
let primaryMapSignature = null;
let expandedBranchIds = new Set();
let expansionProjectId = null;
let expandedEvidenceParentId = null;
let initialLoadComplete = false;
let completionSignalId;
let completionRefreshInFlight = false;
let completionRefreshQueued = false;
let tooltipHideTimer = null;
let tooltipTarget = null;
let rootGuideLayoutFrame = null;

function element(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined && text !== null) item.textContent = String(text);
  if (className) item.className = className;
  return item;
}

function setText(item, value) {
  const next = String(value);
  if (item.textContent !== next) item.textContent = next;
}

function activeWorkspaceProject() {
  return workspace && workspace.projects.find((item) => item.project_id === activeProjectId);
}

function projectApi(path) {
  return activeProjectId ? `${path}?project=${encodeURIComponent(activeProjectId)}` : path;
}

function splitLimits() {
  const width = Math.max(splitWorkspace.clientWidth - splitDivider.offsetWidth, 1);
  const minimum = Math.max(.22, Math.min(.4, 280 / width));
  return {width, minimum, maximum: 1 - minimum};
}

function setSplitPosition(ratio, {announce = false} = {}) {
  const {minimum, maximum} = splitLimits();
  const next = Math.max(minimum, Math.min(maximum, ratio));
  splitRatio = next;
  const percent = Math.round(next * 1000) / 10;
  splitWorkspace.style.setProperty("--split-position", `${percent}%`);
  scheduleRootPathGuideLayout();
  splitDivider.setAttribute("aria-valuenow", String(Math.round(percent)));
  splitDivider.setAttribute("aria-valuetext", `${Math.round(percent)} percent map, ${100 - Math.round(percent)} percent context`);
  if (announce) liveStatus.textContent = `Workspace split: ${Math.round(percent)} percent map and ${100 - Math.round(percent)} percent context`;
}

function splitRatioFromPointer(clientX) {
  const rect = splitWorkspace.getBoundingClientRect();
  const {width} = splitLimits();
  return (clientX - rect.left) / width;
}

function humanize(value) {
  const words = String(value ?? "")
    .replace(/[:/]+/g, " · ")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Not recorded";
}

function shortIdentifier(value) {
  const text = String(value || "");
  const suffix = text.includes("_") ? text.split("_").pop() : text;
  return suffix ? suffix.slice(0, 6) : "unknown";
}

function displayClaimTitle(title) {
  const value = String(title || "Untitled research node").trim();
  return value.replace(/^\d+(?:\.\d+)*[.)]?\s+/, "").trim() || value;
}

function routeCue(node) {
  return geometry.routeCue({title: displayClaimTitle(node && node.title), body: node && node.body});
}

function coverageLabel(state) {
  return ({
    linked_support: "Supported by sources",
    linked_context: "Sources provide context",
    conflicting_evidence: "Sources disagree",
    graph_conflict: "Ideas need reconciliation",
    open_gap: "Open gap",
    unlinked: "Needs evidence",
  })[state] || humanize(state);
}

function safeWebUrl(value) {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch (_error) {
    return null;
  }
}

function appendInlineLinks(item, text) {
  const linkPattern = /\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g;
  let cursor = 0;
  let match = linkPattern.exec(text);
  while (match) {
    if (match.index > cursor) item.appendChild(document.createTextNode(text.slice(cursor, match.index)));
    const href = safeWebUrl(match[2]);
    if (href) {
      const link = element("a", match[1], "inline-source-link");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", `${match[1]} (opens source in a new tab)`);
      link.addEventListener("click", (event) => event.stopPropagation());
      item.appendChild(link);
    } else {
      item.appendChild(document.createTextNode(match[0]));
    }
    cursor = linkPattern.lastIndex;
    match = linkPattern.exec(text);
  }
  if (cursor < text.length) item.appendChild(document.createTextNode(text.slice(cursor)));
}

function appendRichTextParagraphs(item, text, className) {
  String(text).split(/\n\s*\n/).filter((value) => value.trim()).forEach((value) => {
    const paragraph = element("p", null, className);
    appendInlineLinks(paragraph, value.replace(/\s*\n\s*/g, " ").trim());
    item.appendChild(paragraph);
  });
}

function proseClause(title) {
  const value = displayClaimTitle(title).replace(/[.!?]+$/, "");
  if (/^(Canny|FAST|Gabor|Gaussian|Harris|JPEG|ORB|SIFT|SURF)\b/.test(value)) return value;
  return value ? value.charAt(0).toLocaleLowerCase() + value.slice(1) : "the interpretation is supported";
}

function nodeEvidenceSources(node) {
  if (!snapshot || !node) return [];
  const evidenceById = new Map(snapshot.views.evidence.items.map((record) => [record.evidence_id, record]));
  const seen = new Set();
  return (node.evidence_ids || []).flatMap((evidenceId) => {
    const evidence = evidenceById.get(evidenceId);
    if (!evidence) return [];
    const key = evidence.source_id || `${evidence.source_title}:${evidence.source_url || ""}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [evidence];
  });
}

function appendSourceReference(item, evidence) {
  const href = safeWebUrl(evidence.source_url);
  if (!href) {
    item.appendChild(document.createTextNode(evidence.source_title));
    return;
  }
  const link = element("a", evidence.source_title, "inline-source-link");
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.setAttribute("aria-label", `${evidence.source_title} (opens source in a new tab)`);
  link.addEventListener("click", (event) => event.stopPropagation());
  item.appendChild(link);
}

function appendSynthesisSources(item, node) {
  const sources = nodeEvidenceSources(node);
  if (!sources.length) return false;
  item.appendChild(element("strong", "Sources — "));
  sources.forEach((source, index) => {
    if (index > 0) item.appendChild(document.createTextNode(index === sources.length - 1 ? " · " : ", "));
    appendSourceReference(item, source);
  });
  return true;
}

function appendSynthesisAttribution(item, node) {
  return appendSynthesisSources(item, node);
}

function readableLines(value, prefix = "") {
  if (value === null || value === undefined) return [prefix ? `${prefix}: not recorded` : "Not recorded"];
  if (Array.isArray(value)) {
    if (!value.length) return [prefix ? `${prefix}: none` : "None"];
    return value.flatMap((item, index) => readableLines(item, prefix ? `${prefix} ${index + 1}` : `Item ${index + 1}`));
  }
  if (typeof value === "object") {
    return Object.entries(value).flatMap(([key, item]) => readableLines(item, prefix ? `${prefix} · ${humanize(key)}` : humanize(key)));
  }
  return [`${prefix ? `${prefix}: ` : ""}${value}`];
}

function renderOutlinePreview(markdown) {
  if (previewContent === markdown) return;
  const fragment = document.createDocumentFragment();
  const lines = markdown.split(/\r?\n/);
  let paragraph = [];
  let list = null;
  let code = null;
  let fence = null;
  let target = fragment;
  let pendingHeading = null;
  let claimNumber = 0;
  const outlineGraph = snapshot ? activeGraph() : null;
  const outlineClaimNumbers = outlineGraph
    ? hierarchicalClaimNumbers(outlineGraph.nodes, outlineGraph.byId, outlineGraph.children)
    : new Map();

  function append(item) {
    target.appendChild(item);
  }

  function flushPendingHeading(destination = target) {
    if (pendingHeading && destination.dataset && destination.dataset.synthesisDetail === "true") {
      pendingHeading = null;
      return;
    }
    if (pendingHeading && destination.classList && destination.classList.contains("outline-node-section") && !destination.classList.contains("outline-question-section")) {
      const node = outlineGraph && outlineGraph.byId.get(destination.dataset.outlineNodeId);
      const depth = node && outlineGraph ? nodeDepth(node, outlineGraph.byId) : 1;
      const childCount = node && outlineGraph ? (outlineGraph.children.get(node.node_id) || []).length : 0;
      const displayKind = node ? nodeDisplayKind(node, depth, childCount) : "Section";
      const claim = element("p", null, "draft-prose draft-claim");
      const title = displayClaimTitle(pendingHeading.textContent).replace(/[.!?]+$/, "");
      claim.appendChild(element("strong", `${displayKind} ${destination.dataset.claimNumber} — ${title}.`));
      destination.appendChild(claim);
    } else if (pendingHeading) {
      destination.appendChild(pendingHeading);
    }
    pendingHeading = null;
  }

  function labeledParagraph(label, className = "") {
    const item = element("p", null, `draft-prose${className ? ` ${className}` : ""}`);
    item.appendChild(element("strong", `${label} — `));
    return item;
  }

  function appendEvidenceText(item, text) {
    const marker = /\bFact:\s*/g;
    let cursor = 0;
    let match = marker.exec(text);
    while (match) {
      if (match.index > cursor) appendInlineLinks(item, text.slice(cursor, match.index));
      const factStart = marker.lastIndex;
      const nextMarker = marker.exec(text);
      const factEnd = nextMarker ? nextMarker.index : text.length;
      const factBlock = text.slice(factStart, factEnd);
      const sentenceBoundary = factBlock.search(/[.!?]\s+(?=[A-Z])/);
      const factSentenceEnd = sentenceBoundary >= 0 ? sentenceBoundary + 1 : factBlock.length;
      const strong = element("strong");
      appendInlineLinks(strong, factBlock.slice(0, factSentenceEnd).trim());
      item.appendChild(strong);
      if (factSentenceEnd < factBlock.length) appendInlineLinks(item, factBlock.slice(factSentenceEnd));
      cursor = factEnd;
      match = nextMarker;
    }
    if (cursor < text.length) appendInlineLinks(item, text.slice(cursor));
  }

  function readerParagraphs(text) {
    const nodeType = target.dataset ? target.dataset.nodeType : null;
    if (target.dataset && target.dataset.synthesisDetail === "true") {
      const synthesis = element("p", null, "draft-prose draft-synthesis");
      const mappedNode = outlineGraph && outlineGraph.byId.get(target.dataset.outlineNodeId);
      appendInlineLinks(synthesis, text);
      const items = [synthesis];
      if (mappedNode && target.dataset.attributionRendered !== "true") {
        const sources = element("p", null, "draft-prose draft-sources");
        if (appendSynthesisAttribution(sources, mappedNode)) items.push(sources);
        target.dataset.attributionRendered = "true";
      }
      return items;
    }
    if (target.dataset && target.dataset.terminalSynthesis === "true") {
      const synthesis = element("p", null, "draft-prose draft-synthesis");
      appendInlineLinks(synthesis, text);
      return [synthesis];
    }
    const reasoning = text.match(/\b(Inference|Interpretation):\s*/);
    if (reasoning) {
      const evidenceText = text.slice(0, reasoning.index).trim();
      const interpretationText = text.slice(reasoning.index + reasoning[0].length).trim();
      const items = [];
      if (evidenceText) {
        const evidence = labeledParagraph("Evidence", "draft-evidence");
        appendEvidenceText(evidence, evidenceText);
        items.push(evidence);
      }
      if (interpretationText) {
        const interpretation = labeledParagraph(reasoning[1] === "Inference" ? "Interpretation" : reasoning[1], "draft-interpretation");
        appendInlineLinks(interpretation, interpretationText);
        items.push(interpretation);
      }
      return items;
    }
    const missingEvidence = text.match(/^Missing evidence\s*(?::|includes)?\s*/i);
    const label = nodeType === "gap" || missingEvidence ? "Limitation" : nodeType === "interpretation" ? "Interpretation" : "Evidence";
    const item = labeledParagraph(label, label === "Limitation" ? "draft-limitation" : label === "Interpretation" ? "draft-interpretation" : "draft-evidence");
    const body = missingEvidence ? text.slice(missingEvidence[0].length).trim().replace(/^includes\s+/i, "") : text;
    if (label === "Evidence") appendEvidenceText(item, body);
    else appendInlineLinks(item, body);
    return [item];
  }

  function flushParagraph() {
    if (paragraph.length) readerParagraphs(paragraph.join(" ")).forEach(append);
    paragraph = [];
  }

  function flushList() {
    if (list) append(list);
    list = null;
  }

  function flushCode() {
    if (code) {
      const pre = element("pre");
      pre.appendChild(element("code", code.join("\n")));
      append(pre);
    }
    code = null;
    fence = null;
  }

  lines.forEach((line) => {
    const fenceMatch = line.match(/^\s*(`{3,}|~{3,})/);
    if (fence) {
      if (fenceMatch && fenceMatch[1][0] === fence[0] && fenceMatch[1].length >= fence.length) flushCode();
      else code.push(line);
      return;
    }
    if (fenceMatch) {
      flushParagraph();
      flushList();
      flushPendingHeading();
      fence = fenceMatch[1];
      code = [];
      return;
    }
    const nodeAnchor = line.match(/^<!--\s*soleresearch:node\s+(nod_[0-9a-f]{32})\s*-->\s*$/);
    const rootAnchor = /^<!--\s*soleresearch:anchor\s+root\s*-->\s*$/.test(line);
    if (nodeAnchor) {
      flushParagraph();
      flushList();
      const section = element("section", null, "outline-node-section");
      section.dataset.outlineNodeId = nodeAnchor[1];
      const mappedNode = snapshot ? snapshot.views.nodes.items.find((item) => item.node_id === nodeAnchor[1]) : null;
      const mappedDepth = mappedNode && outlineGraph ? nodeDepth(mappedNode, outlineGraph.byId) : 0;
      const mappedParent = mappedNode && outlineGraph ? outlineGraph.byId.get(mappedNode.parent_id) : null;
      const synthesisDetail = Boolean(mappedParent && (mappedParent.tags || []).includes("terminal-synthesis"));
      if (mappedNode && mappedNode.node_type === "question") section.classList.add("outline-question-section");
      else section.dataset.claimNumber = outlineClaimNumbers.get(nodeAnchor[1]) || String(++claimNumber);
      section.dataset.outlineDepth = String(Math.min(4, Math.max(0, mappedDepth)));
      section.dataset.nodeType = mappedNode ? mappedNode.node_type : "";
      section.dataset.terminalSynthesis = mappedNode && (mappedNode.tags || []).includes("terminal-synthesis") ? "true" : "false";
      section.dataset.synthesisDetail = synthesisDetail ? "true" : "false";
      if (mappedNode && mappedNode.node_type === "question") section.appendChild(element("p", "Research question", "draft-section-kicker"));
      section.tabIndex = 0;
      section.addEventListener("click", () => selectDraftNode(nodeAnchor[1]));
      section.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectDraftNode(nodeAnchor[1]);
        }
      });
      flushPendingHeading(section);
      fragment.appendChild(section);
      target = section;
      return;
    }
    if (rootAnchor) {
      flushParagraph();
      flushList();
      flushPendingHeading(fragment);
      target = fragment;
      return;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      flushPendingHeading();
      target = fragment;
      pendingHeading = element(`h${Math.max(2, heading[1].length)}`, heading[2]);
      return;
    }
    const bullet = line.match(/^\s*[-*+]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      flushPendingHeading();
      if (!list) list = element("ul");
      const listItem = element("li");
      appendInlineLinks(listItem, bullet[1]);
      list.appendChild(listItem);
      return;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      return;
    }
    flushList();
    flushPendingHeading();
    paragraph.push(line.trim());
  });
  flushParagraph();
  flushList();
  flushCode();
  flushPendingHeading();
  if (!fragment.childNodes.length) fragment.appendChild(element("p", "The outline is empty.", "empty"));
  outlinePreview.replaceChildren(fragment);
  previewContent = markdown;
  if (selectedNodeId) surfaceDraftNode(selectedNodeId, {scroll: false});
}

function card(title, paragraphs = [], pills = [], warning = false) {
  const item = element("article", null, warning ? "record warning" : "record");
  item.appendChild(element("h3", title));
  paragraphs.filter((value) => value !== null && value !== undefined && value !== "").forEach((text) => item.appendChild(element("p", text)));
  const visiblePills = pills.filter((value) => value !== null && value !== undefined && value !== "");
  if (visiblePills.length) {
    const metadata = element("div", null, "metadata");
    visiblePills.forEach((text) => metadata.appendChild(element("span", text, "pill")));
    item.appendChild(metadata);
  }
  return item;
}

function viewHeader(view, label) {
  const header = element("header", null, "view-header");
  header.appendChild(element("p", label, "view-title"));
  const suffix = view.truncated ? "display limit reached" : "complete set";
  header.appendChild(element("p", `${view.items.length} of ${view.total} · ${suffix}`, "view-truth"));
  content.appendChild(header);
}

function truth(view, label) {
  const suffix = view.truncated ? " · truncated at local display limit" : " · complete";
  content.appendChild(element("p", `Showing ${view.items.length} of ${view.total} ${label}${suffix}`, "view-truth"));
}

function listOrEmpty(items, render, message) {
  if (!items.length) content.appendChild(element("p", message, "empty"));
  items.forEach((item) => content.appendChild(render(item)));
}

function renderSearchableCollection({
  items,
  renderItem,
  searchText,
  query,
  setQuery,
  expanded,
  setExpanded,
  placeholder,
  emptyMessage,
  limit = 12,
  forceVisible = () => false,
}) {
  if (!items.length) {
    content.appendChild(element("p", emptyMessage, "empty"));
    return;
  }
  const controls = element("div", null, "inspector-search");
  const input = element("input");
  input.type = "search";
  input.value = query;
  input.placeholder = placeholder;
  input.setAttribute("aria-label", placeholder);
  const status = element("p", null, "collection-status");
  controls.append(input, status);
  content.appendChild(controls);
  const cards = items.map((item, index) => {
    const cardItem = renderItem(item);
    cardItem.dataset.collectionIndex = String(index);
    cardItem.dataset.searchText = searchText(item).toLocaleLowerCase();
    content.appendChild(cardItem);
    return cardItem;
  });
  const showMore = element("button", null, "show-more-button");
  showMore.type = "button";
  content.appendChild(showMore);

  function apply() {
    const normalized = input.value.trim().toLocaleLowerCase();
    setQuery(input.value);
    let matches = 0;
    let visible = 0;
    cards.forEach((cardItem, index) => {
      const match = !normalized || cardItem.dataset.searchText.includes(normalized);
      if (match) matches += 1;
      const show = match && (normalized || expanded() || index < limit || forceVisible(cardItem));
      cardItem.hidden = !show;
      if (show) visible += 1;
    });
    status.textContent = normalized
      ? `${matches} matching ${matches === 1 ? "record" : "records"}`
      : `Showing ${visible} of ${items.length}`;
    showMore.hidden = Boolean(normalized) || expanded() || items.length <= limit;
    showMore.textContent = `Show all ${items.length}`;
  }

  input.addEventListener("input", apply);
  showMore.addEventListener("click", () => {
    setExpanded(true);
    apply();
  });
  apply();
}

function locatorText(locator) {
  if (!locator) return "No locator";
  const parts = [`type ${locator.type}`];
  ["page", "section", "paragraph", "figure", "table", "timestamp", "start_char", "end_char", "label"].forEach((key) => {
    if (locator[key] !== null && locator[key] !== undefined && locator[key] !== "") parts.push(`${key.replace("_", " ")} ${locator[key]}`);
  });
  return parts.join(" · ");
}

function details(label, lines) {
  const disclosure = element("details");
  disclosure.appendChild(element("summary", label));
  const body = element("div", null, "detail-body");
  lines.filter((line) => line !== null && line !== undefined && line !== "").forEach((line) => body.appendChild(element("p", line)));
  disclosure.appendChild(body);
  return disclosure;
}

function surfaceDraftNode(nodeId, {scroll = true} = {}) {
  const sections = Array.from(outlinePreview.querySelectorAll("[data-outline-node-id]"));
  sections.forEach((section) => section.classList.toggle("draft-focus", section.dataset.outlineNodeId === nodeId));
  const target = sections.find((section) => section.dataset.outlineNodeId === nodeId);
  if (!target) return false;
  renderDraftProvenance(target, nodeId);
  if (scroll && activeContextView === "draft") target.scrollIntoView({behavior: "smooth", block: "start"});
  return true;
}

function renderDraftProvenance(target, nodeId) {
  outlinePreview.querySelectorAll(".section-provenance").forEach((item) => item.remove());
  const node = snapshot && snapshot.views.nodes.items.find((item) => item.node_id === nodeId);
  if (!node) return;
  const evidenceById = new Map(snapshot.views.evidence.items.map((item) => [item.evidence_id, item]));
  const synthesis = (node.tags || []).includes("terminal-synthesis");
  let evidenceIds = node.evidence_ids || [];
  let hiddenBranchCount = 0;
  if (synthesis) {
    const graph = activeGraph();
    const descendants = [];
    const stack = [...(graph.children.get(nodeId) || [])];
    hiddenBranchCount = stack.length;
    while (stack.length) {
      const descendant = stack.shift();
      descendants.push(descendant);
      stack.push(...(graph.children.get(descendant.node_id) || []));
    }
    evidenceIds = Array.from(new Set(descendants.flatMap((descendant) => descendant.evidence_ids || [])));
  }
  const strip = element("aside", null, "section-provenance");
  strip.setAttribute("aria-label", synthesis ? "Section provenance" : "Claim provenance");
  const descendantSources = new Set(evidenceIds.map((evidenceId) => evidenceById.get(evidenceId)?.source_id).filter(Boolean));
  const coverage = synthesis
    ? {state: evidenceIds.length ? "linked_context" : "unlinked", evidence_count: evidenceIds.length, source_count: descendantSources.size}
    : node.coverage || {state: "unlinked", evidence_count: 0, source_count: 0};
  const coverageText = synthesis
    ? `${coverage.evidence_count} cited evidence · ${coverage.source_count} sources · organized in ${hiddenBranchCount} expandable ${hiddenBranchCount === 1 ? "branch" : "branches"}`
    : `${coverageLabel(coverage.state)} · ${coverage.evidence_count} evidence · ${coverage.source_count} sources`;
  strip.appendChild(element("span", coverageText, `coverage-chip coverage-${coverage.state}`));
  if (synthesis) {
    target.appendChild(strip);
    return;
  }
  evidenceIds.forEach((evidenceId) => {
    const evidence = evidenceById.get(evidenceId);
    const jump = element("button", evidence ? evidence.source_title : "Evidence outside displayed set", "provenance-link");
    jump.type = "button";
    jump.addEventListener("click", (event) => {
      event.stopPropagation();
      activateContextTab("inspect");
      focusEvidence(evidenceId);
    });
    strip.appendChild(jump);
  });
  target.appendChild(strip);
}

function activeGraph() {
  const nodeView = snapshot.views.nodes;
  const edgeView = snapshot.views.edges;
  const nodes = nodeView.items.filter((node) => !node.retired);
  const byId = new Map(nodes.map((node) => [node.node_id, node]));
  const children = new Map();
  nodes.forEach((node) => {
    const parent = byId.has(node.parent_id) ? node.parent_id : null;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(node);
  });
  children.forEach((items) => items.sort((left, right) => left.position - right.position || left.node_id.localeCompare(right.node_id)));
  return {nodeView, edgeView, nodes, byId, children};
}

function selectedResearchContext() {
  const graph = activeGraph();
  const node = graph.byId.get(selectedNodeId) || null;
  if (!node) return {graph, node, nodes: [], evidence: [], sources: []};
  const nodes = [];
  const stack = [node];
  while (stack.length) {
    const current = stack.shift();
    nodes.push(current);
    stack.push(...(graph.children.get(current.node_id) || []));
  }
  const evidenceIds = new Set(nodes.flatMap((item) => item.evidence_ids || []));
  const evidence = snapshot.views.evidence.items.filter((item) => evidenceIds.has(item.evidence_id));
  const sourceIds = new Set(evidence.map((item) => item.source_id).filter(Boolean));
  const sources = snapshot.views.sources.items.filter((item) => sourceIds.has(item.source_id));
  return {graph, node, nodes, evidence, sources};
}

function initializeBranchExpansion({byId, children}) {
  if (expansionProjectId === activeProjectId) return;
  expandedBranchIds = new Set();
  expansionProjectId = activeProjectId;
  (children.get(null) || []).forEach((root) => {
    const firstTier = children.get(root.node_id) || [];
    if (firstTier.length > 2) expandedBranchIds.add(root.node_id);
    firstTier.forEach((topic) => {
      if ((children.get(topic.node_id) || []).length > 2) expandedBranchIds.add(topic.node_id);
    });
  });
  let cursor = byId.get(selectedNodeId);
  while (cursor && cursor.parent_id && byId.has(cursor.parent_id)) {
    expandedBranchIds.add(cursor.parent_id);
    cursor = byId.get(cursor.parent_id);
  }
}

function visibleGraph({nodes, byId, children}) {
  const visibleNodes = [];
  const visited = new Set();

  function visit(node, depth = 0) {
    if (visited.has(node.node_id)) return;
    visited.add(node.node_id);
    visibleNodes.push(node);
    const branch = children.get(node.node_id) || [];
    if (depth >= 2 && branch.length && !expandedBranchIds.has(node.node_id)) return;
    branch.forEach((child) => visit(child, depth + 1));
  }

  (children.get(null) || []).forEach((root) => visit(root, 0));
  nodes.filter((node) => !visited.has(node.node_id) && !byId.has(node.parent_id)).forEach((root) => visit(root, 0));
  const visibleById = new Map(visibleNodes.map((node) => [node.node_id, node]));
  const visibleChildren = new Map();
  visibleNodes.forEach((node) => {
    const parentId = visibleById.has(node.parent_id) ? node.parent_id : null;
    if (!visibleChildren.has(parentId)) visibleChildren.set(parentId, []);
    visibleChildren.get(parentId).push(node);
  });
  return {nodes: visibleNodes, byId: visibleById, children: visibleChildren};
}

function collapseUnfocusedBranches(nodeId) {
  const {byId} = activeGraph();
  const focusedPath = new Set();
  let cursor = byId.get(nodeId);
  while (cursor) {
    focusedPath.add(cursor.node_id);
    cursor = cursor.parent_id ? byId.get(cursor.parent_id) : null;
  }
  let changed = false;
  Array.from(expandedBranchIds).forEach((expandedId) => {
    const expandedNode = byId.get(expandedId);
    if (expandedNode && nodeDepth(expandedNode, byId) >= 2 && !focusedPath.has(expandedId)) {
      expandedBranchIds.delete(expandedId);
      changed = true;
    }
  });
  return changed;
}

function revealNodePath(nodeId) {
  const {byId} = activeGraph();
  let cursor = byId.get(nodeId);
  while (cursor && cursor.parent_id && byId.has(cursor.parent_id)) {
    expandedBranchIds.add(cursor.parent_id);
    cursor = byId.get(cursor.parent_id);
  }
  primaryMapSignature = null;
}

function hierarchicalClaimNumbers(nodes, byId, children) {
  const numbers = new Map();
  const visited = new Set();

  function numberChildren(parentId, prefix) {
    (children.get(parentId) || []).forEach((node, index) => {
      if (visited.has(node.node_id)) return;
      visited.add(node.node_id);
      const claimNumber = prefix ? `${prefix}.${index + 1}` : String(index + 1);
      numbers.set(node.node_id, claimNumber);
      numberChildren(node.node_id, claimNumber);
    });
  }

  (children.get(null) || []).forEach((root, index) => {
    visited.add(root.node_id);
    if (root.node_type === "question") {
      numberChildren(root.node_id, "");
      return;
    }
    const claimNumber = String(index + 1);
    numbers.set(root.node_id, claimNumber);
    numberChildren(root.node_id, claimNumber);
  });
  nodes.filter((node) => byId.has(node.node_id) && !visited.has(node.node_id)).forEach((node, index) => {
    const claimNumber = String(numbers.size + index + 1);
    numbers.set(node.node_id, claimNumber);
    numberChildren(node.node_id, claimNumber);
  });
  return numbers;
}

function nodeDepth(node, byId) {
  let depth = 0;
  let parentId = node.parent_id;
  const visited = new Set([node.node_id]);
  while (parentId && byId.has(parentId) && !visited.has(parentId)) {
    visited.add(parentId);
    depth += 1;
    parentId = byId.get(parentId).parent_id;
  }
  return depth;
}

function nodeDisplayKind(node, depth, childCount) {
  if (node.node_type === "question" || depth === 0) return "Research question";
  if (node.node_type === "gap") return "Open question";
  if (depth === 1) return "Topic";
  if ((depth === 2 && node.node_type === "concept") || childCount > 0 || node.node_type === "outline") return "Section";
  if (node.node_type === "interpretation" || node.node_type === "conclusion") return "Claim";
  if (node.node_type === "evidence") return "Evidence";
  return "Topic";
}

function focusScaleForDepth(depth) {
  if (depth <= 0) return .82;
  if (depth === 1) return 1.75;
  if (depth === 2) return 2.8;
  if (depth === 3) return 3.6;
  return 4;
}

function nodeSizeForDepth(depth) {
  if (depth <= 0) return 330;
  if (depth === 1) return 172;
  if (depth === 2) return 64;
  if (depth === 3) return 44;
  return 32;
}

function svgElement(tag, attributes) {
  const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attributes).forEach(([name, value]) => item.setAttribute(name, String(value)));
  return item;
}

function cameraState() {
  return {focusRootId, selectedNodeId, cameraScale, cameraX, cameraY};
}

function reflectSelectedTopicInUrl() {
  const url = new URL(window.location.href);
  if (focusRootId && activeProjectId && selectedNodeId) {
    const context = new URLSearchParams({project: activeProjectId, topic: selectedNodeId});
    url.hash = context.toString();
  } else {
    url.hash = "";
  }
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function hideMapTooltip() {
  if (tooltipHideTimer) window.clearTimeout(tooltipHideTimer);
  tooltipHideTimer = null;
  const tooltip = mapCanvas.querySelector(".map-hover-tooltip");
  if (tooltip) tooltip.hidden = true;
  if (tooltipTarget) tooltipTarget.removeAttribute("aria-describedby");
  tooltipTarget = null;
}

function scheduleMapTooltipHide() {
  if (tooltipHideTimer) window.clearTimeout(tooltipHideTimer);
  tooltipHideTimer = window.setTimeout(hideMapTooltip, 120);
}

function showMapTooltip(target, text) {
  if (!text) return;
  let tooltip = mapCanvas.querySelector(".map-hover-tooltip");
  if (!tooltip) {
    tooltip = element("div", null, "map-hover-tooltip");
    tooltip.setAttribute("role", "tooltip");
    tooltip.id = "map-route-tooltip";
    tooltip.tabIndex = -1;
    tooltip.addEventListener("mouseenter", () => {
      if (tooltipHideTimer) window.clearTimeout(tooltipHideTimer);
    });
    tooltip.addEventListener("mouseleave", scheduleMapTooltipHide);
    mapCanvas.appendChild(tooltip);
  }
  if (tooltipHideTimer) window.clearTimeout(tooltipHideTimer);
  if (tooltipTarget && tooltipTarget !== target) tooltipTarget.removeAttribute("aria-describedby");
  tooltipTarget = target;
  target.setAttribute("aria-describedby", tooltip.id);
  setText(tooltip, text);
  tooltip.hidden = false;
  tooltip.classList.remove("below");
  const canvasRect = mapCanvas.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();
  const tooltipRect = tooltip.getBoundingClientRect();
  const minimumX = tooltipRect.width / 2 + 12;
  const maximumX = Math.max(minimumX, canvasRect.width - tooltipRect.width / 2 - 12);
  const centeredX = targetRect.left + targetRect.width / 2 - canvasRect.left;
  tooltip.style.left = `${Math.max(minimumX, Math.min(maximumX, centeredX))}px`;
  const roomAbove = targetRect.top - canvasRect.top;
  let tooltipTop;
  if (roomAbove >= tooltipRect.height + 18) {
    tooltipTop = roomAbove - tooltipRect.height - 10;
    tooltip.classList.add("below");
  } else {
    tooltip.classList.add("below");
    tooltipTop = targetRect.bottom - canvasRect.top + 10;
  }
  const maximumTop = Math.max(12, canvasRect.height - tooltipRect.height - 12);
  tooltip.style.top = `${Math.max(12, Math.min(maximumTop, tooltipTop))}px`;
}

function attachMapTooltip(target, text) {
  target.addEventListener("mouseenter", () => showMapTooltip(target, text));
  target.addEventListener("mouseleave", scheduleMapTooltipHide);
  target.addEventListener("focus", () => showMapTooltip(target, text));
  target.addEventListener("blur", scheduleMapTooltipHide);
  target.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideMapTooltip();
  });
}

function appendRouteControl(stage, {
  sourceId,
  destinationId,
  routePosition,
  destinationTitle,
  cue,
  rootRoute = false,
  onActivate,
}) {
  const route = document.createElement("button");
  route.className = "map-route-control";
  route.type = "button";
  route.dataset.sourceNodeId = sourceId;
  route.dataset.destinationNodeId = destinationId;
  route.style.left = `${routePosition.x}px`;
  route.style.top = `${routePosition.y}px`;
  route.style.setProperty("--route-angle", `${routePosition.angle || 0}rad`);
  route.setAttribute("aria-label", `Follow path to ${destinationTitle}`);
  route.appendChild(element("span", "→", "map-route-arrow"));
  route.classList.toggle("map-route-root", rootRoute);
  attachMapTooltip(route, `Go to ${destinationTitle} — ${cue}`);
  const follow = onActivate || (() => followRouteToNode(destinationId));
  const activate = (event) => {
    event.preventDefault();
    event.stopPropagation();
    follow();
  };
  route.addEventListener("click", activate);
  route.addEventListener("keydown", (event) => {
    if (geometry.isRouteActivation({type: event.type, key: event.key})) activate(event);
  });
  stage.appendChild(route);
  if (!routePosition.persistent) return;
  const cueElement = element("span", cue, "map-route-cue-label");
  cueElement.style.left = `${routePosition.cueX}px`;
  cueElement.style.top = `${routePosition.cueY}px`;
  cueElement.style.width = `${routePosition.cueWidth}px`;
  cueElement.classList.toggle("map-route-cue-root", rootRoute);
  cueElement.dataset.routeCueFor = destinationId;
  cueElement.setAttribute("aria-hidden", "true");
  stage.appendChild(cueElement);
}

function appendRootPathGuide({rootNode, destinations, routes}) {
  const guide = element("nav", null, "map-root-path-guide");
  guide.dataset.rootNodeId = rootNode.node_id;
  guide.setAttribute("aria-label", `Paths from ${displayClaimTitle(rootNode.title)}`);
  destinations.forEach((destination, index) => {
    const routePosition = routes.get(destination.node_id);
    if (!routePosition) return;
    const destinationTitle = displayClaimTitle(destination.title);
    const cue = routeCue(destination);
    const button = element("button", null, "map-root-path-item");
    button.type = "button";
    button.dataset.guideIndex = String(index);
    button.dataset.destinationNodeId = destination.node_id;
    button.style.setProperty("--route-angle", `${routePosition.angle || 0}rad`);
    button.setAttribute("aria-label", `Follow path to ${destinationTitle}: ${cue}`);
    const arrow = element("span", "→", "map-root-path-arrow");
    arrow.setAttribute("aria-hidden", "true");
    const copy = element("span", null, "map-root-path-copy");
    copy.append(
      element("strong", cue, "map-root-path-cue"),
      element("span", destinationTitle, "map-root-path-destination")
    );
    button.append(arrow, copy);
    attachMapTooltip(button, `Go to ${destinationTitle} — ${cue}`);
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      followRouteToNode(destination.node_id);
    });
    guide.appendChild(button);
  });
  return guide;
}

function positionRootPathGuides() {
  Array.from(mapCanvas.querySelectorAll(".map-root-path-guide")).forEach((guide) => {
    const active = guide.dataset.rootNodeId === focusRootId;
    guide.classList.toggle("active", active);
    guide.hidden = !active;
    guide.setAttribute("aria-hidden", active ? "false" : "true");
    if (!active) return;
    const items = Array.from(guide.querySelectorAll(".map-root-path-item"));
    const layout = geometry.rootGuideLayout({width: mapCanvas.clientWidth, height: mapCanvas.clientHeight, count: items.length});
    items.forEach((item, index) => {
      const rect = layout.rects[index];
      item.style.left = `${rect.x}px`;
      item.style.top = `${rect.y}px`;
      item.style.width = `${rect.width}px`;
      item.style.height = `${rect.height}px`;
    });
  });
}

function scheduleRootPathGuideLayout() {
  if (rootGuideLayoutFrame !== null) return;
  rootGuideLayoutFrame = window.requestAnimationFrame(() => {
    rootGuideLayoutFrame = null;
    positionRootPathGuides();
  });
}

function updateSelectedMapNode() {
  const selectedMapNodeId = workspaceOverview ? focusRootId : selectedNodeId;
  Array.from(mapCanvas.querySelectorAll("[data-node-id]")).forEach((item) => {
    const selected = item.dataset.nodeId === selectedMapNodeId;
    item.classList.toggle("selected", selected);
    item.setAttribute("aria-selected", selected ? "true" : "false");
    item.tabIndex = selected ? 0 : -1;
  });
  updateEvidenceSatellites();
}

function updateEvidenceSatellites() {
  const selectedMapNodeId = workspaceOverview ? focusRootId : expandedEvidenceParentId;
  let anyExpanded = false;
  Array.from(mapCanvas.querySelectorAll("[data-node-id]")).forEach((item) => item.classList.remove("evidence-expanded"));
  Array.from(mapCanvas.querySelectorAll("[data-evidence-parent-id]")).forEach((item) => {
    const parent = mapCanvas.querySelector(`[data-node-id="${item.dataset.evidenceParentId}"]`);
    const expanded = item.dataset.evidenceParentId === selectedMapNodeId && parent && !parent.classList.contains("map-filtered-out");
    item.hidden = !expanded;
    item.classList.toggle("expanded", expanded);
    if (parent) parent.classList.toggle("evidence-expanded", expanded);
    anyExpanded = anyExpanded || Boolean(expanded);
  });
  mapCanvas.classList.toggle("map-evidence-focus", anyExpanded);
}

function appendEvidenceSatellites(evidenceStage, {parentId, parentDepth = 0, position, parentSize, evidenceIds, evidenceById, evidenceAngle = 0, expanded, occupiedCircles = []}) {
  const showTitlePreviews = parentDepth >= 3 && evidenceIds.length <= 6;
  const visualSize = showTitlePreviews ? 60 : 28;
  const parentCircle = occupiedCircles.find((circle) => circle.id === parentId) || {
    id: parentId,
    x: position.x,
    y: position.y,
    radius: geometry.displayedRadius(parentSize, 1.2),
    depth: parentDepth,
  };
  const satelliteCircles = geometry.placeSatellites({
    parent: parentCircle,
    count: evidenceIds.length,
    radius: geometry.displayedRadius(visualSize, 1.06),
    occupied: occupiedCircles,
    clearance: 18,
    preferredAngle: evidenceAngle,
  });
  evidenceIds.forEach((evidenceId, index) => {
    const evidence = evidenceById.get(evidenceId);
    const satelliteCircle = satelliteCircles[index];
    const satelliteX = satelliteCircle.x;
    const satelliteY = satelliteCircle.y;
    const satellite = element("button", null, "evidence-satellite");
    satellite.classList.toggle("evidence-satellite-preview", showTitlePreviews);
    satellite.type = "button";
    satellite.hidden = !expanded;
    satellite.dataset.evidenceId = evidenceId;
    satellite.dataset.evidenceParentId = parentId;
    const evidenceTitle = evidence ? evidence.source_title : "Evidence record";
    satellite.dataset.tooltip = evidenceTitle;
    satellite.style.left = `${satelliteX}px`;
    satellite.style.top = `${satelliteY}px`;
    satellite.setAttribute("aria-label", `Open evidence ${index + 1} of ${evidenceIds.length}${evidence ? ` from ${evidence.source_title}` : ""}`);
    satellite.appendChild(element(
      "span",
      showTitlePreviews ? evidenceTitle : `E${index + 1}`,
      showTitlePreviews ? "evidence-satellite-title" : "evidence-satellite-mark"
    ));
    attachMapTooltip(satellite, evidenceTitle);
    satellite.addEventListener("click", (event) => {
      event.stopPropagation();
      cameraHistory.push(cameraState());
      cameraScale = Math.max(cameraScale, 4);
      cameraX = -satelliteX * cameraScale;
      cameraY = -satelliteY * cameraScale;
      updateMapCamera();
      activateContextTab("inspect");
      focusEvidence(evidenceId);
    });
    evidenceStage.appendChild(satellite);
  });
  return satelliteCircles;
}

function coverageMatches(state, filter) {
  if (filter === "all") return true;
  if (filter === "linked") return state === "linked_support" || state === "linked_context";
  if (filter === "conflict") return state === "conflicting_evidence" || state === "graph_conflict";
  return state === filter;
}

function applyMapFilters({announce = false} = {}) {
  const query = researchSearch.value.trim().toLocaleLowerCase();
  const filter = coverageFilter.value;
  const matches = [];
  Array.from(mapCanvas.querySelectorAll("[data-node-id]")).forEach((item) => {
    const matchesText = !query || item.dataset.searchText.includes(query);
    const matchesCoverage = coverageMatches(item.dataset.coverageState, filter);
    const visible = matchesText && matchesCoverage;
    item.classList.toggle("map-filtered-out", !visible);
    if (visible) matches.push(item);
  });
  setText(researchMatchCount, query || filter !== "all" ? `${matches.length} match${matches.length === 1 ? "" : "es"}` : "");
  updateEvidenceSatellites();
  if (announce) liveStatus.textContent = `${matches.length} research map ${matches.length === 1 ? "match" : "matches"}`;
  return matches;
}

function updateMapCamera() {
  hideMapTooltip();
  const stages = Array.from(mapCanvas.querySelectorAll(".map-stage, .map-evidence-stage, .map-route-stage"));
  stages.forEach((stage) => {
    stage.style.transform = `translate(${cameraX}px, ${cameraY}px) scale(${cameraScale})`;
    stage.style.setProperty("--camera-inverse-scale", String(1 / cameraScale));
  });
  mapCanvas.classList.toggle("map-zoom-far", Boolean(workspaceOverview && cameraScale < .4));
  mapCanvas.classList.toggle("map-zoom-very-far", Boolean(workspaceOverview && cameraScale < .28));
  mapCanvas.classList.toggle("map-focus-active", Boolean(focusRootId));
  const focusDepth = focusRootId && graphPositions.has(focusRootId) ? graphPositions.get(focusRootId).depth : -1;
  mapCanvas.classList.toggle("map-focus-root", focusDepth === 0);
  mapCanvas.classList.toggle("map-focus-branch", focusDepth >= 1);
  mapCanvas.classList.toggle("map-focus-topic", focusDepth >= 2);
  mapCanvas.classList.toggle("map-routes-hidden", cameraScale < 1 && !focusRootId);
  Array.from(mapCanvas.querySelectorAll(".map-route-control")).forEach((route) => {
    route.classList.toggle("route-from-focus", route.dataset.sourceNodeId === focusRootId);
  });
  positionRootPathGuides();
  setText(mapFit, `Fit · ${Math.round(cameraScale * 100)}%`);
  mapZoomOut.disabled = cameraScale <= .011;
  mapZoomIn.disabled = cameraScale >= 3.99;
  mapBack.disabled = cameraHistory.length === 0;
}

function renderBreadcrumb(byId) {
  const fragment = document.createDocumentFragment();
  const rootCount = workspace ? workspace.projects.length : 1;
  const overview = element("button", `Root {${rootCount}}`, "map-crumb");
  overview.type = "button";
  overview.setAttribute("aria-label", `Root: ${rootCount} independent research ${rootCount === 1 ? "question" : "questions"}`);
  overview.title = "All independent research questions";
  if (workspaceOverview || !focusRootId) overview.setAttribute("aria-current", "page");
  overview.addEventListener("click", () => {
    if (workspace && workspace.projects.length > 1) showWorkspaceOverview();
    else fitGraph({record: true});
  });
  fragment.appendChild(overview);
  if (workspaceOverview) {
    mapBreadcrumb.replaceChildren(fragment);
    return;
  }
  const project = activeWorkspaceProject();
  if (workspace && workspace.projects.length > 1 && project) {
    fragment.appendChild(element("span", "/", "map-crumb-separator"));
    const projectCrumb = element("button", `Branch {${project.directory}}`, "map-crumb");
    projectCrumb.type = "button";
    projectCrumb.setAttribute("aria-label", `Branch directory: ${project.name}`);
    projectCrumb.title = project.name;
    if (!focusRootId) projectCrumb.setAttribute("aria-current", "page");
    projectCrumb.addEventListener("click", () => fitGraph({record: true}));
    fragment.appendChild(projectCrumb);
  }
  const path = [];
  let cursor = byId.get(focusRootId);
  const visited = new Set();
  while (cursor && !visited.has(cursor.node_id)) {
    visited.add(cursor.node_id);
    path.unshift(cursor);
    cursor = byId.get(cursor.parent_id);
  }
  const children = activeGraph().children;
  path.forEach((node, index) => {
    fragment.appendChild(element("span", "/", "map-crumb-separator"));
    const isLeaf = !(children.get(node.node_id) || []).length;
    const role = isLeaf && index === path.length - 1
      ? "Leaf"
      : index === 0
        ? "Branch"
        : `Sub-branch ${index}`;
    const crumb = element("button", role, "map-crumb");
    crumb.type = "button";
    crumb.setAttribute("aria-label", `${role}: ${node.title}`);
    crumb.title = node.title;
    if (index === path.length - 1) crumb.setAttribute("aria-current", "page");
    crumb.addEventListener("click", () => moveCameraToNode(node.node_id, {record: true, focus: true}));
    fragment.appendChild(crumb);
  });
  mapBreadcrumb.replaceChildren(fragment);
}

function showWorkspaceOverview() {
  if (!workspace || workspace.projects.length < 2) return;
  workspaceOverview = true;
  focusRootId = null;
  cameraHistory = [];
  primaryMapSignature = null;
  reflectSelectedTopicInUrl();
  setText(document.getElementById("project-name"), `${workspace.workspace_name} · ${workspace.projects.length} questions`);
  renderPrimaryMap();
  liveStatus.textContent = `${workspace.projects.length} independent research questions shown`;
}

function fitGraph({record = false} = {}) {
  if (!graphBounds) return;
  if (record) cameraHistory.push(cameraState());
  cameraScale = geometry.fitScaleForBounds(graphBounds, mapCanvas.clientWidth, mapCanvas.clientHeight);
  cameraX = -((graphBounds.minX + graphBounds.maxX) / 2) * cameraScale;
  cameraY = -((graphBounds.minY + graphBounds.maxY) / 2) * cameraScale;
  focusRootId = null;
  reflectSelectedTopicInUrl();
  renderBreadcrumb(activeGraph().byId);
  updateMapCamera();
  liveStatus.textContent = "Continuous graph fit to screen";
}

function moveCameraToNode(mapNodeId, {record = true, focus = false, selectedId = mapNodeId} = {}) {
  const position = graphPositions.get(mapNodeId);
  if (!position) return;
  if (record) cameraHistory.push(cameraState());
  focusRootId = mapNodeId;
  selectedNodeId = selectedId;
  reflectSelectedTopicInUrl();
  cameraScale = position.depth === 0
    ? focusScaleForDepth(position.depth)
    : Math.max(cameraScale, focusScaleForDepth(position.depth));
  cameraScale = Math.min(4, cameraScale);
  cameraX = -position.x * cameraScale;
  cameraY = -position.y * cameraScale;
  updateSelectedMapNode();
  renderBreadcrumb(activeGraph().byId);
  updateMapCamera();
  renderedSignatures.map = null;
  renderInspector({force: true});
  surfaceDraftNode(selectedId);
  if (focus) {
    const selected = Array.from(mapCanvas.querySelectorAll("[data-node-id]")).find((item) => item.dataset.nodeId === mapNodeId);
    if (selected) selected.focus({preventScroll: true});
  }
  liveStatus.textContent = "Camera moved to research node";
}

function followRouteToNode(nodeId) {
  const graph = activeGraph();
  const node = graph.byId.get(nodeId);
  if (!node || !graphPositions.has(nodeId)) return;
  const navigation = geometry.routeNavigationState({
    destinationId: nodeId,
    hasChildren: (graph.children.get(nodeId) || []).length > 0,
    expandedIds: Array.from(expandedBranchIds),
  });
  cameraHistory.push(cameraState());
  revealNodePath(nodeId);
  expandedBranchIds = new Set(navigation.expandedIds);
  expandedEvidenceParentId = null;
  selectedNodeId = navigation.selectedNodeId;
  primaryMapSignature = null;
  renderPrimaryMap({preserveCamera: true});
  moveCameraToNode(nodeId, {record: false, focus: true});
  liveStatus.textContent = `Followed path to ${displayClaimTitle(node.title)}`;
}

function collapseTreeNode(nodeId) {
  if (workspaceOverview || !expandedBranchIds.has(nodeId)) return false;
  expandedBranchIds.delete(nodeId);
  expandedEvidenceParentId = null;
  selectedNodeId = nodeId;
  primaryMapSignature = null;
  renderPrimaryMap({preserveCamera: true});
  moveCameraToNode(nodeId, {record: false, focus: true});
  const childCount = (activeGraph().children.get(nodeId) || []).length;
  liveStatus.textContent = `${childCount} branches collapsed`;
  return true;
}

function selectNode(nodeId) {
  activateTab(document.getElementById("tab-map"));
  if (workspaceOverview) {
    const preview = Array.from(mapCanvas.querySelectorAll("[data-node-id]")).find((item) => item.dataset.nodeId === nodeId);
    if (preview && preview.dataset.projectId && preview.dataset.targetNodeId && !preview.disabled) {
      switchWorkspaceProject(preview.dataset.projectId, preview.dataset.targetNodeId).catch((error) => {
        liveStatus.textContent = `Question load failed: ${error.message}`;
      });
    }
    return;
  }
  const graph = activeGraph();
  const node = graph.byId.get(nodeId);
  const depth = node ? nodeDepth(node, graph.byId) : 0;
  const childCount = (graph.children.get(nodeId) || []).length;
  const evidenceCount = node ? (node.evidence_ids || []).length : 0;
  const collapsedUnfocused = collapseUnfocusedBranches(nodeId);
  if (depth >= 2 && childCount > 0) {
    const collapse = selectedNodeId === nodeId && expandedBranchIds.has(nodeId);
    if (collapse) expandedBranchIds.delete(nodeId);
    else expandedBranchIds.add(nodeId);
    expandedEvidenceParentId = null;
    cameraHistory.push(cameraState());
    selectedNodeId = nodeId;
    primaryMapSignature = null;
    renderPrimaryMap({preserveCamera: true});
    const stage = mapCanvas.querySelector(".map-stage");
    if (stage) stage.getBoundingClientRect();
    moveCameraToNode(nodeId, {record: false});
    liveStatus.textContent = collapse ? `${childCount} branches collapsed` : `${childCount} branches expanded`;
    return;
  }
  if (collapsedUnfocused) {
    selectedNodeId = nodeId;
    primaryMapSignature = null;
    renderPrimaryMap({preserveCamera: true});
    const stage = mapCanvas.querySelector(".map-stage");
    if (stage) stage.getBoundingClientRect();
  }
  if (childCount === 0 && evidenceCount) {
    const closePreview = expandedEvidenceParentId === nodeId;
    expandedEvidenceParentId = closePreview ? null : nodeId;
    cameraHistory.push(cameraState());
    selectedNodeId = nodeId;
    primaryMapSignature = null;
    renderPrimaryMap({preserveCamera: true});
    moveCameraToNode(nodeId, {record: false});
    liveStatus.textContent = closePreview ? "Evidence preview closed" : `${evidenceCount} evidence preview${evidenceCount === 1 ? "" : "s"} opened`;
    return;
  }
  expandedEvidenceParentId = null;
  moveCameraToNode(nodeId, {record: true});
}

function selectDraftNode(nodeId) {
  activateTab(document.getElementById("tab-map"));
  if (workspaceOverview) {
    const workspaceNodeId = `workspace:${activeProjectId}:${nodeId}`;
    if (graphPositions.has(workspaceNodeId)) {
      moveCameraToNode(workspaceNodeId, {record: true, selectedId: nodeId});
      return;
    }
  }
  expandedEvidenceParentId = null;
  revealNodePath(nodeId);
  renderPrimaryMap();
  moveCameraToNode(nodeId, {record: true});
}

async function switchWorkspaceProject(projectId, targetNodeId = null) {
  if (!workspace || !workspace.projects.some((item) => item.project_id === projectId && item.available)) return;
  const overviewReturn = workspaceOverview ? {
    focusRootId: null,
    selectedNodeId: targetNodeId,
    cameraScale,
    cameraX,
    cameraY,
  } : null;
  activeProjectId = projectId;
  workspaceOverview = workspace.projects.length > 1;
  evidenceSearchQuery = "";
  evidenceExpanded = {passages: false, sources: false};
  focusedEvidenceId = null;
  historySearchQuery = "";
  historyExpanded = false;
  expansionProjectId = null;
  expandedBranchIds = new Set();
  expandedEvidenceParentId = null;
  selectedNodeId = null;
  focusRootId = null;
  cameraHistory = overviewReturn ? [overviewReturn] : [];
  primaryMapSignature = null;
  previewContent = null;
  Object.keys(renderedSignatures).forEach((key) => { renderedSignatures[key] = null; });
  setText(refreshState, "Loading question directory…");
  await loadState({announce: true});
  const workspaceNodeId = `workspace:${projectId}:${targetNodeId}`;
  if (targetNodeId && workspaceOverview && graphPositions.has(workspaceNodeId)) {
    moveCameraToNode(workspaceNodeId, {record: false, focus: true, selectedId: targetNodeId});
  } else if (targetNodeId && graphPositions.has(targetNodeId)) {
    moveCameraToNode(targetNodeId, {record: false, focus: true});
  }
}

function renderWorkspaceMap() {
  const projects = workspace.projects;
  const signature = JSON.stringify({workspace: projects});
  if (signature === primaryMapSignature && mapCanvas.querySelector(".map-stage")) {
    applyMapFilters();
    return;
  }
  primaryMapSignature = signature;
  graphPositions = new Map();
  const previewNodes = new Map();
  const rootSizeUnit = 88;
  const evidenceNodeSize = 98;
  const minimumBranchSize = Math.round(evidenceNodeSize * 1.5);
  const maximumBranchSize = evidenceNodeSize * 2;
  const projectGraphs = new Map();
  const branchScores = new Map();
  const branchNumbers = new Map();
  projects.forEach((project) => {
    const nodes = project.preview.nodes.length ? project.preview.nodes : [{
      node_id: `portal:${project.project_id}`,
      parent_id: null,
      node_type: "question",
      title: project.questions[0] || project.name,
      evidence_count: project.evidence_count,
    }];
    const byId = new Map(nodes.map((node) => [node.node_id, node]));
    const children = new Map();
    nodes.forEach((node) => {
      const parent = byId.has(node.parent_id) ? node.parent_id : null;
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(node);
    });
    children.forEach((items) => items.sort((left, right) => left.node_id.localeCompare(right.node_id)));
    projectGraphs.set(project.project_id, {nodes, byId, children});
    let projectClaimNumber = 0;
    nodes.forEach((node) => {
      if (!byId.has(node.parent_id)) return;
      branchNumbers.set(`${project.project_id}:${node.node_id}`, ++projectClaimNumber);
      const score = 1 + (node.evidence_count || 0) + (children.get(node.node_id) || []).length;
      branchScores.set(`${project.project_id}:${node.node_id}`, score);
    });
  });
  const recordedBranchScores = Array.from(branchScores.values());
  const minimumBranchScore = recordedBranchScores.length ? Math.min(...recordedBranchScores) : 1;
  const maximumBranchScore = recordedBranchScores.length ? Math.max(...recordedBranchScores) : 1;
  const branchScoreSpan = Math.max(maximumBranchScore - minimumBranchScore, 1);
  const branchSizes = new Map(Array.from(branchScores, ([key, score]) => {
    const normalized = maximumBranchScore === minimumBranchScore ? .5 : (score - minimumBranchScore) / branchScoreSpan;
    const size = Math.round(minimumBranchSize + normalized * (maximumBranchSize - minimumBranchSize));
    return [key, {normalized, size}];
  }));
  const contentScores = projects.map((project) => project.node_count + project.source_count + project.evidence_count);
  const minimumScore = Math.min(...contentScores);
  const maximumScore = Math.max(...contentScores);
  const scoreSpan = Math.max(maximumScore - minimumScore, 1);
  const rootSizes = new Map(projects.map((project) => {
    const score = project.node_count + project.source_count + project.evidence_count;
    const normalized = maximumScore === minimumScore ? .5 : (score - minimumScore) / scoreSpan;
    return [project.project_id, {normalized, size: Math.round(rootSizeUnit * (2 + normalized * 3))}];
  }));
  const projectLayouts = projects.map((project, projectIndex) => {
    const {nodes, byId} = projectGraphs.get(project.project_id);
    const rootSizing = rootSizes.get(project.project_id);
    const records = nodes.map((node, index) => {
      const root = !byId.has(node.parent_id);
      const branchSizing = branchSizes.get(`${project.project_id}:${node.node_id}`) || {normalized: .5, size: minimumBranchSize};
      return {
        id: `workspace:${project.project_id}:${node.node_id}`,
        parentId: root ? null : `workspace:${project.project_id}:${node.parent_id}`,
        order: index,
        size: root ? rootSizing.size : branchSizing.size,
      };
    });
    return {project, projectIndex, rootSizing, byId, layout: geometry.layoutForest(records, {clearance: 18, maximumScale: 1.2})};
  });
  const columns = Math.max(1, Math.ceil(Math.sqrt(projects.length)));
  const largestProjectWidth = Math.max(...projectLayouts.map(({layout}) => layout.bounds.maxX - layout.bounds.minX), 1);
  const largestProjectHeight = Math.max(...projectLayouts.map(({layout}) => layout.bounds.maxY - layout.bounds.minY), 1);
  const workspaceCircles = [];
  projectLayouts.forEach(({project, projectIndex, rootSizing, byId, layout}) => {
    const column = projectIndex % columns;
    const row = Math.floor(projectIndex / columns);
    const centerX = (column - (columns - 1) / 2) * (largestProjectWidth + 900);
    const centerY = (row - (Math.ceil(projects.length / columns) - 1) / 2) * (largestProjectHeight + 900);
    const localCenterX = (layout.bounds.minX + layout.bounds.maxX) / 2;
    const localCenterY = (layout.bounds.minY + layout.bounds.maxY) / 2;
    layout.circles.forEach((circle) => {
      const nodeId = circle.id.split(":").slice(2).join(":");
      const node = byId.get(nodeId);
      if (!node) return;
      const key = circle.id;
      const branchSizing = branchSizes.get(`${project.project_id}:${node.node_id}`) || {normalized: .5, size: minimumBranchSize};
      const translated = {
        id: key,
        x: circle.x - localCenterX + centerX,
        y: circle.y - localCenterY + centerY,
        radius: circle.radius,
        depth: circle.depth,
      };
      workspaceCircles.push(translated);
      graphPositions.set(key, translated);
      previewNodes.set(key, {project, node, isRoot: circle.depth === 0, rootSizing, branchSizing, projectIndex});
    });
  });
  const workspaceAudit = geometry.validateCircles(workspaceCircles, {clearance: 24});
  if (!workspaceAudit.valid) throw new Error("workspace layout contains intersecting project nodes");
  graphBounds = geometry.boundsForCircles(workspaceCircles, 24);
  const stage = element("div", null, "map-stage workspace-stage");
  stage.setAttribute("role", "tree");
  stage.setAttribute("aria-label", "Independent research questions");
  const evidenceStage = element("div", null, "map-evidence-stage");
  evidenceStage.setAttribute("role", "presentation");
  const routeStage = element("div", null, "map-route-stage");
  routeStage.setAttribute("role", "presentation");
  const activeNodes = new Map(snapshot.views.nodes.items.map((item) => [item.node_id, item]));
  const activeChildren = activeGraph().children;
  const evidenceById = new Map(snapshot.views.evidence.items.map((record) => [record.evidence_id, record]));
  const edges = svgElement("svg", {class: "map-edges workspace-edges", "aria-hidden": "true"});
  previewNodes.forEach(({project, node}) => {
    if (!node.parent_id) return;
    const source = graphPositions.get(`workspace:${project.project_id}:${node.parent_id}`);
    const target = graphPositions.get(`workspace:${project.project_id}:${node.node_id}`);
    if (source && target) edges.appendChild(svgElement("line", {x1: source.x, y1: source.y, x2: target.x, y2: target.y, class: "map-edge map-edge-contains"}));
  });
  stage.appendChild(edges);
  const workspaceRouteEdges = Array.from(previewNodes, ([key, {project, node}]) => {
    const sourceId = `workspace:${project.project_id}:${node.parent_id}`;
    const source = node.parent_id ? graphPositions.get(sourceId) : null;
    return source ? {
      id: `${project.project_id}:${node.parent_id}:${node.node_id}`,
      sourceId,
      destinationId: key,
      cue: routeCue(node),
      persistent: source.depth === 0,
    } : null;
  }).filter(Boolean);
  const occupiedWorkspaceCircles = workspaceCircles.slice();
  previewNodes.forEach(({project, node, isRoot, rootSizing, branchSizing, projectIndex}, key) => {
    const position = graphPositions.get(key);
    const rootSizeClass = rootSizing.normalized < .34 ? " workspace-root-small" : rootSizing.normalized < .67 ? " workspace-root-medium" : " workspace-root-large";
    const branchSizeClass = branchSizing.normalized < .34 ? " workspace-branch-small" : branchSizing.normalized < .67 ? " workspace-branch-medium" : " workspace-branch-large";
    const button = element("button", null, `map-node map-node-${node.node_type}${isRoot ? ` workspace-question-root${rootSizeClass}` : ` workspace-branch${branchSizeClass}`}`);
    button.type = "button";
    button.dataset.nodeId = key;
    if (node.parent_id) button.dataset.parentNodeId = `workspace:${project.project_id}:${node.parent_id}`;
    button.dataset.projectId = project.project_id;
    button.dataset.targetNodeId = node.node_id;
    button.dataset.coverageState = node.evidence_count ? "linked_context" : "unlinked";
    button.dataset.searchText = [project.name, project.directory, node.title].join(" ").toLocaleLowerCase();
    button.style.left = `${position.x}px`;
    button.style.top = `${position.y}px`;
    if (isRoot) button.style.setProperty("--workspace-root-size", `${rootSizing.size}px`);
    else button.style.setProperty("--workspace-branch-size", `${branchSizing.size}px`);
    button.setAttribute("role", "treeitem");
    button.setAttribute("aria-level", String(position.depth + 1));
    button.tabIndex = key === focusRootId || (!focusRootId && projectIndex === 0 && isRoot) ? 0 : -1;
    button.disabled = !project.available;
    button.setAttribute("aria-label", project.available ? `Open ${isRoot ? "independent question" : "question branch"}: ${node.title}` : `Question unavailable: ${node.title}`);
    if (isRoot) {
      const mark = element("span", `Q${projectIndex + 1}`, "workspace-root-mark");
      mark.setAttribute("aria-hidden", "true");
      button.appendChild(mark);
    }
    if (isRoot) {
      button.append(
        element("span", "Question root", "node-type"),
        element("span", project.name, "map-node-title"),
        element("span", project.available
        ? `${project.node_count} nodes · ${project.source_count} sources · ${project.evidence_count} evidence`
        : `${project.directory} · needs repair`, "map-node-foot")
      );
    } else {
      const branchContent = element("span", null, "workspace-branch-content");
      const displayKind = nodeDisplayKind(node, position.depth, 0);
      branchContent.append(
        element("span", `${displayKind} ${branchNumbers.get(`${project.project_id}:${node.node_id}`) || "—"}`, "node-type"),
        element("span", node.title, "map-node-title"),
        element("span", `${node.evidence_count} evidence`, "map-node-foot")
      );
      button.appendChild(branchContent);
    }
    if (project.available) {
      button.title = `Open ${project.directory} at ${node.title}`;
    } else {
      button.title = project.availability_error || "Project state needs repair";
    }
    if (!isRoot) attachMapTooltip(button, node.title);
    stage.appendChild(button);
    const activeNode = project.project_id === activeProjectId ? activeNodes.get(node.node_id) : null;
    const evidenceIds = activeNode ? activeNode.evidence_ids || [] : [];
    if (!isRoot && activeNode && !(activeChildren.get(node.node_id) || []).length && evidenceIds.length) {
      const satelliteCircles = appendEvidenceSatellites(evidenceStage, {
        parentId: key,
        parentDepth: position.depth,
        position,
        parentSize: branchSizing.size,
        evidenceIds,
        evidenceById,
        expanded: key === focusRootId,
        occupiedCircles: occupiedWorkspaceCircles,
      });
      occupiedWorkspaceCircles.push(...satelliteCircles);
    }
  });
  const workspaceRoutes = new Map(geometry.placeRoutes({
    edges: workspaceRouteEdges,
    nodes: occupiedWorkspaceCircles,
    targetRadius: 18,
    clearance: 10,
  }).map((route) => [route.destinationId, route]));
  const workspaceRouteValues = Array.from(workspaceRoutes.values());
  const workspaceRouteFootprints = geometry.routeFootprintCircles(workspaceRouteValues);
  const workspaceRouteTargets = geometry.routeTargetCircles(workspaceRouteValues);
  previewNodes.forEach(({project, node}) => {
    if (!node.parent_id) return;
    const sourceId = `workspace:${project.project_id}:${node.parent_id}`;
    const destinationId = `workspace:${project.project_id}:${node.node_id}`;
    const source = graphPositions.get(sourceId);
    const routePosition = workspaceRoutes.get(destinationId);
    if (!source || !routePosition) return;
    appendRouteControl(routeStage, {
      sourceId,
      destinationId,
      routePosition,
      destinationTitle: node.title,
      cue: routeCue(node),
      rootRoute: source.depth === 0,
      onActivate: () => selectNode(destinationId),
    });
  });
  const finalWorkspaceCircles = [...occupiedWorkspaceCircles, ...workspaceRouteTargets, ...workspaceRouteFootprints];
  const finalProjectBounds = projects.map((project) => geometry.boundsForCircles(
    finalWorkspaceCircles.filter((circle) => String(circle.id).includes(project.project_id)),
    24
  )).filter(Boolean);
  if (!geometry.boundsAreDisjoint(finalProjectBounds, 24)) {
    throw new Error("workspace project bounds intersect after routes and evidence placement");
  }
  graphBounds = geometry.boundsForCircles(finalWorkspaceCircles, 24) || graphBounds;
  mapCanvas.classList.add("workspace-overview");
  mapCanvas.replaceChildren(stage, evidenceStage, routeStage);
  renderBreadcrumb(new Map());
  applyMapFilters();
  fitGraph();
}

function renderPrimaryMap({preserveCamera = false} = {}) {
  if (!snapshot) return;
  if (workspaceOverview && workspace && workspace.projects.length > 1) {
    renderWorkspaceMap();
    return;
  }
  mapCanvas.classList.remove("workspace-overview");
  const graph = activeGraph();
  const {nodeView, edgeView, nodes: allNodes, byId, children} = graph;
  if (!byId.has(selectedNodeId)) {
    const preferred = allNodes.find((node) => node.node_type === "question" && !node.parent_id) || allNodes[0];
    selectedNodeId = preferred ? preferred.node_id : null;
  }
  if (focusRootId && !byId.has(focusRootId)) focusRootId = null;
  initializeBranchExpansion(graph);
  const visible = visibleGraph(graph);
  const nodes = visible.nodes;
  const claimNumbers = hierarchicalClaimNumbers(allNodes, byId, children);
  const evidenceById = new Map(snapshot.views.evidence.items.map((record) => [record.evidence_id, record]));
  const signature = JSON.stringify({nodes: allNodes, edges: edgeView.items, expanded: Array.from(expandedBranchIds).sort()});
  if (signature === primaryMapSignature && mapCanvas.querySelector(".map-stage")) {
    applyMapFilters();
    return;
  }
  primaryMapSignature = signature;

  const layout = geometry.layoutForest(allNodes.map((node) => ({
    id: node.node_id,
    parentId: byId.has(node.parent_id) ? node.parent_id : null,
    order: Number(node.position || 0),
    size: nodeSizeForDepth(nodeDepth(node, byId)),
  })), {clearance: 18, maximumScale: 1.2});
  graphPositions = new Map(layout.circles.map((circle) => [circle.id, {
    x: circle.x,
    y: circle.y,
    depth: circle.depth,
    radius: circle.radius,
  }]));
  const visibleIds = new Set(nodes.map((node) => node.node_id));
  const visibleCircles = layout.circles.filter((circle) => visibleIds.has(circle.id));
  const geometryAudit = geometry.validateCircles(visibleCircles, {clearance: 18});
  if (!geometryAudit.valid) throw new Error("research map layout contains intersecting nodes");
  const graphMargin = nodeSizeForDepth(0) / 2 + 24;
  graphBounds = geometry.boundsForCircles(visibleCircles, 18) || (nodes.length ? {
    minX: -graphMargin,
    maxX: graphMargin,
    minY: -graphMargin,
    maxY: graphMargin,
  } : null);

  const stage = element("div", null, "map-stage");
  stage.setAttribute("role", "tree");
  stage.setAttribute("aria-label", "Connected research outline");
  const evidenceStage = element("div", null, "map-evidence-stage");
  evidenceStage.setAttribute("role", "presentation");
  const routeStage = element("div", null, "map-route-stage");
  routeStage.setAttribute("role", "presentation");
  const edges = svgElement("svg", {class: "map-edges", "aria-hidden": "true"});
  nodes.forEach((node) => {
    const source = graphPositions.get(node.parent_id);
    const target = graphPositions.get(node.node_id);
    if (source && target) edges.appendChild(svgElement("line", {x1: source.x, y1: source.y, x2: target.x, y2: target.y, class: "map-edge map-edge-contains"}));
  });
  edgeView.items.filter((edge) => !edge.retired).forEach((edge) => {
    const source = graphPositions.get(edge.source_node_id);
    const target = graphPositions.get(edge.target_node_id);
    if (!source || !target) return;
    const bend = Math.max(50, Math.hypot(target.x - source.x, target.y - source.y) * .14);
    const middleX = (source.x + target.x) / 2;
    const middleY = (source.y + target.y) / 2 - bend;
    edges.appendChild(svgElement("path", {d: `M ${source.x} ${source.y} Q ${middleX} ${middleY} ${target.x} ${target.y}`, class: `map-edge map-edge-${edge.edge_type}`}));
  });
  stage.appendChild(edges);
  const routeEdges = nodes.filter((node) => graphPositions.has(node.parent_id)).map((node) => ({
    id: `${node.parent_id}:${node.node_id}`,
    sourceId: node.parent_id,
    destinationId: node.node_id,
    cue: routeCue(node),
    persistent: graphPositions.get(node.parent_id).depth === 0,
  }));
  const occupiedCircles = visibleCircles.slice();
  nodes.forEach((node) => {
    const position = graphPositions.get(node.node_id);
    const parentPosition = graphPositions.get(node.parent_id);
    const childCount = (children.get(node.node_id) || []).length;
    const collapsible = position.depth >= 2 && childCount > 0;
    const branchExpanded = !collapsible || expandedBranchIds.has(node.node_id);
    const displayKind = nodeDisplayKind(node, position.depth, childCount);
    const displayTitle = displayClaimTitle(node.title);
    const depthClass = ` map-node-depth-${Math.min(position.depth, 4)}`;
    const button = element("button", null, `map-node map-node-${node.node_type}${depthClass}${node.node_id === selectedNodeId ? " selected" : ""}${position.depth === 0 ? " map-node-root" : ""}`);
    button.type = "button";
    button.dataset.nodeId = node.node_id;
    if (node.parent_id) button.dataset.parentNodeId = node.parent_id;
    const claimNumber = claimNumbers.get(node.node_id) || "—";
    if (position.depth > 0) button.dataset.claimNumber = claimNumber;
    button.dataset.coverageState = node.coverage ? node.coverage.state : "unlinked";
    button.dataset.searchText = [
      node.title,
      node.body,
      node.node_type,
      ...(node.evidence_ids || []).map((evidenceId) => evidenceById.get(evidenceId)?.source_title || ""),
    ].join(" ").toLocaleLowerCase();
    button.style.left = `${position.x}px`;
    button.style.top = `${position.y}px`;
    button.setAttribute("role", "treeitem");
    button.setAttribute("aria-level", String(position.depth + 1));
    button.setAttribute("aria-selected", node.node_id === selectedNodeId ? "true" : "false");
    button.tabIndex = node.node_id === selectedNodeId ? 0 : -1;
    if (collapsible) {
      button.classList.add("map-node-collapsible");
      button.setAttribute("aria-expanded", branchExpanded ? "true" : "false");
    }
    button.dataset.nodeKind = displayKind;
    button.setAttribute("aria-label", position.depth === 0 ? `Research question: ${displayTitle}` : `${displayKind} ${claimNumber}: ${displayTitle}`);
    const evidenceCount = (node.evidence_ids || []).length;
    const nodeFoot = childCount === 0
      ? (evidenceCount ? `${evidenceCount} evidence` : "No evidence")
      : `${evidenceCount} evidence · ${childCount} ${childCount === 1 ? "branch" : "branches"}${collapsible ? ` · ${branchExpanded ? "expanded" : "collapsed"}` : ""}`;
    button.append(
      position.depth === 0
        ? element("span", "Research question", "node-type")
        : element("span", `${displayKind} ${claimNumber}`, "node-type"),
      element("span", displayTitle, "map-node-title"),
      element("span", nodeFoot, "map-node-foot")
    );
    if (collapsible) {
      const toggleMark = element("span", branchExpanded ? "−" : "+", "branch-toggle-mark");
      toggleMark.setAttribute("aria-hidden", "true");
      button.appendChild(toggleMark);
    }
    button.addEventListener("click", () => selectNode(node.node_id));
    attachMapTooltip(button, displayTitle);
    stage.appendChild(button);

    const evidenceIds = node.evidence_ids || [];
    if (childCount === 0 && evidenceIds.length && node.node_id === expandedEvidenceParentId) {
      const satelliteCircles = appendEvidenceSatellites(evidenceStage, {
        parentId: node.node_id,
        parentDepth: position.depth,
        position,
        parentSize: nodeSizeForDepth(position.depth),
        evidenceIds,
        evidenceById,
        evidenceAngle: Math.atan2(
          position.y - (parentPosition ? parentPosition.y : position.y),
          position.x - (parentPosition ? parentPosition.x : position.x - 1)
        ),
        expanded: true,
        occupiedCircles,
      });
      occupiedCircles.push(...satelliteCircles);
    }
  });
  const routes = new Map(geometry.placeRoutes({
    edges: routeEdges,
    nodes: occupiedCircles,
    targetRadius: 18,
    clearance: 10,
  }).map((route) => [route.destinationId, route]));
  const routeValues = Array.from(routes.values());
  const routeFootprints = geometry.routeFootprintCircles(routeValues);
  const routeTargets = geometry.routeTargetCircles(routeValues);
  nodes.forEach((node) => {
    const source = graphPositions.get(node.parent_id);
    const routePosition = routes.get(node.node_id);
    if (!source || !routePosition) return;
    appendRouteControl(routeStage, {
      sourceId: node.parent_id,
      destinationId: node.node_id,
      routePosition,
      destinationTitle: displayClaimTitle(node.title),
      cue: routeCue(node),
      rootRoute: source.depth === 0,
      onActivate: () => followRouteToNode(node.node_id),
    });
  });
  const rootGuides = nodes.filter((node) => graphPositions.get(node.node_id)?.depth === 0).map((rootNode) => appendRootPathGuide({
    rootNode,
    destinations: (children.get(rootNode.node_id) || []).filter((child) => visibleIds.has(child.node_id)),
    routes,
  }));
  graphBounds = geometry.boundsForCircles([...occupiedCircles, ...routeTargets, ...routeFootprints], 18) || graphBounds;
  if (!nodes.length) stage.appendChild(element("p", "No graph nodes yet. Ask the orchestrator to create the first research question.", "empty"));
  mapCanvas.replaceChildren(stage, evidenceStage, routeStage, ...rootGuides);
  applyMapFilters();
  renderBreadcrumb(byId);
  if (preserveCamera) updateMapCamera();
  else if (focusRootId && graphPositions.has(focusRootId)) moveCameraToNode(focusRootId, {record: false});
  else fitGraph();
}

function renderActivity() {
  if (!snapshot) return;
  const runs = snapshot.views.runs.items;
  const run = runs.find((item) => item.status === "active") || runs.find((item) => item.status === "paused") || runs[0] || null;
  const audit = snapshot.views.audit.timeline.items;
  const latestAudit = audit.length ? audit[audit.length - 1] : null;
  const status = document.getElementById("activity-status");
  const detail = document.getElementById("activity-detail");
  const indicator = document.getElementById("activity-indicator");
  const agentList = document.getElementById("activity-agent-list");
  const runState = document.getElementById("activity-run-state");
  const cycle = document.getElementById("activity-cycle");
  const controller = document.getElementById("activity-controller");
  const progress = document.getElementById("activity-progress");
  const agentPreview = document.getElementById("activity-agent-preview");
  const telemetry = document.getElementById("activity-telemetry");
  const needsAttention = Boolean(run && (run.status === "paused" || run.gate.status === "human_review" || (run.gate.pending_decisions || []).length));
  indicator.classList.toggle("active", Boolean(run && run.status === "active" && !needsAttention));
  indicator.classList.toggle("attention", needsAttention);
  if (run) {
    const tasks = run.tasks || [];
    const assignedTasks = tasks.filter((task) => task.status === "pending");
    const completedTasks = tasks.filter((task) => task.status === "completed");
    const pendingDecisions = run.gate.pending_decisions || [];
    const lastEvent = run.events.length ? run.events[run.events.length - 1] : null;
    setText(runState, humanize(run.status));
    setText(cycle, `Cycle ${run.current_cycle}`);
    setText(controller, `${humanize(run.controller)} controller`);
    if (pendingDecisions.length) setText(status, `${pendingDecisions.length} human ${pendingDecisions.length === 1 ? "decision" : "decisions"} required`);
    else if (run.gate.status === "human_review") setText(status, "Waiting at the human review gate");
    else if (run.status === "paused") setText(status, `Research paused${run.pause_reason ? ` — ${run.pause_reason}` : ""}`);
    else if (assignedTasks.length) setText(status, `${assignedTasks.length} outstanding worker ${assignedTasks.length === 1 ? "assignment" : "assignments"}`);
    else if (run.status === "active") setText(status, "Coordinator is between worker assignments");
    else setText(status, `Research run ${humanize(run.status).toLowerCase()}`);
    setText(detail, pendingDecisions.length
      ? `Waiting on: ${readableLines(pendingDecisions[0], "Decision")[0]}${pendingDecisions.length > 1 ? ` · ${pendingDecisions.length - 1} more in Activity` : ""}.`
      : lastEvent
        ? `Latest authoritative event: ${humanize(lastEvent.event_type)}${lastEvent.reason ? ` · ${lastEvent.reason}` : ""} · ${lastEvent.created_at}.`
        : `Run created ${run.created_at}; no events have been recorded yet.`);
    setText(progress, `${completedTasks.length} / ${tasks.length} tasks`);
    const previewTask = assignedTasks[0] || completedTasks[completedTasks.length - 1] || null;
    setText(agentPreview, assignedTasks.length
      ? `${assignedTasks.length} outstanding · ${humanize(previewTask.role)}: ${previewTask.subquestion}`
      : previewTask
        ? `No outstanding work · latest ${humanize(previewTask.role)} assignment ${humanize(previewTask.status).toLowerCase()}`
        : run.status === "active"
          ? "No worker assigned · coordinator owns the next action"
          : "No worker assignments recorded");
    setText(telemetry, run.telemetry && run.telemetry.individual_tool_calls_recorded
      ? "Task and individual tool-call visibility"
      : "Task-level visibility · individual tool calls are not recorded");

    const visibleTasks = assignedTasks.length ? assignedTasks : completedTasks.slice(-2).reverse();
    if (visibleTasks.length) {
      agentList.replaceChildren(...visibleTasks.map((task) => {
        const item = element("article", null, `agent-activity-card ${task.status === "pending" ? "current" : ""}`.trim());
        item.dataset.taskStatus = task.status;
        const heading = element("header");
        const identity = element("p", `${humanize(task.role)} · agent ${shortIdentifier(task.worker_id)}`, "agent-identity");
        identity.title = task.worker_id;
        heading.append(identity, element("span", task.status === "pending" ? "Assigned" : humanize(task.status), "agent-task-status"));
        const question = element("p", task.subquestion, "agent-task-question");
        question.title = task.subquestion;
        const method = element("p", `Method: ${task.evidence_strategy}`, "agent-task-method");
        method.title = task.evidence_strategy;
        const capabilities = task.allowed_capabilities.length ? task.allowed_capabilities.map(humanize).join(", ") : "none declared";
        const resultSummary = task.result
          ? ` · ${task.result.accessed_source_count} sources · ${task.result.evidence_count} evidence`
          : "";
        const meta = element("p", `Cycle ${task.cycle} · depth ${task.depth} · tools: ${capabilities}${resultSummary}`, "agent-task-meta");
        meta.title = `Allowed capabilities: ${capabilities}`;
        item.append(heading, question, method, meta);
        return item;
      }));
    } else {
      agentList.replaceChildren(element("p", run.status === "active"
        ? "No worker task is currently assigned; the coordinator owns the next action."
        : "No worker assignments were recorded for this run.", "agent-activity-empty"));
    }
  } else {
    setText(status, "No orchestration run is attached to this project");
    setText(detail, latestAudit ? `Latest authoritative action: ${humanize(latestAudit.type)} · ${latestAudit.at}. No agent or tool-call telemetry was recorded for this synthesis.` : "No run or audit activity has been recorded yet.");
    setText(runState, "No run");
    setText(cycle, "Cycle —");
    setText(controller, "Controller —");
    setText(progress, "0 tasks");
    setText(agentPreview, "No orchestration run attached");
    setText(telemetry, "No run telemetry recorded");
    agentList.replaceChildren(element("p", "Start a bounded orchestration run to see worker assignments and their evidence strategies here.", "agent-activity-empty"));
  }
  setText(document.getElementById("activity-sources"), snapshot.views.sources.total);
  setText(document.getElementById("activity-evidence"), snapshot.views.evidence.total);
}

function renderMap() {
  const context = selectedResearchContext();
  const {edgeView, byId, children} = context.graph;
  const node = context.node;
  if (!node) {
    content.appendChild(element("p", "Select a research node to inspect its evidence, relationships, and review state.", "empty"));
    return;
  }

  const header = element("header", null, "focus-header");
  header.append(element("p", "Selected research context", "view-title"), element("p", nodeDisplayKind(node, nodeDepth(node, byId), (children.get(node.node_id) || []).length), "view-truth"));
  content.appendChild(header);
  const proposals = snapshot.views.proposals.items;
  const relatedProposals = proposals.filter((proposal) => JSON.stringify(proposal.operations).includes(node.node_id));
  if (relatedProposals.length) {
    const proposalReview = element("details", null, "proposal-review");
    proposalReview.appendChild(element("summary", `${relatedProposals.length} proposed ${relatedProposals.length === 1 ? "change" : "changes"} for this context`));
    relatedProposals.slice(0, 3).forEach((proposal) => proposalReview.appendChild(element(
      "p",
      `${humanize(proposal.actor_type)} proposal · ${proposal.operations.length} ${proposal.operations.length === 1 ? "change" : "changes"}`,
      "proposal-line"
    )));
    proposalReview.appendChild(element("p", "Review or direct the change through the connected agent chat.", "telemetry-note"));
    content.appendChild(proposalReview);
  }
  const coverage = node.coverage || {state: context.evidence.length ? "linked_context" : "unlinked"};
  const focused = card(node.title || "Untitled research node", [], [humanize(node.node_type), coverageLabel(coverage.state)]);
  focused.dataset.recordKey = `node:${node.node_id}`;
  if (node.body) appendRichTextParagraphs(focused, node.body, "focus-body");
  const metrics = element("dl", null, "overview-metrics");
  [
    ["Branches", Math.max(0, context.nodes.length - 1)],
    ["Evidence", context.evidence.length],
    ["Sources", context.sources.length],
  ].forEach(([label, value]) => {
    const metric = element("div");
    metric.append(element("dt", label), element("dd", value));
    metrics.appendChild(metric);
  });
  focused.appendChild(metrics);
  const relations = edgeView.items.filter((edge) => !edge.retired && (edge.source_node_id === node.node_id || edge.target_node_id === node.node_id));
  if (relations.length) {
    const relationList = element("div", null, "relationship-list");
    relations.forEach((edge) => {
      const outgoing = edge.source_node_id === node.node_id;
      const other = byId.get(outgoing ? edge.target_node_id : edge.source_node_id);
      relationList.appendChild(element("p", `${outgoing ? "→" : "←"} ${humanize(edge.edge_type)} · ${other ? other.title : "unavailable node"}`, "relationship"));
    });
    focused.appendChild(relationList);
  }
  if (context.evidence.length) {
    const reviewEvidence = element("button", `Review ${context.evidence.length} evidence ${context.evidence.length === 1 ? "passage" : "passages"} from ${context.sources.length} ${context.sources.length === 1 ? "source" : "sources"}`, "evidence-jump overview-evidence-action");
    reviewEvidence.type = "button";
    reviewEvidence.addEventListener("click", () => {
      activeEvidenceMode = "passages";
      activateTab(document.getElementById("tab-evidence"), true);
    });
    focused.appendChild(reviewEvidence);
  } else {
    focused.appendChild(element("p", "No exact-locator evidence is attached within this context yet.", "gap-note"));
  }
  const openGaps = context.nodes.filter((item) => item.node_type === "gap" || item.coverage?.state === "open_gap");
  if (openGaps.length) focused.appendChild(element("p", `${openGaps.length} open ${openGaps.length === 1 ? "gap needs" : "gaps need"} review.`, "gap-note"));
  content.appendChild(focused);

  snapshot.views.conflicts.items
    .filter((conflict) => JSON.stringify(conflict).includes(node.node_id))
    .forEach((conflict) => content.appendChild(card("Outline conflict", [conflict.details], [humanize(conflict.status)], true)));
}

function sourceCard(source) {
  const quality = source.quality || {};
  const identifiers = source.identifiers || {};
  const item = card(
    source.title || "Untitled source",
    [
      source.authors && source.authors.length ? source.authors.join(", ") : null,
      source.published ? `Published ${source.published}` : null,
    ],
    [humanize(source.human_reading_state), humanize(source.source_type), `${humanize(quality.authority)} authority`]
  );
  item.dataset.recordKey = `source:${source.source_id}`;
  const sourceUrl = safeWebUrl(source.canonical_url);
  if (sourceUrl) {
    const link = element("a", source.title || "Open source", "evidence-source-link");
    link.href = sourceUrl;
    link.setAttribute("aria-label", `Open source: ${source.title || "Untitled source"}`);
    item.querySelector("h3").replaceChildren(link);
  }
  const assessmentComplete = [quality.authority, quality.evidence_directness, quality.relevance].every((value) => value && value !== "unknown");
  item.appendChild(element(
    "p",
    assessmentComplete
      ? `${humanize(quality.authority)} authority · ${humanize(quality.evidence_directness)} directness · ${humanize(quality.relevance)} relevance`
      : "Source assessment is incomplete.",
    assessmentComplete ? "source-assessment" : "gap-note"
  ));
  item.appendChild(details("Source details", [
    quality.notes ? quality.notes : null,
    `Method transparency: ${humanize(quality.methodology_transparency)} · publication: ${humanize(quality.publication_status)}`,
    `DOI: ${identifiers.doi || "not recorded"} · arXiv: ${identifiers.arxiv || "not recorded"}`,
  ]));
  return item;
}

function evidenceModeSwitch() {
  const switcher = element("div", null, "evidence-mode-switch");
  switcher.setAttribute("aria-label", "Evidence view");
  [["passages", "Passages"], ["sources", "Sources"]].forEach(([mode, label]) => {
    const button = element("button", label, "evidence-mode-button");
    button.type = "button";
    button.dataset.evidenceMode = mode;
    button.setAttribute("aria-pressed", activeEvidenceMode === mode ? "true" : "false");
    button.addEventListener("click", () => {
      if (activeEvidenceMode === mode) return;
      activeEvidenceMode = mode;
      renderInspector({force: true});
      const next = panels.evidence.querySelector(`[data-evidence-mode="${mode}"]`);
      if (next) next.focus();
    });
    switcher.appendChild(button);
  });
  return switcher;
}

function renderEvidence() {
  const context = selectedResearchContext();
  const items = activeEvidenceMode === "sources" ? context.sources : context.evidence;
  const scopedView = {items, total: items.length, truncated: false};
  viewHeader(scopedView, activeEvidenceMode === "sources" ? "Sources for selected context" : "Evidence for selected context");
  content.appendChild(evidenceModeSwitch());
  renderSearchableCollection({
    items,
    renderItem: activeEvidenceMode === "sources" ? sourceCard : (record) => {
      const latest = record.attestations && record.attestations[record.attestations.length - 1];
      const item = card(record.source_title, [], [latest && humanize(latest.stance)]);
      const sourceUrl = safeWebUrl(record.source_url);
      if (sourceUrl) {
        const link = element("a", record.source_title, "evidence-source-link");
        link.href = sourceUrl;
        link.setAttribute("aria-label", `Open source: ${record.source_title}`);
        item.querySelector("h3").replaceChildren(link);
      }
      const quote = element("blockquote", record.excerpt, "evidence-quote");
      item.insertBefore(quote, item.querySelector(".metadata"));
      if (latest && latest.paraphrase) item.appendChild(element("p", `Interpretation: ${latest.paraphrase}`));
      item.appendChild(details("Exact location", [locatorText(record.locator), `Source version ${record.source_version}`]));
      item.dataset.evidenceId = record.evidence_id;
      item.dataset.recordKey = `evidence:${record.evidence_id}`;
      item.tabIndex = -1;
      return item;
    },
    searchText: activeEvidenceMode === "sources"
      ? (source) => [source.title, ...(source.authors || []), source.published, source.quality?.notes].filter(Boolean).join(" ")
      : (record) => [record.source_title, record.excerpt, record.attestations?.at(-1)?.paraphrase, locatorText(record.locator)].filter(Boolean).join(" "),
    query: evidenceSearchQuery,
    setQuery: (value) => { evidenceSearchQuery = value; },
    expanded: () => evidenceExpanded[activeEvidenceMode],
    setExpanded: (value) => { evidenceExpanded[activeEvidenceMode] = value; },
    placeholder: activeEvidenceMode === "sources" ? "Search sources" : "Search evidence passages",
    emptyMessage: activeEvidenceMode === "sources"
      ? "No sources support the selected context yet."
      : "No exact-locator evidence supports the selected context yet.",
    forceVisible: (item) => item.dataset.evidenceId === focusedEvidenceId,
  });
}

function focusEvidence(evidenceId) {
  activeEvidenceMode = "passages";
  evidenceSearchQuery = "";
  focusedEvidenceId = evidenceId;
  activateTab(document.getElementById("tab-evidence"), true);
  const target = Array.from(panels.evidence.querySelectorAll("[data-evidence-id]")).find((item) => item.dataset.evidenceId === evidenceId);
  if (target) {
    target.focus();
    target.scrollIntoView({block: "nearest"});
    liveStatus.textContent = `Focused evidence ${evidenceId}`;
  } else {
    liveStatus.textContent = `Evidence ${evidenceId} is outside the bounded displayed set`;
  }
}

function renderDiscussions() {
  const graph = activeGraph();
  let noteNode = graph.byId.get(selectedNodeId) || null;
  let notes = [];
  while (noteNode && !notes.length) {
    notes = snapshot.views.annotations.items.filter((entry) => entry.entity_id === noteNode.node_id);
    if (!notes.length) noteNode = graph.byId.get(noteNode.parent_id) || null;
  }
  notes.sort((left, right) => right.created_at.localeCompare(left.created_at));
  const view = {items: notes, total: notes.length, truncated: false};
  viewHeader(view, noteNode && noteNode.node_id !== selectedNodeId ? "Notes from nearest parent context" : "Notes for selected context");
  listOrEmpty(notes, (entry) => {
    const item = card("Human direction", [], [humanize(entry.status)]);
    item.dataset.recordKey = `annotation:${entry.annotation_entry_id}`;
    if (entry.content.length <= 420) item.appendChild(element("p", entry.content, "note-direction"));
    else {
      item.appendChild(element("p", `${entry.content.slice(0, 320).trim()}…`, "note-direction"));
      item.appendChild(details("Read full direction", [entry.content]));
    }
    if (entry.response) {
      const response = element("p", null, "note-response");
      response.append(element("strong", "Research response — "), document.createTextNode(entry.response));
      item.appendChild(response);
    }
    item.appendChild(element("p", entry.updated_at || entry.created_at, "timeline-meta"));
    return item;
  }, "No human direction or decisions are attached to this research context yet.");
}

function budgetRows(run) {
  const wrapper = element("div", null, "budget-list");
  const limits = run.budget.limits;
  const consumed = run.budget.consumed;
  const remaining = run.remaining;
  const rows = [
    ["Cycles", consumed.cycles, limits.cycles, remaining.cycles],
    ["Tasks", consumed.tasks, limits.tasks, remaining.tasks],
    ["Deep sources", consumed.deep_sources, limits.deep_sources, remaining.deep_sources],
    ["Agents", run.pending_task_ids.length + 1, limits.agents, remaining.agents],
    ["Minutes (observed)", Number((run.observed_elapsed_seconds / 60).toFixed(1)), limits.minutes, Number(remaining.minutes.toFixed(1))],
    [`Provider ${limits.provider_usage.unit}`, consumed.provider_usage, limits.provider_usage.ceiling, remaining.provider_usage.remaining],
  ];
  rows.forEach(([label, used, limit, left]) => {
    const row = element("div", null, "budget-row");
    const copy = element("div", null, "budget-copy");
    copy.append(element("span", label), element("span", `${used} / ${limit} · ${left} remaining`));
    const progress = element("progress");
    progress.max = Math.max(Number(limit), 1);
    progress.value = Math.min(Math.max(Number(used), 0), progress.max);
    progress.setAttribute("aria-label", `${label}: ${used} of ${limit} used`);
    row.append(copy, progress);
    wrapper.appendChild(row);
  });
  wrapper.appendChild(element("p", `Maximum worker depth ${limits.max_depth} · ${remaining.max_depth} remaining`, "timeline-meta"));
  return wrapper;
}

function renderRuns() {
  const view = snapshot.views.runs;
  const run = view.items.find((item) => item.status === "active") || view.items.find((item) => item.status === "paused") || view.items[0];
  viewHeader({items: run ? [run] : [], total: run ? 1 : 0, truncated: false}, "Current research activity");
  if (!run) {
    content.appendChild(element("p", "No research runs yet. Activity appears here when bounded orchestration begins.", "empty"));
    return;
  }
  const item = card(
    `Research run · ${humanize(run.status)}`,
    [run.pause_reason, `Cycle ${run.current_cycle}`, `${run.pending_task_ids.length} pending ${run.pending_task_ids.length === 1 ? "task" : "tasks"}`],
    [humanize(run.status), `Gate: ${humanize(run.gate.status)}`],
    run.status === "paused" || run.gate.status === "human_review"
  );
  item.dataset.recordKey = `run:${run.run_id}`;
  item.appendChild(budgetRows(run));
  const tasks = run.tasks || [];
  if (tasks.length) {
    const taskList = element("div", null, "task-list");
    taskList.appendChild(element("h4", `Current task${tasks.length === 1 ? "" : "s"}`));
    tasks.forEach((task) => {
      const taskItem = element("article", null, "task-record");
      taskItem.append(
        element("p", `${humanize(task.role)} · ${humanize(task.status)}`, "task-heading"),
        element("p", task.subquestion),
        element("p", `Evidence strategy: ${task.evidence_strategy}`, "timeline-meta")
      );
      taskList.appendChild(taskItem);
    });
    item.appendChild(taskList);
  }
  const decisions = run.gate.pending_decisions || [];
  if (decisions.length) item.appendChild(details(`Pending decisions (${decisions.length})`, readableLines(decisions, "Decision")));
  item.appendChild(details(`Run event history (${run.events.length})`, run.events.map((event) => `${event.sequence}. ${event.created_at} · ${humanize(event.event_type)}${event.reason ? ` · ${event.reason}` : ""}`)));
  const telemetry = run.telemetry || {};
  item.appendChild(details("Technical run details", [
    `Controller: ${humanize(run.controller)} · observed ${run.observed_at}`,
    telemetry.individual_tool_calls_recorded ? "Individual tool calls recorded." : "Individual tool calls are not captured by the current run contract.",
    `Run ID: ${run.run_id}`,
  ]));
  content.appendChild(item);

  const previous = view.items.filter((candidate) => candidate !== run);
  if (previous.length) {
    const archive = element("details", null, "past-runs");
    archive.appendChild(element("summary", `Previous runs (${previous.length})`));
    const body = element("div", null, "detail-body");
    previous.forEach((candidate) => body.appendChild(element(
      "p",
      `${humanize(candidate.status)} · ${candidate.pause_reason || `cycle ${candidate.current_cycle}`}`,
      "past-run-line"
    )));
    archive.appendChild(body);
    content.appendChild(archive);
  }
}

function renderAudit() {
  const timeline = snapshot.views.audit.timeline;
  viewHeader(timeline, "Research history");
  renderSearchableCollection({
    items: timeline.items.slice().reverse(),
    renderItem: (entry) => {
      const item = element("article", null, "timeline-item");
      item.dataset.recordKey = `audit:${entry.id}`;
      item.append(element("h3", humanize(entry.type)), element("p", entry.at));
      readableLines(entry.summary).forEach((line) => item.appendChild(element("p", line)));
      item.appendChild(element("p", `Actor: ${humanize(entry.actor)}`, "timeline-meta"));
      return item;
    },
    searchText: (entry) => [entry.type, entry.actor, entry.at, JSON.stringify(entry.summary)].join(" "),
    query: historySearchQuery,
    setQuery: (value) => { historySearchQuery = value; },
    expanded: () => historyExpanded,
    setExpanded: (value) => { historyExpanded = value; },
    placeholder: "Search research history",
    emptyMessage: "No audit events yet. File-derived actions will appear chronologically.",
    limit: 20,
  });
  // Keep the contract phrase explicit for assistive and static validation.
  content.setAttribute("aria-label", "chronological audit events");
}

function viewSnapshot(view) {
  if (!snapshot) return null;
  if (view === "map") return {
    selectedNodeId,
    nodes: snapshot.views.nodes,
    edges: snapshot.views.edges,
    gaps: snapshot.views.gaps,
    conflicts: snapshot.views.conflicts,
    evidence: snapshot.views.evidence,
    proposals: snapshot.views.proposals,
  };
  if (view === "evidence") return {
    selectedNodeId,
    mode: activeEvidenceMode,
    evidence: snapshot.views.evidence,
    sources: snapshot.views.sources,
  };
  if (view === "discussions") return {
    selectedNodeId,
    annotations: snapshot.views.annotations,
  };
  if (view === "audit") return snapshot.views.audit.timeline;
  return snapshot.views[view];
}

function renderInspector({force = false} = {}) {
  content = panels[activeView];
  if (!snapshot) return false;
  const signature = JSON.stringify(viewSnapshot(activeView));
  if (!force && renderedSignatures[activeView] === signature) return false;

  const scrollTop = content.scrollTop;
  const focusedElement = document.activeElement && content.contains(document.activeElement) ? document.activeElement : null;
  const focusedRecord = focusedElement && focusedElement.closest("[data-record-key]");
  const focused = focusedRecord ? focusedRecord.dataset.recordKey : null;
  const focusKind = focusedElement && focusedElement.matches("summary") ? "summary" : (focusedElement && focusedElement.dataset.evidenceId ? `evidence:${focusedElement.dataset.evidenceId}` : "record");
  const openDetails = Array.from(content.querySelectorAll("[data-record-key]")).flatMap((record) =>
    Array.from(record.querySelectorAll("details")).map((item, index) => item.open ? `${record.dataset.recordKey}:${index}` : null).filter(Boolean)
  );
  content.replaceChildren();
  ({map: renderMap, evidence: renderEvidence, discussions: renderDiscussions, runs: renderRuns, audit: renderAudit})[activeView]();
  renderedSignatures[activeView] = signature;
  Array.from(content.querySelectorAll("[data-record-key]")).forEach((record) => {
    Array.from(record.querySelectorAll("details")).forEach((item, index) => {
      item.open = openDetails.includes(`${record.dataset.recordKey}:${index}`);
    });
  });
  content.scrollTop = scrollTop;
  if (focused) {
    const record = Array.from(content.querySelectorAll("[data-record-key]")).find((item) => item.dataset.recordKey === focused);
    if (record) {
      let target = record;
      if (focusKind === "summary") target = record.querySelector("summary") || record;
      if (focusKind.startsWith("evidence:")) target = Array.from(record.querySelectorAll("[data-evidence-id]")).find((item) => `evidence:${item.dataset.evidenceId}` === focusKind) || record;
      if (!target.hasAttribute("tabindex") && target === record) target.tabIndex = -1;
      target.focus({preventScroll: true});
      content.scrollTop = scrollTop;
    }
  }
  return true;
}

function updateOutline(outline) {
  if (editor.value !== outline.content) {
    const scrollTop = editor.scrollTop;
    editor.value = outline.content;
    editor.scrollTop = scrollTop;
  }
  renderOutlinePreview(outline.content);
}

function enableLoadedMode() {
  initialLoadComplete = true;
  if (boot.annotation_enabled) {
    setText(document.getElementById("mode-badge"), "AI draft · chat directed");
    setText(saveState, "Agent-recorded human direction enabled");
  } else {
    setText(document.getElementById("mode-badge"), "AI draft · read only");
    setText(saveState, "Annotations disabled");
  }
}

function failInitialLoad(error) {
  if (initialLoadComplete) return;
  editor.readOnly = true;
  setText(document.getElementById("mode-badge"), "Unavailable");
  setText(saveState, error.message);
  setText(refreshState, "Initial load failed");
  previewContent = null;
  outlinePreview.replaceChildren(element("p", "The authoritative outline could not be loaded. Editing remains unavailable.", "empty"));
  liveStatus.textContent = `Outline unavailable: ${error.message}`;
}

async function loadState({announce = false} = {}) {
  if (initialLoadComplete) setText(refreshState, "Updating…");
  const workspaceResponse = await fetch("/api/v1/workspace", {cache: "no-store"});
  if (!workspaceResponse.ok) throw new Error("Research workspace could not be loaded");
  const nextWorkspace = await workspaceResponse.json();
  const firstWorkspaceLoad = workspace === null;
  workspace = nextWorkspace;
  if (!activeProjectId || !workspace.projects.some((item) => item.project_id === activeProjectId)) activeProjectId = workspace.default_project_id;
  if (firstWorkspaceLoad && workspace.projects.length > 1) workspaceOverview = true;
  const [outlineResponse, stateResponse] = await Promise.all([
    fetch(projectApi("/api/v1/outline"), {cache: "no-store"}),
    fetch(projectApi("/api/v1/state"), {cache: "no-store"}),
  ]);
  if (!outlineResponse.ok || !stateResponse.ok) throw new Error("Local project state could not be loaded");
  const outline = await outlineResponse.json();
  const nextSnapshot = await stateResponse.json();
  snapshot = nextSnapshot;
  updateOutline(outline);
  setText(document.getElementById("project-name"), workspaceOverview ? `${workspace.workspace_name} · ${workspace.projects.length} questions` : snapshot.project.name);
  const projectionState = snapshot.outline.dirty ? "Reconciliation required" : "Projection reconciled";
  setText(document.getElementById("outline-meta"), projectionState);
  const publication = snapshot.publication;
  setText(refreshState, publication
    ? `Live · revision ${publication.published_revision} · ${publication.produced_at}`
    : "Read from authoritative files");
  renderPrimaryMap();
  renderActivity();
  renderInspector();
  if (selectedNodeId) {
    const selectedSection = outlinePreview.querySelector(`[data-outline-node-id="${selectedNodeId}"]`);
    if (selectedSection) renderDraftProvenance(selectedSection, selectedNodeId);
  }
  enableLoadedMode();
  if (announce) liveStatus.textContent = "Research state refreshed";
}

async function pollCompletionSignal() {
  const response = await fetch(projectApi("/api/v1/completion"), {cache: "no-store"});
  if (!response.ok) return;
  const signal = await response.json();
  const observed = signal && signal.schema_version === 1 && (signal.signal_id === null || /^sig_[0-9a-f]{32}$/.test(signal.signal_id))
    ? signal.signal_id
    : null;
  if (completionSignalId === undefined) {
    completionSignalId = observed;
    return;
  }
  if (!observed || observed === completionSignalId) return;
  completionSignalId = observed;
  if (completionRefreshInFlight) {
    completionRefreshQueued = true;
    return;
  }
  completionRefreshInFlight = true;
  try {
    do {
      completionRefreshQueued = false;
      await loadState({announce: true});
    } while (completionRefreshQueued);
  } finally {
    completionRefreshInFlight = false;
  }
}

function activateTab(tab, focus = false) {
  tabs.forEach((item) => {
    const selected = item === tab;
    item.classList.toggle("active", selected);
    item.setAttribute("aria-selected", selected ? "true" : "false");
    item.tabIndex = selected ? 0 : -1;
    panels[item.dataset.view].hidden = !selected;
  });
  Array.from(researchInspector.querySelectorAll('[role="tablist"]')).forEach((tablist) => {
    const groupTabs = Array.from(tablist.querySelectorAll('[role="tab"][data-view]'));
    if (!groupTabs.some((item) => item.getAttribute("aria-selected") === "true") && groupTabs.length) groupTabs[0].tabIndex = 0;
  });
  activeView = tab.dataset.view;
  inspectorMore.open = Boolean(tab.closest(".inspector-secondary-tabs"));
  renderInspector();
  if (focus) {
    tab.focus();
    tab.scrollIntoView({block: "nearest", inline: "nearest"});
  }
}

function activateContextTab(view, {focus = false} = {}) {
  activeContextView = view;
  contextTabs.forEach((tab) => {
    const selected = tab.dataset.contextView === view;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", selected ? "true" : "false");
    tab.tabIndex = selected ? 0 : -1;
  });
  Object.entries(contextPanels).forEach(([name, panel]) => {
    panel.hidden = name !== view;
  });
  if (view === "draft" && selectedNodeId) surfaceDraftNode(selectedNodeId);
  if (view === "inspect") renderInspector({force: true});
  if (focus) {
    const tab = contextTabs.find((item) => item.dataset.contextView === view);
    if (tab) tab.focus({preventScroll: true});
  }
  liveStatus.textContent = `${view === "draft" ? "Draft" : "Inspector"} context shown`;
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => activateTab(tab));
  tab.addEventListener("keydown", (event) => {
    const tablist = tab.closest('[role="tablist"]');
    const groupTabs = tablist ? Array.from(tablist.querySelectorAll('[role="tab"][data-view]')) : [tab];
    const index = groupTabs.indexOf(tab);
    let next = null;
    if (event.key === "ArrowRight") next = (index + 1) % groupTabs.length;
    if (event.key === "ArrowLeft") next = (index - 1 + groupTabs.length) % groupTabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = groupTabs.length - 1;
    if (next !== null) {
      event.preventDefault();
      activateTab(groupTabs[next], true);
    }
  });
});

inspectorMore.addEventListener("toggle", () => {
  if (!inspectorMore.open && (activeView === "runs" || activeView === "audit")) {
    activateTab(document.getElementById("tab-map"));
  }
});

contextTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => activateContextTab(tab.dataset.contextView));
  tab.addEventListener("keydown", (event) => {
    let next = null;
    if (event.key === "ArrowRight") next = (index + 1) % contextTabs.length;
    if (event.key === "ArrowLeft") next = (index - 1 + contextTabs.length) % contextTabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = contextTabs.length - 1;
    if (next !== null) {
      event.preventDefault();
      activateContextTab(contextTabs[next].dataset.contextView, {focus: true});
    }
  });
});

researchSearch.addEventListener("input", () => applyMapFilters());
coverageFilter.addEventListener("change", () => applyMapFilters({announce: true}));
researchSearch.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    researchSearch.value = "";
    applyMapFilters({announce: true});
    mapFind.open = false;
    return;
  }
  if (event.key !== "Enter") return;
  const first = applyMapFilters({announce: true})[0];
  if (first) selectNode(first.dataset.nodeId);
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.target.matches("input, textarea, select")) return;
  event.preventDefault();
  mapFind.open = true;
  researchSearch.focus();
});

splitDivider.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  splitDragState = {pointerId: event.pointerId};
  splitDivider.setPointerCapture(event.pointerId);
  document.body.classList.add("resizing-workspace");
  setSplitPosition(splitRatioFromPointer(event.clientX));
});

splitDivider.addEventListener("pointermove", (event) => {
  if (!splitDragState || splitDragState.pointerId !== event.pointerId) return;
  setSplitPosition(splitRatioFromPointer(event.clientX));
});

function finishSplitResize(event) {
  if (!splitDragState || splitDragState.pointerId !== event.pointerId) return;
  splitDragState = null;
  if (splitDivider.hasPointerCapture(event.pointerId)) splitDivider.releasePointerCapture(event.pointerId);
  document.body.classList.remove("resizing-workspace");
  setSplitPosition(splitRatio, {announce: true});
}

splitDivider.addEventListener("pointerup", finishSplitResize);
splitDivider.addEventListener("pointercancel", finishSplitResize);
splitDivider.addEventListener("dblclick", () => setSplitPosition(.5, {announce: true}));
splitDivider.addEventListener("keydown", (event) => {
  let next = null;
  if (event.key === "ArrowLeft") next = splitRatio - .025;
  if (event.key === "ArrowRight") next = splitRatio + .025;
  if (event.key === "Home") next = splitLimits().minimum;
  if (event.key === "End") next = splitLimits().maximum;
  if (next === null) return;
  event.preventDefault();
  setSplitPosition(next, {announce: true});
});

window.addEventListener("resize", () => {
  setSplitPosition(splitRatio);
});

mapZoomOut.addEventListener("click", () => {
  const previous = cameraScale;
  cameraScale = Math.max(.01, cameraScale / 1.2);
  const ratio = cameraScale / previous;
  cameraX *= ratio;
  cameraY *= ratio;
  updateMapCamera();
  liveStatus.textContent = `Map zoom ${Math.round(cameraScale * 100)} percent`;
});
mapZoomIn.addEventListener("click", () => {
  const previous = cameraScale;
  cameraScale = Math.min(4, cameraScale * 1.2);
  const ratio = cameraScale / previous;
  cameraX *= ratio;
  cameraY *= ratio;
  updateMapCamera();
  liveStatus.textContent = `Map zoom ${Math.round(cameraScale * 100)} percent`;
});
mapFit.addEventListener("click", () => fitGraph({record: true}));
mapBack.addEventListener("click", () => {
  const previous = cameraHistory.pop();
  if (!previous) return;
  ({focusRootId, selectedNodeId, cameraScale, cameraX, cameraY} = previous);
  reflectSelectedTopicInUrl();
  updateSelectedMapNode();
  renderBreadcrumb(activeGraph().byId);
  updateMapCamera();
  renderedSignatures.map = null;
  renderInspector({force: true});
  if (selectedNodeId) surfaceDraftNode(selectedNodeId);
  liveStatus.textContent = "Camera returned to previous research view";
});
mapCanvas.addEventListener("pointerdown", (event) => {
  const preview = event.target.closest("[data-project-id][data-target-node-id]");
  if (workspaceOverview && preview && !preview.disabled) {
    event.preventDefault();
    liveStatus.textContent = "Opening question directory…";
    switchWorkspaceProject(preview.dataset.projectId, preview.dataset.targetNodeId).catch((error) => {
      liveStatus.textContent = `Question load failed: ${error.message}`;
    });
    return;
  }
  if (event.target.closest("[data-node-id], [data-evidence-id], [data-destination-node-id]")) return;
  dragState = {pointerId: event.pointerId, x: event.clientX, y: event.clientY, cameraX, cameraY};
  mapCanvas.setPointerCapture(event.pointerId);
  mapCanvas.classList.add("panning");
});
mapCanvas.addEventListener("keydown", (event) => {
  const treeItem = event.target.closest('[role="treeitem"][data-node-id]');
  if (treeItem && ["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) {
    const items = Array.from(mapCanvas.querySelectorAll('.map-stage [role="treeitem"][data-node-id]'))
      .filter((item) => !item.disabled && !item.hidden && !item.classList.contains("map-filtered-out"));
    let next = null;
    const index = items.indexOf(treeItem);
    if (event.key === "ArrowDown") next = items[Math.min(items.length - 1, index + 1)];
    if (event.key === "ArrowUp") next = items[Math.max(0, index - 1)];
    if (event.key === "Home") next = items[0];
    if (event.key === "End") next = items[items.length - 1];
    if (event.key === "ArrowLeft" && treeItem.getAttribute("aria-expanded") === "true" && collapseTreeNode(treeItem.dataset.nodeId)) {
      event.preventDefault();
      return;
    }
    if (event.key === "ArrowLeft" && treeItem.dataset.parentNodeId) {
      next = items.find((item) => item.dataset.nodeId === treeItem.dataset.parentNodeId) || null;
    }
    if (event.key === "ArrowRight") {
      next = items.find((item) => item.dataset.parentNodeId === treeItem.dataset.nodeId) || null;
      if (!next && treeItem.getAttribute("aria-expanded") === "false" && !workspaceOverview) {
        followRouteToNode(treeItem.dataset.nodeId);
        return;
      }
    }
    if (next) {
      event.preventDefault();
      items.forEach((item) => { item.tabIndex = item === next ? 0 : -1; });
      next.focus({preventScroll: true});
    }
    return;
  }
  const preview = event.target.closest("[data-project-id][data-target-node-id]");
  if (!workspaceOverview || !preview || preview.disabled || !["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  liveStatus.textContent = "Opening question directory…";
  switchWorkspaceProject(preview.dataset.projectId, preview.dataset.targetNodeId).catch((error) => {
    liveStatus.textContent = `Question load failed: ${error.message}`;
  });
});
mapCanvas.addEventListener("pointermove", (event) => {
  if (!dragState || dragState.pointerId !== event.pointerId) return;
  cameraX = dragState.cameraX + event.clientX - dragState.x;
  cameraY = dragState.cameraY + event.clientY - dragState.y;
  updateMapCamera();
});
mapCanvas.addEventListener("pointerup", (event) => {
  if (!dragState || dragState.pointerId !== event.pointerId) return;
  dragState = null;
  mapCanvas.releasePointerCapture(event.pointerId);
  mapCanvas.classList.remove("panning");
});
mapCanvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  const rect = mapCanvas.getBoundingClientRect();
  const pointX = event.clientX - rect.left - rect.width / 2;
  const pointY = event.clientY - rect.top - rect.height / 2;
  const worldX = (pointX - cameraX) / cameraScale;
  const worldY = (pointY - cameraY) / cameraScale;
  cameraScale = Math.max(.01, Math.min(4, cameraScale * (event.deltaY > 0 ? .9 : 1.1)));
  cameraX = pointX - worldX * cameraScale;
  cameraY = pointY - worldY * cameraScale;
  updateMapCamera();
}, {passive: false});
mapCanvas.addEventListener("pointercancel", () => {
  dragState = null;
  mapCanvas.classList.remove("panning");
});
activateContextTab("draft");

loadState({announce: true})
  .then(() => pollCompletionSignal().catch(() => {}))
  .catch(failInitialLoad);
window.setInterval(() => pollCompletionSignal().catch(() => {}), 1000);
window.setInterval(() => loadState().catch((error) => {
  if (!initialLoadComplete) failInitialLoad(error);
  else {
    const publication = snapshot && snapshot.publication;
    setText(refreshState, publication
      ? `Stale · last live revision ${publication.published_revision} · refresh failed`
      : "Stale · refresh failed");
  }
}), 5000);
