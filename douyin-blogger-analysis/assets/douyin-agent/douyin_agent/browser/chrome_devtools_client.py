from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from random import uniform
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


_AWEME_REQUEST_DELAY_MIN_SECONDS = 1.5
_AWEME_REQUEST_DELAY_MAX_SECONDS = 4.0
_PC_LIBRA_DIVERT_FUNCTION = r'''() => {
    function n(e) {
        let r = "";
        if (e) {
            r = e?.header?.["user-agent"] || e?.request?.header?.["user-agent"] || "";
        }
        if (!r && typeof navigator !== "undefined" && navigator?.userAgent) {
            r = navigator.userAgent;
        }
        return r;
    }

    function a() {
        const f = n();
        const p = {os: "", version: "", isMas: false};
        if (/osName\/Mas/i.test(f)) {
            p.isMas = true;
        }

        const platform = navigator?.platform;
        const v = platform === "Win32" || platform === "Windows";
        const h = ["Mac68K", "MacPPC", "Macintosh", "MacIntel"].includes(platform);

        if (h) {
            p.os = "Mac";
            return p;
        }
        if (platform === "X11" && !v && !h) {
            p.os = "Unix";
            return p;
        }
        if (String(platform).indexOf("Linux") > -1) {
            p.os = "Linux";
            return p;
        }
        if (String(platform).toLowerCase().indexOf("ohos") > -1) {
            p.os = "Ohos";
            return p;
        }
        if (v) {
            p.os = "Windows";
        }
        return p;
    }

    return a().os;
}'''
_DEVICE_WEB_CPU_CORE_FUNCTION = r'''() => {
    const prefix = "device_web_cpu_core=";
    const cookie = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith(prefix));
    if (!cookie) {
        return 0;
    }

    try {
        const value = Number(decodeURIComponent(cookie.slice(prefix.length)));
        return Number.isInteger(value) && value > 0 ? value : 0;
    } catch {
        return 0;
    }
}'''


def _default_mcp_args() -> list[str]:
    return [
        "-y",
        "chrome-devtools-mcp@latest",
        "--no-performance-crux",
        "--no-usage-statistics",
        "--experimental-structured-content",
        "--autoConnect",
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


def _extract_evaluated_value(result: Any) -> Any:
    payload = _extract_json_object(result)
    for key in ("result", "value", "returnValue"):
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    message = payload.get("message")
    if isinstance(message, str):
        fence_start = message.find("```json\n")
        if fence_start != -1:
            value_start = fence_start + len("```json\n")
            fence_end = message.find("\n```", value_start)
            if fence_end != -1:
                value = json.loads(message[value_start:fence_end])
                if isinstance(value, str):
                    try:
                        return json.loads(value)
                    except json.JSONDecodeError:
                        return value
                return value
    raise ValueError("No evaluate_script result returned")


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
        self._pc_libra_divert: str | None = None
        self._device_web_cpu_core: int | None = None

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

    async def has_axios_instance(self) -> bool:
        result = await self.session.call_tool(
            "evaluate_script",
            {"function": "() => Boolean(window.axiosInstance)"},
        )
        return bool(_extract_evaluated_value(result))

    async def get_pc_libra_divert(self) -> str:
        if self._pc_libra_divert is not None:
            return self._pc_libra_divert

        result = await self.session.call_tool(
            "evaluate_script",
            {"function": _PC_LIBRA_DIVERT_FUNCTION},
        )
        pc_libra_divert = _extract_evaluated_value(result)
        if not isinstance(pc_libra_divert, str):
            raise ValueError("Browser pc_libra_divert result is not a string")
        self._pc_libra_divert = pc_libra_divert
        return pc_libra_divert

    async def get_device_web_cpu_core(self) -> int:
        if self._device_web_cpu_core is not None:
            return self._device_web_cpu_core

        result = await self.session.call_tool(
            "evaluate_script",
            {"function": _DEVICE_WEB_CPU_CORE_FUNCTION},
        )
        device_web_cpu_core = _extract_evaluated_value(result)
        if isinstance(device_web_cpu_core, bool) or not isinstance(device_web_cpu_core, int):
            raise ValueError("Browser device_web_cpu_core result is not an integer")
        self._device_web_cpu_core = device_web_cpu_core
        return device_web_cpu_core

    async def request_aweme_posts(
        self,
        *,
        sec_user_id: str,
        max_cursor: int | str,
        need_time_list: int,
        count: int,
    ) -> dict[str, Any]:
        params = {
            "aid": 6383,
            "channel": "channel_pc_web",
            "count": count,
            "cut_version": 1,
            "device_platform": "webapp",
            "locate_query": False,
            "max_cursor": max_cursor,
            "need_time_list": need_time_list,
            "pc_client_type": 1,
            "publish_video_strategy_type": 2,
            "sec_user_id": sec_user_id,
            "show_live_replay_strategy": 1,
            "support_h265": 1,
            "support_dash": 1,
            "time_list_query": 0,
            "update_version_code": "170400",
        }
        return await self._request_douyin_api("/aweme/v1/web/aweme/post/", params)

    async def request_aweme_comments(
        self,
        aweme_id: str,
        *,
        cursor: int | str,
        count: int,
    ) -> dict[str, Any]:
        return await self._request_douyin_api(
            "/aweme/v1/web/comment/list/",
            {
                "device_platform": "webapp",
                "aid": 6383,
                "channel": "channel_pc_web",
                "aweme_id": aweme_id,
                "cursor": cursor,
                "count": count,
                "item_type": 0,
                "cut_version": 1,
                "pc_img_format": "webp",
                "pc_client_type": 1,
                "support_h265": 1,
                "support_dash": 1,
                "update_version_code": "170400",
            },
        )

    async def request_aweme_comment_replies(
        self,
        *,
        comment_id: str,
        item_id: str,
        cursor: int | str,
        count: int,
    ) -> dict[str, Any]:
        return await self._request_douyin_api(
            "/aweme/v1/web/comment/list/reply/",
            {
                "device_platform": "webapp",
                "aid": 6383,
                "channel": "channel_pc_web",
                "comment_id": comment_id,
                "item_id": item_id,
                "cursor": cursor,
                "count": count,
                "item_type": 0,
                "cut_version": 1,
                "pc_img_format": "webp",
                "pc_client_type": 1,
                "support_h265": 1,
                "support_dash": 1,
                "update_version_code": "170400",
            },
        )

    async def _request_douyin_api(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        delay_seconds = uniform(
            _AWEME_REQUEST_DELAY_MIN_SECONDS,
            _AWEME_REQUEST_DELAY_MAX_SECONDS,
        )
        await asyncio.sleep(delay_seconds)
        request_params = {
            **params,
            "pc_libra_divert": await self.get_pc_libra_divert(),
            "cpu_core_num": await self.get_device_web_cpu_core(),
        }
        params_json = json.dumps(request_params, ensure_ascii=False)
        result = await self.session.call_tool(
            "evaluate_script",
            {
                "function": f'''async () => {{
                    const axios = window.axiosInstance;
                    if (!axios) throw new Error("window.axiosInstance 不存在");
                    const res = await axios({{
                        url: {json.dumps(endpoint)},
                        method: "GET",
                        baseURL: "",
                        withCredentials: true,
                        params: {params_json},
                        headers: {{"Content-Type": "application/json"}}
                    }});
                    return JSON.stringify(res.data);
                }}'''
            },
        )
        payload = _extract_evaluated_value(result)
        if not isinstance(payload, dict):
            raise ValueError("Douyin post request did not return a JSON object")
        return payload

    async def wait_for_network_idle_or_delay(self) -> None:
        await asyncio.sleep(self.post_scroll_delay_seconds)

    async def notify_login_required(self, profile_url: str) -> None:
        print(
            "window.axiosInstance is not available yet. "
            "If the opened Chrome window asks for login, finish logging into Douyin there. "
            f"Waiting before continuing collection for {profile_url} ...",
            file=sys.stderr,
        )
