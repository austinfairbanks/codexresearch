# Sole Research agent guide

Preserve the local-first contract: project JSON, JSONL, Markdown, BibTeX, and
CSL-JSON files are authoritative. A dashboard is a read-only projection, not a
second source of truth.

For a `public_only` project with an already configured publisher, use its Codex
Site as the primary presentation medium; that configuration is standing
authorization for ordinary coherent snapshot publication. Explicit human
approval is still required to configure or enable publication, deploy the Site,
or change its access. Never publish `local_private` data.

When Codex Sites is unavailable, the project is private, or the current harness
is not Codex, use the agent-neutral local fallback documented in
`docs/LOCAL_DASHBOARD.md`:

```bash
scripts/serve-dashboard /absolute/path/to/project
```

The launcher is deliberately loopback-only and read-only. Explicit human
approval is required to configure or enable publication, deploy or change Site
access, expose a non-loopback address or tunnel, or enable edits; ordinary
snapshot publication remains allowed for an already configured `public_only`
project. Keep each independent research question in a separate immediate child
project directory. Run `doctor` after canonical changes and preserve the
existing Site and local-server workflows.
