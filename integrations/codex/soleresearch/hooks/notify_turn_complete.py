from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


MAX_HOOK_INPUT_BYTES = 65_536
REFRESH_SIGNAL_ENV = "SOLERESEARCH_REFRESH_SIGNAL"
SCHEMA_VERSION = 1


def _signal_path() -> Path:
    explicit = os.environ.get(REFRESH_SIGNAL_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    uid = str(os.getuid()) if hasattr(os, "getuid") else "user"
    return (Path(tempfile.gettempdir()) / f"soleresearch-turn-complete-{uid}.json").resolve()


def _atomic_signal(path: Path) -> None:
    record = {
        "schema_version": SCHEMA_VERSION,
        "signal_id": "sig_" + uuid.uuid4().hex,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(record, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
        if len(raw) <= MAX_HOOK_INPUT_BYTES:
            payload = json.loads(raw.decode("utf-8"))
            if (
                isinstance(payload, dict)
                and payload.get("hook_event_name") == "Stop"
                and isinstance(payload.get("turn_id"), str)
                and payload["turn_id"]
            ):
                _atomic_signal(_signal_path())
    except Exception:
        # Completion refresh is advisory. The existing UI polling remains the fallback,
        # and a signaling failure must never block or continue a Codex turn.
        pass
    sys.stdout.write('{"continue":true}\n')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
