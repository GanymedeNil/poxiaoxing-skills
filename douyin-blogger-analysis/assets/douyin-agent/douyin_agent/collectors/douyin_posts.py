from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from douyin_agent.browser.types import BrowserClient


@dataclass(frozen=True)
class CollectionSummary:
    pages_seen: int
    items_collected: int
    terminal_has_more: int | None
    termination_reason: str
    scroll_count: int = 0


@dataclass(frozen=True)
class AwemeCollectionResult:
    profile_url: str
    aweme_list: list[dict[str, Any]]
    summary: CollectionSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_url": self.profile_url,
            "aweme_list": self.aweme_list,
            "summary": {
                "pages_seen": self.summary.pages_seen,
                "items_collected": self.summary.items_collected,
                "terminal_has_more": self.summary.terminal_has_more,
                "termination_reason": self.summary.termination_reason,
                "scroll_count": self.summary.scroll_count,
            },
        }


def _validate_post_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    aweme_list = payload.get("aweme_list")
    if not isinstance(aweme_list, list):
        raise ValueError("Douyin post response aweme_list is not a list")
    return aweme_list


def _summary(
    *,
    pages_seen: int,
    items_collected: int,
    has_more: Any,
    reason: str,
) -> CollectionSummary:
    terminal_has_more = has_more if isinstance(has_more, int) else None
    return CollectionSummary(
        pages_seen=pages_seen,
        items_collected=items_collected,
        terminal_has_more=terminal_has_more,
        termination_reason=reason,
    )


async def collect_aweme_posts(
    browser: BrowserClient,
    profile_url: str,
    *,
    sec_user_id: str,
    login_wait_rounds: int = 300,
    count: int = 18,
) -> AwemeCollectionResult:
    await browser.open_page(profile_url)
    login_notification_sent = False

    for wait_index in range(login_wait_rounds + 1):
        if await browser.has_axios_instance():
            break
        if not login_notification_sent:
            await browser.notify_login_required(profile_url)
            login_notification_sent = True
        if wait_index == login_wait_rounds:
            return AwemeCollectionResult(
                profile_url=profile_url,
                aweme_list=[],
                summary=_summary(
                    pages_seen=0,
                    items_collected=0,
                    has_more=None,
                    reason="axios_unavailable",
                ),
            )
        await browser.wait_for_network_idle_or_delay()

    aweme_list: list[dict[str, Any]] = []
    pages_seen = 0
    max_cursor: int | str = 0
    need_time_list = 1

    while True:
        payload = await browser.request_aweme_posts(
            sec_user_id=sec_user_id,
            max_cursor=max_cursor,
            need_time_list=need_time_list,
            count=count,
        )
        aweme_list.extend(_validate_post_payload(payload))
        pages_seen += 1
        has_more = payload.get("has_more")

        if has_more == 0:
            return AwemeCollectionResult(
                profile_url=profile_url,
                aweme_list=aweme_list,
                summary=_summary(
                    pages_seen=pages_seen,
                    items_collected=len(aweme_list),
                    has_more=0,
                    reason="has_more_0",
                ),
            )

        next_cursor = payload.get("max_cursor")
        if next_cursor is None:
            termination_reason = "missing_max_cursor"
        elif next_cursor == max_cursor:
            termination_reason = "cursor_stalled"
        else:
            max_cursor = next_cursor
            need_time_list = 0
            continue

        return AwemeCollectionResult(
            profile_url=profile_url,
            aweme_list=aweme_list,
            summary=_summary(
                pages_seen=pages_seen,
                items_collected=len(aweme_list),
                has_more=has_more,
                reason=termination_reason,
            ),
        )
