from __future__ import annotations

import os
from typing import Any

import httpx

from probegen.errors import GithubApiError
from probegen.models import BehaviorChangeManifest, CoverageGapManifest, ProbeProposal
from probegen.models.eval_case import ConversationMessage
from probegen.models.probes import ProbeCase

PROBEGEN_COMMENT_MARKER = "<!-- probegen-comment -->"
PROBEGEN_RESULTS_MARKER = "<!-- probegen-results -->"
GITHUB_API_VERSION = "2022-11-28"


def _format_probe_input_for_display(probe: "ProbeCase") -> str:  # type: ignore
    """Format full probe input for display in details section."""
    if probe.input_format == "conversation" and probe.input:
        import json

        messages = [
            item.model_dump() if isinstance(item, ConversationMessage) else item for item in probe.input
        ]
        return json.dumps(messages, indent=2, ensure_ascii=False)
    else:
        return str(probe.input) if probe.input else "(empty)"


def _format_probe_details(probe: "ProbeCase", index: int) -> str:  # type: ignore
    """Format a single probe's full details for collapsible section."""
    lines = [
        f"<details>",
        f"<summary><strong>📋 Probe #{index} — {probe.probe_type}</strong></summary>",
        "",
        f"**Probe ID:** `{probe.probe_id}`  ",
        f"**Gap ID:** `{probe.gap_id}`  ",
        f"**Risk flag:** {probe.related_risk_flag}",
        "",
        "**Full Input:**",
        "```json",
        _format_probe_input_for_display(probe),
        "```",
        "",
        f"**Expected Behavior:** {probe.expected_behavior}  ",
        f"**Behavior Type:** `{probe.expected_behavior_type}`",
    ]

    if probe.rubric:
        lines.extend(["", f"**Rubric:** {probe.rubric}"])

    lines.extend(
        [
            "",
            "**Rationale:** " + probe.probe_rationale,
            "",
            "**Confidence Scores:**  ",
            f"- Specificity: {probe.specificity_confidence:.2f}  ",
            f"- Testability: {probe.testability_confidence:.2f}  ",
            f"- Realism: {probe.realism_confidence:.2f}",
        ]
    )

    if probe.nearest_existing_case_id and probe.nearest_existing_similarity is not None:
        lines.extend(
            [
                "",
                f"**Nearest similar case:** `{probe.nearest_existing_case_id}` ({probe.nearest_existing_similarity:.2f} similarity)",
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
    marker: str = PROBEGEN_COMMENT_MARKER,
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
                    return int(run_id)
            if len(runs) < 100:
                return None
            page += 1
    except httpx.HTTPError as exc:
        raise GithubApiError(f"GitHub workflow run lookup failed: {exc}") from exc
    finally:
        if should_close:
            http_client.close()


def render_pr_comment(
    proposal: ProbeProposal,
    *,
    stage1_manifest: BehaviorChangeManifest | None = None,
    stage2_manifest: CoverageGapManifest | None = None,
    updated_for_commit: str | None = None,
) -> str:
    coverage_summary = stage2_manifest.coverage_summary if stage2_manifest is not None else None
    lines = [PROBEGEN_COMMENT_MARKER]
    if updated_for_commit:
        lines.extend([f"> ⟳ Updated for commit `{updated_for_commit}`", ""])
    lines.extend(["## Probegen: Behavioral Impact Detected", ""])
    if stage1_manifest is not None and stage1_manifest.changes:
        change = stage1_manifest.changes[0]
        lines.extend(
            [
                f"**Artifact:** `{change.artifact_path}`  ",
                f"**Risk level:** {stage1_manifest.overall_risk.title()}  ",
                f"**Primary change:** {change.inferred_intent}",
                "",
                "### Behavioral Impact Summary",
            ]
        )
        lines.extend([f"- {flag}" for flag in change.unintended_risk_flags] or ["- No explicit risk flags surfaced."])
        lines.append("")

    # Warnings appear before Analysis Mode for visibility
    if stage2_manifest is not None and stage2_manifest.unmapped_artifacts:
        lines.extend(
            [
                "### Warnings",
                *[
                    f"> ⚠️ **Setup issue:** No eval dataset mapped for `{artifact}` — coverage analysis skipped for this artifact."
                    for artifact in stage2_manifest.unmapped_artifacts
                ],
                "",
            ]
        )
    elif coverage_summary is not None and coverage_summary.mode == "bootstrap":
        lines.extend(
            [
                "### Warnings",
                "> ⚠️ **Starter mode** — Running in starter mode — probes are grounded in your diff and product context. Add eval dataset mappings to unlock coverage-aware analysis.",
                "",
            ]
        )

    if coverage_summary is not None:
        lines.extend(["### Analysis Mode"])
        if coverage_summary.mode == "bootstrap":
            reason = coverage_summary.bootstrap_reason or "No existing eval corpus was available."
            extra_note = ""
            if stage2_manifest is not None and stage2_manifest.unmapped_artifacts:
                extra_note = (
                    "\n- If you have `platforms:` configured in `probegen.yaml`, verify that the corresponding"
                    " API key secret is set in your repository's GitHub Actions secrets."
                )
            lines.extend(
                [
                    f"- Starter mode: {reason}",
                    f"- Probes below are plausible starter evals grounded in the diff and available product context.{extra_note}",
                    "",
                ]
            )
        else:
            dataset_bits = [bit for bit in [coverage_summary.platform, coverage_summary.dataset] if bit]
            dataset_label = ":".join(dataset_bits) if dataset_bits else "configured coverage sources"
            lines.extend(
                [
                    f"- Coverage-aware mode using `{dataset_label}`",
                    f"- Relevant cases: {coverage_summary.total_relevant_cases}; behavior-covering cases: {coverage_summary.cases_covering_changed_behavior}; coverage ratio: {coverage_summary.coverage_ratio:.2f}",
                    "",
                ]
            )
    lines.extend(
        [
            f"### Proposed Probes ({proposal.probe_count})",
            "",
            "| # | Type | Input (truncated) | Tests |",
            "|---|---|---|---|",
        ]
    )
    for index, probe in enumerate(proposal.probes, start=1):
        if probe.input_format == "conversation" and probe.input:
            last_message = probe.input[-1]
            raw_input = (
                last_message.content
                if isinstance(last_message, ConversationMessage)
                else last_message.get("content", "")
            )
        else:
            raw_input = probe.input if probe.input_format != "conversation" else ""
        preview = str(raw_input).replace("\n", " ")
        preview = f"{preview[:57]}..." if len(preview) > 60 else preview
        lines.append(
            f"| {index} | `{probe.probe_type}` | `{preview}` | {probe.expected_behavior} |"
        )

    lines.extend(
        [
            "",
            "> 💡 **Click _Full Details_ below to review each probe's rationale, confidence scores, and full input.**",
            "",
        ]
    )

    for index, probe in enumerate(proposal.probes, start=1):
        lines.append(_format_probe_details(probe, index))
        lines.append("")

    lines.extend(
        [
            "**To approve all probes:** Add label `probegen:approve` to this PR **before merging**.  ",
            f"**Full proposal + rationale:** `{proposal.export_formats.raw_json or '.probegen/ProbeProposal.json'}`  ",
            f"**Promptfoo export:** `{proposal.export_formats.promptfoo or '.probegen/probes.yaml'}`",
        ]
    )
    return "\n".join(lines)


def render_results_comment(
    *,
    dataset_name: str | None,
    total_written: int,
    passed: int | None = None,
    failed: int | None = None,
    failures: list[dict[str, str]] | None = None,
    run_id: str | None = None,
) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    lines = [
        PROBEGEN_RESULTS_MARKER,
        "## Probegen: Probes Added + Results",
        "",
    ]
    if total_written > 0:
        lines.append(f"**{total_written} probes written to:** `{dataset_name or 'configured datasets'}`")
    else:
        lines.append("**No probes were written.**")
        if dataset_name:
            lines.append(f"**Targets attempted:** `{dataset_name}`")
        if failures and run_id and repo:
            lines.append(
                f"\n**Probe files available:** [View in Actions artifacts](https://github.com/{repo}/actions/runs/{run_id})"
            )
    if passed is not None and failed is not None:
        lines.append(f"**Auto-run completed:** {passed} passed, {failed} failed")
    if failures:
        lines.extend(["", "### Failures", "", "| Probe | Type | Failure |", "|---|---|---|"])
        lines.extend(
            [f"| {item['probe_id']} | `{item['probe_type']}` | {item['failure']} |" for item in failures]
        )
    return "\n".join(lines)
