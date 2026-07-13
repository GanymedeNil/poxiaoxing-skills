from __future__ import annotations

from typing import Any, Protocol


class BrowserClient(Protocol):
    async def open_page(self, url: str) -> None:
        """Open the target page."""

    async def has_axios_instance(self) -> bool:
        """Return whether the current page exposes window.axiosInstance."""

    async def request_aweme_posts(
        self,
        *,
        sec_user_id: str,
        max_cursor: int | str,
        need_time_list: int,
        count: int,
    ) -> dict[str, Any]:
        """Request one Douyin post-list page inside the logged-in page."""

    async def request_aweme_comments(
        self,
        aweme_id: str,
        *,
        cursor: int | str,
        count: int,
    ) -> dict[str, Any]:
        """Request one top-level comment page for an aweme."""

    async def request_aweme_comment_replies(
        self,
        *,
        comment_id: str,
        item_id: str,
        cursor: int | str,
        count: int,
    ) -> dict[str, Any]:
        """Request one reply page for a top-level comment."""

    async def wait_for_network_idle_or_delay(self) -> None:
        """Wait briefly before checking page readiness again."""

    async def notify_login_required(self, profile_url: str) -> None:
        """Tell the user that manual login may be required in the opened browser."""
