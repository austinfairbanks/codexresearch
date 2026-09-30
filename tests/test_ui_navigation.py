"""Behavioral checks for search and asynchronous read-only navigation."""

import shutil
import subprocess
from pathlib import Path

import pytest


APP = Path(__file__).parents[1] / "src/soleresearch/ui/app.js"


def run_javascript(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for browser logic checks")
    harness = r"""
const fs = require('node:fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
function extract(name) {
  const marker = `function ${name}(`;
  let start = source.indexOf(marker);
  assert(start >= 0, name);
  if (source.slice(start - 6, start) === 'async ') start -= 6;
  return source.slice(start, source.indexOf('\n}', start) + 2);
}
"""
    result = subprocess.run([node, "-e", harness + script, str(APP)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_search_reaches_unrendered_descendants_and_linked_source_titles() -> None:
    run_javascript(r"""
const vm = require('node:vm');
const nodes = [
  {node_id: 'root', title: 'Research question', node_type: 'question'},
  {node_id: 'topic', parent_id: 'root', title: 'Tendon repair', node_type: 'concept'},
  {node_id: 'hidden', parent_id: 'topic', title: 'A qualifying result', body: 'Does the effect replicate?',
   evidence_ids: ['ev1'], coverage: {state: 'conflicting_evidence'}},
];
const project = {project_id: 'p1', name: 'Repair research', available: true};
const context = vm.createContext({
  snapshot: {views: {evidence: {items: [{evidence_id: 'ev1', source_title: 'Replication study'}]}}},
  workspaceOverview: false, activeProjectId: 'p1',
  activeGraph: () => ({nodes}), activeWorkspaceProject: () => project,
  displayClaimTitle: (title) => title,
});
vm.runInContext(extract('researchSearchEntries') + '\n' + extract('coverageMatches'), context);
const entries = context.researchSearchEntries();
assert.equal(entries.length, 3);
const match = entries.find(entry => entry.text.includes('replication study'));
assert.equal(match.nodeId, 'hidden');
assert.equal(match.scope, 'Tendon repair');
assert.equal(context.coverageMatches(match.state, 'linked'), true);
assert.equal(context.coverageMatches(match.state, 'conflict'), true);
assert.equal(context.coverageMatches(match.state, 'unlinked'), false);
assert.equal(entries.find(entry => entry.nodeId === 'hidden').text.includes('replicate'), true);
""")


def test_slow_previous_project_read_cannot_replace_new_project() -> None:
    run_javascript(r"""
const vm = require('node:vm');
(async () => {
  let releaseOld;
  let notifyOldStarted;
  const oldStarted = new Promise(resolve => { notifyOldStarted = resolve; });
  const oldState = new Promise(resolve => { releaseOld = resolve; });
  const projects = {projects: [
    {project_id: 'alpha', available: true}, {project_id: 'beta', available: true}
  ], default_project_id: 'alpha'};
  const state = name => ({project: {name}, outline: {dirty: false}});
  const context = vm.createContext({
    stateRequestId: 0, initialLoadComplete: true, activeProjectId: 'alpha', workspace: projects,
    workspaceOverview: false, primaryMapSignature: null, selectedNodeId: null,
    snapshot: state('initial'), refreshState: {},
    document: {body: {classList: {remove() {}}}, getElementById: () => ({})},
    mapCanvas: {classList: {remove() {}}},
    fetch: async path => {
      if (path === '/api/v1/workspace') return {ok: true, json: async () => projects};
      const project = new URL(path, 'http://localhost').searchParams.get('project');
      if (path.startsWith('/api/v1/outline')) return {ok: true, json: async () => ({content: project})};
      if (project === 'alpha') {
        notifyOldStarted();
        return {ok: true, json: () => oldState};
      }
      return {ok: true, json: async () => state(project)};
    },
    setText() {}, updateOutline() {}, renderPrimaryMap() {}, renderActivity() {}, renderInspector() {},
    renderReaderContents() {}, setConnectionStatus() {}, enableLoadedMode() {},
  });
  vm.runInContext(extract('loadState'), context);
  const previous = context.loadState();
  await oldStarted;
  context.activeProjectId = 'beta';
  await context.loadState();
  assert.equal(context.snapshot.project.name, 'beta');
  releaseOld(state('alpha'));
  await previous;
  assert.equal(context.snapshot.project.name, 'beta');
  assert.equal(context.activeProjectId, 'beta');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_connection_failure_marks_saved_view_stale_and_recovers() -> None:
    run_javascript(r"""
const vm = require('node:vm');
const status = {classList: {toggle(name, value) { this[name] = value; }}};
const retry = {hidden: true};
const context = vm.createContext({
  lastSuccessfulRead: new Date(), snapshot: {project: {name: 'Preserved'}},
  document: {getElementById: id => id === 'connection-status' ? status : retry},
  setText: (item, value) => { item.textContent = value; },
});
vm.runInContext(extract('setConnectionStatus'), context);
context.setConnectionStatus(new Error('Unavailable'));
assert.match(status.textContent, /last loaded version/);
assert.equal(status.classList['is-stale'], true);
assert.equal(retry.hidden, false);
assert.equal(context.snapshot.project.name, 'Preserved');
context.setConnectionStatus();
assert.equal(status.classList['is-stale'], false);
assert.equal(retry.hidden, true);
assert.match(status.textContent, /Local preview/);
""")


def test_superseded_project_navigation_cannot_move_new_projects_camera() -> None:
    run_javascript(r"""
const vm = require('node:vm');
(async () => {
  const completions = [];
  const moves = [];
  const context = vm.createContext({
    workspace: {projects: [{project_id: 'alpha', available: true}, {project_id: 'beta', available: true}]},
    workspaceOverview: true, projectNavigationId: 0,
    cameraScale: 1, cameraX: 0, cameraY: 0,
    renderedSignatures: {}, refreshState: {}, setText() {},
    graphPositions: new Map([['workspace:alpha:a', {}], ['workspace:beta:b', {}]]),
    loadState: () => new Promise(resolve => completions.push(resolve)),
    moveCameraToNode: (id, options) => moves.push({id, selected: options.selectedId}),
  });
  vm.runInContext(extract('switchWorkspaceProject'), context);
  const earlier = context.switchWorkspaceProject('alpha', 'a');
  const later = context.switchWorkspaceProject('beta', 'b');
  context.snapshot = {project: {project_id: 'beta'}};
  completions[1]();
  await later;
  completions[0]();
  await earlier;
  assert.deepEqual(moves, [{id: 'workspace:beta:b', selected: 'b'}]);
  assert.equal(context.activeProjectId, 'beta');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
