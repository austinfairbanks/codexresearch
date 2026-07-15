# Soleresearch offline outsole demo

This directory is an external validation project, not package code. Its narrow question is:

> Which observable outsole-wear features should a future running-shoe remaining-life model record before any threshold is inferred?

The four inputs under `fixtures/` are synthetic. They prove the evidence, graph,
human-gate, outline, export, and rebuild workflow without claiming scientific
findings. The script starts the actual UI server on ephemeral loopback, performs
HTTP bootstrap/outline/state requests and a CSRF/ETag-protected outline save,
then shuts it down. It does not execute browser JavaScript or provide visual
browser QA. No external network, retailer, model/provider, account, or paid
service is used.

Run from `src/soleresearch/` after `uv sync --locked --dev`:

```bash
UV_CACHE_DIR=/tmp/soleresearch-uv-cache \
  uv run --frozen python ../../research/soleresearch-outsole-demo/run_offline_demo.py
```

The script binds `SOLERESEARCH_CONFIG_HOME` to its selected output and overrides
any conflicting ambient value. Parent-side UI/controller operations and CLI
subprocesses therefore use one external broker. For a fresh external smoke,
select an absolute or demo-relative output path:

```bash
SOLERESEARCH_DEMO_OUTPUT=/tmp/soleresearch-demo-smoke \
  UV_CACHE_DIR=/tmp/soleresearch-uv-cache \
  uv run --frozen python ../../research/soleresearch-outsole-demo/run_offline_demo.py
```

The script refuses to overwrite an existing `demo-output/project/`. Remove or archive the generated demo deliberately before rerunning. Controller capabilities are adjacent to, not inside, the generated project, are never exported, and are scrubbed after the completed demo. The entire `demo-output/` tree is ignored; only this script, README, `.gitignore`, and synthetic fixtures are intended for source control. `demo-output/demo-audit.json` records command results and deterministic comparisons without tokens.

`demo-output/harness-acceptance.json` is separate normalized evidence from an
independent real Codex harness run. It records the plugin-skill invocation,
bounded SOL controller envelope, spawned minimal-context child, fail-closed
corrections, and final `agent_accepted` state. It contains no ephemeral absolute
paths or controller records and does not claim human ratification.

Live public-web validation was not approved or executed in this demo. Replace the synthetic fixtures only in a separately approved smoke run with sources the operator is allowed to inspect and retain.
