from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit, urlunsplit

from soleresearch.controller import require_controller
from soleresearch.errors import ProjectError, SchemaError
from soleresearch.evidence import _validate_locator
from soleresearch.evidence import EvidenceRepository
from soleresearch.git_isolation import (
    checkpoint_run_worktree,
    create_run_worktree,
    disabled_git_policy,
    remove_run_worktree,
)
from soleresearch.graph import GraphRepository
from soleresearch.project import load_project, utc_now
from soleresearch.schemas import SCHEMA_VERSION, schema_spec, validate_document
from soleresearch.sources import SourceRepository, load_extraction
from soleresearch.storage import (
    atomic_write_json,
    canonical_json,
    confined_project_path,
    read_json,
    read_jsonl,
)
from soleresearch.transactions import transactional_write
from soleresearch.writing import ProjectWriteLock, run_write_scope

DEFAULT_ENVELOPE = {
    "cycles": 3,
    "tasks": 10,
    "deep_sources": 15,
    "agents": 4,
    "max_depth": 1,
    "minutes": 90,
    "provider_usage": {"unit": "dollars", "ceiling": 5.0},
}
EXTENSION_KEYS = {"cycles", "tasks", "deep_sources", "minutes", "provider_usage"}
WORKER_CAPABILITIES = {"read_source", "web_search", "local_artifact_read", "request_nested_worker"}
REQUIRED_RESULT_OPERATIONS = {"propose_graph_diff", "exact_locator_evidence", "source_provenance", "outline_suggestions", "disagreements", "gaps"}
MAX_SELECTED_CONTEXT_BYTES = 65_536
MAX_WORKER_SOURCE_CHARACTERS = 60_000
MAX_WORKER_BUNDLE_BYTES = 131_072
MAX_WORKER_BUNDLE_SOURCES = 15
MAX_WORKER_BUNDLE_EVIDENCE = 64
GIT_INTENT = ".soleresearch/git-run-intent.json"
ProjectRunLock = ProjectWriteLock


def _opaque(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _fixed_now(now: Callable[[], str]) -> tuple[str, datetime]:
    value = now()
    try:
        parsed = _parse_time(value)
    except ValueError as exc:
        raise ProjectError(f"invalid orchestration clock value: {value}") from exc
    if parsed.tzinfo != UTC:
        raise ProjectError("orchestration clock must return UTC Z timestamps")
    return value, parsed


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _jsonl_bytes(records: Sequence[dict[str, Any]]) -> bytes:
    return ("" if not records else "\n".join(canonical_json(item) for item in records) + "\n").encode()


def _gate_status(
    state_status: str, *, has_pending_tasks: bool, has_pending_decisions: bool
) -> str:
    """Derive the gate from lifecycle state before considering pending work."""
    if state_status == "paused":
        return "paused"
    if state_status == "finished":
        return "finished"
    if has_pending_decisions:
        return "human_review"
    return "busy" if has_pending_tasks else "clean"


def _derived_node_id(diff_id: str, index: int) -> str:
    digest = hashlib.sha256(f"{diff_id}:{index}:node".encode("utf-8")).hexdigest()[:32]
    return f"nod_{digest}"


def _safe_provenance_url(value: str | None) -> str | None:
    """Remove query/fragment material and refuse credential-bearing authorities."""
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", "", ""))


def _reject_bundle_secrets(value: Any, *, forbidden_paths: Sequence[Path]) -> None:
    serialized = canonical_json(value)
    if "ctl_" in serialized or any(str(path) in serialized for path in forbidden_paths):
        raise ProjectError("worker bundle must not contain controller secrets or canonical paths")
    credential = re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|password|credential)\s*[:=]\s*[^\s,;}]+|bearer\s+[A-Za-z0-9._~+/-]+"
    )
    if credential.search(serialized):
        raise ProjectError("worker bundle contains an obvious credential-bearing value")


def _validate_task_result_contract(
    task: dict[str, Any], result: dict[str, Any], *, receipt_time: datetime
) -> None:
    """Validate task/result relationships that do not require canonical storage."""
    for field in ("run_id", "task_id", "worker_id", "base_revision"):
        if result[field] != task[field]:
            raise ProjectError(f"result {field} does not match immutable task packet")
    missing = sorted(set(task["required_result_operations"]) - set(result["completed_operations"]))
    if missing:
        raise ProjectError("result did not complete required operations: " + ", ".join(missing))
    if receipt_time > _parse_time(task["reservation"]["lease_expires_at"]):
        raise ProjectError("result receipt occurred after its immutable task lease")
    if _parse_time(result["completed_at"]) > receipt_time:
        raise ProjectError("result completed_at cannot be later than receipt time")
    if _parse_time(result["completed_at"]) < _parse_time(task["created_at"]):
        raise ProjectError("result completed_at cannot be earlier than task creation")
    deep_count = len({item["source_id"] for item in result["accessed_sources"] if item["deeply_processed"]})
    if deep_count > task["reservation"]["deep_sources"]:
        raise ProjectError("result exceeds its task deep-source reservation")
    if result["usage"]["amount"] > task["reservation"]["provider_usage"]:
        raise ProjectError("result exceeds its task provider-usage reservation")
    if result["usage"]["unit"] != task["inherited_remaining_budget"]["provider_usage"]["unit"]:
        raise ProjectError("result usage unit does not match the immutable task budget")


def _validate_result_scope_contract(
    task: dict[str, Any],
    result: dict[str, Any],
    *,
    source_records: dict[str, dict[str, Any]],
) -> None:
    """Validate capability, artifact, domain, and output scope against a task."""
    unknown_completed = sorted(set(result["completed_operations"]) - REQUIRED_RESULT_OPERATIONS)
    if unknown_completed:
        raise ProjectError("result completed unsupported operations: " + ", ".join(unknown_completed))
    if not set(result["used_capabilities"]) <= set(task["allowed_capabilities"]):
        raise ProjectError("result used capabilities outside its task allowlist")
    if not set(result["used_artifact_references"]) <= set(task["artifact_references"]):
        raise ProjectError("result used artifact references outside its task packet")
    allowed = {item.lower().rstrip(".") for item in task["allowed_domains"]}
    for accessed in result["accessed_sources"]:
        record = source_records.get(accessed["source_id"])
        if record is None:
            raise ProjectError("result accessed a source outside its worker bundle")
        artifact = f"source:{record['source_id']}"
        artifact_authorized = artifact in task["artifact_references"] and artifact in result["used_artifact_references"]
        if accessed["access_url"] is None:
            if not artifact_authorized:
                raise ProjectError("local accessed source lacks an explicitly used source artifact reference")
            continue
        parsed_url = urlsplit(accessed["access_url"])
        host = (parsed_url.hostname or "").lower().rstrip(".")
        if parsed_url.scheme not in {"http", "https"} or not host or not any(
            host == domain or host.endswith("." + domain) for domain in allowed
        ):
            raise ProjectError("result accessed a domain outside its task allowlist")
        provenance_urls = {
            value for value in record.get("provenance_urls", []) if value
        }
        safe_access_url = _safe_provenance_url(accessed["access_url"])
        if safe_access_url is None or safe_access_url not in provenance_urls:
            raise ProjectError("result access_url does not match immutable source URL provenance")
        web_authorized = "web_search" in task["allowed_capabilities"] and "web_search" in result["used_capabilities"]
        if not artifact_authorized and not web_authorized:
            raise ProjectError("web accessed source lacks artifact or explicit web capability authorization")
    fields = {
        "propose_graph_diff": "proposed_graph_operations", "exact_locator_evidence": "evidence",
        "source_provenance": "accessed_sources", "outline_suggestions": "outline_suggestions",
        "disagreements": "disagreements", "gaps": "gaps",
    }
    completed = set(result["completed_operations"])
    error_codes = {item["code"] for item in result["errors"]}
    for operation, field in fields.items():
        values = result[field]
        if values and operation not in completed:
            raise ProjectError(f"nonempty {field} lacks its completed operation")
        if operation in completed and not values and f"no_result:{operation}" not in error_codes:
            raise ProjectError(f"completed operation {operation} requires output or explicit no-result error")


def validate_result_with_bundle(
    result: dict[str, Any],
    bundle: dict[str, Any],
    *,
    receipt_time: str,
) -> None:
    """Run broker-equivalent task, scope, and bundled-provenance checks offline."""
    try:
        validate_document("result", result)
        validate_document("worker_bundle", bundle)
        validate_document("task", bundle["task"])
        validate_document("result", bundle["result_template"])
    except SchemaError as exc:
        raise ProjectError(str(exc)) from exc
    identity_payload = {**bundle, "bundle_id": ""}
    expected_bundle_id = "bun_" + hashlib.sha256(
        canonical_json(identity_payload).encode("utf-8")
    ).hexdigest()[:32]
    if bundle["bundle_id"] != expected_bundle_id:
        raise ProjectError("worker bundle integrity check failed")
    if len(_json_bytes(bundle)) > MAX_WORKER_BUNDLE_BYTES:
        raise ProjectError(f"worker bundle exceeds {MAX_WORKER_BUNDLE_BYTES} serialized bytes")
    if len(bundle["sources"]) > MAX_WORKER_BUNDLE_SOURCES:
        raise ProjectError(f"worker bundle exceeds {MAX_WORKER_BUNDLE_SOURCES} source materials")
    if len(bundle["evidence"]) > MAX_WORKER_BUNDLE_EVIDENCE:
        raise ProjectError(f"worker bundle exceeds {MAX_WORKER_BUNDLE_EVIDENCE} evidence materials")
    _reject_bundle_secrets(bundle, forbidden_paths=())
    task = bundle["task"]
    if bundle["result_schema"] != schema_spec("result"):
        raise ProjectError("worker bundle result schema is not the packaged contract")
    if bundle["lease_expires_at"] != task["reservation"]["lease_expires_at"]:
        raise ProjectError("worker bundle lease does not match immutable task packet")
    expected_source_refs = {
        value for value in task["artifact_references"] if value.startswith("source:")
    }
    bundled_source_refs = {item["artifact_reference"] for item in bundle["sources"]}
    expected_evidence_refs = {
        value for value in task["artifact_references"] if value.startswith("evidence:")
    }
    bundled_evidence_refs = {item["artifact_reference"] for item in bundle["evidence"]}
    if (
        bundled_source_refs != expected_source_refs
        or len(bundle["sources"]) != len(bundled_source_refs)
        or bundled_evidence_refs != expected_evidence_refs
        or len(bundle["evidence"]) != len(bundled_evidence_refs)
    ):
        raise ProjectError("worker bundle material does not exactly match task artifact references")
    for item in bundle["sources"]:
        if item["artifact_reference"] != f"source:{item['source_id']}":
            raise ProjectError("worker bundle source artifact is not canonical")
        extraction = item["extraction"]
        included = 0 if extraction is None else len(str(extraction.get("text", "")))
        if (
            included != item["included_characters"]
            or item["included_characters"] > item["original_characters"]
            or item["material_truncated"] != (item["included_characters"] < item["original_characters"])
        ):
            raise ProjectError("worker bundle source bounds metadata is inconsistent")
    for item in bundle["evidence"]:
        record = item["record"]
        expected_fields = {
            "evidence_id", "source_id", "source_hash", "source_version",
            "locator", "excerpt", "retrieved_at",
        }
        if set(record) != expected_fields:
            raise ProjectError("worker bundle evidence projection fields are invalid")
        if not all(isinstance(record[field], str) and record[field] for field in (
            "evidence_id", "source_id", "source_hash", "source_version", "excerpt", "retrieved_at"
        )) or not isinstance(record["locator"], dict):
            raise ProjectError("worker bundle evidence projection is malformed")
        if item["artifact_reference"] != f"evidence:{record['evidence_id']}":
            raise ProjectError("worker bundle evidence artifact is not canonical")
    try:
        parsed_receipt = _parse_time(receipt_time)
    except ValueError as exc:
        raise ProjectError(f"invalid trusted receipt time: {receipt_time}") from exc
    if parsed_receipt.tzinfo != UTC:
        raise ProjectError("trusted receipt time must be UTC")
    _validate_task_result_contract(task, result, receipt_time=parsed_receipt)
    source_material = {item["source_id"]: item for item in bundle["sources"]}
    _validate_result_scope_contract(task, result, source_records=source_material)
    accessed: set[tuple[str, str | None, str | None]] = set()
    for item in result["accessed_sources"]:
        material = source_material.get(item["source_id"])
        if material is None:
            raise ProjectError("result accessed a source outside its worker bundle")
        identity = (item["source_id"], item["source_hash"], item["source_version"])
        accessed.add(identity)
        if item["deeply_processed"]:
            if item["source_hash"] is None or item["source_version"] is None:
                raise ProjectError("deeply processed source requires immutable hash/version provenance")
            if (
                item["source_hash"] != material["source_hash"]
                or item["source_version"] != material["source_version"]
                or material["extraction"] is None
            ):
                raise ProjectError("result source provenance is not an assigned inspected immutable version")
    for evidence in result["evidence"]:
        identity = (evidence["source_id"], evidence["source_hash"], evidence["source_version"])
        if identity not in accessed:
            raise ProjectError("result evidence provenance is absent from accessed sources")
        material = source_material.get(evidence["source_id"])
        if material is None or material["extraction"] is None:
            raise ProjectError("result evidence source is absent from assigned inspected material")
        if (
            evidence["source_hash"] != material["source_hash"]
            or evidence["source_version"] != material["source_version"]
        ):
            raise ProjectError("result evidence provenance does not match assigned immutable material")
        exact = _validate_locator(
            {"source_id": material["source_id"]},
            material["extraction"],
            evidence["locator"],
            evidence["excerpt"],
        )
        identity_value = canonical_json({
            "source_hash": evidence["source_hash"],
            "source_version": evidence["source_version"],
            "locator": exact,
            "excerpt": evidence["excerpt"],
        })
        expected_evidence_id = "ev_" + hashlib.sha256(identity_value.encode()).hexdigest()[:24]
        if evidence["evidence_id"] != expected_evidence_id:
            raise ProjectError("result evidence_id is not canonical for its exact immutable passage")


def _validate_run_id(value: str) -> str:
    if re.fullmatch(r"run_[0-9a-f]{32}", value) is None:
        raise ProjectError("run_id must be an opaque run_ identity")
    return value


def _validate_selected_context(value: Any, *, project_root: Path) -> None:
    encoded = canonical_json(value).encode("utf-8")
    if len(encoded) > MAX_SELECTED_CONTEXT_BYTES:
        raise ProjectError(f"selected context exceeds {MAX_SELECTED_CONTEXT_BYTES} bytes")
    count = 0

    def visit(item: Any, depth: int = 0) -> None:
        nonlocal count
        count += 1
        if count > 2000 or depth > 12:
            raise ProjectError("selected context exceeds structural bounds")
        if isinstance(item, dict):
            for key, nested in item.items():
                lowered = str(key).lower()
                if any(part in lowered for part in ("token", "secret", "password", "credential", "capability")):
                    raise ProjectError("selected context must not contain secrets or capabilities")
                visit(nested, depth + 1)
        elif isinstance(item, list):
            for nested in item:
                visit(nested, depth + 1)
        elif isinstance(item, str):
            if item.startswith("ctl_") or item == str(project_root) or item.startswith(str(project_root) + "/"):
                raise ProjectError("selected context must not contain controller secrets or canonical paths")

    visit(value)


def _remove_empty_run_tree(path: Path) -> None:
    if not path.exists():
        return
    if any(item.is_file() or item.is_symlink() for item in path.rglob("*")):
        return
    shutil.rmtree(path)


def _recover_git_intent(root: Path) -> None:
    with ProjectWriteLock(root):
        path = confined_project_path(root, GIT_INTENT)
        if not path.exists():
            return
        value = read_json(path)
        required = {"schema_version", "run_id", "repository", "worktree", "branch", "baseline_revision"}
        if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 1:
            raise ProjectError("invalid durable Git run intent")
        run_id = _validate_run_id(value["run_id"])
        manifest_path = root / "runs" / run_id / "manifest.json"
        if manifest_path.exists():
            manifest = read_json(manifest_path)
            git = manifest.get("git", {}) if isinstance(manifest, dict) else {}
            if git.get("worktree") != value["worktree"] or git.get("branch") != value["branch"]:
                raise ProjectError("Git intent conflicts with committed run manifest")
        else:
            if not remove_run_worktree({"enabled": True, **value}):
                raise ProjectError("Git orphan cleanup could not be verified; durable intent retained")
            _remove_empty_run_tree(root / "runs" / run_id)
        path.unlink()


def _validate_envelope(value: dict[str, Any]) -> dict[str, Any]:
    expected = {"cycles", "tasks", "deep_sources", "agents", "max_depth", "minutes", "provider_usage"}
    if set(value) != expected:
        raise ProjectError("run envelope fields must be exactly: " + ", ".join(sorted(expected)))
    for field in ("cycles", "tasks", "deep_sources", "agents", "max_depth"):
        number = value[field]
        minimum = 0 if field == "max_depth" else 1
        if not isinstance(number, int) or isinstance(number, bool) or number < minimum:
            raise ProjectError(f"run envelope {field} must be an integer >= {minimum}")
    if value["agents"] > 4:
        raise ProjectError("v1 supports at most 4 concurrent agents including the orchestrator")
    if value["max_depth"] > 1:
        raise ProjectError("v1 permits only root worker to one nested worker level")
    minutes = value["minutes"]
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or minutes <= 0:
        raise ProjectError("run envelope minutes must be positive")
    usage = value["provider_usage"]
    if not isinstance(usage, dict) or set(usage) != {"unit", "ceiling"}:
        raise ProjectError("provider usage requires one explicit unit and ceiling")
    if usage["unit"] not in {"dollars", "tokens", "credits", "rate_limit"}:
        raise ProjectError("unsupported provider usage unit")
    if not isinstance(usage["ceiling"], (int, float)) or isinstance(usage["ceiling"], bool) or usage["ceiling"] <= 0:
        raise ProjectError("provider usage ceiling must be positive")
    return deepcopy(value)


class RunRepository:
    def __init__(self, project_path: Path, run_id: str) -> None:
        self.root = project_path.resolve()
        self.project = load_project(self.root)
        _recover_git_intent(self.root)
        self.run_id = _validate_run_id(run_id)
        self.run_dir = confined_project_path(self.root, Path("runs") / run_id)
        if not self.run_dir.is_dir():
            raise ProjectError(f"unknown research run: {run_id}")
        self.manifest = validate_document("run_manifest", read_json(self.run_dir / "manifest.json"))
        if self.manifest["run_id"] != run_id or self.manifest["project_id"] != self.project["project_id"]:
            raise ProjectError("run manifest identity does not match its project/path")
        self.canonical_root = (
            Path(self.manifest["git"]["worktree"]).resolve()
            if self.manifest["git"]["enabled"] else self.root
        )
        if self.manifest["git"]["enabled"]:
            if not (self.canonical_root / ".git").is_file():
                raise ProjectError("enabled Git run lacks a valid linked-worktree .git file")
            canonical_project = load_project(self.canonical_root)
            if canonical_project["project_id"] != self.project["project_id"]:
                raise ProjectError("Git worktree project identity does not match run manifest")
        self._validate_files()

    @classmethod
    def open_read_only(cls, project_path: Path, run_id: str) -> "RunRepository":
        """Validate a complete run without elapsed accounting or intent recovery."""
        repository = cls.__new__(cls)
        repository.root = project_path.resolve()
        repository.project = load_project(repository.root)
        repository.run_id = _validate_run_id(run_id)
        repository.run_dir = confined_project_path(repository.root, Path("runs") / run_id)
        if not repository.run_dir.is_dir():
            raise ProjectError(f"unknown research run: {run_id}")
        repository.manifest = validate_document(
            "run_manifest", read_json(repository._run_path("manifest.json"))
        )
        if repository.manifest["run_id"] != run_id or repository.manifest["project_id"] != repository.project["project_id"]:
            raise ProjectError("run manifest identity does not match its project/path")
        repository.canonical_root = (
            Path(repository.manifest["git"]["worktree"]).resolve()
            if repository.manifest["git"]["enabled"] else repository.root
        )
        if repository.manifest["git"]["enabled"]:
            if not (repository.canonical_root / ".git").is_file():
                raise ProjectError("enabled Git run lacks a valid linked-worktree .git file")
            canonical_project = load_project(repository.canonical_root)
            if canonical_project["project_id"] != repository.project["project_id"]:
                raise ProjectError("Git worktree project identity does not match run manifest")
        repository._validate_files()
        return repository

    def _run_path(self, relative: str | Path) -> Path:
        return confined_project_path(self.root, self.run_dir.relative_to(self.root) / Path(relative))

    def _run_glob(self, directory: str, pattern: str) -> list[Path]:
        root = self._run_path(directory)
        if not root.exists():
            return []
        if not root.is_dir():
            raise ProjectError(f"run ledger directory is not a directory: {root.relative_to(self.root)}")
        return [confined_project_path(self.root, path.relative_to(self.root)) for path in sorted(root.glob(pattern))]

    @classmethod
    def create(
        cls,
        project_path: Path,
        *,
        controller: str,
        envelope: dict[str, Any] | None = None,
        git_repository: Path | None = None,
        worktree_parent: Path | None = None,
        baseline_revision: str | None = None,
        allow_dirty_baseline: bool = False,
        now: Callable[[], str] = utc_now,
        run_id: str | None = None,
        after_replace: Callable[[int, str], None] | None = None,
    ) -> "RunRepository":
        root = project_path.resolve()
        project = load_project(root)
        _recover_git_intent(root)
        if controller not in {"human", "sol"}:
            raise ProjectError("run controller must be human or sol")
        git_triple = (git_repository, worktree_parent, baseline_revision)
        if allow_dirty_baseline and not all(value is not None for value in git_triple):
            raise ProjectError(
                "allow_dirty_baseline requires repository, worktree parent, and baseline revision together"
            )
        limits = _validate_envelope(envelope or DEFAULT_ENVELOPE)
        identity = _validate_run_id(run_id or _opaque("run"))
        instant, _ = _fixed_now(now)
        with ProjectRunLock(root):
            runs_dir = confined_project_path(root, "runs")
            runs_dir.mkdir(parents=True, exist_ok=True)
            for state_path in sorted(runs_dir.glob("run_*/state.json")):
                state = validate_document("run_state", read_json(state_path))
                if state["status"] != "finished":
                    raise ProjectError(f"project already has a writable run: {state['run_id']}")
            run_dir = confined_project_path(root, Path("runs") / identity)
            _remove_empty_run_tree(run_dir)
            if run_dir.exists():
                raise ProjectError(f"run already exists: {identity}")
            git = disabled_git_policy()
            if git_repository is not None or worktree_parent is not None or baseline_revision is not None:
                if git_repository is None or worktree_parent is None or baseline_revision is None:
                    raise ProjectError("Git isolation requires repository, worktree parent, and baseline revision together")
                if git_repository.resolve() != root:
                    raise ProjectError("Git isolation requires the research project root as repository")
                intent = {
                    "schema_version": 1, "run_id": identity, "repository": str(root),
                    "worktree": str((worktree_parent.resolve() / identity)),
                    "branch": f"soleresearch/run/{identity}", "baseline_revision": baseline_revision,
                }
                atomic_write_json(confined_project_path(root, GIT_INTENT), intent)
                git = create_run_worktree(
                    repository=git_repository,
                    worktree_parent=worktree_parent,
                    run_id=identity,
                    baseline_revision=baseline_revision,
                    allow_dirty_baseline=allow_dirty_baseline,
                )
                atomic_write_json(
                    Path(git["worktree"]) / ".soleresearch/run-binding.json",
                    {
                        "schema_version": 1, "project_id": project["project_id"], "run_id": identity,
                        "controller_root": str(root), "worktree": git["worktree"], "branch": git["branch"],
                    },
                )
            canonical_root = Path(git["worktree"]).resolve() if git["enabled"] else root
            if load_project(canonical_root)["project_id"] != project["project_id"]:
                if git["enabled"]:
                    remove_run_worktree(git)
                raise ProjectError("Git baseline must contain this research project")
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "run_id": identity,
                "project_id": project["project_id"],
                "controller": controller,
                "created_at": instant,
                "base_revision": GraphRepository(canonical_root).revision,
                "config": limits,
                "git": git,
            }
            state = {
                "schema_version": SCHEMA_VERSION, "run_id": identity, "status": "active",
                "pause_reason": None, "current_cycle": 1, "accepted_result_ids": [],
                "ratification_diff_ids": [], "updated_at": instant,
            }
            gate = {
                "schema_version": SCHEMA_VERSION, "run_id": identity, "status": "clean",
                "controller": controller, "pending_decisions": [], "updated_at": instant,
            }
            budget = {
                "schema_version": SCHEMA_VERSION, "run_id": identity, "limits": limits,
                "consumed": {"cycles": 1, "tasks": 0, "deep_sources": 0, "provider_usage": 0.0},
                "deep_source_ids": [], "accounted_result_ids": [], "active_since": instant,
                "elapsed_seconds": 0.0, "updated_at": instant,
            }
            updates: dict[str, bytes] = {}
            for kind, value, name in (
                ("run_manifest", manifest, "manifest.json"), ("run_state", state, "state.json"),
                ("run_gate", gate, "gate.json"), ("run_budget", budget, "budget.json"),
            ):
                validate_document(kind, value)
                updates[f"runs/{identity}/{name}"] = _json_bytes(value)
            event = {
                "schema_version": 1, "event_id": _opaque("evt"), "run_id": identity,
                "sequence": 1, "event_type": "run_created", "actor": controller,
                "reason": None, "data": {"base_revision": manifest["base_revision"]}, "created_at": instant,
            }
            validate_document("run_event", event)
            updates[f"runs/{identity}/events.jsonl"] = _jsonl_bytes([event])
            updates[f"runs/{identity}/edit-queue.jsonl"] = b""
            updates[f"runs/{identity}/extensions.jsonl"] = b""
            try:
                transactional_write(root, updates, after_replace=after_replace)
            except BaseException:
                if git["enabled"]:
                    cleaned = remove_run_worktree(git)
                else:
                    cleaned = True
                if cleaned:
                    confined_project_path(root, GIT_INTENT).unlink(missing_ok=True)
                _remove_empty_run_tree(run_dir)
                raise
            confined_project_path(root, GIT_INTENT).unlink(missing_ok=True)
            return cls(root, identity)

    def _validate_files(self) -> None:
        state = validate_document("run_state", read_json(self._run_path("state.json")))
        gate = validate_document("run_gate", read_json(self._run_path("gate.json")))
        budget = validate_document("run_budget", read_json(self._run_path("budget.json")))
        for label, value in (("state", state), ("gate", gate), ("budget", budget)):
            if value["run_id"] != self.run_id:
                raise ProjectError(f"run {label} identity does not match manifest")
        for path in self._run_glob("tasks/v1", "*.json"):
            task = validate_document("task", read_json(path))
            if task["run_id"] != self.run_id or task["task_id"] != path.stem:
                raise ProjectError("task ledger identity does not match run/path")
        for path in self._run_glob("results/v1", "*.json"):
            result = validate_document("result", read_json(path))
            if result["run_id"] != self.run_id or result["task_id"] != path.stem:
                raise ProjectError("result ledger identity does not match run/path")
        for path in self._run_glob("workers", "*.json"):
            worker = validate_document("worker", read_json(path))
            if worker["run_id"] != self.run_id or worker["worker_id"] != path.stem:
                raise ProjectError("worker ledger identity does not match run/path")
        tasks = [read_json(path) for path in self._run_glob("tasks/v1", "*.json")]
        workers = {path.stem: read_json(path) for path in self._run_glob("workers", "*.json")}
        task_ids = {item["task_id"] for item in tasks}
        task_depths = {item["task_id"]: item["depth"] for item in tasks}
        worker_ids = [item["worker_id"] for item in tasks]
        if len(set(worker_ids)) != len(worker_ids):
            raise ProjectError("worker_id must be immutable and unique per task")
        if set(workers) != set(worker_ids):
            raise ProjectError("worker ledger contains missing or orphan identities")
        for task in tasks:
            if task["worker_id"] not in workers:
                raise ProjectError("task references missing worker record")
            worker = workers[task["worker_id"]]
            if any(worker[field] != task[field] for field in ("run_id", "worker_id", "role", "depth", "parent_task_id")):
                raise ProjectError("task/worker relational identity mismatch")
            if task["parent_task_id"] is not None and task["parent_task_id"] not in task_ids:
                raise ProjectError("task references unknown parent task")
            if task["requeue_of_task_id"] is not None and task["requeue_of_task_id"] not in task_ids:
                raise ProjectError("task requeue lineage references unknown task")
            if task["parent_task_id"] is None and task["depth"] != 0:
                raise ProjectError("root worker task depth must be zero")
            if task["parent_task_id"] is not None and task["depth"] != task_depths[task["parent_task_id"]] + 1:
                raise ProjectError("child task depth must equal parent depth plus one")
        results = [read_json(path) for path in self._run_glob("results/v1", "*.json")]
        task_map = {item["task_id"]: item for item in tasks}
        for result in results:
            task = task_map.get(result["task_id"])
            if task is None or result["worker_id"] != task["worker_id"] or result["base_revision"] != task["base_revision"]:
                raise ProjectError("result/task relational identity mismatch")
            if not set(task["required_result_operations"]) <= set(result["completed_operations"]):
                raise ProjectError("result/task required-operation ledger mismatch")
            self._validate_result_provenance(result)
        events = read_jsonl(self._run_path("events.jsonl"))
        for sequence, item in enumerate(events, start=1):
            validate_document("run_event", item)
            if item["run_id"] != self.run_id or item["sequence"] != sequence:
                raise ProjectError("run event identity/sequence mismatch")
        extensions = read_jsonl(self._run_path("extensions.jsonl"))
        for item in extensions:
            validate_document("budget_extension", item)
            if item["run_id"] != self.run_id:
                raise ProjectError("budget extension identity mismatch")
        result_ids = {
            "res_" + hashlib.sha256(canonical_json(item).encode()).hexdigest()[:32]
            for item in results
        }
        if not set(state["accepted_result_ids"]) <= set(budget["accounted_result_ids"]) <= result_ids:
            raise ProjectError("run result accounting ledger is relationally inconsistent")
        result_by_id = {
            "res_" + hashlib.sha256(canonical_json(item).encode()).hexdigest()[:32]: item
            for item in results
        }
        accounted_results = [result_by_id[identity] for identity in budget["accounted_result_ids"]]
        expected_sources = sorted({
            source["source_id"] for result in accounted_results for source in result["accessed_sources"]
            if source["deeply_processed"]
        })
        expected_usage = sum(result["usage"]["amount"] for result in accounted_results)
        if budget["deep_source_ids"] != expected_sources or budget["consumed"]["provider_usage"] != expected_usage:
            raise ProjectError("result provenance and budget consumption are inconsistent")
        if budget["consumed"]["deep_sources"] != len(set(budget["deep_source_ids"])):
            raise ProjectError("deep source accounting is inconsistent")
        if budget["consumed"]["tasks"] != len(tasks):
            raise ProjectError("task budget accounting is inconsistent")
        if state["current_cycle"] != budget["consumed"]["cycles"]:
            raise ProjectError("cycle state and budget accounting are inconsistent")
        expected_limits = deepcopy(self.manifest["config"])
        cumulative = {key: 0.0 for key in EXTENSION_KEYS}
        for extension in extensions:
            for key, value in extension["delta"].items():
                cumulative[key] += float(value)
        for key, total in cumulative.items():
            ceiling = self.manifest["config"]["provider_usage"]["ceiling"] if key == "provider_usage" else self.manifest["config"][key]
            if total > ceiling:
                raise ProjectError("cumulative extension ledger exceeds its bounded initial envelope")
            if key == "provider_usage":
                expected_limits[key]["ceiling"] += total
            else:
                expected_limits[key] += int(total) if key in {"cycles", "tasks", "deep_sources"} else total
        if budget["limits"] != expected_limits:
            raise ProjectError("budget limits do not reconcile with immutable manifest and extensions")
        if gate["controller"] not in {"human", "sol"}:
            raise ProjectError("invalid current run controller")
        pending_task_ids = {item["task_id"] for item in self._pending_tasks()}
        expected_gate = _gate_status(
            state["status"],
            has_pending_tasks=bool(pending_task_ids),
            has_pending_decisions=bool(gate["pending_decisions"]),
        )
        if gate["status"] != expected_gate:
            raise ProjectError("run state and gate lifecycle are inconsistent")
        graph_diffs = {item["diff_id"]: item for item in GraphRepository(self.canonical_root).diffs()}
        for decision in gate["pending_decisions"]:
            if decision.get("result_id") not in state["accepted_result_ids"]:
                raise ProjectError("pending decision references an unaccepted result")
            diff = graph_diffs.get(decision.get("diff_id"))
            if diff is None or diff["status"] not in {"proposed", "stale"}:
                raise ProjectError("pending decision references a non-reviewable graph diff")
        for diff_id in state["ratification_diff_ids"]:
            diff = graph_diffs.get(diff_id)
            if diff is None or diff["status"] != "applied" or diff["applied_authority"] != "agent_accepted":
                raise ProjectError("ratification ledger references a non-agent-applied diff")

    def _state(self) -> dict[str, Any]:
        return validate_document("run_state", read_json(self.run_dir / "state.json"))

    def _gate(self) -> dict[str, Any]:
        return validate_document("run_gate", read_json(self.run_dir / "gate.json"))

    def _budget(self) -> dict[str, Any]:
        return validate_document("run_budget", read_json(self.run_dir / "budget.json"))

    def _event_record(self, event_type: str, actor: str, reason: str | None, data: dict[str, Any], *, now: Callable[[], str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        records = read_jsonl(self.run_dir / "events.jsonl")
        record = {
            "schema_version": SCHEMA_VERSION, "event_id": _opaque("evt"), "run_id": self.run_id,
            "sequence": len(records) + 1, "event_type": event_type, "actor": actor,
            "reason": reason, "data": data, "created_at": now(),
        }
        validate_document("run_event", record)
        records.append(record)
        return record, records

    def _commit(
        self,
        updates: dict[str, bytes],
        *,
        event_type: str,
        actor: str,
        reason: str | None,
        data: dict[str, Any],
        now: Callable[[], str],
        after_replace: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        event, events = self._event_record(event_type, actor, reason, data, now=now)
        payload = dict(updates)
        payload[f"runs/{self.run_id}/events.jsonl"] = _jsonl_bytes(events)
        transactional_write(self.root, payload, after_replace=after_replace)
        return event

    def _authorize(self, supplied_token: str | None, actor: str | None = None) -> str:
        gate = self._gate()
        expected_actor = gate["controller"] if actor is None else actor
        capability = require_controller(self.project["project_id"], supplied_token)
        expected_authority = "human_accepted" if expected_actor == "human" else "agent_accepted"
        if capability.authority != expected_authority:
            raise ProjectError(f"{expected_actor} controller capability required")
        if actor is not None and actor != gate["controller"]:
            raise ProjectError("only the active run controller may perform this action")
        return capability.subject

    def _update_elapsed(self, budget: dict[str, Any], instant: str, parsed: datetime) -> None:
        if budget["active_since"] is not None:
            elapsed = (parsed - _parse_time(budget["active_since"])).total_seconds()
            if elapsed < 0:
                raise ProjectError("orchestration clock moved backwards")
            budget["elapsed_seconds"] += elapsed
            budget["active_since"] = instant
        budget["updated_at"] = instant

    def _pending_tasks(self) -> list[dict[str, Any]]:
        result_ids = {path.stem for path in self._run_glob("results/v1", "*.json")}
        terminal: set[str] = set()
        for event in read_jsonl(self._run_path("events.jsonl")):
            if event["event_type"] in {"task_lease_expired", "task_cancelled", "task_requeued"} and isinstance(event["data"].get("task_id"), str):
                terminal.add(event["data"]["task_id"])
        tasks = [read_json(path) for path in self._run_glob("tasks/v1", "*.json")]
        terminal.update(item["requeue_of_task_id"] for item in tasks if item.get("requeue_of_task_id"))
        return [item for item in tasks if item["task_id"] not in result_ids and item["task_id"] not in terminal]

    def _task_terminal_reason(self, task_id: str) -> str | None:
        for task_path in (self.run_dir / "tasks/v1").glob("*.json"):
            if read_json(task_path).get("requeue_of_task_id") == task_id:
                return "superseded by requeue"
        reason = None
        for event in read_jsonl(self.run_dir / "events.jsonl"):
            if event["data"].get("task_id") != task_id:
                continue
            if event["event_type"] == "task_lease_expired":
                reason = "expired"
            elif event["event_type"] == "task_cancelled":
                reason = "cancelled"
            elif event["event_type"] == "task_requeued":
                reason = "superseded by requeue"
        return reason

    def _validate_result_provenance(self, result: dict[str, Any]) -> None:
        sources = SourceRepository(self.canonical_root)
        accessed: set[tuple[str, str | None, str | None]] = set()
        for item in result["accessed_sources"]:
            source = sources.get(item["source_id"])
            if item["source_id"] != source["source_id"]:
                raise ProjectError("result accessed_sources must use canonical source IDs, not aliases")
            identity = (source["source_id"], item["source_hash"], item["source_version"])
            accessed.add(identity)
            if item["deeply_processed"]:
                if item["source_hash"] is None or item["source_version"] is None:
                    raise ProjectError("deeply processed source requires immutable hash/version provenance")
                if not any(
                    version["content_hash"] == item["source_hash"] and version["source_version"] == item["source_version"]
                    for version in source["versions"]
                ):
                    raise ProjectError("result source provenance is not an inspected immutable version")
        for evidence in result["evidence"]:
            identity = (evidence["source_id"], evidence["source_hash"], evidence["source_version"])
            if identity not in accessed:
                raise ProjectError("result evidence provenance is absent from accessed sources")
            locator = evidence["locator"]
            if not isinstance(locator, dict) or locator.get("type") not in {"page", "section", "figure", "table", "paragraph", "timestamp", "captured_passage"}:
                raise ProjectError("result evidence requires an exact locator")
            source = sources.get(evidence["source_id"])
            if evidence["source_id"] != source["source_id"]:
                raise ProjectError("result evidence must use canonical source IDs, not aliases")
            extraction = load_extraction(
                self.canonical_root, evidence["source_id"], evidence["source_hash"], evidence["source_version"]
            )
            exact = _validate_locator(source, extraction, locator, evidence["excerpt"])
            identity_payload = canonical_json({
                "source_hash": evidence["source_hash"], "source_version": evidence["source_version"],
                "locator": exact, "excerpt": evidence["excerpt"],
            })
            expected_id = "ev_" + hashlib.sha256(identity_payload.encode()).hexdigest()[:24]
            if evidence["evidence_id"] != expected_id:
                raise ProjectError("result evidence_id is not canonical for its exact immutable passage")

    def _validate_result_scope(self, task: dict[str, Any], result: dict[str, Any]) -> None:
        records: dict[str, dict[str, Any]] = {}
        repository = SourceRepository(self.canonical_root)
        for item in result["accessed_sources"]:
            source = repository.get(item["source_id"])
            records[source["source_id"]] = {
                "source_id": source["source_id"],
                "provenance_urls": [
                    value for value in (
                        _safe_provenance_url(source.get("canonical_url")),
                        _safe_provenance_url(source.get("retrieval", {}).get("final_url")),
                        *(_safe_provenance_url(item) for item in source.get("url_aliases", [])),
                    ) if value is not None
                ],
            }
        _validate_result_scope_contract(task, result, source_records=records)

    def worker_bundle(
        self,
        task_id: str,
        *,
        controller_token: str | None,
        now: Callable[[], str] = utc_now,
    ) -> dict[str, Any]:
        """Build one integrity-bound, secret-free context packet for a worker."""
        with ProjectRunLock(self.root):
            self._authorize(controller_token)
            instant, parsed = _fixed_now(now)
            task_path = self._run_path(Path("tasks/v1") / f"{task_id}.json")
            if not task_path.is_file():
                raise ProjectError(f"unknown task for worker bundle: {task_id}")
            task = validate_document("task", read_json(task_path))
            if task_id not in {item["task_id"] for item in self._pending_tasks()}:
                raise ProjectError("worker bundle is available only for a pending task")
            if parsed > _parse_time(task["reservation"]["lease_expires_at"]):
                raise ProjectError("worker bundle task lease has expired")
            source_repository = SourceRepository(self.canonical_root)
            evidence_by_id = {
                item["evidence_id"]: item for item in EvidenceRepository(self.canonical_root).all()
            }
            source_references = [
                value for value in task["artifact_references"] if value.startswith("source:")
            ]
            evidence_references = [
                value for value in task["artifact_references"] if value.startswith("evidence:")
            ]
            if len(source_references) > MAX_WORKER_BUNDLE_SOURCES:
                raise ProjectError(f"worker bundle exceeds {MAX_WORKER_BUNDLE_SOURCES} source materials")
            if len(evidence_references) > MAX_WORKER_BUNDLE_EVIDENCE:
                raise ProjectError(f"worker bundle exceeds {MAX_WORKER_BUNDLE_EVIDENCE} evidence materials")
            per_source_limit = max(
                1, MAX_WORKER_SOURCE_CHARACTERS // max(1, len(source_references))
            )
            sources: list[dict[str, Any]] = []
            evidence: list[dict[str, Any]] = []
            for reference in task["artifact_references"]:
                if reference.startswith("source:"):
                    source = source_repository.get(reference.removeprefix("source:"))
                    if reference != f"source:{source['source_id']}":
                        raise ProjectError("worker bundles require canonical source artifact references")
                    extraction = None
                    original_characters = 0
                    included_characters = 0
                    truncated = False
                    if source["content_hash"] is not None:
                        canonical_extraction = load_extraction(
                            self.canonical_root,
                            source["source_id"],
                            source["content_hash"],
                            source["source_version"],
                        )
                        text = str(canonical_extraction.get("text", ""))
                        original_characters = len(text)
                        included_text = text[:per_source_limit]
                        included_characters = len(included_text)
                        truncated = included_characters < original_characters
                        extraction = {
                            "format": "bundle_excerpt",
                            "text": included_text,
                            "pages": [], "sections": [], "paragraphs": [],
                            "artifacts": [], "timeline": [],
                        }
                    sources.append({
                        "artifact_reference": reference,
                        "source_id": source["source_id"],
                        "title": (
                            _safe_provenance_url(source["title"]) or ""
                            if urlsplit(source["title"]).scheme in {"http", "https"}
                            else source["title"]
                        ),
                        "authors": source["authors"],
                        "published": source["published"],
                        "source_type": source["source_type"],
                        "source_hash": source["content_hash"],
                        "source_version": source["source_version"],
                        "provenance_urls": sorted({
                            value for value in (
                                _safe_provenance_url(source.get("canonical_url")),
                                _safe_provenance_url(source.get("retrieval", {}).get("final_url")),
                                *(_safe_provenance_url(item) for item in source.get("url_aliases", [])),
                            ) if value is not None
                        }),
                        "quality": source["quality"],
                        "data_policy": source["data_policy"],
                        "extraction": extraction,
                        "material_truncated": truncated,
                        "original_characters": original_characters,
                        "included_characters": included_characters,
                    })
                elif reference.startswith("evidence:"):
                    identity = reference.removeprefix("evidence:")
                    record = evidence_by_id.get(identity)
                    if record is None:
                        raise ProjectError(f"task references unknown evidence artifact: {identity}")
                    evidence.append({
                        "artifact_reference": reference,
                        "record": {
                            key: deepcopy(record[key]) for key in (
                                "evidence_id", "source_id", "source_hash", "source_version",
                                "locator", "excerpt", "retrieved_at",
                            )
                        },
                    })
            required_operations = sorted(task["required_result_operations"])
            template = {
                "schema_version": SCHEMA_VERSION,
                "run_id": task["run_id"], "task_id": task["task_id"],
                "worker_id": task["worker_id"], "base_revision": task["base_revision"],
                "used_capabilities": [], "used_artifact_references": [],
                "accessed_sources": [], "evidence": [], "proposed_graph_operations": [],
                "outline_suggestions": [], "disagreements": [], "gaps": [],
                "rationale": "", "usage": {
                    "unit": task["inherited_remaining_budget"]["provider_usage"]["unit"],
                    "amount": 0,
                },
                "completed_operations": required_operations,
                "errors": [
                    {"code": f"no_result:{operation}", "message": "No result recorded yet."}
                    for operation in required_operations
                ],
                "completed_at": instant,
            }
            payload = {
                "schema_version": SCHEMA_VERSION,
                "bundle_id": "",
                "broker_created_at": instant,
                "lease_expires_at": task["reservation"]["lease_expires_at"],
                "task": task,
                "sources": sorted(sources, key=lambda item: item["artifact_reference"]),
                "evidence": sorted(evidence, key=lambda item: item["artifact_reference"]),
                "result_schema": schema_spec("result"),
                "result_template": template,
            }
            identity_payload = {**payload, "bundle_id": ""}
            payload["bundle_id"] = "bun_" + hashlib.sha256(
                canonical_json(identity_payload).encode("utf-8")
            ).hexdigest()[:32]
            validate_document("worker_bundle", payload)
            _reject_bundle_secrets(
                payload, forbidden_paths=tuple({self.root, self.canonical_root})
            )
            bundle_bytes = _json_bytes(payload)
            if len(bundle_bytes) > MAX_WORKER_BUNDLE_BYTES:
                raise ProjectError(f"worker bundle exceeds {MAX_WORKER_BUNDLE_BYTES} serialized bytes")
            return payload

    def _remaining(self, budget: dict[str, Any]) -> dict[str, Any]:
        return {
            "cycles": max(0, budget["limits"]["cycles"] - budget["consumed"]["cycles"]),
            "tasks": max(0, budget["limits"]["tasks"] - budget["consumed"]["tasks"]),
            "deep_sources": max(0, budget["limits"]["deep_sources"] - budget["consumed"]["deep_sources"]),
            "agents": max(0, budget["limits"]["agents"] - 1 - len(self._pending_tasks())),
            "max_depth": budget["limits"]["max_depth"],
            "minutes": max(0.0, budget["limits"]["minutes"] - budget["elapsed_seconds"] / 60.0),
            "provider_usage": {
                "unit": budget["limits"]["provider_usage"]["unit"],
                "remaining": max(0.0, budget["limits"]["provider_usage"]["ceiling"] - budget["consumed"]["provider_usage"]),
            },
        }

    def _hard_reason(self, budget: dict[str, Any], *, include_task_cap: bool = True) -> str | None:
        if budget["elapsed_seconds"] / 60.0 >= budget["limits"]["minutes"]:
            return "hard_cap:minutes"
        if include_task_cap and budget["consumed"]["tasks"] >= budget["limits"]["tasks"] and not self._pending_tasks():
            return "hard_cap:tasks"
        if budget["consumed"]["deep_sources"] >= budget["limits"]["deep_sources"]:
            return "hard_cap:deep_sources"
        if budget["consumed"]["provider_usage"] >= budget["limits"]["provider_usage"]["ceiling"]:
            return "hard_cap:provider_usage"
        return None

    def _pause_updates(self, state: dict[str, Any], gate: dict[str, Any], budget: dict[str, Any], reason: str, instant: str) -> dict[str, bytes]:
        state.update({"status": "paused", "pause_reason": reason, "updated_at": instant})
        gate.update({
            "status": _gate_status(
                state["status"],
                has_pending_tasks=bool(self._pending_tasks()),
                has_pending_decisions=bool(gate["pending_decisions"]),
            ),
            "updated_at": instant,
        })
        budget["active_since"] = None
        updates: dict[str, bytes] = {}
        for kind, value, name in (("run_state", state, "state.json"), ("run_gate", gate, "gate.json"), ("run_budget", budget, "budget.json")):
            validate_document(kind, value)
            updates[f"runs/{self.run_id}/{name}"] = _json_bytes(value)
        return updates

    def status(self, *, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        with ProjectRunLock(self.root):
            instant, parsed = _fixed_now(now)
            state, gate, budget = self._state(), self._gate(), self._budget()
            self._update_elapsed(budget, instant, parsed)
            if state["status"] == "active":
                reason = self._hard_reason(budget)
                if reason:
                    self._commit(self._pause_updates(state, gate, budget, reason, instant), event_type="run_paused", actor="system", reason=reason, data={}, now=now)
                else:
                    transactional_write(self.root, {f"runs/{self.run_id}/budget.json": _json_bytes(budget)})
            return {
                "schema_version": SCHEMA_VERSION, "run_id": self.run_id, "state": state,
                "gate": gate, "budget": budget, "remaining": self._remaining(budget),
                "pending_tasks": [item["task_id"] for item in self._pending_tasks()],
                "git": self.manifest["git"],
            }

    def dispatch(
        self,
        *,
        role: str,
        subquestion: str,
        evidence_strategy: str,
        selected_context: dict[str, Any],
        artifact_references: Sequence[str] = (),
        allowed_capabilities: Sequence[str] = (),
        allowed_domains: Sequence[str] = (),
        required_result_operations: Sequence[str] = ("propose_graph_diff",),
        parent_task_id: str | None = None,
        controller_token: str | None,
        task_id: str | None = None,
        worker_id: str | None = None,
        reserve_deep_sources: int = 0,
        reserve_provider_usage: float = 0.0,
        lease_minutes: float = 30.0,
        now: Callable[[], str] = utc_now,
        after_replace: Callable[[int, str], None] | None = None,
        requeue_of_task_id: str | None = None,
    ) -> dict[str, Any]:
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            instant, parsed = _fixed_now(now)
            state, gate, budget = self._state(), self._gate(), self._budget()
            if state["status"] != "active":
                raise ProjectError(f"run is not active: {state['status']} ({state['pause_reason']})")
            self._update_elapsed(budget, instant, parsed)
            reason = self._hard_reason(budget)
            if reason:
                self._commit(self._pause_updates(state, gate, budget, reason, instant), event_type="run_paused", actor="system", reason=reason, data={}, now=now)
                raise ProjectError(f"dispatch blocked by {reason}")
            if budget["consumed"]["tasks"] >= budget["limits"]["tasks"]:
                reason = "hard_cap:tasks"
                self._commit(self._pause_updates(state, gate, budget, reason, instant), event_type="run_paused", actor="system", reason=reason, data={}, now=now)
                raise ProjectError("dispatch blocked by hard_cap:tasks")
            pending = self._pending_tasks()
            if len(pending) >= budget["limits"]["agents"] - 1:
                raise ProjectError("dispatch would exceed concurrent agents cap (orchestrator counts as one)")
            depth = 0
            if parent_task_id is not None:
                parent_path = self.run_dir / "tasks/v1" / f"{parent_task_id}.json"
                if not parent_path.is_file():
                    raise ProjectError(f"unknown parent task: {parent_task_id}")
                parent = validate_document("task", read_json(parent_path))
                depth = parent["depth"] + 1
            if depth > budget["limits"]["max_depth"]:
                raise ProjectError("worker nesting exceeds shared run depth cap")
            if not role.strip() or not subquestion.strip() or not evidence_strategy.strip():
                raise ProjectError("worker role, subquestion, and evidence strategy must be non-empty")
            capabilities = sorted(set(allowed_capabilities))
            unknown_capabilities = sorted(set(capabilities) - WORKER_CAPABILITIES)
            if unknown_capabilities:
                raise ProjectError("worker capability is not allowlisted: " + ", ".join(unknown_capabilities))
            references = sorted(set(artifact_references))
            if any(Path(value).is_absolute() or ".." in Path(value).parts for value in references):
                raise ProjectError("workers receive only confined logical artifact references, never canonical writer paths")
            identity = task_id or _opaque("tsk")
            worker_identity = worker_id or _opaque("wrk")
            _validate_selected_context(selected_context, project_root=self.root)
            if self.canonical_root != self.root:
                _validate_selected_context(selected_context, project_root=self.canonical_root)
            if (self.run_dir / "tasks/v1" / f"{identity}.json").exists():
                raise ProjectError(f"task already exists: {identity}")
            if (self.run_dir / "workers" / f"{worker_identity}.json").exists():
                raise ProjectError(f"worker_id already exists and is immutable: {worker_identity}")
            if requeue_of_task_id is not None:
                prior_path = self.run_dir / "tasks/v1" / f"{requeue_of_task_id}.json"
                if not prior_path.is_file() or requeue_of_task_id == identity:
                    raise ProjectError("requeue lineage must reference a different existing task")
            required = sorted(set(required_result_operations))
            unknown_required = sorted(set(required) - REQUIRED_RESULT_OPERATIONS)
            if unknown_required:
                raise ProjectError("required result operation is not supported: " + ", ".join(unknown_required))
            if (
                not isinstance(reserve_deep_sources, int) or isinstance(reserve_deep_sources, bool) or reserve_deep_sources < 0
                or not isinstance(reserve_provider_usage, (int, float)) or isinstance(reserve_provider_usage, bool) or reserve_provider_usage < 0
                or not isinstance(lease_minutes, (int, float)) or isinstance(lease_minutes, bool) or lease_minutes <= 0 or lease_minutes > 90
            ):
                raise ProjectError("task reservation and lease values are out of bounds")
            pending_reservations = [item["reservation"] for item in pending]
            reserved_sources = sum(item["deep_sources"] for item in pending_reservations)
            reserved_usage = sum(item["provider_usage"] for item in pending_reservations)
            remaining = self._remaining(budget)
            if reserve_deep_sources > remaining["deep_sources"] - reserved_sources:
                raise ProjectError("task deep-source reservation exceeds shared remaining budget")
            if reserve_provider_usage > remaining["provider_usage"]["remaining"] - reserved_usage:
                raise ProjectError("task provider reservation exceeds shared remaining budget")
            worker = {
                "schema_version": SCHEMA_VERSION, "run_id": self.run_id, "worker_id": worker_identity,
                "role": role.strip(), "depth": depth, "parent_task_id": parent_task_id,
                "capabilities": capabilities, "created_at": instant,
            }
            task = {
                "schema_version": SCHEMA_VERSION, "run_id": self.run_id, "task_id": identity,
                "parent_task_id": parent_task_id, "requeue_of_task_id": requeue_of_task_id,
                "worker_id": worker_identity, "role": role.strip(),
                "depth": depth, "base_revision": GraphRepository(self.canonical_root).revision,
                "cycle": state["current_cycle"], "subquestion": subquestion.strip(),
                "evidence_strategy": evidence_strategy.strip(), "allowed_capabilities": capabilities,
                "allowed_domains": sorted(set(allowed_domains)), "artifact_references": references,
                "selected_context": deepcopy(selected_context), "inherited_remaining_budget": remaining,
                "reservation": {
                    "deep_sources": reserve_deep_sources,
                    "provider_usage": reserve_provider_usage,
                    "lease_expires_at": (parsed + timedelta(minutes=float(lease_minutes))).isoformat(timespec="seconds").replace("+00:00", "Z"),
                },
                "required_result_operations": required, "created_at": instant,
            }
            validate_document("worker", worker)
            validate_document("task", task)
            budget["consumed"]["tasks"] += 1
            budget["updated_at"] = instant
            gate.update({
                "status": _gate_status(
                    state["status"], has_pending_tasks=True,
                    has_pending_decisions=bool(gate["pending_decisions"]),
                ),
                "updated_at": instant,
            })
            self._commit(
                {
                    f"runs/{self.run_id}/workers/{worker_identity}.json": _json_bytes(worker),
                    f"runs/{self.run_id}/tasks/v1/{identity}.json": _json_bytes(task),
                    f"runs/{self.run_id}/budget.json": _json_bytes(budget),
                    f"runs/{self.run_id}/gate.json": _json_bytes(gate),
                },
                event_type="task_dispatched", actor=actor, reason=None,
                data={"task_id": identity, "worker_id": worker_identity, "depth": depth, "requeue_of_task_id": requeue_of_task_id}, now=now,
                after_replace=after_replace,
            )
            return task

    def expire_leases(self, *, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            instant, parsed = _fixed_now(now)
            expired: list[str] = []
            for task in list(self._pending_tasks()):
                if parsed < _parse_time(task["reservation"]["lease_expires_at"]):
                    continue
                expired.append(task["task_id"])
                gate = self._gate()
                remaining = [item for item in self._pending_tasks() if item["task_id"] != task["task_id"]]
                state = self._state()
                gate.update({
                    "status": _gate_status(
                        state["status"], has_pending_tasks=bool(remaining),
                        has_pending_decisions=bool(gate["pending_decisions"]),
                    ),
                    "updated_at": instant,
                })
                updates = {f"runs/{self.run_id}/gate.json": _json_bytes(gate)}
                self._commit(updates, event_type="task_lease_expired", actor=actor, reason="lease expired", data={"task_id": task["task_id"]}, now=now)
            return {"schema_version": 1, "run_id": self.run_id, "expired_task_ids": expired}

    def cancel_task(self, task_id: str, reason: str, *, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        if not reason.strip():
            raise ProjectError("task cancellation reason is required")
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            if task_id not in {item["task_id"] for item in self._pending_tasks()}:
                raise ProjectError("only a pending task can be cancelled")
            gate = self._gate()
            remaining = [item for item in self._pending_tasks() if item["task_id"] != task_id]
            state = self._state()
            gate.update({
                "status": _gate_status(
                    state["status"], has_pending_tasks=bool(remaining),
                    has_pending_decisions=bool(gate["pending_decisions"]),
                ),
                "updated_at": now(),
            })
            updates = {f"runs/{self.run_id}/gate.json": _json_bytes(gate)}
            self._commit(updates, event_type="task_cancelled", actor=actor, reason=reason.strip(), data={"task_id": task_id}, now=now)
            return {"schema_version": 1, "run_id": self.run_id, "task_id": task_id, "status": "cancelled"}

    def requeue_task(self, task_id: str, *, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            existing = next(
                (read_json(path) for path in (self.run_dir / "tasks/v1").glob("*.json") if read_json(path).get("requeue_of_task_id") == task_id),
                None,
            )
            if existing is not None:
                return existing
            terminal = {
                event["data"].get("task_id") for event in read_jsonl(self.run_dir / "events.jsonl")
                if event["event_type"] in {"task_lease_expired", "task_cancelled"}
            }
            if task_id not in terminal:
                raise ProjectError("only an expired or cancelled task can be requeued")
            prior = validate_document("task", read_json(self.run_dir / "tasks/v1" / f"{task_id}.json"))
            replacement = self.dispatch(
                role=prior["role"], subquestion=prior["subquestion"], evidence_strategy=prior["evidence_strategy"],
                selected_context=prior["selected_context"], artifact_references=prior["artifact_references"],
                allowed_capabilities=prior["allowed_capabilities"], allowed_domains=prior["allowed_domains"],
                required_result_operations=prior["required_result_operations"], parent_task_id=prior["parent_task_id"],
                controller_token=controller_token, reserve_deep_sources=prior["reservation"]["deep_sources"],
                reserve_provider_usage=prior["reservation"]["provider_usage"], requeue_of_task_id=task_id, now=now,
            )
            self._commit({}, event_type="task_requeued", actor=actor, reason=None, data={"task_id": task_id, "replacement_task_id": replacement["task_id"]}, now=now)
            return replacement

    def _safe_parallel_add_rebase(
        self,
        operations: list[dict[str, Any]],
        *,
        diff_id: str,
    ) -> bool:
        """Allow stale admission only for independent additions at unused positions."""
        if not operations or any(
            item.get("op") != "add" or item.get("target") != "node"
            for item in operations
        ):
            return False
        nodes = GraphRepository(self.canonical_root).nodes()
        node_map = {item["node_id"]: item for item in nodes}
        active_positions = {
            (item["parent_id"], item["position"])
            for item in nodes if not item["retired"]
        }
        requested_positions: set[tuple[str | None, int]] = set()
        requested_ids: set[str] = set()
        for index, operation in enumerate(operations):
            record = operation.get("record")
            if not isinstance(record, dict):
                return False
            identity = record.get("node_id", _derived_node_id(diff_id, index))
            if identity in node_map or identity in requested_ids:
                return False
            requested_ids.add(identity)
            parent_id = record.get("parent_id")
            if parent_id is not None:
                parent = node_map.get(parent_id)
                if parent is None or parent["retired"]:
                    return False
            position = record.get("position", 0)
            if not isinstance(position, int) or isinstance(position, bool) or position < 0:
                return False
            location = (parent_id, position)
            if location in active_positions or location in requested_positions:
                return False
            requested_positions.add(location)
        return True

    def import_result(
        self,
        result: dict[str, Any],
        *,
        controller_token: str | None,
        now: Callable[[], str] = utc_now,
        after_graph_apply: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        try:
            validate_document("result", result)
        except SchemaError as exc:
            raise ProjectError(str(exc)) from exc
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            instant, parsed = _fixed_now(now)
            receipt_now = lambda: instant
            if result["run_id"] != self.run_id:
                raise ProjectError("result run_id does not match destination run")
            task_path = self.run_dir / "tasks/v1" / f"{result['task_id']}.json"
            if not task_path.is_file():
                raise ProjectError(f"result references unknown task: {result['task_id']}")
            task = validate_document("task", read_json(task_path))
            result_path = self.run_dir / "results/v1" / f"{result['task_id']}.json"
            existing = read_json(result_path) if result_path.exists() else None
            if existing is not None and canonical_json(existing) != canonical_json(result):
                raise ProjectError("task already has a different result packet")
            state, gate, budget = self._state(), self._gate(), self._budget()
            result_identity = "res_" + hashlib.sha256(canonical_json(result).encode("utf-8")).hexdigest()[:32]
            graph = GraphRepository(self.canonical_root)
            diff_id = (
                "dif_" + hashlib.sha256(
                    f"{self.run_id}:{task['task_id']}:{result_identity}".encode()
                ).hexdigest()[:32]
                if result["proposed_graph_operations"] else None
            )
            prior_diff = (
                next((item for item in graph.diffs() if item["diff_id"] == diff_id), None)
                if diff_id is not None else None
            )
            if prior_diff is not None:
                with run_write_scope(self.root, self.run_id, canonical_root=self.canonical_root):
                    prior_diff = graph.propose(
                        result["proposed_graph_operations"], base_revision=prior_diff["base_revision"],
                        actor_type="agent", actor_id=task["worker_id"], diff_id=diff_id, now=receipt_now,
                    )
            fully_accounted = (
                existing is not None
                and result_identity in state["accepted_result_ids"]
                and result_identity in budget["accounted_result_ids"]
            )
            if fully_accounted:
                return {"schema_version": SCHEMA_VERSION, "result_id": result_identity, "status": "accepted", "idempotent": True}
            if prior_diff is not None and prior_diff["status"] == "applied":
                if prior_diff["applied_authority"] != "agent_accepted":
                    raise ProjectError("applied result recovery requires an agent-accepted graph diff")
                recovery_receipt = _parse_time(prior_diff["created_at"])
                _validate_task_result_contract(task, result, receipt_time=recovery_receipt)
                self._validate_result_provenance(result)
                self._validate_result_scope(task, result)
                recovery_instant = recovery_receipt.isoformat(timespec="seconds").replace("+00:00", "Z")
                if (
                    budget["active_since"] is not None
                    and _parse_time(budget["active_since"]) <= recovery_receipt
                ):
                    self._update_elapsed(budget, recovery_instant, recovery_receipt)
                if result_identity not in budget["accounted_result_ids"]:
                    budget["consumed"]["provider_usage"] += result["usage"]["amount"]
                    new_sources = {
                        item["source_id"] for item in result["accessed_sources"]
                        if item["deeply_processed"] and item["source_id"] not in budget["deep_source_ids"]
                    }
                    budget["deep_source_ids"] = sorted(set(budget["deep_source_ids"]) | new_sources)
                    budget["consumed"]["deep_sources"] = len(budget["deep_source_ids"])
                    budget["accounted_result_ids"].append(result_identity)
                if result_identity not in state["accepted_result_ids"]:
                    state["accepted_result_ids"].append(result_identity)
                if prior_diff["diff_id"] not in state["ratification_diff_ids"]:
                    state["ratification_diff_ids"].append(prior_diff["diff_id"])
                state["updated_at"] = instant
                pending_after = [
                    item for item in self._pending_tasks() if item["task_id"] != task["task_id"]
                ]
                gate.update({
                    "status": _gate_status(
                        state["status"], has_pending_tasks=bool(pending_after),
                        has_pending_decisions=bool(gate["pending_decisions"]),
                    ),
                    "updated_at": instant,
                })
                budget["updated_at"] = instant
                self._commit(
                    {
                        f"runs/{self.run_id}/results/v1/{task['task_id']}.json": _json_bytes(result),
                        f"runs/{self.run_id}/state.json": _json_bytes(state),
                        f"runs/{self.run_id}/gate.json": _json_bytes(gate),
                        f"runs/{self.run_id}/budget.json": _json_bytes(budget),
                    },
                    event_type="result_recovered_applied", actor=actor,
                    reason="durable graph application preceded run-ledger commit",
                    data={
                        "task_id": task["task_id"], "result_id": result_identity,
                        "diff_id": prior_diff["diff_id"],
                        "trusted_recovery_receipt": recovery_instant,
                    },
                    now=now,
                )
                if state["status"] == "active":
                    self._update_elapsed(budget, instant, parsed)
                    reached = self._hard_reason(budget)
                    if reached:
                        self._commit(
                            self._pause_updates(state, gate, budget, reached, instant),
                            event_type="run_paused", actor="system", reason=reached,
                            data={"after_result_recovery": result_identity}, now=now,
                        )
                return {
                    "schema_version": SCHEMA_VERSION, "result_id": result_identity,
                    "status": "accepted", "stale": False, "graph_diff": prior_diff,
                    "pending_decision": None,
                    "rebased_from_revision": (
                        result["base_revision"]
                        if prior_diff["base_revision"] != result["base_revision"] else None
                    ),
                    "recovered_applied": True,
                }
            terminal_reason = self._task_terminal_reason(task["task_id"])
            if terminal_reason is not None:
                raise ProjectError(f"result task is terminal and cannot be imported: {terminal_reason}")
            if state["status"] == "finished":
                raise ProjectError("cannot import a result into a finished run")
            persisted_receipt = existing is not None and result_identity in budget["accounted_result_ids"]
            if not persisted_receipt:
                _validate_task_result_contract(task, result, receipt_time=parsed)
            self._validate_result_provenance(result)
            self._validate_result_scope(task, result)
            self._update_elapsed(budget, instant, parsed)
            already_accounted = result_identity in budget["accounted_result_ids"]
            if not already_accounted:
                if result["usage"]["unit"] != budget["limits"]["provider_usage"]["unit"]:
                    raise ProjectError("result usage unit does not match the run provider ceiling")
                budget["consumed"]["provider_usage"] += result["usage"]["amount"]
                new_sources = {
                    item["source_id"] for item in result["accessed_sources"]
                    if item["deeply_processed"] and item["source_id"] not in budget["deep_source_ids"]
                }
                budget["deep_source_ids"] = sorted(set(budget["deep_source_ids"]) | new_sources)
                budget["consumed"]["deep_sources"] = len(budget["deep_source_ids"])
                budget["accounted_result_ids"].append(result_identity)
            over = None
            if result_identity in state["accepted_result_ids"]:
                return {"schema_version": SCHEMA_VERSION, "result_id": result_identity, "status": "accepted", "idempotent": True}
            if state["status"] == "paused":
                self._commit(
                    {f"runs/{self.run_id}/results/v1/{task['task_id']}.json": _json_bytes(result), f"runs/{self.run_id}/budget.json": _json_bytes(budget)},
                    event_type="result_held_paused", actor="system", reason=state["pause_reason"],
                    data={"task_id": task["task_id"], "result_id": result_identity}, now=now,
                )
                return {"schema_version": SCHEMA_VERSION, "result_id": result_identity, "status": "held_paused", "reason": state["pause_reason"]}
            if budget["consumed"]["deep_sources"] > budget["limits"]["deep_sources"]:
                over = "hard_cap:deep_sources"
            elif budget["consumed"]["provider_usage"] > budget["limits"]["provider_usage"]["ceiling"]:
                over = "hard_cap:provider_usage"
            elif budget["elapsed_seconds"] / 60.0 > budget["limits"]["minutes"]:
                over = "hard_cap:minutes"
            if over:
                updates = self._pause_updates(state, gate, budget, over, instant)
                updates[f"runs/{self.run_id}/results/v1/{task['task_id']}.json"] = _json_bytes(result)
                self._commit(updates, event_type="result_held_over_cap", actor="system", reason=over, data={"task_id": task["task_id"], "result_id": result_identity}, now=now)
                return {"schema_version": SCHEMA_VERSION, "result_id": result_identity, "status": "held_over_cap", "reason": over}
            original_revision = result["base_revision"]
            current_revision = graph.revision
            stale = original_revision != current_revision
            diff = None
            rebased_from_revision = None
            if result["proposed_graph_operations"]:
                if prior_diff is not None:
                    with run_write_scope(self.root, self.run_id, canonical_root=self.canonical_root):
                        diff = graph.propose(
                            result["proposed_graph_operations"], base_revision=prior_diff["base_revision"],
                            actor_type="agent", actor_id=task["worker_id"], diff_id=diff_id, now=receipt_now,
                        )
                    if diff["base_revision"] != original_revision:
                        rebased_from_revision = original_revision
                    stale = not (
                        diff["status"] == "applied"
                        or (diff["status"] == "proposed" and diff["base_revision"] == graph.revision)
                    )
                else:
                    proposal_revision = original_revision
                    if stale and self._safe_parallel_add_rebase(
                        result["proposed_graph_operations"], diff_id=diff_id
                    ):
                        proposal_revision = current_revision
                        rebased_from_revision = original_revision
                        stale = False
                    try:
                        with run_write_scope(self.root, self.run_id, canonical_root=self.canonical_root):
                            diff = graph.propose(
                                result["proposed_graph_operations"], base_revision=proposal_revision,
                                actor_type="agent", actor_id=task["worker_id"], diff_id=diff_id, now=receipt_now,
                            )
                    except ProjectError:
                        if rebased_from_revision is None:
                            raise
                        # A candidate that fails current-graph semantics remains a stale
                        # proposal; semantic rebase never turns reviewable work into loss.
                        rebased_from_revision = None
                        stale = True
                        with run_write_scope(self.root, self.run_id, canonical_root=self.canonical_root):
                            diff = graph.propose(
                                result["proposed_graph_operations"], base_revision=original_revision,
                                actor_type="agent", actor_id=task["worker_id"], diff_id=diff_id, now=receipt_now,
                            )
            decision = None
            if gate["controller"] == "sol" and diff is not None and not stale:
                with run_write_scope(self.root, self.run_id, canonical_root=self.canonical_root):
                    diff = graph.apply(diff["diff_id"], controller_token=controller_token, now=receipt_now)
                if after_graph_apply is not None:
                    after_graph_apply()
                if diff["diff_id"] not in state["ratification_diff_ids"]:
                    state["ratification_diff_ids"].append(diff["diff_id"])
            elif diff is not None:
                decision = {
                    "decision_type": "review_result", "result_id": result_identity,
                    "task_id": task["task_id"], "diff_id": diff["diff_id"], "stale": stale,
                }
                gate["pending_decisions"].append(decision)
            state["accepted_result_ids"].append(result_identity)
            state["updated_at"] = instant
            pending_after = [item for item in self._pending_tasks() if item["task_id"] != task["task_id"]]
            gate["status"] = _gate_status(
                state["status"], has_pending_tasks=bool(pending_after),
                has_pending_decisions=bool(gate["pending_decisions"]),
            )
            gate["updated_at"] = instant
            budget["updated_at"] = instant
            updates = {
                f"runs/{self.run_id}/results/v1/{task['task_id']}.json": _json_bytes(result),
                f"runs/{self.run_id}/state.json": _json_bytes(state),
                f"runs/{self.run_id}/gate.json": _json_bytes(gate),
                f"runs/{self.run_id}/budget.json": _json_bytes(budget),
            }
            self._commit(updates, event_type="result_imported", actor=actor, reason=None, data={"task_id": task["task_id"], "result_id": result_identity, "diff_id": None if diff is None else diff["diff_id"], "stale": stale, "rebased_from_revision": rebased_from_revision, "admitted_revision": None if diff is None else diff["base_revision"]}, now=now)
            reached = self._hard_reason(budget)
            if reached and not pending_after:
                self._commit(self._pause_updates(state, gate, budget, reached, instant), event_type="run_paused", actor="system", reason=reached, data={}, now=now)
            return {
                "schema_version": SCHEMA_VERSION, "result_id": result_identity,
                "status": "accepted", "stale": stale, "graph_diff": diff,
                "pending_decision": decision,
                "rebased_from_revision": rebased_from_revision,
            }

    def resolve_result(self, result_id: str, *, accept: bool, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            gate, state = self._gate(), self._state()
            decision = next((item for item in gate["pending_decisions"] if item.get("result_id") == result_id), None)
            if decision is None:
                raise ProjectError(f"no pending result decision: {result_id}")
            if accept:
                if decision["stale"]:
                    raise ProjectError("stale result remains reviewable but cannot overwrite newer graph state")
                with run_write_scope(self.root, self.run_id, canonical_root=self.canonical_root):
                    applied = GraphRepository(self.canonical_root).apply(decision["diff_id"], controller_token=controller_token, now=now)
            else:
                applied = None
            gate["pending_decisions"] = [item for item in gate["pending_decisions"] if item is not decision]
            gate["status"] = _gate_status(
                state["status"], has_pending_tasks=bool(self._pending_tasks()),
                has_pending_decisions=bool(gate["pending_decisions"]),
            )
            gate["updated_at"] = state["updated_at"] = now()
            self._commit(
                {f"runs/{self.run_id}/gate.json": _json_bytes(gate), f"runs/{self.run_id}/state.json": _json_bytes(state)},
                event_type="result_accepted" if accept else "result_rejected", actor=actor, reason=None,
                data={"result_id": result_id, "diff_id": decision["diff_id"]}, now=now,
            )
            return {"schema_version": SCHEMA_VERSION, "accepted": accept, "graph_diff": applied}

    def pause(self, reason: str, *, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        if not reason.strip():
            raise ProjectError("pause reason is required")
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            instant, parsed = _fixed_now(now)
            state, gate, budget = self._state(), self._gate(), self._budget()
            if state["status"] != "active":
                raise ProjectError(f"run is not active: {state['status']}")
            self._update_elapsed(budget, instant, parsed)
            self._commit(self._pause_updates(state, gate, budget, reason.strip(), instant), event_type="run_paused", actor=actor, reason=reason.strip(), data={}, now=now)
            return state

    def resume(self, *, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            instant, _ = _fixed_now(now)
            state, gate, budget = self._state(), self._gate(), self._budget()
            if state["status"] != "paused":
                raise ProjectError(f"run is not paused: {state['status']}")
            hard_reason = self._hard_reason(budget)
            if hard_reason:
                raise ProjectError("hard-capped run requires an explicit bounded extension before resume")
            state.update({"status": "active", "pause_reason": None, "updated_at": instant})
            budget.update({"active_since": instant, "updated_at": instant})
            gate.update({
                "status": _gate_status(
                    state["status"], has_pending_tasks=bool(self._pending_tasks()),
                    has_pending_decisions=bool(gate["pending_decisions"]),
                ),
                "updated_at": instant,
            })
            self._commit(
                {f"runs/{self.run_id}/state.json": _json_bytes(state), f"runs/{self.run_id}/budget.json": _json_bytes(budget), f"runs/{self.run_id}/gate.json": _json_bytes(gate)},
                event_type="run_resumed", actor=actor, reason=None, data={}, now=now,
            )
            return state

    def switch_controller(self, controller: str, *, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        if controller not in {"human", "sol"}:
            raise ProjectError("controller must be human or sol")
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            gate = self._gate()
            if gate["status"] != "clean" or gate["pending_decisions"] or self._pending_tasks():
                raise ProjectError("controller handoff is allowed only at a clean gate")
            prior = gate["controller"]
            if prior == controller:
                return gate
            gate.update({"controller": controller, "updated_at": now()})
            self._commit({f"runs/{self.run_id}/gate.json": _json_bytes(gate)}, event_type="controller_switched", actor=actor, reason=None, data={"from": prior, "to": controller}, now=now)
            return gate

    def advance_cycle(self, *, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            gate, state, budget = self._gate(), self._state(), self._budget()
            if gate["status"] != "clean" or self._pending_tasks() or gate["pending_decisions"]:
                raise ProjectError("cycle can advance only at a clean gate")
            if state["current_cycle"] >= budget["limits"]["cycles"]:
                instant = now()
                self._commit(self._pause_updates(state, gate, budget, "hard_cap:cycles", instant), event_type="run_paused", actor="system", reason="hard_cap:cycles", data={}, now=now)
                raise ProjectError("cycle cap reached")
            state["current_cycle"] += 1
            state["updated_at"] = budget["updated_at"] = now()
            budget["consumed"]["cycles"] = state["current_cycle"]
            self._commit(
                {f"runs/{self.run_id}/state.json": _json_bytes(state), f"runs/{self.run_id}/budget.json": _json_bytes(budget)},
                event_type="cycle_advanced", actor=actor, reason=None, data={"cycle": state["current_cycle"]}, now=now,
            )
            return state

    def ratify(self, diff_id: str, *, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        """Human-ratify the records touched by one applied Sol diff."""
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token, "human")
            state, gate = self._state(), self._gate()
            if gate["status"] != "clean" or self._pending_tasks() or gate["pending_decisions"]:
                raise ProjectError("ratification is allowed only at a clean human gate")
            if diff_id not in state["ratification_diff_ids"]:
                raise ProjectError(f"diff is not pending human ratification: {diff_id}")
            graph = GraphRepository(self.canonical_root)
            source = next((item for item in graph.diffs() if item["diff_id"] == diff_id), None)
            if source is None or source["status"] != "applied" or source["applied_authority"] != "agent_accepted":
                raise ProjectError("only an applied agent-accepted diff can be ratified")
            nodes = {item["node_id"]: item for item in graph.nodes()}
            edges = {item["edge_id"]: item for item in graph.edges()}
            touched_nodes: set[str] = set()
            touched_edges: set[str] = set()
            for operation in source["operations"]:
                op = operation["op"]
                if op == "add":
                    touched_nodes.add(operation["record"]["node_id"])
                elif op == "link":
                    touched_edges.add(operation["record"]["edge_id"])
                elif op == "merge":
                    touched_nodes.update((operation["source_id"], operation["target_id"]))
                elif op == "move":
                    touched_nodes.add(operation["target_id"])
                elif op == "unlink":
                    touched_edges.add(operation["target_id"])
                elif operation.get("target") == "node":
                    touched_nodes.add(operation["target_id"])
                elif operation.get("target") == "edge":
                    touched_edges.add(operation["target_id"])
            operations = [
                {"op": "update", "target": "node", "target_id": identity, "changes": {"authority": "human_accepted"}}
                for identity in sorted(touched_nodes) if identity in nodes and nodes[identity]["authority"] != "human_accepted"
            ] + [
                {"op": "update", "target": "edge", "target_id": identity, "changes": {"authority": "human_accepted"}}
                for identity in sorted(touched_edges) if identity in edges and edges[identity]["authority"] != "human_accepted"
            ]
            ratification = None
            if operations:
                identity = "dif_" + hashlib.sha256(f"ratify:{self.run_id}:{diff_id}".encode()).hexdigest()[:32]
                with run_write_scope(self.root, self.run_id, canonical_root=self.canonical_root):
                    ratification = graph.propose(operations, actor_type="human", actor_id=actor, diff_id=identity, now=now)
                    ratification = graph.apply(identity, controller_token=controller_token, now=now)
            state["ratification_diff_ids"].remove(diff_id)
            state["updated_at"] = now()
            self._commit(
                {f"runs/{self.run_id}/state.json": _json_bytes(state)}, event_type="diff_ratified", actor=actor, reason=None,
                data={"source_diff_id": diff_id, "ratification_diff_id": None if ratification is None else ratification["diff_id"]}, now=now,
            )
            return {"schema_version": SCHEMA_VERSION, "source_diff_id": diff_id, "ratification_diff": ratification}

    def extend(self, delta: dict[str, Any], *, actor: str, reason: str, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        if not reason.strip() or not delta or set(delta) - EXTENSION_KEYS:
            raise ProjectError("extension requires a reason and supported bounded delta")
        with ProjectRunLock(self.root):
            subject = self._authorize(controller_token, actor)
            budget = self._budget()
            initial = self.manifest["config"]
            extensions = read_jsonl(self.run_dir / "extensions.jsonl")
            cumulative = {key: 0.0 for key in EXTENSION_KEYS}
            for prior in extensions:
                for key, value in prior["delta"].items():
                    cumulative[key] += float(value)
            normalized: dict[str, Any] = {}
            for key, value in delta.items():
                if key == "provider_usage":
                    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0 or cumulative[key] + value > initial[key]["ceiling"]:
                        raise ProjectError("cumulative provider_usage extensions cannot exceed the initial ceiling")
                    budget["limits"][key]["ceiling"] += value
                else:
                    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0 or cumulative[key] + value > initial[key]:
                        raise ProjectError(f"cumulative {key} extensions cannot exceed its initial limit")
                    if key in {"cycles", "tasks", "deep_sources"} and not isinstance(value, int):
                        raise ProjectError(f"{key} extension must be an integer")
                    budget["limits"][key] += value
                normalized[key] = value
            record = {
                "schema_version": SCHEMA_VERSION, "extension_id": _opaque("ext"), "run_id": self.run_id,
                "actor": actor, "reason": reason.strip(), "delta": normalized, "created_at": now(),
            }
            validate_document("budget_extension", record)
            extensions.append(record)
            budget["updated_at"] = record["created_at"]
            self._commit(
                {f"runs/{self.run_id}/extensions.jsonl": _jsonl_bytes(extensions), f"runs/{self.run_id}/budget.json": _json_bytes(budget)},
                event_type="budget_extended", actor=subject, reason=reason.strip(), data={"extension_id": record["extension_id"], "delta": normalized}, now=now,
            )
            return record

    def finish(self, reason: str, *, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        if not reason.strip():
            raise ProjectError("finish reason is required")
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            state, gate, budget = self._state(), self._gate(), self._budget()
            if self._pending_tasks() or gate["pending_decisions"]:
                raise ProjectError("cannot finish with pending tasks or decisions")
            instant, parsed = _fixed_now(now)
            self._update_elapsed(budget, instant, parsed)
            state.update({"status": "finished", "pause_reason": reason.strip(), "updated_at": instant})
            gate.update({"status": "finished", "updated_at": instant})
            budget.update({"active_since": None, "updated_at": instant})
            self._commit(
                {f"runs/{self.run_id}/state.json": _json_bytes(state), f"runs/{self.run_id}/gate.json": _json_bytes(gate), f"runs/{self.run_id}/budget.json": _json_bytes(budget)},
                event_type="run_finished", actor=actor, reason=reason.strip(), data={}, now=now,
            )
            return state

    def checkpoint(self, paths: Sequence[str], message: str, *, controller_token: str | None, now: Callable[[], str] = utc_now) -> dict[str, Any]:
        with ProjectRunLock(self.root):
            actor = self._authorize(controller_token)
            revision = checkpoint_run_worktree(self.manifest["git"], paths=paths, message=message)
            self._commit({}, event_type="git_checkpoint", actor=actor, reason=message, data={"revision": revision, "paths": list(paths)}, now=now)
            return {"schema_version": SCHEMA_VERSION, "revision": revision, "branch": self.manifest["git"]["branch"]}


def list_run_statuses(project_path: Path) -> list[dict[str, Any]]:
    root = project_path.resolve()
    load_project(root)
    runs = confined_project_path(root, "runs")
    if not runs.exists():
        return []
    return [RunRepository(root, path.name).status() for path in sorted(runs.glob("run_*")) if path.is_dir()]
