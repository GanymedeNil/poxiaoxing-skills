from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any

from douyin_agent.browser.types import BrowserClient, NetworkRequest

POST_API_PATH = "/aweme/v1/web/aweme/post/"

# chrome-devtools-mcp returns this text when Chrome DevTools Protocol has
# evicted the response body from its cache.  Retrying is pointless.
RESPONSE_EVICTED_MARKER = "not available anymore"


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


def _is_post_request(request: NetworkRequest) -> bool:
    return POST_API_PATH in request.url


def _request_key(request: NetworkRequest) -> str:
    return request.request_id or request.url


def _parse_post_payload(request: NetworkRequest, response_body: str) -> dict[str, Any]:
    if not response_body or not response_body.strip():
        raise ValueError(
            f"Empty response body from Douyin post response {request.request_id}"
        )
    try:
        payload = json.loads(response_body)
    except JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON from Douyin post response {request.request_id}: {exc.msg}"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Douyin post response {request.request_id} is not a JSON object")

    if "aweme_list" not in payload:
        raise ValueError(f"Douyin post response {request.request_id} missing aweme_list")

    if not isinstance(payload["aweme_list"], list):
        raise ValueError(f"Douyin post response {request.request_id} aweme_list is not a list")

    return payload


async def collect_aweme_posts(
    browser: BrowserClient,
    profile_url: str,
    *,
    max_idle_rounds: int = 3,
    login_wait_rounds: int = 300,
    max_response_parse_retries: int = 5,
) -> AwemeCollectionResult:
    await browser.open_page(profile_url)

    # Install a JavaScript response interceptor to capture API responses
    # in-page before Chrome DevTools Protocol can evict them.
    await browser.install_response_interceptor()

    seen_request_keys: set[str] = set()
    aweme_list: list[dict[str, Any]] = []
    pages_seen = 0
    idle_rounds = 0
    login_waits = 0
    login_notification_sent = False
    response_parse_failures: dict[str, int] = {}
    captured_by_url: dict[str, str] = {}
    scroll_count = 0

    while True:
        # Drain captured responses from the JS interceptor (if any).
        for captured in await browser.get_captured_responses():
            url = captured.get("url", "")
            body = captured.get("body", "")
            if url and body and url not in captured_by_url:
                captured_by_url[url] = body

        requests = await browser.list_network_requests()
        new_post_requests = []
        for request in requests:
            key = _request_key(request)
            if (
                _is_post_request(request)
                and key not in seen_request_keys
                and response_parse_failures.get(key, 0) < max_response_parse_retries
            ):
                new_post_requests.append(request)

        if not new_post_requests:
            idle_rounds += 1
            if pages_seen == 0 and idle_rounds >= max_idle_rounds and login_waits < login_wait_rounds:
                if not login_notification_sent:
                    await browser.notify_login_required(profile_url)
                    login_notification_sent = True
                login_waits += 1
                await browser.wait_for_network_idle_or_delay()
                continue
            if idle_rounds >= max_idle_rounds:
                return AwemeCollectionResult(
                    profile_url=profile_url,
                    aweme_list=aweme_list,
                    summary=CollectionSummary(
                        pages_seen=pages_seen,
                        items_collected=len(aweme_list),
                        terminal_has_more=None,
                        termination_reason="idle_timeout",
                        scroll_count=scroll_count,
                    ),
                )
            await browser.scroll_down()
            scroll_count += 1
            await browser.wait_for_network_idle_or_delay()
            continue

        idle_rounds = 0

        for request in new_post_requests:
            request_key = _request_key(request)

            # Prefer the captured response from the JS interceptor (bypasses
            # Chrome DevTools Protocol cache eviction).
            response_body = captured_by_url.pop(request.url, None)
            if response_body is None:
                response_body = await browser.get_network_response(request.request_id)
            try:
                payload = _parse_post_payload(request, response_body)
            except ValueError as exc:
                response_parse_failures[request_key] = response_parse_failures.get(request_key, 0) + 1
                body_len = len(response_body) if response_body else 0
                body_preview = repr(response_body[:100]) if response_body else "(empty)"
                print(
                    f"[douyin-agent] Skipped {request.request_id}: "
                    f"body_length={body_len}, preview={body_preview}",
                    file=sys.stderr,
                )
                # If Chrome evicted the response body, retrying won't help.
                # Mark it as seen so we don't waste time on further retries.
                if response_body and RESPONSE_EVICTED_MARKER in response_body.lower():
                    seen_request_keys.add(request_key)
                    print(
                        f"[douyin-agent] Response for {request.request_id} was evicted "
                        f"from Chrome cache, skipping permanently",
                        file=sys.stderr,
                    )
                await browser.notify_skipped_post_response(request.request_id, str(exc))
                if pages_seen == 0 and not login_notification_sent:
                    await browser.notify_login_required(profile_url)
                    login_notification_sent = True
                await browser.wait_for_network_idle_or_delay()
                continue
            seen_request_keys.add(request_key)
            page_items = payload["aweme_list"]
            has_more = payload.get("has_more")

            aweme_list.extend(page_items)
            pages_seen += 1

            if has_more == 0:
                return AwemeCollectionResult(
                    profile_url=profile_url,
                    aweme_list=aweme_list,
                    summary=CollectionSummary(
                        pages_seen=pages_seen,
                        items_collected=len(aweme_list),
                        terminal_has_more=0,
                        termination_reason="has_more_0",
                        scroll_count=scroll_count,
                    ),
                )

        await browser.scroll_down()
        scroll_count += 1
        await browser.wait_for_network_idle_or_delay()
