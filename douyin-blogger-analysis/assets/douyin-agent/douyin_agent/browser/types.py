from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NetworkRequest:
    request_id: str
    url: str


class BrowserClient(Protocol):
    async def open_page(self, url: str) -> None:
        """Open the target page."""

    async def list_network_requests(self) -> list[NetworkRequest]:
        """Return network requests visible to the current page."""

    async def get_network_response(self, request_id: str) -> str:
        """Return the response body for a network request."""

    async def scroll_down(self) -> None:
        """Scroll the page to trigger more lazy-loaded requests."""

    async def wait_for_network_idle_or_delay(self) -> None:
        """Wait briefly for page/network activity after scrolling."""

    async def notify_login_required(self, profile_url: str) -> None:
        """Tell the user that manual login may be required in the opened browser."""

    async def notify_skipped_post_response(self, request_id: str, reason: str) -> None:
        """Tell the user that a matching post response was skipped."""

    async def install_response_interceptor(self) -> None:
        """Inject JavaScript to intercept and capture API responses in-page.

        This bypasses Chrome DevTools Protocol's response body cache,
        which can evict large response bodies under memory pressure.
        """

    async def get_captured_responses(self) -> list[dict[str, str]]:
        """Return and clear captured API responses from the page.

        Returns a list of ``{"url": ..., "body": ...}`` dicts.
        Returns an empty list if no responses have been captured or the
        interceptor is not installed.
        """
