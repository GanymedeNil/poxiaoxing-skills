from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

from douyin_agent.browser.chrome_devtools_client import ChromeDevToolsClient
from douyin_agent.collectors.douyin_posts import AwemeCollectionResult, collect_aweme_posts


def _validate_profile_url(profile_url: str) -> str:
    parsed = urlparse(profile_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Expected a Douyin profile URL with http or https scheme")
    if not parsed.netloc.endswith("douyin.com"):
        raise ValueError("Expected a Douyin profile URL on douyin.com")
    if not parsed.path.startswith("/user/"):
        raise ValueError("Expected a Douyin profile URL path starting with /user/")
    return profile_url


def _result_to_tool_payload(result: AwemeCollectionResult) -> dict[str, Any]:
    return result.to_dict()


async def collect_douyin_aweme_posts_async(
    profile_url: str,
    *,
    max_idle_rounds: int = 3,
    login_wait_rounds: int = 300,
    max_response_parse_retries: int = 5,
) -> dict[str, Any]:
    valid_url = _validate_profile_url(profile_url)
    async with ChromeDevToolsClient() as browser:
        result = await collect_aweme_posts(
            browser,
            valid_url,
            max_idle_rounds=max_idle_rounds,
            login_wait_rounds=login_wait_rounds,
            max_response_parse_retries=max_response_parse_retries,
        )
    return _result_to_tool_payload(result)


def collect_douyin_aweme_posts(
    profile_url: str,
    max_idle_rounds: int = 3,
    login_wait_rounds: int = 300,
    max_response_parse_retries: int = 5,
) -> dict[str, Any]:
    """Collect raw aweme_list items from a Douyin creator homepage using logged-in Chrome."""

    return asyncio.run(
        collect_douyin_aweme_posts_async(
            profile_url,
            max_idle_rounds=max_idle_rounds,
            login_wait_rounds=login_wait_rounds,
            max_response_parse_retries=max_response_parse_retries,
        )
    )
