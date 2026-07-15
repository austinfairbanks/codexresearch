from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Sequence

from soleresearch.errors import ProjectError


GIT_TIMEOUT_SECONDS = 30


def _git(repository: Path, arguments: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectError(f"Git isolation timed out after {GIT_TIMEOUT_SECONDS} seconds") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Git error"
        raise ProjectError(f"Git isolation failed: {detail}")
    return completed.stdout.strip()


def disabled_git_policy() -> dict[str, Any]:
    return {
        "enabled": False,
        "repository": None,
        "baseline_revision": None,
        "branch": None,
        "worktree": None,
        "baseline_dirty": None,
    }


def _branch_absent(repository: Path, branch: str) -> None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            check=False, capture_output=True, timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectError(f"Git isolation timed out after {GIT_TIMEOUT_SECONDS} seconds") from exc
    if completed.returncode == 0:
        raise ProjectError(f"run branch already exists: {branch}")
    if completed.returncode not in {1}:
        raise ProjectError("cannot inspect proposed run branch")


def create_run_worktree(
    *,
    repository: Path,
    worktree_parent: Path,
    run_id: str,
    baseline_revision: str,
    allow_dirty_baseline: bool = False,
) -> dict[str, Any]:
    repository = repository.resolve()
    worktree_parent = worktree_parent.resolve()
    if not (repository / ".git").is_dir():
        raise ProjectError(f"Git isolation requires a repository: {repository}")
    if not baseline_revision.strip():
        raise ProjectError("Git isolation requires an explicit baseline revision")
    baseline = _git(repository, ["rev-parse", "--verify", f"{baseline_revision}^{{commit}}"])
    dirty = _git(repository, ["status", "--porcelain=v1", "--untracked-files=all"])
    if dirty and not allow_dirty_baseline:
        raise ProjectError("Git isolation refuses a dirty baseline without explicit allow_dirty_baseline")
    try:
        worktree_parent.relative_to(repository)
    except ValueError:
        pass
    else:
        raise ProjectError("run worktree parent must be outside the research repository")
    worktree_parent.mkdir(parents=True, exist_ok=True)
    worktree = worktree_parent / run_id
    if worktree.exists():
        raise ProjectError(f"run worktree path already exists: {worktree}")
    branch = f"soleresearch/run/{run_id}"
    _branch_absent(repository, branch)
    _git(repository, ["worktree", "add", "-b", branch, str(worktree), baseline])
    if not (worktree / ".git").is_file():
        try:
            _git(repository, ["worktree", "remove", "--force", str(worktree)])
            _git(repository, ["branch", "-D", branch])
        finally:
            raise ProjectError("Git did not create a valid linked-worktree .git file")
    return {
        "enabled": True,
        "repository": str(repository),
        "baseline_revision": baseline,
        "branch": branch,
        "worktree": str(worktree),
        "baseline_dirty": dirty or None,
    }


def checkpoint_run_worktree(
    git_policy: dict[str, Any],
    *,
    paths: Sequence[str],
    message: str,
    author_name: str = "Soleresearch",
    author_email: str = "soleresearch@localhost",
) -> str:
    if not git_policy.get("enabled"):
        raise ProjectError("Git checkpointing is disabled for this run")
    worktree = Path(str(git_policy["worktree"])).resolve()
    repository = Path(str(git_policy["repository"])).resolve()
    _verify_run_worktree(git_policy)
    if not paths:
        raise ProjectError("checkpoint requires at least one explicit relative path")
    normalized: list[str] = []
    for value in paths:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ProjectError(f"checkpoint path must be confined to the run worktree: {value}")
        candidate = worktree / path
        try:
            candidate.resolve().relative_to(worktree)
        except ValueError as exc:
            raise ProjectError(f"checkpoint path escapes the run worktree: {value}") from exc
        normalized.append(path.as_posix())
    pre_staged = _git(worktree, ["diff", "--cached", "--name-only"])
    if pre_staged:
        raise ProjectError("checkpoint refuses pre-staged paths: " + ", ".join(pre_staged.splitlines()))
    staged: list[str] = []
    try:
        _git(worktree, ["add", "--", *normalized])
        staged = _git(worktree, ["diff", "--cached", "--name-only"]).splitlines()
        if not staged:
            raise ProjectError("checkpoint has no staged changes")
        unexpected = sorted(set(staged) - set(normalized))
        if unexpected:
            raise ProjectError("checkpoint staged out-of-scope paths: " + ", ".join(unexpected))
        _verify_run_worktree(git_policy)
        _git(
            worktree,
            [
                "-c", f"user.name={author_name}",
                "-c", f"user.email={author_email}",
                "commit", "--no-gpg-sign", "-m", message,
            ],
        )
    except BaseException:
        try:
            cleanup = _git(worktree, ["diff", "--cached", "--name-only"]).splitlines()
            if cleanup:
                _git(worktree, ["reset", "--quiet", "HEAD", "--", *cleanup])
        except ProjectError:
            pass
        raise
    revision = _git(worktree, ["rev-parse", "HEAD"])
    branch = _git(worktree, ["branch", "--show-current"])
    if branch != git_policy["branch"]:
        raise ProjectError("run checkpoint escaped its dedicated branch")
    # A checkpoint is deliberately local. This module exposes no merge or push
    # operation and does not consult or mutate remotes.
    _git(repository, ["rev-parse", "--verify", revision])
    return revision


def _verify_run_worktree(git_policy: dict[str, Any]) -> None:
    repository = Path(str(git_policy["repository"])).resolve()
    worktree = Path(str(git_policy["worktree"])).resolve()
    if not (worktree / ".git").is_file():
        raise ProjectError("run worktree .git file is missing")
    if Path(_git(worktree, ["rev-parse", "--show-toplevel"])).resolve() != worktree:
        raise ProjectError("checkpoint worktree top-level does not match manifest")
    common_raw = Path(_git(worktree, ["rev-parse", "--git-common-dir"]))
    common = (worktree / common_raw).resolve() if not common_raw.is_absolute() else common_raw.resolve()
    if common != (repository / ".git").resolve():
        raise ProjectError("checkpoint Git common directory does not match manifest repository")
    if _git(worktree, ["branch", "--show-current"]) != git_policy["branch"]:
        raise ProjectError("checkpoint branch does not match run manifest")


def remove_run_worktree(git_policy: dict[str, Any]) -> bool:
    """Remove an orphan and verify both worktree registration and branch are gone."""
    if not git_policy.get("enabled"):
        return True
    repository = Path(str(git_policy["repository"])).resolve()
    worktree = Path(str(git_policy["worktree"])).resolve()
    branch = str(git_policy["branch"])
    try:
        _git(repository, ["worktree", "remove", "--force", str(worktree)])
    except ProjectError:
        pass
    try:
        _git(repository, ["worktree", "prune"])
        _git(repository, ["branch", "-D", branch])
    except ProjectError:
        pass
    registered = f"worktree {worktree}" in _git(repository, ["worktree", "list", "--porcelain"]).splitlines()
    branch_check = subprocess.run(
        ["git", "-C", str(repository), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False, capture_output=True, timeout=GIT_TIMEOUT_SECONDS,
    )
    return not worktree.exists() and not registered and branch_check.returncode == 1
