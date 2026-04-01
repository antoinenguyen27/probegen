from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ParityError(Exception):
    """Base exception for parity."""


class ConfigError(ParityError):
    """Raised when parity configuration cannot be loaded or validated."""


class ContextWarning(ParityError):
    """Raised internally for missing optional context artifacts."""


class GitDiffError(ParityError):
    """Raised when git-based change detection fails."""


class EventPayloadError(ParityError):
    """Raised when the GitHub event payload is missing or malformed."""

class EmbeddingError(ParityError):
    """Raised when embeddings cannot be created."""


class CacheError(ParityError):
    """Raised when the embedding cache is unavailable."""


class GithubApiError(ParityError):
    """Raised when the GitHub API returns an unexpected response."""


class PlatformIntegrationError(ParityError):
    """Raised when a platform read or write fails."""


@dataclass(slots=True)
class StageError(ParityError):
    """Raised when a pipeline stage fails."""

    message: str
    stage: int | None = None
    cost_usd: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        stage_prefix = f"Stage {self.stage}: " if self.stage is not None else ""
        return f"{stage_prefix}{self.message}"


class SchemaValidationError(StageError):
    """Raised when stage output does not satisfy the expected contract."""


@dataclass(slots=True)
class BudgetExceededError(StageError):
    """Raised when a stage exceeds its configured budget."""

    partial_result: Any = None


@dataclass(slots=True)
class RateLimitStageError(StageError):
    """Raised when a stage repeatedly encounters rate limiting."""

    retry_count: int = 0
