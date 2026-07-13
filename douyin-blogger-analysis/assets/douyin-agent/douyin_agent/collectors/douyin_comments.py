from __future__ import annotations

from collections.abc import Callable
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


def _emit_progress(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _limit_label(limit: int) -> str:
    return "all" if limit == 0 else str(limit)


async def _wait_for_axios(
    browser: BrowserClient,
    modal_url: str,
    login_wait_rounds: int,
    *,
    progress: Callable[[str], None] | None = None,
) -> None:
    _emit_progress(progress, f"[comments] opening {modal_url}")
    await browser.open_page(modal_url)
    for wait_index in range(login_wait_rounds + 1):
        if await browser.has_axios_instance():
            _emit_progress(progress, "[comments] browser ready")
            return
        if wait_index == 0:
            await browser.notify_login_required(modal_url)
            _emit_progress(progress, "[comments] waiting for Douyin login/page initialization")
        elif wait_index % 10 == 0:
            _emit_progress(
                progress,
                f"[comments] still waiting for page initialization round={wait_index}/{login_wait_rounds}",
            )
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
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], int, str]:
    known_ids = {_comment_id(reply) for reply in existing_replies}
    known_ids.discard(None)
    replies = existing_replies
    cursor: int | str = 0
    seen = 0
    new_replies = 0
    page_number = 0

    if reply_limit == 0:
        return replies, new_replies, "skipped"

    def finish(reason: str) -> tuple[list[dict[str, Any]], int, str]:
        _emit_progress(
            progress,
            "[replies] done "
            f"comment_id={comment_id} scanned={seen} new={new_replies} reason={reason}",
        )
        return replies, new_replies, reason

    while True:
        count = _count_for_page(reply_limit, seen)
        if count == 0:
            return finish("limit_reached")
        page_number += 1
        _emit_progress(
            progress,
            "[replies] request "
            f"comment_id={comment_id} page={page_number} cursor={cursor} "
            f"count={count} limit={reply_limit}",
        )
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

        _emit_progress(
            progress,
            "[replies] page "
            f"comment_id={comment_id} page={page_number} items={len(page[:count])} "
            f"scanned={seen} page_new={page_new} total_new={new_replies} "
            f"has_more={payload.get('has_more')}",
        )

        if seen >= reply_limit and reply_limit != 0:
            return finish("limit_reached")
        if page and page_new == 0:
            return finish("known_page")
        if payload.get("has_more") == 0:
            return finish("has_more_0")

        next_cursor = payload.get("cursor")
        if next_cursor is None:
            return finish("missing_cursor")
        if next_cursor == cursor:
            return finish("cursor_stalled")
        cursor = next_cursor


async def collect_aweme_comments(
    browser: BrowserClient,
    modal_url: str,
    aweme_id: str,
    *,
    comment_limit: int = 100,
    reply_limit: int = 0,
    existing_comments: list[dict[str, Any]] | None = None,
    login_wait_rounds: int = 300,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if comment_limit < 0 or reply_limit < 0:
        raise ValueError("Comment limits must be greater than or equal to 0")

    await _wait_for_axios(browser, modal_url, login_wait_rounds, progress=progress)
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
    page_number = 0

    if reply_limit == 0:
        _emit_progress(progress, "[replies] skipped reply_limit=0")

    def finish(reason: str) -> str:
        _emit_progress(
            progress,
            "[comments] done "
            f"scanned={seen} new_comments={new_comments} new_replies={new_replies} reason={reason}",
        )
        return reason

    while True:
        count = _count_for_page(comment_limit, seen)
        if count == 0:
            termination_reason = finish("limit_reached")
            break
        page_number += 1
        _emit_progress(
            progress,
            "[comments] request "
            f"page={page_number} cursor={cursor} count={count} limit={_limit_label(comment_limit)}",
        )
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

            if reply_limit > 0 and int(comment.get("reply_comment_total", 0) or 0) > 0:
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
                    progress=progress,
                )
                record["replies"] = replies
                new_replies += added
                reply_termination_reasons[comment_id] = reply_reason

        _emit_progress(
            progress,
            "[comments] page "
            f"page={page_number} items={len(page[:count])} scanned={seen} "
            f"page_new={page_new} total_new_comments={new_comments} "
            f"total_new_replies={new_replies} has_more={payload.get('has_more')}",
        )

        if seen >= comment_limit and comment_limit != 0:
            termination_reason = finish("limit_reached")
            break
        if page and page_new == 0:
            termination_reason = finish("known_page")
            break
        if payload.get("has_more") == 0:
            termination_reason = finish("has_more_0")
            break

        next_cursor = payload.get("cursor")
        if next_cursor is None:
            termination_reason = finish("missing_cursor")
            break
        if next_cursor == cursor:
            termination_reason = finish("cursor_stalled")
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
