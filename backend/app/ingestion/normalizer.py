"""Normalizer — the most important abstraction in the system.

Every connector emits a raw ``dict``; this module is the single place it becomes a canonical
``Document`` (CLAUDE.md §9, PRAGMA.md §5.2). Nothing downstream — chunker, embedder, dedup,
skills — ever imports a connector-specific type, so adding a fourth source is one new connector,
not edits to every stage (O(1), not O(N×stages)).

Text is reduced to embedding-quality plaintext with the stdlib + regex (no markdown/HTML
dependency): strip HTML tags/entities, strip common Markdown syntax, collapse whitespace. Slack
is decoded from its own ``mrkdwn`` first (``<@U…>`` mentions, ``<url|label>`` links, its three HTML
escapes); Notion needs no stripping — block ``plain_text`` is already clean. Underscores are left
untouched on purpose: technical content is full of ``some_var`` / ``__init__``, and mangling those
would hurt search more than leftover ``_italic_`` markers ever could.

Bot/CI noise is dropped per source. A payload that has no human content left after filtering
raises ``SkipDocument`` (never a half-empty ``Document``) — the Phase-2 ingest task will catch it
to skip cleanly.
"""

from __future__ import annotations

import html
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.models.document import Document, compute_content_hash, compute_document_id

if TYPE_CHECKING:
    from app.providers.llm import LLMClient


class SkipDocument(Exception):
    """Raw payload yielded no ingestable human content (all-bot thread, empty page)."""


# --------------------------------------------------------------------------------------------
# Text cleaning (pure, stdlib + regex)
# --------------------------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Drop HTML tags, then decode entities (tags first, so escaped ``&lt;b&gt;`` survives)."""
    return html.unescape(_HTML_TAG_RE.sub("", text))


def _strip_markdown(text: str) -> str:
    """Remove common Markdown syntax, keeping the words. Deliberately leaves ``_`` alone."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)  # ![alt](url) -> alt
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [label](url) -> label
    text = re.sub(r"```[^\n]*\n?", "", text)  # ``` fences
    text = re.sub(r"`([^`]*)`", r"\1", text)  # `inline code`
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)  # # headings
    text = re.sub(r"(?m)^\s{0,3}>\s?", "", text)  # > blockquotes
    text = re.sub(r"(?m)^\s{0,3}(?:[-*+]|\d+\.)\s+", "", text)  # list bullets
    text = re.sub(r"(\*\*|\*|~~)(.+?)\1", r"\2", text)  # **b** *i* ~~s~~ (not _)
    return text


def _collapse_ws(text: str) -> str:
    """Collapse intra-line whitespace, trim each line, cap consecutive blank lines at one."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(?m)^ +| +$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean(text: str) -> str:
    """Generic cleaner for HTML/Markdown bodies (GitHub). HTML -> Markdown -> whitespace."""
    return _collapse_ws(_strip_markdown(_strip_html(text)))


_SLACK_USER_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|([^>]+))?>")
_SLACK_CHAN_RE = re.compile(r"<#(C[A-Z0-9]+)(?:\|([^>]+))?>")
_SLACK_LINK_RE = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")
_SLACK_CMD_RE = re.compile(r"<!(\w+)(?:\^[^|>]*)?(?:\|([^>]+))?>")


def _decode_slack_mrkdwn(text: str) -> str:
    """Turn Slack ``mrkdwn`` tokens into readable text, then decode its three HTML escapes.

    Runs before any HTML stripping so decoded ``&lt;``/``&gt;`` (literal ``<``/``>`` a user typed)
    can't be mistaken for tags. That's also why the Slack path never calls ``_strip_html``.
    """
    text = _SLACK_USER_RE.sub(lambda m: "@" + (m.group(2) or m.group(1)), text)
    text = _SLACK_CHAN_RE.sub(lambda m: "#" + (m.group(2) or m.group(1)), text)
    text = _SLACK_LINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = _SLACK_CMD_RE.sub(lambda m: "@" + (m.group(2) or m.group(1)), text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _clean_slack(text: str) -> str:
    """Slack message -> plaintext: decode mrkdwn, strip markdown, collapse (no HTML stripping)."""
    return _collapse_ws(_strip_markdown(_decode_slack_mrkdwn(text)))


# --------------------------------------------------------------------------------------------
# Bot / CI filtering
# --------------------------------------------------------------------------------------------

_SLACK_BOT_SUBTYPES = {
    "bot_message",
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
    "channel_archive",
}


def _is_bot_message(msg: dict) -> bool:
    return bool(msg.get("bot_id")) or msg.get("subtype") in _SLACK_BOT_SUBTYPES


def _is_bot_author(user: dict | None) -> bool:
    user = user or {}
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


# --------------------------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------------------------


def _trace_id(raw: dict) -> str:
    """Prefer the connector's trace_id; generate one if absent so the id exists from ingest."""
    return raw.get("trace_id") or uuid.uuid4().hex


def _slack_ts_to_dt(ts: str | float | int) -> datetime:
    return datetime.fromtimestamp(float(ts or 0), tz=UTC)


def _iso_to_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(tz=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _slack_author(msg: dict) -> str:
    return msg.get("username") or msg.get("user") or "unknown"


# --------------------------------------------------------------------------------------------
# Per-source normalizers  (raw dict -> Document)
# --------------------------------------------------------------------------------------------


def _normalize_slack(raw: dict) -> Document:
    messages = raw.get("messages", [])
    human = sorted(
        (m for m in messages if not _is_bot_message(m)),
        key=lambda m: float(m.get("ts") or 0),
    )

    turns: list[str] = []
    participants: list[str] = []
    for m in human:
        clean = _clean_slack((m.get("text") or "").strip())
        if not clean:
            continue
        author = _slack_author(m)
        turns.append(f"{author}: {clean}")
        if author not in participants:
            participants.append(author)

    content = "\n".join(turns).strip()
    if not content:
        raise SkipDocument("slack thread has no human content after filtering")

    source_id = str(raw.get("thread_ts") or human[0].get("ts") or "")
    reactions = [r for m in human for r in (m.get("reactions") or [])]
    metadata = {"channel": (raw.get("channel") or {}).get("name"), "reactions": reactions}

    return Document(
        id=compute_document_id("slack", source_id),
        source="slack",
        doc_type="thread",
        title="",  # threads have no natural title -> ensure_title fills it
        content=content,
        author=participants[0],
        participants=participants,
        created_at=_slack_ts_to_dt(human[0].get("ts")),
        updated_at=_slack_ts_to_dt(human[-1].get("ts")),
        url=raw.get("permalink", ""),
        content_hash=compute_content_hash(content),
        metadata=metadata,
        trace_id=_trace_id(raw),
    )


def _github_source_id(raw: dict) -> str:
    # html_url is globally unique; number alone repeats across repos.
    return str(raw.get("html_url") or raw.get("node_id") or raw.get("number", ""))


def _normalize_github(raw: dict) -> Document:
    # GitHub includes a `pull_request` object only on PRs — key presence, not truthiness
    # (a populated-but-checked-empty dict must still classify as a PR).
    doc_type = "pr" if raw.get("pull_request") is not None else "issue"
    title = (raw.get("title") or "").strip()

    user = raw.get("user") or {}
    author = user.get("login") or "unknown"

    parts: list[str] = []
    participants: list[str] = []

    if not _is_bot_author(user):
        participants.append(author)
        body = _clean(raw.get("body") or "")
        header = f"{title}\n{body}".strip() if body else title
        if header:
            parts.append(f"{author}: {header}")

    for c in raw.get("comments", []):
        cu = c.get("user") or {}
        if _is_bot_author(cu):
            continue
        clogin = cu.get("login") or "unknown"
        ctext = _clean(c.get("body") or "")
        if ctext:
            parts.append(f"{clogin}: {ctext}")
            if clogin not in participants:
                participants.append(clogin)

    approvals: list[dict] = []
    for r in raw.get("reviews", []):
        ru = r.get("user") or {}
        if _is_bot_author(ru):
            continue
        rlogin = ru.get("login") or "unknown"
        if r.get("state"):
            approvals.append({"user": rlogin, "state": r["state"]})
        rtext = _clean(r.get("body") or "")
        if rtext:
            parts.append(f"{rlogin} (review): {rtext}")
        if rlogin not in participants:
            participants.append(rlogin)

    content = "\n\n".join(parts).strip()
    if not content:
        raise SkipDocument("github item has no human content after filtering")

    metadata: dict = {}
    if approvals:
        metadata["review_approvals"] = approvals

    return Document(
        id=compute_document_id("github", _github_source_id(raw)),
        source="github",
        doc_type=doc_type,
        title=title,  # GitHub always has a title -> ensure_title is a no-op
        content=content,
        author=author,
        participants=participants,
        created_at=_iso_to_dt(raw.get("created_at")),
        updated_at=_iso_to_dt(raw.get("updated_at")),
        url=raw.get("html_url", ""),
        content_hash=compute_content_hash(content),
        metadata=metadata,
        trace_id=_trace_id(raw),
    )


def _walk_blocks(blocks: list[dict], out: list[str]) -> list[str]:
    """Depth-first over the Notion block tree, collecting each block's ``rich_text`` in order."""
    for b in blocks:
        btype = b.get("type")
        payload = b.get(btype, {}) if btype else {}
        text = "".join(rt.get("plain_text", "") for rt in payload.get("rich_text", [])).strip()
        if text:
            out.append(text)
        if b.get("children"):
            _walk_blocks(b["children"], out)
    return out


def _notion_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", [])).strip()
    return ""


def _normalize_notion(raw: dict) -> Document:
    page = raw.get("page") or {}
    content = _collapse_ws("\n".join(_walk_blocks(raw.get("blocks", []), [])))
    if not content:
        raise SkipDocument("notion page has no text content")

    created_by = page.get("created_by") or {}
    author = created_by.get("name") or created_by.get("id") or "unknown"

    return Document(
        id=compute_document_id("notion", str(page.get("id") or "")),
        source="notion",
        doc_type="page",
        title=_notion_title(page),  # may be "" -> ensure_title fills it
        content=content,
        author=author,
        participants=[author] if author != "unknown" else [],
        created_at=_iso_to_dt(page.get("created_time")),
        updated_at=_iso_to_dt(page.get("last_edited_time")),
        url=page.get("url", ""),
        content_hash=compute_content_hash(content),
        metadata={},
        trace_id=_trace_id(raw),
    )


# --------------------------------------------------------------------------------------------
# Dispatch + auto-title
# --------------------------------------------------------------------------------------------

_NORMALIZERS: dict[str, Callable[[dict], Document]] = {
    "slack": _normalize_slack,
    "github": _normalize_github,
    "notion": _normalize_notion,
}


async def normalize(raw: dict, source: str) -> Document:
    """Turn one raw source object into a ``Document``. Raises ``SkipDocument`` if it's all noise.

    Async for pipeline uniformity even though the body is pure CPU — the connector has already
    fetched everything into ``raw``; nothing here does I/O.
    """
    try:
        normalizer = _NORMALIZERS[source]
    except KeyError:
        raise ValueError(
            f"unknown source: {source!r} (expected one of {sorted(_NORMALIZERS)})"
        ) from None
    return normalizer(raw)


_TITLE_CACHE: dict[str, str] = {}


async def ensure_title(doc: Document, llm: LLMClient) -> Document:
    """Fill an LLM-generated title only when the source gave none. Cached by ``content_hash``.

    Keying the cache on the content fingerprint is exactly "never re-extract unless content
    changes" (PRAGMA.md §5.2) — a re-synced doc with the same content never re-hits the LLM.
    """
    if doc.title.strip():
        return doc

    title = _TITLE_CACHE.get(doc.content_hash)
    if title is None:
        prompt = "In 8 words or fewer, what is this about: " + doc.content[:500]
        title = (await llm.complete(prompt)).strip()
        _TITLE_CACHE[doc.content_hash] = title

    return doc.model_copy(update={"title": title})
