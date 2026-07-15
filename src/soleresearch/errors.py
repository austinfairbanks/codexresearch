from __future__ import annotations


class SoleResearchError(Exception):
    """Base error for expected, user-actionable failures."""


class SchemaError(SoleResearchError):
    """Raised when a versioned document violates its contract."""


class ProjectError(SoleResearchError):
    """Raised when a project cannot be safely read or written."""


class MigrationRequired(ProjectError):
    """Raised when an explicit, user-invoked project migration is required."""
