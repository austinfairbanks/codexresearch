from __future__ import annotations

import fcntl
import os
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from functools import wraps
from typing import Any, Callable, Iterator, TypeVar

from soleresearch.errors import ProjectError
from soleresearch.storage import confined_project_path, read_json

_LOCKS: ContextVar[dict[str, tuple[Any, int]]] = ContextVar("soleresearch_write_locks", default={})
_RUN_SCOPES: ContextVar[dict[str, str]] = ContextVar("soleresearch_run_scopes", default={})
_LOCK_ALIASES: ContextVar[dict[str, str]] = ContextVar("soleresearch_lock_aliases", default={})


class ProjectWriteLock:
    """Process-aware, context-reentrant, nonblocking project writer lock."""

    def __init__(self, project_path: Path) -> None:
        self.root = project_path.resolve()
        self.key = str(self.root)
        self.handle: Any = None
        self.nested = False

    def __enter__(self) -> "ProjectWriteLock":
        held = dict(_LOCKS.get())
        if self.key in held:
            handle, depth = held[self.key]
            held[self.key] = (handle, depth + 1)
            _LOCKS.set(held)
            self.handle, self.nested = handle, True
            return self
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            root_descriptor = os.open(self.root, directory_flags)
        except OSError as exc:
            raise ProjectError("cannot safely open project root for writer lock") from exc
        try:
            try:
                os.mkdir(".soleresearch", mode=0o700, dir_fd=root_descriptor)
            except FileExistsError:
                pass
            directory_descriptor = os.open(".soleresearch", directory_flags, dir_fd=root_descriptor)
        except OSError as exc:
            os.close(root_descriptor)
            raise ProjectError("cannot safely open .soleresearch lock directory (symbolic link or non-directory)") from exc
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open("run-writer.lock", flags, 0o600, dir_fd=directory_descriptor)
        except OSError as exc:
            os.close(directory_descriptor)
            os.close(root_descriptor)
            raise ProjectError("cannot safely open project writer lock") from exc
        os.close(directory_descriptor)
        os.close(root_descriptor)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ProjectError("project writer lock must be a regular file")
        handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise ProjectError("another process holds the project writer lock") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        held[self.key] = (handle, 1)
        _LOCKS.set(held)
        self.handle = handle
        return self

    def __exit__(self, *args: object) -> None:
        held = dict(_LOCKS.get())
        current = held.get(self.key)
        if current is None:
            return
        handle, depth = current
        if depth > 1:
            held[self.key] = (handle, depth - 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            held.pop(self.key, None)
        _LOCKS.set(held)


def _unfinished_run(root: Path) -> str | None:
    runs = confined_project_path(root, "runs")
    if not runs.exists():
        return None
    for state_path in sorted(runs.glob("run_*/state.json")):
        value = read_json(state_path)
        if not isinstance(value, dict) or value.get("run_id") != state_path.parent.name:
            raise ProjectError(f"invalid run state identity: {state_path}")
        if value.get("status") not in {"active", "paused", "finished"}:
            raise ProjectError(f"invalid run state status: {state_path}")
        if value["status"] != "finished":
            return value["run_id"]
    return None


def _persisted_controller_root(root: Path) -> Path:
    binding_path = confined_project_path(root, ".soleresearch/run-binding.json")
    if not binding_path.exists():
        return root
    value = read_json(binding_path)
    required = {"schema_version", "project_id", "run_id", "controller_root", "worktree", "branch"}
    if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 1:
        raise ProjectError("invalid persisted run worktree binding")
    controller = Path(value["controller_root"])
    if not controller.is_absolute() or Path(value["worktree"]).resolve() != root:
        raise ProjectError("persisted run worktree binding path mismatch")
    project = read_json(root / "project.json")
    controller_project = read_json(controller / "project.json")
    manifest = read_json(controller / "runs" / value["run_id"] / "manifest.json")
    if (
        project.get("project_id") != value["project_id"]
        or controller_project.get("project_id") != value["project_id"]
        or manifest.get("project_id") != value["project_id"]
        or manifest.get("run_id") != value["run_id"]
        or manifest.get("git", {}).get("worktree") != str(root)
        or manifest.get("git", {}).get("branch") != value["branch"]
    ):
        raise ProjectError("persisted run worktree binding is not backed by its controller manifest")
    return controller.resolve()


@contextmanager
def run_write_scope(project_path: Path, run_id: str, *, canonical_root: Path | None = None) -> Iterator[None]:
    root = project_path.resolve()
    canonical = (canonical_root or root).resolve()
    scopes = dict(_RUN_SCOPES.get())
    scopes[str(root)] = run_id
    scopes[str(canonical)] = run_id
    aliases = dict(_LOCK_ALIASES.get())
    aliases[str(canonical)] = str(root)
    token = _RUN_SCOPES.set(scopes)
    alias_token = _LOCK_ALIASES.set(aliases)
    try:
        yield
    finally:
        _LOCK_ALIASES.reset(alias_token)
        _RUN_SCOPES.reset(token)


@contextmanager
def canonical_write_guard(project_path: Path) -> Iterator[None]:
    """Serialize canonical writes and broker them through an active run."""
    root = project_path.resolve()
    lock_root = Path(_LOCK_ALIASES.get().get(str(root), str(_persisted_controller_root(root))))
    with ProjectWriteLock(lock_root):
        active = _unfinished_run(lock_root)
        scoped = _RUN_SCOPES.get().get(str(root))
        if active is not None and scoped != active:
            raise ProjectError(
                f"canonical mutation requires the active run controller broker: {active}"
            )
        yield


F = TypeVar("F", bound=Callable[..., Any])


def guarded_mutation(function: F) -> F:
    @wraps(function)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        with canonical_write_guard(self.root):
            return function(self, *args, **kwargs)
    return wrapped  # type: ignore[return-value]
