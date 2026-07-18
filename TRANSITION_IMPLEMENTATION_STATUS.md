# Codex Plugin + Sites Transition Status

Status date: 2026-07-18

The transition is implemented as a macOS arm64 developer preview. The local
files remain authoritative, the installed Codex plugin launches a standalone
stdio MCP process, and the owner-only Site stores bounded read-only projections
in D1. The existing localhost dashboard remains available as the compatibility
fallback.

## Implemented and verified

| Area | Evidence |
| --- | --- |
| Projection | Versioned schema, canonical project/content hashes, 2 MiB bound, collection descriptors, redaction tests |
| Publication | Separate app and Sites-dispatch credentials, durable exact-retry outbox, bounded backoff, idempotency, stale/conflict checks, 20-revision D1 history |
| Site | Owner-only production deployment, D1 persistence, read-only dashboard, project-scoped reads, live revision polling |
| MCP | Protocol handshake, strict named tools, catalog mapping, explicit workspace selection, immediate-child confinement |
| Plugin | Personal-marketplace install, native hook, standalone macOS arm64 runtime; no target Python, Node, `uv`, or `pnpm` |
| Compatibility | Existing CLI/HTTP dashboard retained; 269 Python tests and Site build/tests pass |

The private deployment is `https://sole-research.general992066.chatgpt.site`.
It requires an authorized ChatGPT/Sites session and is not made public by this
implementation.

## External release gates still required

- A signed/notarized macOS artifact and signed Windows artifact require release
  signing identities that are not present in this development environment.
- macOS x86_64, Windows x86_64, and Linux x86_64 artifacts require builds and
  clean-machine tests on those hosts. The checked-in artifact is macOS arm64.
- The current environment cannot attach an authenticated in-app browser to the
  owner-only Site, so desktop side-by-side interaction and visual viewport QA
  remain a manual release check. API isolation, live D1 publication, build-time
  accessibility assertions, and local dashboard behavior are automated.
- The Sites starter's released Next line currently carries a moderate build-time
  PostCSS advisory with no non-breaking npm remediation. Project content is not
  compiled as CSS; revisit when the supported starter updates its pinned
  transitive dependency.

These gates limit general distribution claims. They do not prevent local use on
the current macOS arm64 workstation.

## Local first run

```bash
integrations/codex/soleresearch/bin/sole-research workspace select "$PWD"
integrations/codex/soleresearch/bin/sole-research doctor
```

Start a new Codex thread after installing or updating the plugin so the new MCP
server and skills are discovered. Create projects only as immediate children of
the selected workspace. Publisher and Sites-dispatch tokens live in separate
mode-0600 files outside project directories.
