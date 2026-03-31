from __future__ import annotations

import copy
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
from parity.models import EvalCaseSnapshot, EvaluatorBindingCandidate
from parity.renderers import (
    build_evaluator_dossiers,
    infer_method_profile,
    platform_evaluator_capabilities,
    summarize_raw_field_patterns,
)
from parity.tools.embedding import (
    EmbeddingBatchUsage,
    execute_planned_embedding_batch,
    plan_embedding_batch,
)
from parity.tools.similarity import classify_embedding_against_corpus, classify_embeddings_against_corpus

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
    target_discovery_count: int = 0
    repo_asset_discovery_count: int = 0
    fetch_request_count: int = 0
    total_cases: int = 0
    sources: list[dict[str, str]] = field(default_factory=list)

    def record_target_discovery(self) -> None:
        self.target_discovery_count += 1

    def record_repo_asset_discovery(self) -> None:
        self.repo_asset_discovery_count += 1

    def record_fetch(self, *, platform: str, target: str, case_count: int) -> None:
        self.fetch_request_count += 1
        self.total_cases += case_count
        source = {"platform": platform, "target": target}
        if source not in self.sources:
            self.sources.append(source)

    def model_dump(self) -> dict[str, Any]:
        return {
            "target_discovery_count": self.target_discovery_count,
            "repo_asset_discovery_count": self.repo_asset_discovery_count,
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
        self._cached_target_snapshots: dict[str, dict[str, Any]] = {}

    def discover_eval_targets(
        self,
        platform: str,
        query: str,
        *,
        project: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        normalized_platform = _normalize_platform(platform)
        normalized_query = query.strip()
        self.retrieval.record_target_discovery()

        if normalized_platform == "langsmith":
            client = LangSmithClient(api_key=self._require_env("langsmith"))
            datasets = list(client.list_datasets(dataset_name_contains=normalized_query or None, limit=limit))
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
            candidates = self._discover_repo_eval_assets(query=normalized_query, limit=limit, promptfoo_only=True)
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

    def fetch_eval_target_snapshot(
        self,
        platform: str,
        *,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
        limit: int | None = None,
        target_id: str | None = None,
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_platform = _normalize_platform(platform)
        resolved_limit = limit or self.config.evals.discovery.sample_limit_per_target
        cases = self._fetch_eval_cases(
            normalized_platform,
            target=target,
            project=project,
            dataset_id=dataset_id,
            limit=resolved_limit,
            target_id=target_id,
        )
        try:
            formal_discovery = self.discover_target_evaluators(
                normalized_platform,
                target=target,
                project=project,
                dataset_id=dataset_id,
                target_id=target_id,
                sample_cases=cases,
            )
        except Exception as exc:
            formal_discovery = {
                "platform": normalized_platform,
                "target": target,
                "dataset_id": dataset_id,
                "project": project,
                "target_id": target_id,
                "candidate_count": 0,
                "candidates": [],
                "notes": [f"Formal evaluator discovery failed; falling back to evidence inference: {exc}"],
            }
        formal_candidates = [
            EvaluatorBindingCandidate.model_validate(item)
            for item in formal_discovery.get("candidates", [])
            if isinstance(item, dict)
        ]
        method_profile = infer_method_profile(
            normalized_platform,
            cases,
            formal_candidates=formal_candidates,
            formal_notes=formal_discovery.get("notes", []),
        )
        evaluator_dossiers = build_evaluator_dossiers(
            normalized_platform,
            target_id=target_id or self._build_target_id(normalized_platform, target, project=project),
            samples=cases,
            method_profile=method_profile,
        )
        locator = target
        if normalized_platform == "promptfoo":
            resolved_path = self._resolve_repo_path(target)
            locator = resolved_path.relative_to(self.repo_root).as_posix() if resolved_path else target
        self.retrieval.record_fetch(platform=normalized_platform, target=locator, case_count=len(cases))
        resolved_target_id = target_id or self._build_target_id(normalized_platform, locator, project=project)
        for case in cases:
            case.source_target_id = resolved_target_id
            case.target_locator = locator
        payload = {
            "target_id": resolved_target_id,
            "platform": normalized_platform,
            "target": target,
            "target_name": target,
            "dataset_id": dataset_id,
            "project": project,
            "artifact_paths": artifact_paths or [],
            "target_locator": locator,
            "sample_count": len(cases),
            "samples": [case.model_dump(mode="json") for case in cases],
            "method_profile": method_profile.model_dump(mode="json"),
            "evaluator_dossiers": [dossier.model_dump(mode="json") for dossier in evaluator_dossiers],
            "formal_evaluator_discovery": formal_discovery,
            "aggregate_method_hints": sorted({hint for case in cases for hint in case.method_hints}),
            "raw_field_patterns": summarize_raw_field_patterns(cases),
            "profile_confidence": method_profile.confidence,
        }
        self._cached_target_snapshots[resolved_target_id] = copy.deepcopy(payload)
        return payload

    def discover_target_evaluators(
        self,
        platform: str,
        *,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
        target_id: str | None = None,
        sample_cases: list[EvalCaseSnapshot] | None = None,
    ) -> dict[str, Any]:
        normalized_platform = _normalize_platform(platform)
        candidates: list[EvaluatorBindingCandidate] = []
        notes: list[str] = []

        if normalized_platform == "promptfoo":
            path = self._resolve_repo_path(target)
            if path is None or not path.exists():
                raise FileNotFoundError(f"Promptfoo config not found within the repository: {target}")
            candidates = PromptfooReader().discover_evaluator_bindings(path)
        elif normalized_platform == "langsmith":
            reader = LangSmithReader(api_key=self._require_env("langsmith"))
            candidates = reader.discover_evaluator_bindings(dataset_name=target or None, dataset_id=dataset_id)
        elif normalized_platform == "braintrust":
            candidates = self._discover_braintrust_repo_evaluator_bindings(
                project=project,
                dataset_name=target,
                sample_cases=sample_cases or [],
                target_id=target_id,
            )
            if not candidates:
                notes.append(
                    "No formal Braintrust scorer bindings were recovered from repo assets. "
                    "Fallback inference may still recover scorer intent from dataset rows."
                )
        elif normalized_platform == "arize_phoenix":
            reader = PhoenixReader(
                base_url=self.config.platforms.arize_phoenix.base_url if self.config.platforms.arize_phoenix else None,
                api_key=self._require_env("arize_phoenix"),
            )
            candidates = reader.discover_evaluator_bindings(dataset_name=target)
            if not candidates:
                notes.append(
                    "The current Phoenix client surface used by Parity does not expose dataset-evaluator CRUD. "
                    "Fallback inference remains enabled."
                )
        else:
            raise ValueError(f"Unsupported platform: {platform}")

        return {
            "platform": normalized_platform,
            "target": target,
            "dataset_id": dataset_id,
            "project": project,
            "target_id": target_id,
            "candidate_count": len(candidates),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "notes": notes,
        }

    def read_evaluator_binding(
        self,
        platform: str,
        *,
        binding_id: str,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_platform = _normalize_platform(platform)
        if normalized_platform == "promptfoo":
            path = self._resolve_repo_path(target)
            if path is None or not path.exists():
                raise FileNotFoundError(f"Promptfoo config not found within the repository: {target}")
            return PromptfooReader().read_evaluator_binding(path, binding_id)
        if normalized_platform == "langsmith":
            reader = LangSmithReader(api_key=self._require_env("langsmith"))
            return reader.read_evaluator_binding(binding_id, dataset_name=target or None, dataset_id=dataset_id)
        if normalized_platform == "braintrust":
            candidate = next(
                (
                    item
                    for item in self._discover_braintrust_repo_evaluator_bindings(project=project, dataset_name=target, sample_cases=[])
                    if item.binding_id == binding_id
                ),
                None,
            )
            if candidate is None:
                raise KeyError(f"Unknown Braintrust evaluator binding: {binding_id}")
            return candidate.model_dump(mode="json")
        if normalized_platform == "arize_phoenix":
            reader = PhoenixReader(
                base_url=self.config.platforms.arize_phoenix.base_url if self.config.platforms.arize_phoenix else None,
                api_key=self._require_env("arize_phoenix"),
            )
            return reader.read_evaluator_binding(binding_id, dataset_name=target)
        raise ValueError(f"Unsupported platform: {platform}")

    def verify_evaluator_binding(
        self,
        platform: str,
        *,
        binding_id: str,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_platform = _normalize_platform(platform)
        if normalized_platform == "promptfoo":
            path = self._resolve_repo_path(target)
            if path is None or not path.exists():
                raise FileNotFoundError(f"Promptfoo config not found within the repository: {target}")
            return PromptfooReader().verify_evaluator_binding(path, binding_id)
        if normalized_platform == "langsmith":
            reader = LangSmithReader(api_key=self._require_env("langsmith"))
            return reader.verify_evaluator_binding(binding_id, dataset_name=target or None, dataset_id=dataset_id)
        if normalized_platform == "braintrust":
            candidate = next(
                (
                    item
                    for item in self._discover_braintrust_repo_evaluator_bindings(project=project, dataset_name=target, sample_cases=[])
                    if item.binding_id == binding_id
                ),
                None,
            )
            return {
                "platform": "braintrust",
                "binding_id": binding_id,
                "verified": candidate is not None,
                "verification_status": "verified" if candidate is not None else "unverified",
                "binding_location": candidate.binding_location if candidate is not None else None,
            }
        if normalized_platform == "arize_phoenix":
            reader = PhoenixReader(
                base_url=self.config.platforms.arize_phoenix.base_url if self.config.platforms.arize_phoenix else None,
                api_key=self._require_env("arize_phoenix"),
            )
            return reader.verify_evaluator_binding(binding_id, dataset_name=target)
        raise ValueError(f"Unsupported platform: {platform}")

    def discover_repo_eval_assets(
        self,
        query: str = "",
        *,
        globs: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        self.retrieval.record_repo_asset_discovery()
        candidates = self._discover_repo_eval_assets(query=query.strip(), globs=globs, limit=limit)
        return {
            "query": query.strip(),
            "count": len(candidates),
            "candidates": candidates,
        }

    def list_platform_evaluator_capabilities(self, platform: str) -> dict[str, Any]:
        normalized_platform = _normalize_platform(platform)
        return {"platform": normalized_platform, **platform_evaluator_capabilities(normalized_platform)}

    def read_repo_eval_asset(self, path: str) -> dict[str, Any]:
        resolved = self._resolve_repo_path(path)
        if resolved is None or not resolved.exists():
            raise FileNotFoundError(f"Eval asset not found within the repository: {path}")
        content = resolved.read_text(encoding="utf-8")
        payload = yaml.safe_load(content) if resolved.suffix.lower() in {".yaml", ".yml"} else None
        summary: dict[str, Any] = {
            "path": resolved.relative_to(self.repo_root).as_posix(),
            "content": content,
            "kind": self._repo_asset_kind(resolved),
        }
        if isinstance(payload, dict):
            summary["keys"] = sorted(payload.keys())
            if isinstance(payload.get("tests"), list):
                summary["test_count"] = len(payload["tests"])
        elif summary["kind"] == "repo_eval_code_asset":
            summary["line_count"] = content.count("\n") + 1
        return summary

    def embed_batch(
        self,
        inputs: list[dict[str, str]],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> dict[str, Any]:
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
            if projected_request_cost_usd is not None and projected_request_cost_usd > remaining_budget_usd + 1e-9:
                self.embedding_spend.blocked_request_count += 1
                cached_embeddings = [plan.cached_results[item.id] for item in plan.items if item.id in plan.cached_results]
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
                        "Reuse returned cached embeddings and continue in partial analysis mode."
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
        results = classify_embeddings_against_corpus(
            candidates,
            corpus,
            duplicate_threshold=duplicate_threshold or self.config.similarity.duplicate_threshold,
            boundary_threshold=boundary_threshold or self.config.similarity.boundary_threshold,
        )
        return {"candidate_count": len(results), "results": results}

    def build_runtime_metadata(self) -> dict[str, Any]:
        return {
            "stage2_embedding_spend_cap_usd": self.embedding_spend_cap_usd,
            "retrieval": self.retrieval.model_dump(),
            "embedding": self.embedding_spend.model_dump(),
        }

    def build_recovery_state(self) -> dict[str, Any]:
        return {
            "cached_target_snapshots": [
                copy.deepcopy(self._cached_target_snapshots[target_id])
                for target_id in sorted(self._cached_target_snapshots)
            ]
        }

    def _fetch_eval_cases(
        self,
        platform: str,
        *,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
        limit: int | None = None,
        target_id: str | None = None,
    ) -> list[EvalCaseSnapshot]:
        if platform == "langsmith":
            reader = LangSmithReader(api_key=self._require_env("langsmith"))
            return reader.fetch_examples(dataset_name=target or None, dataset_id=dataset_id, limit=limit)
        if platform == "braintrust":
            if not project:
                raise ValueError("Braintrust fetch requires `project`.")
            reader = BraintrustDirectReader(
                api_key=self._require_env("braintrust"),
                org_name=self.config.platforms.braintrust.org if self.config.platforms.braintrust else None,
            )
            return reader.fetch_examples(project=project, dataset_name=target, limit=limit)
        if platform == "arize_phoenix":
            reader = PhoenixReader(
                base_url=self.config.platforms.arize_phoenix.base_url if self.config.platforms.arize_phoenix else None,
                api_key=self._require_env("arize_phoenix"),
            )
            return reader.fetch_examples(dataset_name=target, limit=limit)
        if platform == "promptfoo":
            path = self._resolve_repo_path(target)
            if path is None or not path.exists():
                raise FileNotFoundError(f"Promptfoo config not found within the repository: {target}")
            cases = PromptfooReader().fetch_examples(path)
            for case in cases:
                case.source_target_id = target_id or self._build_target_id("promptfoo", path.relative_to(self.repo_root).as_posix())
                case.target_locator = path.relative_to(self.repo_root).as_posix()
            return cases
        raise ValueError(f"Unsupported platform: {platform}")

    def _discover_repo_eval_assets(
        self,
        *,
        query: str,
        globs: list[str] | None = None,
        limit: int,
        promptfoo_only: bool = False,
    ) -> list[dict[str, Any]]:
        patterns = globs or self.config.evals.discovery.repo_asset_globs
        candidates: list[dict[str, Any]] = []
        seen_paths: set[Path] = set()
        configured_path = (
            self.config.resolve_path(self.config.platforms.promptfoo.config_path, self.repo_root)
            if self.config.platforms.promptfoo
            else None
        )
        if configured_path is not None and configured_path.exists():
            seen_paths.add(configured_path)
            if self._asset_candidate_matches(configured_path, query=query):
                candidates.append(self._repo_asset_candidate(configured_path, "configured_path"))

        for pattern in patterns:
            for path in self.repo_root.glob(pattern):
                if len(candidates) >= limit:
                    return candidates
                resolved = path.resolve()
                if resolved in seen_paths or not path.is_file() or self._should_ignore(path):
                    continue
                seen_paths.add(resolved)
                kind = self._repo_asset_kind(path)
                if promptfoo_only and kind != "promptfoo_config":
                    continue
                if kind == "generic_repo_asset":
                    continue
                if not self._asset_candidate_matches(path, query=query):
                    continue
                candidates.append(self._repo_asset_candidate(path, "path_match"))
        return candidates[:limit]

    def _asset_candidate_matches(self, path: Path, *, query: str) -> bool:
        if not query:
            return True
        normalized_query = query.lower()
        relative = path.relative_to(self.repo_root).as_posix().lower()
        return normalized_query in relative or normalized_query in path.stem.lower()

    def _discover_braintrust_repo_evaluator_bindings(
        self,
        *,
        project: str | None,
        dataset_name: str,
        sample_cases: list[EvalCaseSnapshot],
        target_id: str | None = None,
    ) -> list[EvaluatorBindingCandidate]:
        tokens = _dedupe_query_tokens(project, dataset_name, sample_cases)
        assets = self._discover_repo_eval_assets(query="", limit=50)
        candidates: list[EvaluatorBindingCandidate] = []
        for asset in assets:
            if asset.get("kind") != "repo_eval_code_asset":
                continue
            asset_path = asset.get("target")
            if not isinstance(asset_path, str):
                continue
            lowered_path = asset_path.lower()
            if tokens and not any(token in lowered_path for token in tokens):
                continue
            label = Path(asset_path).stem.replace("_", " ")
            candidates.append(
                EvaluatorBindingCandidate.model_validate(
                    {
                        "binding_id": f"braintrust::repo_scorer::{asset_path}",
                        "label": f"Braintrust repo scorer `{label}`",
                        "scope": "repo_code",
                        "execution_surface": "repo_harness",
                        "source": "repo_asset",
                        "discovery_mode": "repo_formal",
                        "binding_object_id": asset_path,
                        "binding_location": asset_path,
                        "binding_status": "available",
                        "verification_status": "verified",
                        "mapping_hints": {},
                        "reusable": True,
                        "confidence": 0.85,
                        "notes": [
                            "Recovered from a repo-managed Braintrust scorer/eval asset.",
                            f"Resolved for target {target_id or dataset_name}.",
                        ],
                    }
                )
            )
        return candidates

    def _repo_asset_candidate(self, path: Path, match_reason: str) -> dict[str, Any]:
        kind = self._repo_asset_kind(path)
        return {
            "platform": "promptfoo" if kind == "promptfoo_config" else "repo_asset",
            "target": path.relative_to(self.repo_root).as_posix(),
            "dataset_id": None,
            "project": None,
            "kind": kind,
            "match_reason": match_reason,
        }

    def _repo_asset_kind(self, path: Path) -> str:
        if self._is_promptfoo_config(path):
            return "promptfoo_config"
        if self._is_eval_code_asset(path):
            return "repo_eval_code_asset"
        return "generic_repo_asset"

    def _is_promptfoo_config(self, path: Path) -> bool:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return False
        return isinstance(payload, dict) and isinstance(payload.get("tests"), list)

    def _is_eval_code_asset(self, path: Path) -> bool:
        if path.suffix.lower() not in {".py", ".ts", ".js"}:
            return False
        lowered = path.name.lower()
        return any(token in lowered for token in ("eval", "judge", "scorer", "grader"))

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

    def _build_target_id(self, platform: str, locator: str, *, project: str | None = None) -> str:
        parts = [platform]
        if project:
            parts.append(project)
        parts.append(locator)
        return "::".join(parts)


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

    @server.tool(name="discover_eval_targets")
    def discover_eval_targets_tool(
        platform: str,
        query: str,
        project: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return toolbox.discover_eval_targets(platform, query, project=project, limit=limit)

    @server.tool(name="fetch_eval_target_snapshot")
    def fetch_eval_target_snapshot_tool(
        platform: str,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
        limit: int | None = None,
        target_id: str | None = None,
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        return toolbox.fetch_eval_target_snapshot(
            platform,
            target=target,
            project=project,
            dataset_id=dataset_id,
            limit=limit,
            target_id=target_id,
            artifact_paths=artifact_paths,
        )

    @server.tool(name="discover_target_evaluators")
    def discover_target_evaluators_tool(
        platform: str,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        return toolbox.discover_target_evaluators(
            platform,
            target=target,
            project=project,
            dataset_id=dataset_id,
            target_id=target_id,
        )

    @server.tool(name="read_evaluator_binding")
    def read_evaluator_binding_tool(
        platform: str,
        binding_id: str,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        return toolbox.read_evaluator_binding(
            platform,
            binding_id=binding_id,
            target=target,
            project=project,
            dataset_id=dataset_id,
        )

    @server.tool(name="verify_evaluator_binding")
    def verify_evaluator_binding_tool(
        platform: str,
        binding_id: str,
        target: str,
        project: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        return toolbox.verify_evaluator_binding(
            platform,
            binding_id=binding_id,
            target=target,
            project=project,
            dataset_id=dataset_id,
        )

    @server.tool(name="discover_repo_eval_assets")
    def discover_repo_eval_assets_tool(
        query: str = "",
        globs: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return toolbox.discover_repo_eval_assets(query=query, globs=globs, limit=limit)

    @server.tool(name="read_repo_eval_asset")
    def read_repo_eval_asset_tool(path: str) -> dict[str, Any]:
        return toolbox.read_repo_eval_asset(path)

    @server.tool(name="list_platform_evaluator_capabilities")
    def list_platform_evaluator_capabilities_tool(platform: str) -> dict[str, Any]:
        return toolbox.list_platform_evaluator_capabilities(platform)

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


def _dedupe_query_tokens(
    project: str | None,
    dataset_name: str | None,
    sample_cases: list[EvalCaseSnapshot],
) -> list[str]:
    tokens: list[str] = []
    for raw in [project, dataset_name]:
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized and normalized not in tokens:
                tokens.append(normalized)
    for sample in sample_cases[:5]:
        for raw in [sample.source_target_name, sample.project]:
            if isinstance(raw, str):
                normalized = raw.strip().lower()
                if normalized and normalized not in tokens:
                    tokens.append(normalized)
    return tokens


def _platform_env_name(config: ParityConfig, platform: str) -> str:
    if platform == "langsmith":
        return config.platforms.langsmith.api_key_env if config.platforms.langsmith else "LANGSMITH_API_KEY"
    if platform == "braintrust":
        return config.platforms.braintrust.api_key_env if config.platforms.braintrust else "BRAINTRUST_API_KEY"
    if platform == "arize_phoenix":
        return config.platforms.arize_phoenix.api_key_env if config.platforms.arize_phoenix else "PHOENIX_API_KEY"
    raise ValueError(f"Unsupported platform for env resolution: {platform}")
