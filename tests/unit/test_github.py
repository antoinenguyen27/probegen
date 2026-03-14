from __future__ import annotations

import httpx
import respx

from probegen.github import (
    GITHUB_API_VERSION,
    PROBEGEN_COMMENT_MARKER,
    find_existing_comment,
    post_pr_comment,
    update_pr_comment,
)


@respx.mock
def test_post_pr_comment_uses_issue_comments_endpoint() -> None:
    route = respx.post("https://api.github.com/repos/org/repo/issues/142/comments").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )

    result = post_pr_comment(142, "body", "org/repo", "token")

    assert route.called
    request = route.calls[0].request
    assert request.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
    assert result["id"] == 1


@respx.mock
def test_update_pr_comment_uses_comment_endpoint() -> None:
    route = respx.patch("https://api.github.com/repos/org/repo/issues/comments/9").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )

    result = update_pr_comment(9, "body", "org/repo", "token")

    assert route.called
    assert result["id"] == 9


@respx.mock
def test_find_existing_comment_returns_marker_match() -> None:
    route = respx.get("https://api.github.com/repos/org/repo/issues/142/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "body": "unrelated"},
                {"id": 2, "body": f"{PROBEGEN_COMMENT_MARKER}\ncomment"},
            ],
        )
    )

    comment_id = find_existing_comment(142, "org/repo", "token")

    assert route.called
    assert comment_id == 2
