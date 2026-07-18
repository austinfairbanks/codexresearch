from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from soleresearch.refresh import empty_refresh_signal, read_refresh_signal


PLUGIN = Path(__file__).parents[1] / "integrations/codex/soleresearch"
HOOK_CONFIG = PLUGIN / "hooks/hooks.json"


def _run_hook(
    signal_path: Path,
    payload: object,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["SOLERESEARCH_REFRESH_SIGNAL"] = str(signal_path)
    return subprocess.run(
        [sys.executable, "-m", "soleresearch", "hook", "turn-complete"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def test_plugin_declares_bounded_fail_safe_stop_hook() -> None:
    config = json.loads(HOOK_CONFIG.read_text(encoding="utf-8"))

    assert set(config) == {"hooks"}
    assert set(config["hooks"]) == {"Stop"}
    stop_hook = config["hooks"]["Stop"]
    assert len(stop_hook) == 1
    command_hook = stop_hook[0]["hooks"]
    assert len(command_hook) == 1
    assert command_hook[0] == {
        "type": "command",
        "command": '"$PLUGIN_ROOT/bin/sole-research" hook turn-complete',
        "commandWindows": '"%PLUGIN_ROOT%\\bin\\sole-research.exe" hook turn-complete',
        "timeout": 35,
    }


def test_hook_uses_packaged_native_runtime(tmp_path: Path) -> None:
    signal_path = tmp_path / "turn-complete.json"

    result = _run_hook(
        signal_path,
        {"hook_event_name": "Stop", "turn_id": "turn_fixture"},
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"continue": True}
    assert read_refresh_signal(signal_path)["signal_id"].startswith("sig_")


def test_stop_hook_writes_signal_consumed_by_ui_contract(tmp_path: Path) -> None:
    signal_path = tmp_path / "turn-complete.json"
    result = _run_hook(
        signal_path,
        {
            "hook_event_name": "Stop",
            "turn_id": "turn_fixture",
            "session_id": "session_fixture",
            "last_assistant_message": "Private response text is not persisted.",
        },
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"continue": True}
    assert result.stderr == ""
    signal = read_refresh_signal(signal_path)
    assert signal["schema_version"] == 1
    assert signal["signal_id"].startswith("sig_")
    assert signal["completed_at"].endswith("Z")
    assert set(json.loads(signal_path.read_text(encoding="utf-8"))) == {
        "schema_version",
        "signal_id",
        "completed_at",
    }


def test_hook_ignores_wrong_or_malformed_events_without_failing_turn(tmp_path: Path) -> None:
    signal_path = tmp_path / "turn-complete.json"
    for payload in ({"hook_event_name": "PostToolUse", "turn_id": "turn_fixture"}, {}, "not an object"):
        result = _run_hook(signal_path, payload)
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"continue": True}
        assert result.stderr == ""
        assert not signal_path.exists()


def test_hook_write_failure_is_silent_and_reader_fails_closed(tmp_path: Path) -> None:
    unwritable_target = tmp_path / "already-a-directory"
    unwritable_target.mkdir()
    result = _run_hook(unwritable_target, {"hook_event_name": "Stop", "turn_id": "turn_fixture"})

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"continue": True}
    assert result.stderr == ""
    assert read_refresh_signal(unwritable_target) == empty_refresh_signal()


def test_reader_ignores_drifted_or_oversized_signal_contracts(tmp_path: Path) -> None:
    signal_path = tmp_path / "turn-complete.json"
    signal_path.write_text('{"schema_version":2,"signal_id":"sig_bad","completed_at":null}\n', encoding="utf-8")
    assert read_refresh_signal(signal_path) == empty_refresh_signal()

    signal_path.write_text("x" * 4097, encoding="utf-8")
    assert read_refresh_signal(signal_path) == empty_refresh_signal()
