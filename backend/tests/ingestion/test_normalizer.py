from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.ingestion import normalizer as norm
from app.ingestion.normalizer import (
    SkipDocument,
    _collapse_ws,
    _decode_slack_mrkdwn,
    _strip_html,
    _strip_markdown,
    ensure_title,
    normalize,
)
from app.models.document import compute_document_id

# --------------------------------------------------------------------------------------------
# Recorded raw payloads (shapes the connectors will emit)
# --------------------------------------------------------------------------------------------


def _slack_raw() -> dict:
    # Deliberately out of order + a bot message wedged in the middle.
    return {
        "thread_ts": "1700000000.000100",
        "permalink": "https://acme.slack.com/archives/C1/p1700000000000100",
        "channel": {"name": "support"},
        "messages": [
            {
                "user": "bob",
                "text": "Try clearing the cache <@U9|alice> &amp; retry",
                "ts": "1700000200.000000",
                "reactions": [{"name": "+1", "count": 2}],
            },
            {
                "bot_id": "B123",
                "subtype": "bot_message",
                "text": "CI build passed",
                "ts": "1700000100.000000",
            },
            {
                "user": "alice",
                "text": "The *webhook* returns 500 on `retry`",
                "ts": "1700000000.000100",
            },
        ],
    }


def _github_pr_raw() -> dict:
    return {
        "number": 82,
        "title": "Webhook returns 500",
        "body": "The endpoint **fails** with a 500 on retry.",
        "pull_request": {"url": "https://api.github.com/repos/acme/api/pulls/82"},
        "html_url": "https://github.com/acme/api/pull/82",
        "user": {"login": "alice", "type": "User"},
        "created_at": "2026-06-01T12:00:00Z",
        "updated_at": "2026-06-02T08:00:00Z",
        "comments": [
            {"user": {"login": "dependabot[bot]", "type": "Bot"}, "body": "Bump lib to 2.0"},
            {"user": {"login": "bob", "type": "User"}, "body": "Confirmed, reproduced locally."},
        ],
        "reviews": [
            {"user": {"login": "carol", "type": "User"}, "body": "LGTM", "state": "APPROVED"},
        ],
    }


def _github_issue_raw() -> dict:
    return {
        "number": 9,
        "title": "Docs typo",
        "body": "Fix the typo in the README.",
        "html_url": "https://github.com/acme/api/issues/9",
        "user": {"login": "dana", "type": "User"},
        "created_at": "2026-06-01T12:00:00Z",
        "updated_at": "2026-06-01T12:00:00Z",
    }


def _notion_raw() -> dict:
    return {
        "page": {
            "id": "page-abc",
            "url": "https://notion.so/page-abc",
            "created_time": "2026-05-01T00:00:00Z",
            "last_edited_time": "2026-05-02T00:00:00Z",
            "created_by": {"name": "Dana"},
            "properties": {"Name": {"type": "title", "title": [{"plain_text": "Refund Policy"}]}},
        },
        "blocks": [
            {
                "type": "paragraph",
                "paragraph": {"rich_text": [{"plain_text": "Refunds within 30 days."}]},
                "children": [
                    {
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [{"plain_text": "Annual plans are prorated."}]
                        },
                    }
                ],
            },
            {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Exceptions"}]}},
        ],
    }


# --------------------------------------------------------------------------------------------
# Slack
# --------------------------------------------------------------------------------------------


async def test_slack_normalizes_to_expected_document():
    doc = await normalize(_slack_raw(), "slack")

    assert doc.source == "slack"
    assert doc.doc_type == "thread"
    assert doc.id == compute_document_id("slack", "1700000000.000100")
    # chronological, bot dropped, mrkdwn + entities decoded, markdown stripped:
    assert doc.content == (
        "alice: The webhook returns 500 on retry\nbob: Try clearing the cache @alice & retry"
    )
    assert doc.participants == ["alice", "bob"]
    assert doc.author == "alice"
    assert doc.url == "https://acme.slack.com/archives/C1/p1700000000000100"
    assert doc.title == ""  # no natural title -> ensure_title fills it later
    assert doc.metadata["channel"] == "support"
    assert doc.metadata["reactions"] == [{"name": "+1", "count": 2}]


async def test_slack_drops_bot_messages():
    doc = await normalize(_slack_raw(), "slack")
    assert "CI build passed" not in doc.content


async def test_slack_reassembles_in_chronological_order():
    doc = await normalize(_slack_raw(), "slack")
    # alice's earlier message (ts …000100) precedes bob's later one (ts …200000000)
    assert doc.content.index("alice:") < doc.content.index("bob:")
    assert doc.created_at < doc.updated_at


async def test_slack_all_bot_thread_raises_skip():
    raw = {
        "messages": [
            {"bot_id": "B1", "text": "deploy started", "ts": "1"},
            {"subtype": "bot_message", "text": "deploy finished", "ts": "2"},
        ]
    }
    with pytest.raises(SkipDocument):
        await normalize(raw, "slack")


# --------------------------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------------------------


async def test_github_pr_concatenates_with_attribution():
    doc = await normalize(_github_pr_raw(), "github")

    assert doc.doc_type == "pr"
    assert doc.title == "Webhook returns 500"
    assert doc.author == "alice"
    assert doc.url == "https://github.com/acme/api/pull/82"
    # title + body + human comment + review, each attributed:
    assert "alice: Webhook returns 500" in doc.content
    assert "The endpoint fails with a 500 on retry." in doc.content  # ** stripped
    assert "bob: Confirmed, reproduced locally." in doc.content
    assert "carol (review): LGTM" in doc.content
    assert doc.participants == ["alice", "bob", "carol"]
    assert doc.metadata["review_approvals"] == [{"user": "carol", "state": "APPROVED"}]


async def test_github_drops_bot_authored_comments():
    doc = await normalize(_github_pr_raw(), "github")
    assert "dependabot" not in doc.content
    assert "Bump lib" not in doc.content


async def test_github_issue_is_classified_as_issue():
    doc = await normalize(_github_issue_raw(), "github")
    assert doc.doc_type == "issue"
    assert doc.title == "Docs typo"


# --------------------------------------------------------------------------------------------
# Notion
# --------------------------------------------------------------------------------------------


async def test_notion_recurses_blocks_in_document_order():
    doc = await normalize(_notion_raw(), "notion")

    assert doc.doc_type == "page"
    assert doc.title == "Refund Policy"
    assert doc.author == "Dana"
    assert doc.url == "https://notion.so/page-abc"
    assert doc.content == ("Refunds within 30 days.\nAnnual plans are prorated.\nExceptions")
    # child block appears after its parent, heading last:
    assert doc.content.index("Refunds") < doc.content.index("Annual plans")
    assert doc.content.index("Annual plans") < doc.content.index("Exceptions")


# --------------------------------------------------------------------------------------------
# Cleaning units
# --------------------------------------------------------------------------------------------


def test_decode_slack_mrkdwn():
    assert _decode_slack_mrkdwn("<@U1|bob>") == "@bob"
    assert _decode_slack_mrkdwn("<@U1>") == "@U1"
    assert _decode_slack_mrkdwn("<#C1|general>") == "#general"
    assert _decode_slack_mrkdwn("<https://x.com|site>") == "site"
    assert _decode_slack_mrkdwn("<https://x.com>") == "https://x.com"
    assert _decode_slack_mrkdwn("a &amp; b &lt;c&gt;") == "a & b <c>"


def test_strip_markdown_leaves_underscores_intact():
    assert _strip_markdown("**bold**") == "bold"
    assert _strip_markdown("*it*") == "it"
    assert _strip_markdown("~~gone~~") == "gone"
    assert _strip_markdown("[label](http://u)") == "label"
    # identifiers must survive — underscores are deliberately untouched:
    assert "__init__" in _strip_markdown("call __init__ here")
    assert "some_var" in _strip_markdown("the some_var value")


def test_strip_html():
    assert _strip_html("<p>hi <b>there</b></p>") == "hi there"
    assert _strip_html("a &amp; b") == "a & b"


def test_collapse_ws():
    assert _collapse_ws("a   b\t c") == "a b c"
    assert _collapse_ws("x\n\n\n\ny") == "x\n\ny"


# --------------------------------------------------------------------------------------------
# Dispatch + auto-title
# --------------------------------------------------------------------------------------------


async def test_normalize_unknown_source_raises_valueerror():
    with pytest.raises(ValueError, match="unknown source"):
        await normalize({}, "linear")


async def test_ensure_title_no_llm_call_when_title_present():
    doc = await normalize(_github_pr_raw(), "github")  # GitHub always has a title
    llm = AsyncMock()

    out = await ensure_title(doc, llm)

    assert out.title == "Webhook returns 500"
    llm.complete.assert_not_called()


async def test_ensure_title_generates_and_caches_on_content_hash():
    norm._TITLE_CACHE.clear()
    doc = await normalize(_slack_raw(), "slack")  # title == ""
    llm = AsyncMock()
    llm.complete.return_value = "Webhook 500 on retry"

    out1 = await ensure_title(doc, llm)
    out2 = await ensure_title(doc, llm)  # same content_hash -> cache hit

    assert out1.title == "Webhook 500 on retry"
    assert out2.title == "Webhook 500 on retry"
    assert llm.complete.call_count == 1  # LLM invoked once, not twice
