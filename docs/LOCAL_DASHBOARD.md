# Local dashboard for any agent

Sole Research files are authoritative. A configured Codex Site is the primary
read-only presentation for a `public_only` project; the local dashboard is the
safe fallback for other agent harnesses, offline use, and every `local_private`
project.

## What must be installed

Node.js and npm are not required to run this dashboard. They are used only by
maintainers building the hosted Codex Site.

On macOS arm64, this repository includes a compatible standalone
`sole-research` binary, so the launcher may work with no additional runtime.
On every supported development platform, the recommended setup is `uv`:

```bash
# macOS with Homebrew
brew install uv

# Or the official uv installer on macOS or Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart the shell if `uv --version` is not found immediately. From the Sole
Research repository, install the locked environment and verify it:

```bash
uv sync --locked
uv run sole-research --help
```

Sole Research requires Python 3.12 or newer. A separate Python installation is
normally unnecessary because `uv` downloads a compatible Python automatically
when the machine does not already have one. For an offline installation,
install Python 3.12+ and the locked dependencies before disconnecting.

The authoritative installation references are the
[uv installation guide](https://docs.astral.sh/uv/getting-started/installation/)
and [uv Python-version guide](https://docs.astral.sh/uv/concepts/python-versions/).

## Start the dashboard

From this repository, start the fallback with one command:

```bash
scripts/serve-dashboard /absolute/path/to/research-project
```

For a workspace whose immediate children are independent projects:

```bash
scripts/serve-dashboard /absolute/path/to/workspace/question-a \
  --workspace-dir /absolute/path/to/workspace
```

The launcher:

- honors an operator-selected `sole-research` on `PATH`, then prefers the source
  checkout's `.venv` and `uv` before considering its bundled binary;
- uses the bundle only when it is an executable Mach-O arm64 artifact running on
  macOS arm64, so Linux and Intel hosts cannot select an incompatible runtime;
- runs `doctor` before serving;
- binds only to `127.0.0.1` and never enables editing;
- asks the server to bind port 8765 by default and, only if that bind reports
  `EADDRINUSE`, immediately retries with port 0 for an atomic OS assignment;
- prints a `soleresearch_dashboard_starting` JSON record, followed by the
  server's JSON record containing the final `listening` URL; and
- forwards Ctrl-C and termination signals directly to the server for clean
  shutdown.

Select a different preferred port with `--port PORT`. Treat the final
`listening` value printed by the server as authoritative. For compatibility, an
older PATH or bundled runtime that lacks atomic fallback receives port 0 from
the outset instead of using a racy availability check.

Do not add `--edit`, bind a non-loopback address, publish a `local_private`
project, or expose the dashboard through a tunnel without explicit human
authorization. The launcher intentionally has no options for those operations.
Agents may open the printed URL with whatever browser-control facility their
harness provides; browser availability is not required to preserve or inspect
the canonical files.

The underlying compatibility command remains available:

```bash
sole-research serve /absolute/path/to/research-project \
  --workspace-dir /absolute/path/to/workspace \
  --host 127.0.0.1 \
  --port 8765
```
