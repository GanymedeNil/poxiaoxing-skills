from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from douyin_agent.browser.types import NetworkRequest

PROFILE_DIR_ENV = "DOUYIN_AGENT_CHROME_PROFILE_DIR"


def _default_profile_dir() -> str:
    configured_dir = os.environ.get(PROFILE_DIR_ENV)
    if configured_dir:
        return configured_dir
    return str(Path.home() / ".cache" / "douyin-agent" / "chrome-profile")


def _default_mcp_args() -> list[str]:
    return [
        "-y",
        "chrome-devtools-mcp@latest",
        "--no-performance-crux",
        "--no-usage-statistics",
        "--experimental-structured-content",
        "--autoConnect"
    ]


def _extract_text_content(result: Any) -> str:
    content = getattr(result, "content", None) or []
    text_parts = [
        item.text
        for item in content
        if getattr(item, "type", None) == "text" and hasattr(item, "text")
    ]
    return "\n".join(text_parts)


def _extract_json_object(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    text = _extract_text_content(result)
    if not text:
        return {}

    stripped_text = text.strip()
    if not stripped_text.startswith(("{", "[")):
        return {"message": text}

    parsed = json.loads(stripped_text)
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}


def _parse_network_requests_text(text: str) -> list[NetworkRequest]:
    requests: list[NetworkRequest] = []
    pattern = re.compile(r"^reqid=(?P<request_id>\S+)\s+\S+\s+(?P<url>https?://\S+)", re.MULTILINE)
    for match in pattern.finditer(text):
        requests.append(
            NetworkRequest(
                request_id=match.group("request_id"),
                url=match.group("url"),
            )
        )
    return requests


def _extract_response_body_from_text(text: str) -> str | None:
    match = re.search(r"(?ms)^### Response Body\s*\n(?P<body>.*?)(?=\n### |\Z)", text)
    if match is None:
        return None
    return match.group("body").strip()


class ChromeDevToolsClient:
    def __init__(
        self,
        *,
        command: str = "npx",
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        post_scroll_delay_seconds: float = 2.0,
    ) -> None:
        self.command = command
        self.args = args or _default_mcp_args()
        self.env = env
        self.post_scroll_delay_seconds = post_scroll_delay_seconds
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> ChromeDevToolsClient:
        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env or dict(os.environ),
        )
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(server_params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("ChromeDevToolsClient must be used as an async context manager")
        return self._session

    async def open_page(self, url: str) -> None:
        await self.session.call_tool("new_page", {"url": url, "timeout": 0})

    async def list_network_requests(self) -> list[NetworkRequest]:
        result = await self.session.call_tool(
            "list_network_requests",
            {"includePreservedRequests": True},
        )
        payload = _extract_json_object(result)
        raw_requests = payload.get("networkRequests") or payload.get("requests") or []
        requests: list[NetworkRequest] = []
        for item in raw_requests:
            request_id = str(item.get("reqid") or item.get("requestId") or item.get("id") or "")
            url = str(item.get("url") or "")
            if request_id and url:
                requests.append(NetworkRequest(request_id=request_id, url=url))
        if requests:
            return requests

        text = _extract_text_content(result)
        if text:
            return _parse_network_requests_text(text)
        return requests

    async def get_network_response(self, request_id: str) -> str:
        # Save the response body to a temporary file to avoid inline
        # truncation.  chrome-devtools-mcp truncates large response bodies
        # when returning them inline (e.g. at ~10 000 chars), but writing
        # to a file preserves the full content.
        temp_fd, temp_path = tempfile.mkstemp(suffix=".network-response")
        os.close(temp_fd)
        try:
            result = await self.session.call_tool(
                "get_network_request",
                {
                    "reqid": int(request_id) if request_id.isdigit() else request_id,
                    "responseFilePath": temp_path,
                },
            )
            # Prefer the file content (full, untruncated).
            try:
                with open(temp_path, "r", encoding="utf-8") as f:
                    body = f.read()
                if body.strip():
                    return body
                print(
                    f"[douyin-agent] Empty response body file for request {request_id}",
                    file=sys.stderr,
                )
            except (FileNotFoundError, IOError):
                print(
                    f"[douyin-agent] Response body file not created for request {request_id}",
                    file=sys.stderr,
                )
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

        # Fall back to inline extraction from the same call result.
        payload = _extract_json_object(result)
        for key in ("responseBody", "response", "body"):
            value = payload.get(key)
            if isinstance(value, str):
                if not value.strip():
                    print(
                        f"[douyin-agent] Empty response body for request {request_id} "
                        f"(structured key={key})",
                        file=sys.stderr,
                    )
                return value
        network_request = payload.get("networkRequest")
        if isinstance(network_request, dict):
            value = network_request.get("responseBody")
            if isinstance(value, str):
                if not value.strip():
                    print(
                        f"[douyin-agent] Empty response body for request {request_id} "
                        f"(networkRequest.responseBody)",
                        file=sys.stderr,
                    )
                return value
        text = _extract_text_content(result)
        if text:
            response_body = _extract_response_body_from_text(text)
            if response_body is not None:
                return response_body
            print(
                f"[douyin-agent] No response body section found for request {request_id}, "
                f"returning empty string",
                file=sys.stderr,
            )
            return ""
        raise ValueError(f"No response body returned for network request {request_id}")

    async def scroll_down(self) -> None:
        result = await self.session.call_tool(
            "evaluate_script",
            {
                "function": """() => {
                    const before = window.pageYOffset || document.documentElement.scrollTop || 0;

                    // 1. Try scrolling the main window by one viewport.
                    window.scrollBy(0, window.innerHeight);

                    // 2. Find and scroll any nested scrollable containers
                    //    (Douyin often uses an inner div for scrolling).
                    let containerScrolled = false;
                    const allElements = document.querySelectorAll('div, main, section');
                    for (const el of allElements) {
                        if (el.scrollHeight <= el.clientHeight) continue;
                        if (el.clientHeight < 100) continue;
                        const style = window.getComputedStyle(el);
                        if (style.overflowY !== 'auto' && style.overflowY !== 'scroll') continue;
                        el.scrollTop = el.scrollHeight;
                        containerScrolled = true;
                    }

                    // 3. Dispatch scroll events to trigger lazy-loading observers.
                    window.dispatchEvent(new Event('scroll', { bubbles: true }));
                    document.dispatchEvent(new Event('scroll', { bubbles: true }));

                    const after = window.pageYOffset || document.documentElement.scrollTop || 0;
                    return JSON.stringify({
                        before,
                        after,
                        moved: after !== before,
                        containerScrolled,
                        bodyHeight: document.body.scrollHeight,
                        viewportHeight: window.innerHeight,
                    });
                }"""
            },
        )
        # Log the scroll result for debugging.
        text = _extract_text_content(result)
        payload = _extract_json_object(result)
        scroll_info = payload.get("result") or payload.get("value") or text or ""
        if scroll_info:
            print(
                f"[douyin-agent] Scroll: {scroll_info}",
                file=sys.stderr,
            )

    async def wait_for_network_idle_or_delay(self) -> None:
        await asyncio.sleep(self.post_scroll_delay_seconds)

    async def notify_login_required(self, profile_url: str) -> None:
        print(
            "No Douyin post API response was detected yet. "
            "If the opened Chrome window asks for login, finish logging into Douyin there. "
            f"Waiting before continuing collection for {profile_url} ...",
            file=sys.stderr,
        )

    async def notify_skipped_post_response(self, request_id: str, reason: str) -> None:
        print(
            f"Skipped Douyin post response {request_id}: {reason}. Waiting for a valid response...",
            file=sys.stderr,
        )

    async def install_response_interceptor(self) -> None:
        """Inject JavaScript to capture Douyin API responses in-page.

        Hooks ``fetch`` and ``XMLHttpRequest`` to store response bodies in
        ``window.__douyin_captured_responses`` before Chrome DevTools Protocol
        can evict them from its cache.
        """
        await self.session.call_tool(
            "evaluate_script",
            {
                "function": """() => {
                    if (window.__douyin_interceptor_installed) return;
                    window.__douyin_interceptor_installed = true;
                    window.__douyin_captured_responses = [];

                    const API_PATH = '/aweme/v1/web/aweme/post/';

                    // Intercept fetch
                    const originalFetch = window.fetch;
                    window.fetch = async function(...args) {
                        const response = await originalFetch.apply(this, args);
                        const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
                        if (url.includes(API_PATH)) {
                            try {
                                const clone = response.clone();
                                const text = await clone.text();
                                window.__douyin_captured_responses.push({url: url, body: text});
                            } catch(e) {}
                        }
                        return response;
                    };

                    // Intercept XMLHttpRequest
                    const originalOpen = XMLHttpRequest.prototype.open;
                    const originalSend = XMLHttpRequest.prototype.send;
                    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                        this.__douyin_url = url;
                        return originalOpen.apply(this, [method, url, ...rest]);
                    };
                    XMLHttpRequest.prototype.send = function(...args) {
                        const xhr = this;
                        xhr.addEventListener('load', function() {
                            if (xhr.__douyin_url && xhr.__douyin_url.includes(API_PATH)) {
                                try {
                                    window.__douyin_captured_responses.push({
                                        url: xhr.__douyin_url,
                                        body: xhr.responseText
                                    });
                                } catch(e) {}
                            }
                        });
                        return originalSend.apply(this, args);
                    };
                }"""
            },
        )

    async def get_captured_responses(self) -> list[dict[str, str]]:
        """Return and clear captured Douyin API responses from the page."""
        result = await self.session.call_tool(
            "evaluate_script",
            {
                "function": """() => {
                    const responses = window.__douyin_captured_responses || [];
                    window.__douyin_captured_responses = [];
                    return JSON.stringify(responses);
                }"""
            },
        )
        # Try structured content first (result/value/returnValue keys)
        payload = _extract_json_object(result)
        for key in ("result", "value", "returnValue"):
            value = payload.get(key)
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            if isinstance(value, list):
                return value
        # Fall back to text content
        text = _extract_text_content(result)
        if text:
            stripped = text.strip()
            if stripped.startswith("["):
                try:
                    return json.loads(stripped)
                except (json.JSONDecodeError, TypeError):
                    pass
        return []
