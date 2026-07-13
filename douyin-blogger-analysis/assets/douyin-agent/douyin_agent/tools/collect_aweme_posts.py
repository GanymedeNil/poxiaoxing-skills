from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlparse

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
    _extract_sec_user_id(profile_url)
    return profile_url


def _extract_sec_user_id(profile_url: str) -> str:
    parsed = urlparse(profile_url)
    prefix = "/user/"
    if not parsed.path.startswith(prefix):
        raise ValueError("Expected a Douyin profile URL containing sec_user_id after /user/")
    encoded_sec_user_id = parsed.path[len(prefix):].split("/", 1)[0]
    sec_user_id = unquote(encoded_sec_user_id)
    if not sec_user_id:
        raise ValueError("Expected a non-empty sec_user_id in the Douyin profile URL")
    return sec_user_id


def _result_to_tool_payload(result: AwemeCollectionResult) -> dict[str, Any]:
    return result.to_dict()


async def collect_douyin_aweme_posts_async(
    profile_url: str,
    *,
    max_idle_rounds: int = 3,
    login_wait_rounds: int = 300,
    max_response_parse_retries: int = 5,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    _ = max_idle_rounds, max_response_parse_retries
    valid_url = _validate_profile_url(profile_url)
    sec_user_id = _extract_sec_user_id(valid_url)
    if progress is not None:
        progress(f"[posts] target sec_user_id={sec_user_id} profile_url={valid_url}")
    async with ChromeDevToolsClient() as browser:
        result = await collect_aweme_posts(
            browser,
            valid_url,
            sec_user_id=sec_user_id,
            login_wait_rounds=login_wait_rounds,
            progress=progress,
        )
    return _result_to_tool_payload(result)


def collect_douyin_aweme_posts(
    profile_url: str,
    max_idle_rounds: int = 3,
    login_wait_rounds: int = 300,
    max_response_parse_retries: int = 5,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Collect raw aweme_list items from a Douyin creator homepage using logged-in Chrome."""

    return asyncio.run(
        collect_douyin_aweme_posts_async(
            profile_url,
            max_idle_rounds=max_idle_rounds,
            login_wait_rounds=login_wait_rounds,
            max_response_parse_retries=max_response_parse_retries,
            progress=progress,
        )
    )
