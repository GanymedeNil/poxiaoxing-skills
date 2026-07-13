from __future__ import annotations

from copy import deepcopy
from typing import Any

from douyin_agent.browser.types import BrowserClient


_PAGE_SIZE = 20


def _comment_id(comment: dict[str, Any]) -> str | None:
    value = comment.get("cid") or comment.get("comment_id")
    return str(value) if value is not None else None


def _count_for_page(limit: int, seen: int) -> int:
    if limit == 0:
        return _PAGE_SIZE
    return min(_PAGE_SIZE, max(0, limit - seen))


async def _wait_for_axios(browser: BrowserClient, modal_url: str, login_wait_rounds: int) -> None:
    await browser.open_page(modal_url)
    for wait_index in range(login_wait_rounds + 1):
        if await browser.has_axios_instance():
            return
        if wait_index == 0:
            await browser.notify_login_required(modal_url)
        if wait_index == login_wait_rounds:
            raise RuntimeError("window.axiosInstance is unavailable for comment collection")
        await browser.wait_for_network_idle_or_delay()


async def _collect_replies(
    browser: BrowserClient,
    *,
    comment_id: str,
    aweme_id: str,
    reply_limit: int,
    existing_replies: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, str]:
    known_ids = {_comment_id(reply) for reply in existing_replies}
    known_ids.discard(None)
    replies = existing_replies
    cursor: int | str = 0
    seen = 0
    new_replies = 0

    while True:
        count = _count_for_page(reply_limit, seen)
        if count == 0:
            return replies, new_replies, "limit_reached"
        payload = await browser.request_aweme_comment_replies(
            comment_id=comment_id,
            item_id=aweme_id,
            cursor=cursor,
            count=count,
        )
        page = payload.get("comments")
        if not isinstance(page, list):
            raise ValueError("Douyin reply response comments is not a list")

        page_new = 0
        for reply in page[:count]:
            if not isinstance(reply, dict):
                continue
            seen += 1
            reply_id = _comment_id(reply)
            if reply_id is not None and reply_id not in known_ids:
                replies.append(deepcopy(reply))
                known_ids.add(reply_id)
                page_new += 1
                new_replies += 1

        if seen >= reply_limit and reply_limit != 0:
            return replies, new_replies, "limit_reached"
        if page and page_new == 0:
            return replies, new_replies, "known_page"
        if payload.get("has_more") == 0:
            return replies, new_replies, "has_more_0"

        next_cursor = payload.get("cursor")
        if next_cursor is None:
            return replies, new_replies, "missing_cursor"
        if next_cursor == cursor:
            return replies, new_replies, "cursor_stalled"
        cursor = next_cursor


async def collect_aweme_comments(
    browser: BrowserClient,
    modal_url: str,
    aweme_id: str,
    *,
    comment_limit: int = 20,
    reply_limit: int = 20,
    existing_comments: list[dict[str, Any]] | None = None,
    login_wait_rounds: int = 300,
) -> dict[str, Any]:
    if comment_limit < 0 or reply_limit < 0:
        raise ValueError("Comment limits must be greater than or equal to 0")

    await _wait_for_axios(browser, modal_url, login_wait_rounds)
    comments = deepcopy(existing_comments or [])
    records_by_id = {
        comment_id: record
        for record in comments
        if isinstance(record, dict)
        and isinstance(record.get("comment"), dict)
        and (comment_id := _comment_id(record["comment"])) is not None
    }

    cursor: int | str = 0
    seen = 0
    new_comments = 0
    new_replies = 0
    reply_termination_reasons: dict[str, str] = {}

    while True:
        count = _count_for_page(comment_limit, seen)
        if count == 0:
            termination_reason = "limit_reached"
            break
        payload = await browser.request_aweme_comments(aweme_id, cursor=cursor, count=count)
        page = payload.get("comments")
        if not isinstance(page, list):
            raise ValueError("Douyin comment response comments is not a list")

        page_new = 0
        for comment in page[:count]:
            if not isinstance(comment, dict):
                continue
            seen += 1
            comment_id = _comment_id(comment)
            if comment_id is None:
                continue

            record = records_by_id.get(comment_id)
            if record is None:
                record = {"comment": deepcopy(comment), "replies": []}
                comments.append(record)
                records_by_id[comment_id] = record
                page_new += 1
                new_comments += 1

            if int(comment.get("reply_comment_total", 0) or 0) > 0:
                existing_replies = record.get("replies")
                if not isinstance(existing_replies, list):
                    existing_replies = []
                    record["replies"] = existing_replies
                replies, added, reply_reason = await _collect_replies(
                    browser,
                    comment_id=comment_id,
                    aweme_id=aweme_id,
                    reply_limit=reply_limit,
                    existing_replies=existing_replies,
                )
                record["replies"] = replies
                new_replies += added
                reply_termination_reasons[comment_id] = reply_reason

        if seen >= comment_limit and comment_limit != 0:
            termination_reason = "limit_reached"
            break
        if page and page_new == 0:
            termination_reason = "known_page"
            break
        if payload.get("has_more") == 0:
            termination_reason = "has_more_0"
            break

        next_cursor = payload.get("cursor")
        if next_cursor is None:
            termination_reason = "missing_cursor"
            break
        if next_cursor == cursor:
            termination_reason = "cursor_stalled"
            break
        cursor = next_cursor

    return {
        "comments": comments,
        "summary": {
            "new_comments": new_comments,
            "new_replies": new_replies,
            "comments_scanned": seen,
            "comments_termination_reason": termination_reason,
            "reply_termination_reasons": reply_termination_reasons,
        },
    }
