# Sole Research Site

Private, read-only Codex Sites projection for local Sole Research projects.
The Site stores bounded snapshots and an immutable revision ledger in D1. It
does not expose research mutation routes, source document bodies, capabilities,
or publisher credentials.

The frontend is shared with `src/soleresearch/ui`; the predev/prebuild step
copies it into the deployable static bundle. Local development therefore uses
Node only in this repository. Installed users run the standalone plugin binary
and the hosted Site and do not need Node, Python, a tunnel, or a local web
server.

For local development:

```bash
SOLERESEARCH_PUBLISH_TOKEN=replace-with-a-long-random-value npm run dev
npm run build
```

The Python publisher posts to the project-bound
`/api/v1/projects/<project-id>/snapshots` route; `/api/v1/publish` remains a
compatibility alias. Owner-only deployments
also require the separate Sites dispatch credential in the
`OAI-Sites-Authorization` header. The route requires the site-scoped application
secret, rejects projections over 2 MiB, verifies their content hash, rejects
stale or conflicting revisions, and atomically writes the current row plus the
latest 20 immutable revisions. Browser routes are read-only and rely on the
Sites audience policy for access.
