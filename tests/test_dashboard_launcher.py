from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from soleresearch.ui import _asset, serve


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts/serve-dashboard"


def _runtime(path: Path, label: str, *, supports_fallback: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    help_output = "--fallback-to-free-port" if supports_fallback else "--port PORT"
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' '{label}|'\"$*\" >> \"$FAKE_CALL_LOG\"\n"
        f"case \"$*\" in *'serve --help'*) printf '%s\\n' '{help_output}';; esac\n"
        "case \"$*\" in *doctor*) printf '{\"status\":\"healthy\"}\\n';; esac\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _checkout(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    checkout = tmp_path / "source checkout"
    script = checkout / "scripts/serve-dashboard"
    script.parent.mkdir(parents=True)
    shutil.copy2(LAUNCHER, script)
    project = tmp_path / "project with space\tmarker"
    project.mkdir()
    (project / "project.json").write_text("{}\n", encoding="utf-8")
    log = tmp_path / "calls.log"
    tool_bin = tmp_path / "tools"
    tool_bin.mkdir()
    env = {
        **os.environ,
        "PATH": f"{tool_bin}:/usr/bin:/bin",
        "FAKE_CALL_LOG": str(log),
    }
    return checkout, project, log, env


def _run(script: Path, project: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), str(project), *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def _starting_record(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return next(
        json.loads(line)
        for line in result.stdout.splitlines()
        if "soleresearch_dashboard_starting" in line
    )


def test_operator_selected_path_runtime_has_deliberate_precedence(tmp_path: Path) -> None:
    checkout, project, log, env = _checkout(tmp_path)
    _runtime(Path(env["PATH"].split(":", 1)[0]) / "sole-research", "path")
    _runtime(checkout / ".venv/bin/sole-research", "venv")

    result = _run(checkout / "scripts/serve-dashboard", project, env, "--port", "48765")

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert calls.count("path|") == 3 and "venv|" not in calls
    assert "--host 127.0.0.1 --port 48765 --fallback-to-free-port" in calls
    starting = _starting_record(result)
    assert starting["runner"] == "path"
    assert starting["read_only"] is True


def test_source_checkout_prefers_venv_then_uv_before_bundle(tmp_path: Path) -> None:
    checkout, project, log, env = _checkout(tmp_path)
    _runtime(checkout / ".venv/bin/sole-research", "venv")
    _runtime(Path(env["PATH"].split(":", 1)[0]) / "uv", "uv")
    _runtime(checkout / "integrations/codex/soleresearch/bin/sole-research", "bundle")

    first = _run(checkout / "scripts/serve-dashboard", project, env)
    assert first.returncode == 0, first.stderr
    assert _starting_record(first)["runner"] == "venv"
    assert "venv|" in log.read_text(encoding="utf-8")

    (checkout / ".venv/bin/sole-research").unlink()
    log.write_text("", encoding="utf-8")
    second = _run(checkout / "scripts/serve-dashboard", project, env)
    assert second.returncode == 0, second.stderr
    assert _starting_record(second)["runner"] == "uv"
    calls = log.read_text(encoding="utf-8")
    assert "uv|run --project" in calls and "bundle|" not in calls


def test_unsupported_platform_never_selects_checked_in_bundle(tmp_path: Path) -> None:
    checkout, project, _log, env = _checkout(tmp_path)
    _runtime(checkout / "integrations/codex/soleresearch/bin/sole-research", "bundle")
    uname = Path(env["PATH"].split(":", 1)[0]) / "uname"
    uname.write_text("#!/bin/sh\nprintf 'Linux\\n'\n", encoding="utf-8")
    uname.chmod(0o755)

    result = _run(checkout / "scripts/serve-dashboard", project, env)

    assert result.returncode == 2
    assert "no compatible sole-research runtime" in result.stderr
    assert "docs/LOCAL_DASHBOARD.md" in result.stderr


def test_local_dashboard_guide_documents_no_node_setup() -> None:
    guide = (ROOT / "docs/LOCAL_DASHBOARD.md").read_text(encoding="utf-8")
    assert "Node.js and npm are not required" in guide
    assert "brew install uv" in guide
    assert "uv sync --locked" in guide
    assert "Python 3.12 or newer" in guide


def test_older_runtime_uses_port_zero_without_availability_race(tmp_path: Path) -> None:
    checkout, project, log, env = _checkout(tmp_path)
    _runtime(
        Path(env["PATH"].split(":", 1)[0]) / "sole-research",
        "old-path",
        supports_fallback=False,
    )

    result = _run(checkout / "scripts/serve-dashboard", project, env, "--port", "48766")

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert "serve " in calls and "--port 0" in calls
    assert "--fallback-to-free-port" not in calls
    starting = _starting_record(result)
    assert starting["port_mode"] == "ephemeral_compatibility"
    assert starting["port_selected"] == 0


def test_leading_zero_port_is_canonical_base_ten_json(tmp_path: Path) -> None:
    checkout, project, log, env = _checkout(tmp_path)
    _runtime(Path(env["PATH"].split(":", 1)[0]) / "sole-research", "path")

    result = _run(checkout / "scripts/serve-dashboard", project, env, "--port", "001")

    assert result.returncode == 0, result.stderr
    starting = _starting_record(result)
    assert starting["port_requested"] == 1
    assert starting["port_selected"] == 1
    assert "--port 1 --fallback-to-free-port" in log.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "arguments, expected",
    [
        (("--port", "-1"), "port must be between"),
        (("--port", "70000"), "port must be between"),
        (("--unknown",), "unknown option"),
        (("second-project",), "only one project"),
        (("--workspace-dir", "missing"), "workspace directory does not exist"),
    ],
)
def test_invalid_arguments_fail_before_server_start(
    tmp_path: Path, arguments: tuple[str, ...], expected: str
) -> None:
    checkout, project, log, env = _checkout(tmp_path)
    _runtime(Path(env["PATH"].split(":", 1)[0]) / "sole-research", "path")

    result = _run(checkout / "scripts/serve-dashboard", project, env, *arguments)

    assert result.returncode == 2
    assert expected in result.stderr
    assert not log.exists()


def test_server_retries_only_address_in_use_and_closes_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[int] = []

    class Server:
        server_port = 43123
        served = False
        closed = False

        def serve_forever(self) -> None:
            self.served = True
            raise KeyboardInterrupt

        def server_close(self) -> None:
            self.closed = True

    server = Server()

    def create(_project: Path, **kwargs: object) -> Server:
        calls.append(int(kwargs["port"]))
        if len(calls) == 1:
            raise OSError(errno.EADDRINUSE, "busy")
        return server

    monkeypatch.setattr("soleresearch.ui.create_server", create)
    serve(project, port=8765, fallback_to_free_port=True)

    assert calls == [8765, 0]
    assert server.served and server.closed
    record = json.loads(capsys.readouterr().out)
    assert record["listening"] == "http://127.0.0.1:43123"
    assert record["edit_enabled"] is False


def test_launcher_and_server_use_current_assets() -> None:
    assert os.access(LAUNCHER, os.X_OK)
    assert _asset("app.js") == (ROOT / "src/soleresearch/ui/app.js").read_bytes()
