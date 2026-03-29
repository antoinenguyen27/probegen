from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from langsmith import Client as LangSmithClient
from mcp.server.fastmcp import FastMCP
from phoenix.client import Client as PhoenixClient

from parity.config import ParityConfig
from parity.errors import EmbeddingError
from parity.integrations.braintrust import BraintrustDirectReader
from parity.integrations.langsmith import LangSmithReader
from parity.integrations.phoenix import PhoenixReader
from parity.integrations.promptfoo import PromptfooReader
from parity.tools.embedding import (
    EmbeddingBatchUsage,
    execute_planned_embedding_batch,
    plan_embedding_batch,
)
from parity.tools.similarity import classify_embedding_against_corpus, classify_embeddings_against_corpus

_PROMPTFOO_DISCOVERY_GLOBS = (
    "promptfooconfig.yaml",
    "promptfooconfig.yml",
    "**/promptfooconfig.yaml",
    "**/promptfooconfig.yml",
    "**/*promptfoo*.yaml",
    "**/*promptfoo*.yml",
    "**/eval*.yaml",
    "**/eval*.yml",
)
_IGNORED_DISCOVERY_DIRS = {".git", ".claude", ".parity", ".venv", "__pycache__", "node_modules", "dist", "build"}


@dataclass(slots=True)
class Stage2EmbeddingSpendLedger:
    request_count: int = 0
    blocked_request_count: int = 0
    input_count: int = 0
    cached_count: int = 0
    miss_count: int = 0
    input_tokens: int = 0
    estimated_cost_usd: float = 0.0
    cache_warning: bool = False
    models: set[str] = field(default_factory=set)

    def record_usage(self, usage: EmbeddingBatchUsage, *, cache_warning: bool) -> None:
        self.request_count += usage.request_count
        self.input_count += usage.input_count
        self.cached_count += usage.cached_count
        self.miss_count += usage.miss_count
        self.input_tokens += usage.input_tokens
        self.estimated_cost_usd += usage.estimated_cost_usd or 0.0
        self.cache_warning = self.cache_warning or cache_warning
        self.models.add(usage.model)

    def model_dump(self) -> dict[str, Any]:
        return {
            "request_count": self.request_count,
            "blocked_request_count": self.blocked_request_count,
            "input_count": self.input_count,
            "cached_count": self.cached_count,
            "miss_count": self.miss_count,
            "input_tokens": self.input_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "cache_warning": self.cache_warning,
            "models": sorted(self.models),
        }


@dataclass(slots=True)
class Stage2RetrievalLedger:
    fetch_request_count: int = 0
    total_cases: int = 0
    sources: list[dict[str, str]] = field(default_factory=list)

    def record_fetch(self, *, platform: str, target: str, case_count: int) -> None:
        self.fetch_request_count += 1
        self.total_cases += case_count
        source = {"platform": platform, "target": target}
        if source not in self.sources:
            self.sources.append(source)

    def model_dump(self) -> dict[str, Any]:
        return {
            "fetch_request_count": self.fetch_request_count,
            "total_cases": self.total_cases,
            "sources": self.sources,
        }


@dataclass(slots=True)
class Stage2MCPServerBundle:
    server: FastMCP
    toolbox: "Stage2Toolbox"


class Stage2Toolbox:
    def __init__(
        self,
        *,
        config: ParityConfig,
        repo_root: str | Path,
        env: dict[str, str] | None = None,
        embedding_spend_cap_usd: float | None = None,
    ) -> None:
        self.config = config
        self.repo_root = Path(repo_root).resolve()
        self.env = env or {}
        self.embedding_spend_cap_usd = embedding_spend_cap_usd
        self.embedding_spend = Stage2EmbeddingSpendLedger()
        self.retrieval = Stage2RetrievalLedger()

    def search_eval_targets(
        self,
        platform: str,
        query: str,
        *,
        project: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Find candidate eval targets on a platform without exposing platform credentials to the agent."""
        normalized_platform = _normalize_platform(platform)
        normalized_query = query.strip()

        if normalized_platform == "langsmith":
            client = LangSmithClient(api_key=self._require_env("langsmith"))
            datasets = list(
                client.list_datasets(
                    dataset_name_contains=normalized_query or None,
                    limit=limit,
                )
            )
            candidates = [
                {
                    "platform": "langsmith",
                    "target": getattr(dataset, "name", ""),
                    "dataset_id": str(getattr(dataset, "id", "")),
                    "project": None,
                    "match_reason": "name_contains" if normalized_query else "recent_dataset",
                }
                for dataset in datasets
            ]
            return {"platform": "langsmith", "query": normalized_query, "candidates": candidates}

        if normalized_platform == "arize_phoenix":
            client = PhoenixClient(
                base_url=self.config.platforms.arize_phoenix.base_url if self.config.platforms.arize_phoenix else None,
                api_key=self._require_env("arize_phoenix"),
            )
            datasets = client.datasets.list(limit=None)
            filtered = []
            for dataset in datasets:
                name = str(getattr(dataset, "name", "") or "")
                if normalized_query and normalized_query.lower() not in name.lower():
                    continue
                filtered.append(
                    {
                        "platform": "arize_phoenix",
                        "target": name,
                        "dataset_id": str(getattr(dataset, "id", "")),
                        "project": None,
                        "match_reason": "name_contains" if normalized_query else "dataset_list",
                    }
                )
                if len(filtered) >= limit:
                    break
            return {"platform": "arize_phoenix", "query": normalized_query, "candidates": filtered}

        if normalized_platform == "promptfoo":
            candidates = self._discover_promptfoo_targets(query=normalized_query, limit=limit)
            return {"platform": "promptfoo", "query": normalized_query, "candidates": candidates}

        if normalized_platform == "braintrust":
            return {
                "platform": "braintrust",
                "query": normalized_query,
                "project": project,
                "candidates": [],
                "note": (
                    "Braintrust target discovery is limited in the current host tool. "
                    "Use explicit project/dataset mappings when possible."
                ),
            }

        raise ValueError(f"Unsupported platform: {platform}")

    def fetch_eval_cases(
        self,
        platform: str,
        *,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Fetch eval cases through host-owned integrations so raw platform keys never cross the agent boundary."""
        normalized_platform = _normalize_platform(platform)

        if normalized_platform == "langsmith":
            reader = LangSmithReader(api_key=self._require_env("langsmith"))
            cases = reader.fetch_examples(dataset_name=target or None, dataset_id=dataset_id, limit=limit)
        elif normalized_platform == "braintrust":
            if not project:
                raise ValueError("Braintrust fetch requires `project`.")
            reader = BraintrustDirectReader(
                api_key=self._require_env("braintrust"),
                org_name=self.config.platforms.braintrust.org if self.config.platforms.braintrust else None,
            )
            cases = reader.fetch_examples(project=project, dataset_name=target, limit=limit)
        elif normalized_platform == "arize_phoenix":
            reader = PhoenixReader(
                base_url=self.config.platforms.arize_phoenix.base_url if self.config.platforms.arize_phoenix else None,
                api_key=self._require_env("arize_phoenix"),
            )
            cases = reader.fetch_examples(dataset_name=target, limit=limit)
        elif normalized_platform == "promptfoo":
            path = self._resolve_repo_path(target)
            if path is None or not path.exists():
                raise FileNotFoundError(f"Promptfoo config not found within the repository: {target}")
            cases = PromptfooReader().fetch_examples(path)
            target = path.relative_to(self.repo_root).as_posix()
        else:
            raise ValueError(f"Unsupported platform: {platform}")

        self.retrieval.record_fetch(platform=normalized_platform, target=target, case_count=len(cases))
        return {
            "platform": normalized_platform,
            "target": target,
            "project": project,
            "case_count": len(cases),
            "cases": [case.model_dump(mode="json") for case in cases],
        }

    def embed_batch(
        self,
        inputs: list[dict[str, str]],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> dict[str, Any]:
        """Embed a batch of eval inputs using host-owned credentials and cache settings."""
        resolved_model = model or self.config.embedding.model
        resolved_dimensions = dimensions if dimensions is not None else self.config.embedding.dimensions
        cache_path = self.config.resolve_path(self.config.embedding.cache_path, self.repo_root)
        plan = plan_embedding_batch(
            inputs,
            model=resolved_model,
            cache_path=cache_path,
            dimensions=resolved_dimensions,
        )
        remaining_budget_usd: float | None = None
        projected_request_cost_usd = plan.usage.estimated_cost_usd
        if self.embedding_spend_cap_usd is not None:
            remaining_budget_usd = max(self.embedding_spend_cap_usd - self.embedding_spend.estimated_cost_usd, 0.0)
            if plan.usage.miss_count > 0 and projected_request_cost_usd is None:
                raise EmbeddingError(
                    f"Embedding model `{resolved_model}` is not supported for spend tracking; "
                    "configure a priced embedding model or remove the total spend cap."
                )
            if (
                projected_request_cost_usd is not None
                and projected_request_cost_usd > remaining_budget_usd + 1e-9
            ):
                self.embedding_spend.blocked_request_count += 1
                cached_embeddings = [
                    plan.cached_results[item.id] for item in plan.items if item.id in plan.cached_results
                ]
                return {
                    "count": len(cached_embeddings),
                    "cache_warning": plan.cache_warning,
                    "embeddings": cached_embeddings,
                    "missing_ids": [item.id for item in plan.misses],
                    "budget_exceeded": True,
                    "complete": not plan.misses,
                    "remaining_budget_usd": remaining_budget_usd,
                    "estimated_request_cost_usd": projected_request_cost_usd,
                    "usage": plan.usage.model_dump(),
                    "message": (
                        "Embedding spend cap would be exceeded by this request. "
                        "Reuse any returned cached embeddings and continue in partial/bootstrap mode "
                        "without additional embedding calls."
                    ),
                }
        embeddings, cache_warning, usage = execute_planned_embedding_batch(
            plan,
            model=resolved_model,
            cache_path=cache_path,
            dimensions=resolved_dimensions,
        )
        self.embedding_spend.record_usage(usage, cache_warning=cache_warning)
        return {
            "count": len(embeddings),
            "cache_warning": cache_warning,
            "embeddings": embeddings,
            "missing_ids": [],
            "budget_exceeded": False,
            "complete": True,
            "remaining_budget_usd": remaining_budget_usd,
            "estimated_request_cost_usd": usage.estimated_cost_usd,
            "usage": usage.model_dump(),
        }

    def find_similar(
        self,
        candidate: dict[str, Any],
        corpus: list[dict[str, Any]],
        *,
        duplicate_threshold: float | None = None,
        boundary_threshold: float | None = None,
    ) -> dict[str, Any]:
        """Compare a single candidate against an embedded corpus."""
        return classify_embedding_against_corpus(
            candidate["embedding"],
            corpus,
            candidate_id=candidate["id"],
            duplicate_threshold=duplicate_threshold or self.config.similarity.duplicate_threshold,
            boundary_threshold=boundary_threshold or self.config.similarity.boundary_threshold,
        )

    def find_similar_batch(
        self,
        candidates: list[dict[str, Any]],
        corpus: list[dict[str, Any]],
        *,
        duplicate_threshold: float | None = None,
        boundary_threshold: float | None = None,
    ) -> dict[str, Any]:
        """Compare a scoped batch of candidates against an embedded corpus while preserving per-candidate results."""
        results = classify_embeddings_against_corpus(
            candidates,
            corpus,
            duplicate_threshold=duplicate_threshold or self.config.similarity.duplicate_threshold,
            boundary_threshold=boundary_threshold or self.config.similarity.boundary_threshold,
        )
        return {
            "candidate_count": len(results),
            "results": results,
        }

    def _discover_promptfoo_targets(self, *, query: str, limit: int) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen_paths: set[Path] = set()
        configured_path = (
            self.config.resolve_path(self.config.platforms.promptfoo.config_path, self.repo_root)
            if self.config.platforms.promptfoo
            else None
        )
        if configured_path is not None and configured_path.exists():
            seen_paths.add(configured_path)
            if self._promptfoo_candidate_matches(configured_path, query=query):
                candidates.append(self._promptfoo_candidate(configured_path, "configured_path"))

        for pattern in _PROMPTFOO_DISCOVERY_GLOBS:
            for path in self.repo_root.glob(pattern):
                if len(candidates) >= limit:
                    return candidates
                resolved = path.resolve()
                if resolved in seen_paths or not path.is_file() or self._should_ignore(path):
                    continue
                seen_paths.add(resolved)
                if not self._is_promptfoo_config(path):
                    continue
                if not self._promptfoo_candidate_matches(path, query=query):
                    continue
                candidates.append(self._promptfoo_candidate(path, "path_match"))
        return candidates[:limit]

    def _promptfoo_candidate_matches(self, path: Path, *, query: str) -> bool:
        if not query:
            return True
        normalized_query = query.lower()
        relative = path.relative_to(self.repo_root).as_posix().lower()
        return normalized_query in relative or normalized_query in path.stem.lower()

    def _promptfoo_candidate(self, path: Path, match_reason: str) -> dict[str, Any]:
        return {
            "platform": "promptfoo",
            "target": path.relative_to(self.repo_root).as_posix(),
            "dataset_id": None,
            "project": None,
            "match_reason": match_reason,
        }

    def _is_promptfoo_config(self, path: Path) -> bool:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return False
        return isinstance(payload, dict) and isinstance(payload.get("tests"), list)

    def _should_ignore(self, path: Path) -> bool:
        return any(part in _IGNORED_DISCOVERY_DIRS for part in path.parts)

    def _resolve_repo_path(self, target: str) -> Path | None:
        try:
            candidate = (self.repo_root / target).resolve()
        except Exception:
            return None
        try:
            candidate.relative_to(self.repo_root)
        except ValueError:
            return None
        return candidate

    def _require_env(self, platform: str) -> str:
        env_name = _platform_env_name(self.config, platform)
        value = self.env.get(env_name) or ""
        if not value:
            raise RuntimeError(f"Missing required credential `{env_name}` for platform `{platform}`.")
        return value

    def build_runtime_metadata(self) -> dict[str, Any]:
        return {
            "stage2_embedding_spend_cap_usd": self.embedding_spend_cap_usd,
            "retrieval": self.retrieval.model_dump(),
            "embedding": self.embedding_spend.model_dump(),
        }


def build_stage2_mcp_server(
    *,
    config: ParityConfig,
    repo_root: str | Path,
    env: dict[str, str] | None = None,
    embedding_spend_cap_usd: float | None = None,
) -> Stage2MCPServerBundle:
    toolbox = Stage2Toolbox(
        config=config,
        repo_root=repo_root,
        env=env,
        embedding_spend_cap_usd=embedding_spend_cap_usd,
    )
    server = FastMCP("parity-stage2")

    @server.tool(name="search_eval_targets")
    def search_eval_targets(
        platform: str,
        query: str,
        project: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return toolbox.search_eval_targets(platform, query, project=project, limit=limit)

    @server.tool(name="fetch_eval_cases")
    def fetch_eval_cases(
        platform: str,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return toolbox.fetch_eval_cases(
            platform,
            target=target,
            project=project,
            dataset_id=dataset_id,
            limit=limit,
        )

    @server.tool(name="embed_batch")
    def embed_batch_tool(
        inputs: list[dict[str, str]],
        model: str | None = None,
        dimensions: int | None = None,
    ) -> dict[str, Any]:
        return toolbox.embed_batch(inputs, model=model, dimensions=dimensions)

    @server.tool(name="find_similar")
    def find_similar_tool(
        candidate: dict[str, Any],
        corpus: list[dict[str, Any]],
        duplicate_threshold: float | None = None,
        boundary_threshold: float | None = None,
    ) -> dict[str, Any]:
        return toolbox.find_similar(
            candidate,
            corpus,
            duplicate_threshold=duplicate_threshold,
            boundary_threshold=boundary_threshold,
        )

    @server.tool(name="find_similar_batch")
    def find_similar_batch_tool(
        candidates: list[dict[str, Any]],
        corpus: list[dict[str, Any]],
        duplicate_threshold: float | None = None,
        boundary_threshold: float | None = None,
    ) -> dict[str, Any]:
        return toolbox.find_similar_batch(
            candidates,
            corpus,
            duplicate_threshold=duplicate_threshold,
            boundary_threshold=boundary_threshold,
        )

    return Stage2MCPServerBundle(server=server, toolbox=toolbox)


def _normalize_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    if normalized == "phoenix":
        return "arize_phoenix"
    return normalized


def _platform_env_name(config: ParityConfig, platform: str) -> str:
    if platform == "langsmith":
        return config.platforms.langsmith.api_key_env if config.platforms.langsmith else "LANGSMITH_API_KEY"
    if platform == "braintrust":
        return config.platforms.braintrust.api_key_env if config.platforms.braintrust else "BRAINTRUST_API_KEY"
    if platform == "arize_phoenix":
        return (
            config.platforms.arize_phoenix.api_key_env
            if config.platforms.arize_phoenix
            else "PHOENIX_API_KEY"
        )
    raise ValueError(f"Unsupported platform: {platform}")
