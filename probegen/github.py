from __future__ import annotations

from typing import Any

import httpx

from probegen.errors import GithubApiError
from probegen.models import BehaviorChangeManifest, CoverageGapManifest, ProbeProposal
from probegen.models.eval_case import ConversationMessage

PROBEGEN_COMMENT_MARKER = "<!-- probegen-comment -->"
PROBEGEN_RESULTS_MARKER = "<!-- probegen-results -->"
GITHUB_API_VERSION = "2022-11-28"


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


def render_pr_comment(
    proposal: ProbeProposal,
    *,
    stage1_manifest: BehaviorChangeManifest | None = None,
    stage2_manifest: CoverageGapManifest | None = None,
    updated_for_commit: str | None = None,
) -> str:
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
    if stage2_manifest is not None and stage2_manifest.unmapped_artifacts:
        lines.extend(
            [
                "### Warnings",
                *[
                    f"- No eval dataset mapped for `{artifact}`; coverage analysis may be incomplete."
                    for artifact in stage2_manifest.unmapped_artifacts
                ],
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
        if probe.input_format == "conversation":
            last_message = probe.input[-1]
            raw_input = (
                last_message.content
                if isinstance(last_message, ConversationMessage)
                else last_message.get("content", "")
            )
        else:
            raw_input = probe.input
        preview = str(raw_input).replace("\n", " ")
        preview = f"{preview[:57]}..." if len(preview) > 60 else preview
        lines.append(
            f"| {index} | `{probe.probe_type}` | `{preview}` | {probe.expected_behavior} |"
        )
    lines.extend(
        [
            "",
            "**To approve all probes:** Add label `probegen:approve` to this PR.  ",
            f"**Full proposal + rationale:** `{proposal.export_formats.raw_json or '.probegen/ProbeProposal.json'}`  ",
            f"**Promptfoo export:** `{proposal.export_formats.promptfoo or '.probegen/probes.yaml'}`",
        ]
    )
    return "\n".join(lines)


def render_results_comment(
    *,
    dataset_name: str,
    total_written: int,
    passed: int | None = None,
    failed: int | None = None,
    failures: list[dict[str, str]] | None = None,
) -> str:
    lines = [
        PROBEGEN_RESULTS_MARKER,
        "## Probegen: Probes Added + Results",
        "",
        f"**{total_written} probes written to:** `{dataset_name}`",
    ]
    if passed is not None and failed is not None:
        lines.append(f"**Auto-run completed:** {passed} passed, {failed} failed")
    if failures:
        lines.extend(["", "### Failures", "", "| Probe | Type | Failure |", "|---|---|---|"])
        lines.extend(
            [f"| {item['probe_id']} | `{item['probe_type']}` | {item['failure']} |" for item in failures]
        )
    return "\n".join(lines)
