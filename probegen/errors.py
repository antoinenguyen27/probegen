from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProbegenError(Exception):
    """Base exception for probegen."""


class ConfigError(ProbegenError):
    """Raised when probegen configuration cannot be loaded or validated."""


class ContextWarning(ProbegenError):
    """Raised internally for missing optional context artifacts."""


class GitDiffError(ProbegenError):
    """Raised when git-based change detection fails."""


class EventPayloadError(ProbegenError):
    """Raised when the GitHub event payload is missing or malformed."""


class SchemaValidationError(ProbegenError):
    """Raised when stage output does not satisfy the expected contract."""


class EmbeddingError(ProbegenError):
    """Raised when embeddings cannot be created."""


class CacheError(ProbegenError):
    """Raised when the embedding cache is unavailable."""


class GithubApiError(ProbegenError):
    """Raised when the GitHub API returns an unexpected response."""


class PlatformIntegrationError(ProbegenError):
    """Raised when a platform read or write fails."""


@dataclass(slots=True)
class StageError(ProbegenError):
    """Raised when a pipeline stage fails."""

    message: str
    stage: int | None = None
    cost_usd: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        stage_prefix = f"Stage {self.stage}: " if self.stage is not None else ""
        return f"{stage_prefix}{self.message}"


@dataclass(slots=True)
class BudgetExceededError(StageError):
    """Raised when a stage exceeds its configured budget."""

    partial_result: Any = None


@dataclass(slots=True)
class RateLimitStageError(StageError):
    """Raised when a stage repeatedly encounters rate limiting."""

    retry_count: int = 0
