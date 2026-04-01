from __future__ import annotations

import json
import os
from typing import Any

import httpx

from parity.config import FIXED_APPROVAL_LABEL
from parity.errors import GithubApiError
from parity.models import (
    BehaviorChangeManifest,
    EvalAnalysisManifest,
    EvalProposalManifest,
    ProbeIntent,
)
from parity.models.eval_case import ConversationMessage

PARITY_COMMENT_MARKER = "<!-- parity-comment -->"
PARITY_RESULTS_MARKER = "<!-- parity-results -->"
GITHUB_API_VERSION = "2022-11-28"


def _format_intent_input_for_display(intent: ProbeIntent) -> str:
    if intent.input_format == "conversation" and intent.input:
        messages = [item.model_dump() if isinstance(item, ConversationMessage) else item for item in intent.input]
        return json.dumps(messages, indent=2, ensure_ascii=False)
    return str(intent.input) if intent.input else "(empty)"


def _format_intent_details(intent: ProbeIntent, index: int, write_status: str, abstention_reason: str | None) -> str:
    return _format_intent_details_with_evaluator(intent, index, write_status, abstention_reason, None)


def _format_intent_details_with_evaluator(
    intent: ProbeIntent,
    index: int,
    write_status: str,
    abstention_reason: str | None,
    evaluator_plan,
) -> str:
    lines = [
        "<details>",
        f"<summary><strong>Eval #{index} — {intent.intent_type}</strong></summary>",
        "",
        f"**Intent ID:** `{intent.intent_id}`  ",
        f"**Gap ID:** `{intent.gap_id}`  ",
        f"**Target:** `{intent.target_id}`  ",
        f"**Method:** `{intent.method_kind}`  ",
        f"**Write status:** `{write_status}`  ",
        f"**Risk flag:** {intent.related_risk_flag}",
        "",
        "**Full Input:**",
        "```json",
        _format_intent_input_for_display(intent),
        "```",
        "",
        f"**Behavior under test:** {intent.behavior_under_test}  ",
        f"**Pass criteria:** {intent.pass_criteria}  ",
        f"**Failure mode:** {intent.failure_mode}",
    ]
    if evaluator_plan is not None:
        lines.extend(
            [
                "",
                f"**Evaluator linkage:** `{evaluator_plan.action}`  ",
                f"**Evaluator scope:** `{evaluator_plan.scope}`  ",
                f"**Execution surface:** `{evaluator_plan.execution_surface}`",
            ]
        )
        if evaluator_plan.binding_ref:
            lines.append(f"**Evaluator binding:** `{evaluator_plan.binding_ref}`")
    if abstention_reason:
        lines.extend(["", f"**Abstention reason:** {abstention_reason}"])
    if evaluator_plan is not None:
        lines.extend(["", f"**Evaluator rationale:** {evaluator_plan.rationale}"])
    lines.extend(
        [
            "",
            "**Rationale:** " + intent.probe_rationale,
            "",
            "**Confidence Scores:**  ",
            f"- Specificity: {intent.specificity_confidence:.2f}  ",
            f"- Testability: {intent.testability_confidence:.2f}  ",
            f"- Novelty: {intent.novelty_confidence:.2f}  ",
            f"- Realism: {intent.realism_confidence:.2f}  ",
            f"- Target fit: {intent.target_fit_confidence:.2f}",
        ]
    )
    if intent.nearest_existing_case_id and intent.nearest_existing_similarity is not None:
        lines.extend(
            [
                "",
                f"**Nearest compatible case:** `{intent.nearest_existing_case_id}` ({intent.nearest_existing_similarity:.2f} similarity)",
            ]
        )
    lines.extend(["", "</details>"])
    return "\n".join(lines)


def github_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def post_pr_comment(
    pr_number: int,
    body: str,
    repo: str,
    token: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    http_client = client or httpx.Client(timeout=30.0)
    should_close = client is None
    try:
        response = http_client.post(
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
            headers=github_headers(token),
            json={"body": body},
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise GithubApiError(f"GitHub comment post failed: {exc}") from exc
    finally:
        if should_close:
            http_client.close()


def update_pr_comment(
    comment_id: int,
    body: str,
    repo: str,
    token: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    http_client = client or httpx.Client(timeout=30.0)
    should_close = client is None
    try:
        response = http_client.patch(
            f"https://api.github.com/repos/{repo}/issues/comments/{comment_id}",
            headers=github_headers(token),
            json={"body": body},
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise GithubApiError(f"GitHub comment update failed: {exc}") from exc
    finally:
        if should_close:
            http_client.close()


def find_existing_comment(
    pr_number: int,
    repo: str,
    token: str,
    *,
    marker: str = PARITY_COMMENT_MARKER,
    client: httpx.Client | None = None,
) -> int | None:
    http_client = client or httpx.Client(timeout=30.0)
    should_close = client is None
    page = 1
    try:
        while True:
            response = http_client.get(
                f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                headers=github_headers(token),
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            comments = response.json()
            for comment in comments:
                if marker in comment.get("body", ""):
                    return int(comment["id"])
            if len(comments) < 100:
                return None
            page += 1
    except httpx.HTTPError as exc:
        raise GithubApiError(f"GitHub comment lookup failed: {exc}") from exc
    finally:
        if should_close:
            http_client.close()


def find_latest_workflow_run_id(
    repo: str,
    workflow_id: str,
    token: str,
    *,
    event: str | None = None,
    status: str | None = None,
    head_sha: str | None = None,
    branch: str | None = None,
    conclusion: str | None = "success",
    artifact_name: str | None = None,
    client: httpx.Client | None = None,
) -> int | None:
    http_client = client or httpx.Client(timeout=30.0)
    should_close = client is None
    page = 1
    try:
        while True:
            params: dict[str, Any] = {"per_page": 100, "page": page}
            if event:
                params["event"] = event
            if status:
                params["status"] = status
            if head_sha:
                params["head_sha"] = head_sha
            if branch:
                params["branch"] = branch

            response = http_client.get(
                f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_id}/runs",
                headers=github_headers(token),
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
            runs = payload.get("workflow_runs", [])
            for run in runs:
                if conclusion and run.get("conclusion") != conclusion:
                    continue
                run_id = run.get("id")
                if run_id is not None:
                    if artifact_name and not _workflow_run_has_artifact(
                        repo,
                        int(run_id),
                        artifact_name,
                        token,
                        client=http_client,
                    ):
                        continue
                    return int(run_id)
            if len(runs) < 100:
                return None
            page += 1
    except httpx.HTTPError as exc:
        raise GithubApiError(f"GitHub workflow run lookup failed: {exc}") from exc
    finally:
        if should_close:
            http_client.close()


def _workflow_run_has_artifact(
    repo: str,
    run_id: int,
    artifact_name: str,
    token: str,
    *,
    client: httpx.Client | None = None,
) -> bool:
    http_client = client or httpx.Client(timeout=30.0)
    should_close = client is None
    page = 1
    try:
        while True:
            response = http_client.get(
                f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts",
                headers=github_headers(token),
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            payload = response.json()
            artifacts = payload.get("artifacts", [])
            for artifact in artifacts:
                if artifact.get("name") == artifact_name:
                    return True
            if len(artifacts) < 100:
                return False
            page += 1
    except httpx.HTTPError as exc:
        raise GithubApiError(f"GitHub artifact lookup failed: {exc}") from exc
    finally:
        if should_close:
            http_client.close()


def render_pr_comment(
    proposal: EvalProposalManifest,
    *,
    stage1_manifest: BehaviorChangeManifest | None = None,
    stage2_manifest: EvalAnalysisManifest | None = None,
    updated_for_commit: str | None = None,
) -> str:
    target_lookup = {target.target_id: target for target in proposal.targets}
    rendering_lookup = {rendering.intent_id: rendering for rendering in proposal.renderings}
    evaluator_plan_lookup = {plan.intent_id: plan for plan in proposal.evaluator_plans}
    lines = [PARITY_COMMENT_MARKER]
    if updated_for_commit:
        lines.extend([f"> ⟳ Updated for commit `{updated_for_commit}`", ""])
    lines.extend(["## Parity: Native Evals Proposed", ""])

    if stage1_manifest is not None and stage1_manifest.changes:
        change = stage1_manifest.changes[0]
        lines.extend(
            [
                f"**Artifact:** `{change.artifact_path}`  ",
                f"**Risk level:** {stage1_manifest.overall_risk.title()}  ",
                f"**Primary change:** {change.inferred_intent}",
                "",
            ]
        )

    if stage2_manifest is not None and stage2_manifest.unresolved_artifacts:
        if stage2_manifest.analysis_status == "degraded":
            lines.extend(
                [
                    "### Warnings",
                    (
                        f"> ⚠️ Stage 2 analysis degraded before full native target resolution completed. "
                        f"{stage2_manifest.degradation_reason}"
                    ),
                    *[
                        f"> ⚠️ `{artifact}` remained unresolved when analysis degraded. Any bootstrap proposals for this artifact are provisional."
                        for artifact in stage2_manifest.unresolved_artifacts
                    ],
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "### Warnings",
                    *[
                        f"> ⚠️ No usable native eval target was discovered for `{artifact}`. Those items are proposal-only bootstrap candidates."
                        for artifact in stage2_manifest.unresolved_artifacts
                    ],
                    "",
                ]
            )
    elif stage2_manifest is not None and stage2_manifest.analysis_status == "degraded":
        lines.extend(
            [
                "### Warnings",
                (
                    f"> ⚠️ Stage 2 analysis degraded before full native target resolution completed. "
                    f"{stage2_manifest.degradation_reason}"
                ),
                "",
            ]
        )

    if proposal.warnings:
        lines.extend(["### Proposal Warnings", ""])
        lines.extend([f"> ⚠️ {warning}" for warning in proposal.warnings])
        lines.append("")

    if stage2_manifest is not None:
        lines.extend(["### Resolved Targets", ""])
        for resolved_target in stage2_manifest.resolved_targets[:8]:
            profile = resolved_target.profile
            method = resolved_target.method_profile
            lines.append(
                f"- `{profile.target_id}` → `{profile.platform}` / `{profile.target_name}` "
                f"({method.method_kind}, {method.renderability_status}, evaluator={method.evaluator_scope}, samples={len(resolved_target.samples)})"
            )
        lines.append("")
        lines.extend(["### Coverage Summary", ""])
        for summary in stage2_manifest.coverage_by_target[:8]:
            lines.append(
                f"- `{summary.target_id}`: mode={summary.mode}, method={summary.method_kind}, "
                f"coverage={summary.coverage_ratio:.2f}, profile={summary.profile_status}"
            )
        lines.append("")

    lines.extend(
        [
            f"### Proposed Evals ({proposal.intent_count})",
            "",
            "| # | Target | Method | Write | Evaluator | Behavior |",
            "|---|---|---|---|---|---|",
        ]
    )
    for index, intent in enumerate(proposal.intents, start=1):
        rendering = rendering_lookup.get(intent.intent_id)
        evaluator_plan = evaluator_plan_lookup.get(intent.intent_id)
        target = target_lookup.get(intent.target_id)
        target_label = target.target_name if target is not None else intent.target_id
        behavior = intent.behavior_under_test.replace("\n", " ")
        if len(behavior) > 80:
            behavior = f"{behavior[:77]}..."
        lines.append(
            f"| {index} | `{target_label}` | `{intent.method_kind}` | "
            f"`{rendering.write_status if rendering else 'unsupported'}` | "
            f"`{evaluator_plan.action if evaluator_plan is not None else 'manual'}` | {behavior} |"
        )

    lines.extend(
        [
            "",
            "> Expand each eval below to review the target fit, pass criteria, and any abstention reason.",
            "",
        ]
    )
    for index, intent in enumerate(proposal.intents, start=1):
        rendering = rendering_lookup.get(intent.intent_id)
        lines.append(
            _format_intent_details_with_evaluator(
                intent,
                index,
                rendering.write_status if rendering is not None else "unsupported",
                rendering.abstention_reason if rendering is not None else "No rendering was generated.",
                evaluator_plan_lookup.get(intent.intent_id),
            )
        )
        lines.append("")

    lines.extend(
        [
            f"**To approve:** Add label `{FIXED_APPROVAL_LABEL}` to this PR before merging.  ",
            "**Full proposal:** `.parity/EvalProposalManifest.json`",
        ]
    )
    return "\n".join(lines)


def render_results_comment(
    *,
    targets: str | None,
    total_written: int,
    skipped_review_only: list[str] | None = None,
    unsupported_targets: list[str] | None = None,
    failures: list[str] | None = None,
    run_id: str | None = None,
) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    lines = [PARITY_RESULTS_MARKER, "## Parity: Evals Written", ""]
    if total_written > 0:
        lines.append(f"**{total_written} evals written to:** `{targets or 'discovered targets'}`")
    else:
        lines.append("**No evals were written.**")
        if targets:
            lines.append(f"**Targets attempted:** `{targets}`")
    if skipped_review_only:
        lines.extend(["", f"**Skipped review-only targets:** {', '.join(skipped_review_only)}"])
    if unsupported_targets:
        lines.extend(["", f"**Unsupported targets:** {', '.join(unsupported_targets)}"])
    if failures:
        lines.extend(["", "### Failures", ""])
        lines.extend([f"- {failure}" for failure in failures])
    if failures and run_id and repo:
        lines.extend(
            [
                "",
                f"**Run artifacts:** [View in Actions artifacts](https://github.com/{repo}/actions/runs/{run_id})",
            ]
        )
    return "\n".join(lines)
