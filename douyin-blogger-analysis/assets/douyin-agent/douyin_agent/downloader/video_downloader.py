from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

DOUYIN_REFERER = "https://www.douyin.com"
DOUYIN_SOURCE_PLAY_URL_PREFIX = "https://www.douyin.com/aweme/v1/play/"
DEFAULT_CONCURRENCY = 3
CHUNK_SIZE = 1024 * 1024  # 1 MB
MAX_DESC_LENGTH = 80
ProgressCallback = Callable[[int, int, "DownloadResult"], None]


def _sanitize_name(name: str, *, max_length: int = MAX_DESC_LENGTH) -> str:
    """Remove characters that are unsafe in file/directory names."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f\n\r\t]', "_", name).strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip()
    return cleaned or "untitled"


def extract_video_url(item: dict[str, Any]) -> str | None:
    """Extract a playable video URL from an aweme item."""
    video = item.get("video") or {}
    play_addr = video.get("play_addr") or {}
    url_list = play_addr.get("url_list") or []
    valid_urls = [str(url) for url in url_list if url and str(url).startswith("http")]
    for url in valid_urls:
        if url.startswith(DOUYIN_SOURCE_PLAY_URL_PREFIX):
            return url
    if valid_urls:
        return valid_urls[0]
    return None


def video_folder_name(item: dict[str, Any]) -> str:
    """Derive a human-readable, filesystem-safe folder name for a video.

    Uses the video description when available, otherwise falls back to aweme_id.
    """
    desc = (item.get("desc") or "").strip()
    if desc:
        return _sanitize_name(desc)
    aweme_id = item.get("aweme_id") or "unknown"
    return _sanitize_name(str(aweme_id))


@dataclass
class DownloadResult:
    """Outcome of downloading videos for an aweme list."""

    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.downloaded + self.skipped + self.failed


async def download_video(
    session: aiohttp.ClientSession,
    video_url: str,
    dest_path: Path,
) -> int:
    """Download a single video to *dest_path*.

    Returns the number of bytes written.
    Raises ``aiohttp.ClientError`` (or subclasses) on failure.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"Referer": DOUYIN_REFERER}
    total = 0
    async with session.get(video_url, headers=headers) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                f.write(chunk)
                total += len(chunk)
    return total


async def download_aweme_videos(
    aweme_list: list[dict[str, Any]],
    base_dir: Path,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    progress_callback: ProgressCallback | None = None,
) -> DownloadResult:
    """Download every video in *aweme_list* into *base_dir*/<video_name>/video.mp4.

    * Skips videos whose target file already exists (non-empty).
    * Missing video URLs are silently ignored.
    * Download failures are logged and counted; they do not abort the batch.
    """
    base_dir.mkdir(parents=True, exist_ok=True)

    # Build the download work-list, resolving name collisions with a counter.
    used_names: set[str] = set()
    candidates: list[tuple[str, Path]] = []
    result = DownloadResult()

    for item in aweme_list:
        url = extract_video_url(item)
        if not url:
            continue

        name = video_folder_name(item)
        original = name
        counter = 2
        while name in used_names:
            name = f"{original}_{counter}"
            counter += 1
        used_names.add(name)

        candidates.append((url, base_dir / name / "video.mp4"))

    total = len(candidates)
    completed = 0
    work: list[tuple[str, Path]] = []
    for url, dest in candidates:
        if dest.exists() and dest.stat().st_size > 0:
            result.skipped += 1
            completed += 1
            if progress_callback:
                progress_callback(completed, total, result)
            continue
        work.append((url, dest))

    if not work:
        return result

    semaphore = asyncio.Semaphore(concurrency)

    async def _download_one(url: str, dest: Path) -> bool:
        async with semaphore:
            try:
                await download_video(session, url, dest)
                return True
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{dest.parent.name}: {exc}")
                return False

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300)
    headers = {
        "Referer": DOUYIN_REFERER,
        "User-Agent": "Mozilla/5.0",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        tasks = [asyncio.create_task(_download_one(url, dest)) for url, dest in work]
        for task in asyncio.as_completed(tasks):
            ok = await task
            if ok:
                result.downloaded += 1
            else:
                result.failed += 1
            completed += 1
            if progress_callback:
                progress_callback(completed, total, result)

    return result
