from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from douyin_agent.browser.chrome_devtools_client import ChromeDevToolsClient
from douyin_agent.collectors.douyin_comments import collect_aweme_comments
from douyin_agent.downloader.video_downloader import video_folder_name


@dataclass(frozen=True)
class CommentTarget:
    sec_user_id: str
    aweme_id: str
    modal_url: str


def _sec_user_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc.endswith("douyin.com"):
        raise ValueError("Expected a Douyin URL with http or https scheme")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0] != "user":
        raise ValueError("Expected a Douyin /user/<sec_user_id> URL")
    return parts[1]


def _aweme_list_from_json(input_json: Path) -> list[Any]:
    payload = json.loads(input_json.read_text(encoding="utf-8"))
    aweme_list = payload.get("aweme_list") if isinstance(payload, dict) else None
    if not isinstance(aweme_list, list):
        raise ValueError("Expected aweme_list to be a list")
    return aweme_list


def _sec_user_id_from_posts(input_json: Path, aweme_id: str) -> str:
    aweme_list = _aweme_list_from_json(input_json)

    for item in aweme_list:
        if not isinstance(item, dict) or str(item.get("aweme_id")) != str(aweme_id):
            continue
        author = item.get("author")
        sec_user_id = author.get("sec_uid") if isinstance(author, dict) else None
        if not isinstance(sec_user_id, str) or not sec_user_id.strip():
            raise ValueError(f"Expected author.sec_uid for aweme_id {aweme_id} in {input_json}")
        return sec_user_id.strip()

    raise ValueError(f"aweme_id {aweme_id} is not present in {input_json}")


def parse_comment_target(
    *,
    video_url: str | None,
    profile_url: str | None,
    aweme_id: str | None,
    input_json: Path | None = None,
) -> CommentTarget:
    if video_url:
        if profile_url or aweme_id or input_json:
            raise ValueError("Use --video-url or --profile-url/--input-json with --aweme-id, not both")
        parsed = urlparse(video_url)
        modal_id = parse_qs(parsed.query).get("modal_id", [None])[0]
        if not modal_id:
            raise ValueError("Video URL must include modal_id")
        sec_user_id = _sec_user_id(video_url)
        target_aweme_id = str(modal_id)
    else:
        if not aweme_id or (not profile_url and input_json is None):
            raise ValueError("Provide --video-url or --aweme-id with --profile-url/--input-json")
        sec_user_id = (
            _sec_user_id(profile_url)
            if profile_url
            else _sec_user_id_from_posts(input_json, str(aweme_id))
        )
        target_aweme_id = str(aweme_id)

    return CommentTarget(
        sec_user_id=sec_user_id,
        aweme_id=target_aweme_id,
        modal_url=(
            f"https://www.douyin.com/user/{sec_user_id}"
            f"?from_tab_name=main&modal_id={target_aweme_id}"
        ),
    )


def resolve_comments_path(
    aweme_id: str,
    input_json: Path | None,
    sec_user_id: str,
    *,
    workdir: Path | None = None,
) -> Path:
    if input_json is not None:
        aweme_list = _aweme_list_from_json(input_json)

        used_names: set[str] = set()
        for item in aweme_list:
            if not isinstance(item, dict):
                continue
            name = video_folder_name(item)
            original = name
            counter = 2
            while name in used_names:
                name = f"{original}_{counter}"
                counter += 1
            used_names.add(name)
            if str(item.get("aweme_id")) == str(aweme_id):
                return input_json.parent / name / "comments.json"
        raise ValueError(f"aweme_id {aweme_id} is not present in {input_json}")

    root = workdir or Path.cwd()
    return root / "data" / sec_user_id / "comments" / str(aweme_id) / "comments.json"


def _load_existing_comments(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    comments = payload.get("comments")
    if not isinstance(comments, list):
        raise ValueError(f"Expected comments to be a list in {path}")
    return comments


async def collect_douyin_aweme_comments_async(
    *,
    video_url: str | None,
    profile_url: str | None,
    aweme_id: str | None,
    input_json: Path | None,
    comment_limit: int = 100,
    reply_limit: int = 0,
    workdir: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    target = parse_comment_target(
        video_url=video_url,
        profile_url=profile_url,
        aweme_id=aweme_id,
        input_json=input_json,
    )
    output_path = resolve_comments_path(
        target.aweme_id,
        input_json,
        target.sec_user_id,
        workdir=workdir,
    )
    existing_comments = _load_existing_comments(output_path)
    if progress is not None:
        progress(
            "[comments] target "
            f"aweme_id={target.aweme_id} existing_comments={len(existing_comments)} "
            f"output={output_path}"
        )
    async with ChromeDevToolsClient() as browser:
        result = await collect_aweme_comments(
            browser,
            target.modal_url,
            target.aweme_id,
            comment_limit=comment_limit,
            reply_limit=reply_limit,
            existing_comments=existing_comments,
            progress=progress,
        )

    payload = {
        "profile_url": target.modal_url,
        "aweme_id": target.aweme_id,
        "collected_at": datetime.now(UTC).isoformat(),
        "comment_limit": comment_limit,
        "reply_limit": reply_limit,
        **result,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(output_path)
    if progress is not None:
        summary = result["summary"]
        progress(
            "[comments] saved "
            f"new_comments={summary['new_comments']} new_replies={summary['new_replies']} "
            f"scanned={summary['comments_scanned']} reason={summary['comments_termination_reason']} "
            f"output={output_path}"
        )
    return output_path


def collect_douyin_aweme_comments(**kwargs: Any) -> Path:
    return asyncio.run(collect_douyin_aweme_comments_async(**kwargs))
