# Phase 7 Handoff

## Task

- Plan: `PLANNED_VISION.md`, `SOLERESEARCH-001 r2 approved`
- Unit: Phase 7 — external validation, landscape, and completion audit
- Status: complete for offline V1; live public-web smoke and interactive browser visual QA explicitly unverified
- Scope: dated official-source landscape, external synthetic outsole-topic mechanics demo, ten-step/plan-clause audit, deterministic export/index validation, final package/plugin/Docker checks; no live source, provider/model, retailer, Git action, dependency, paper prose, or package domain logic
- Baseline: accepted Phase 6 tree; parent `e351a184ccb21f60cf98a75fd79dd8394898a142`; unrelated parent changes preserved

## Changes

| File or area | Outcome | Contract or invariant affected |
| --- | --- | --- |
| `LANDSCAPE.md` | Point-in-time matrix for OpenAI/Perplexity/LangChain/Hugging Face/GPT Researcher/NotebookLM/PaperQA2/Elicit/STORM/ResearchRabbit/Litmaps/Zotero | Direct official links; facts separated from Soleresearch own/integrate/defer inferences |
| `COMPLETION_AUDIT.md` | Maps outcome/invariants/contracts, all ten demo steps, coverage, and explicit deferrals to evidence | No offline/semantic check is mislabeled as live or visual evidence |
| `research/soleresearch-outsole-demo/{README.md,.gitignore,fixtures/,run_offline_demo.py}` | Reproducible external 4-source synthetic demo | Topic logic remains outside package; no scientific claim; generated tree ignored; capabilities scrubbed after completion |
| `research/soleresearch-outsole-demo/demo-output/` | Successful ignored local run state, two exports, Zotero bundle, audit | Generated only; no controller capability remains; project/exports contain no raw token |

## Demonstration

- Project: `prj_28717670edfe4ad1831975521d8c8ad1`
- Initial reviewed graph diff: `dif_a13112b43a7c4c988353db4d64ccbe71`
- Human outline edit: `hed_2386f7155e744bdcaadc14206bdf6f2e`
- Reconciliation: changed, one durable reconciliation event
- Continuation run: `run_d1d3d3b36a9c472d82335f56cfa62017`
- Continuation reviewed diff: `dif_9b2623fb77d116e1d820a70d7be8687f`
- Stop: early, recorded reason; 4 sources, 4 exact-locator evidence records, `$0` provider usage
- Export: two directory trees hash-identical
- Index: deletion left research views identical; rebuild restored identical research views and logical snapshot; only the explicit `index_present` indicator toggled
- Zotero: `pending_human_review`; no API/library mutation
- Live public web / retailer: false / false

## Validation

| Command or check | Result | Evidence or reason skipped |
| --- | --- | --- |
| offline demo script | pass | ordered initial diff → served loopback UI HTTP edit/state inspection → reconcile → bounded continuation/gate → early stop → export/rebuild/Zotero |
| independent Codex harness acceptance | pass | bundled orchestration skill scripts invoked; one minimal-context capability-limited child spawned; strict result admitted in a bounded SOL run; hard caps observed; authority remains `agent_accepted`, pending separate human ratification |
| fresh absolute-output smoke with conflicting ambient controller root | pass | `SOLERESEARCH_CONFIG_HOME=/tmp/soleresearch-conflicting-should-stay-absent SOLERESEARCH_DEMO_OUTPUT=/tmp/soleresearch-demo-selfcontained ...`; script overrode ambient, completed all steps, scrubbed its broker, and never created the conflicting root |
| result-contract parity and worker preflight | pass | Draft 2020-12 schema has eight literal `oneOf` operation branches and six bidirectional `allOf` output/completion/no-result conditions; `tools validate-result --file` is present in the catalog, Codex skill, and generated adapter |
| `UV_CACHE_DIR=/tmp/soleresearch-uv-cache uv run --frozen pytest -q` | pass, root-confirmed final validation | 232/232 passed after final parity and preflight hardening |
| `UV_CACHE_DIR=/tmp/soleresearch-uv-cache uv lock --check` | pass | 34 packages resolved; no dependency change |
| `uv build --offline --out-dir /tmp/soleresearch-phase7-dist` | pass | sdist and wheel built |
| plugin validator | pass | validated `integrations/codex/soleresearch` |
| skill quick validators | pass | `orchestrate-research` and `prepare-zotero-bundle` valid |
| `docker compose config --quiet` | pass | Compose contract valid |
| local-base `docker compose build` | pass | CLI and UI images built from `devcontainer-devcontainer:latest` |
| Docker CLI status smoke | pass | non-root container read demo: valid; 4 sources, 4 evidence, 3 nodes, clean outline, revision 4 |
| Python 3.12 demo script compile | pass | `uv run --frozen python -m py_compile` with temp bytecode cache |
| deterministic export/index assertions | pass | all five booleans in `demo-audit.json` true |
| raw capability token containment scan | pass | 2 generated capability values checked across project, both exports, and Zotero bundle: 0 hits; capability directory then scrubbed |
| controller/private-key/API-token pattern scan | pass | no credential-shaped values in owned source/demo tree |
| package domain-term scan | pass | no outsole/running-shoe/remaining-life behavior under package code/contracts/plugin |
| artifact extension scan | pass | no model weight formats |
| file mode / `git diff --check` | pass | docs 0644, scripts 0755, no unexpected modes or whitespace errors |
| served UI HTTP bootstrap/edit/state routes | pass | actual `127.0.0.1:0` server; GET bootstrap, GET outline/ETag, CSRF+`If-Match` PUT, GET state, clean shutdown |
| interactive browser visual/JavaScript QA | skipped | no browser execution; HTTP/API/semantic DOM/accessibility evidence is not visual evidence |
| live 3–5-source web smoke | skipped | not explicitly approved; synthetic local fixtures used, as required by dispatch fallback |

## Contract and safety checks

| Check | Result | Evidence or reason skipped |
| --- | --- | --- |
| exact locators + immutable source hashes/versions | pass | four section locators tied to fixture SHA-256/source versions |
| source content cannot grant execution/capability/budget | pass | task allowlist/packet tests; fixtures are inert Markdown |
| topic-specific content outside generic package | pass | scoped term scan |
| human vs agent authority | pass | both graph diffs reviewed with human controller; distinct external capabilities |
| generated state and credentials excluded from Git | pass | demo-root `.gitignore`; `demo-output/` check-ignore pass; controller config absent |
| no live/paid/auth/retailer/Git mutation | pass | demo audit false/zero; no Git commands in demo workflow |
| source facts vs design inference | pass | landscape header and per-row decision column make boundary explicit |

## Artifact ledger

| Path | Role | SHA-256 / identity | Schema / adapter / model version | Git-ignored verified |
| --- | --- | --- | --- | --- |
| `research/soleresearch-outsole-demo/fixtures/source-1.md` | synthetic input | `def983112eb8bc85399fe29acec38808cb5df11510ba2bb9b8b491b33bbc44d8` | Markdown fixture / n/a | no; intended source-controlled fixture |
| `.../source-2.md` | synthetic input | `e5fd926fddb81e2c717b5d01a173b1b9295e2c0975cd8999a0785719140a0871` | Markdown fixture / n/a | no; intended source-controlled fixture |
| `.../source-3.md` | synthetic input | `3bb7a42c7980bf881a75e0649853d23e92ebeacc5ef101927713573c7a91a6ec` | Markdown fixture / n/a | no; intended source-controlled fixture |
| `.../source-4.md` | synthetic input | `1c24271ae300659458697f646cf26cc020ac8419c4d25ab09977b0ff264982ec` | Markdown fixture / n/a | no; intended source-controlled fixture |
| `demo-output/demo-audit.json` | output evidence | `5d1a0f5cbf47181549137facd217db890f6e4b53a37b98f32b53c1e817215822` | demo audit v1 | yes |
| `demo-output/export-a/manifest.json` | export output | `078e54a4ef903962adb410c2900927a4cbc913831f8395f52d82fd2b18d49eaf` | export v1 | yes |
| `demo-output/zotero-review/review-manifest.json` | integration output | `81e8a0e6b24f61f2b5250aa14c939d7d603ed7036345f16b388727274d2a3e15` | review manifest v1 | yes |
| `demo-output/harness-acceptance.json` | normalized independent acceptance evidence | SHA-256 `ec7d3ed9fc461f924e4c414a22829c3caf36d0f65df0c4a8549e29e7e2f614ed`; project `prj_2fd10c99ad92415f903e714ed0bb119d`; run `run_7432dfdd67464240ad006690c0713ed7` | harness acceptance v1 | yes |
| `/tmp/soleresearch-phase7-dist/*` | build output | ephemeral | package 0.1.0 | outside Git |
| Docker `soleresearch-cli`, `soleresearch-ui` | validation output | local image manifests in build log | package 0.1.0 | outside Git |

## Decisions and blockers

- Approved decisions applied: root orchestrator accepted delegated phase gates; fallback demo remained offline because no live-smoke approval was sent.
- Ambiguities: none silently resolved. “Open Deep Research” is represented explicitly by both current LangChain and Hugging Face open implementations.
- Resolved contract gap: harness acceptance exposed opaque graph-operation and completion-declaration schema gaps while the broker rejected both fail-closed. The Draft 2020-12 result schema now has eight literal `oneOf` operation branches plus six bidirectional `allOf` output/completion/no-result conditions, and `tools validate-result --file` brings preflight into the catalog, Codex skill, and generated adapter. The harness artifact preserves the original rejection history.
- Blockers: none from schema/broker parity. Scientific source validation and interactive browser visual QA remain explicitly unverified and are not claimed by the offline fallback.
- Next authorized step: independent reviewer and outside-perspective audit; no commit/push/install/live run without separate human approval.
