from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

DOUYIN_REFERER = "https://www.douyin.com"
DOUYIN_SOURCE_PLAY_URL_PREFIX = "https://www.douyin.com/aweme/v1/play/"
DEFAULT_CONCURRENCY = 3
CHUNK_SIZE = 1024 * 1024  # 1 MB
MAX_DESC_LENGTH = 80
AUDIO_URL_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
AUDIO_URL_MARKERS = ("/ies-music/",)
IMAGE_URL_EXTENSIONS = {".avif", ".jpeg", ".jpg", ".png", ".webp"}
ProgressCallback = Callable[[int, int, "DownloadResult"], None]


def _sanitize_name(name: str, *, max_length: int = MAX_DESC_LENGTH) -> str:
    """Remove characters that are unsafe in file/directory names."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f\n\r\t]', "_", name).strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip()
    return cleaned or "untitled"


def _http_urls(url_list: Any) -> list[str]:
    if not isinstance(url_list, list):
        return []
    return [str(url) for url in url_list if url and str(url).startswith("http")]


def _url_suffix(url: str) -> str:
    return Path(urlparse(url).path).suffix.lower()


def _is_audio_url(url: str) -> bool:
    normalized = url.lower()
    return any(marker in normalized for marker in AUDIO_URL_MARKERS) or _url_suffix(url) in AUDIO_URL_EXTENSIONS


def _image_suffix(url: str) -> str:
    suffix = _url_suffix(url)
    if suffix in IMAGE_URL_EXTENSIONS:
        return suffix
    return ".jpg"


def is_image_aweme(item: dict[str, Any]) -> bool:
    """Return True when a Douyin item is an image/gallery post."""
    return (
        item.get("aweme_type") == 68
        or item.get("media_type") == 2
        or bool(item.get("images"))
    )


def extract_video_url(item: dict[str, Any]) -> str | None:
    """Extract a playable video URL from an aweme item."""
    if is_image_aweme(item):
        return None

    video = item.get("video") or {}
    play_addr = video.get("play_addr") or {}
    valid_urls = [url for url in _http_urls(play_addr.get("url_list")) if not _is_audio_url(url)]
    for url in valid_urls:
        if url.startswith(DOUYIN_SOURCE_PLAY_URL_PREFIX):
            return url
    if valid_urls:
        return valid_urls[0]
    return None


def extract_image_urls(item: dict[str, Any]) -> list[str]:
    """Extract one downloadable image URL per image in a Douyin gallery post."""
    images = item.get("images") or []
    if not isinstance(images, list):
        return []

    image_urls: list[str] = []
    for image in images:
        if not isinstance(image, dict):
            continue

        candidates: list[str] = []
        for key in ("url_list", "download_url_list"):
            candidates.extend(_http_urls(image.get(key)))
        for key in ("display_image", "origin_cover", "large_image", "thumbnail"):
            nested = image.get(key)
            if isinstance(nested, dict):
                candidates.extend(_http_urls(nested.get("url_list")))

        for url in candidates:
            if not _is_audio_url(url):
                image_urls.append(url)
                break

    return image_urls


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
    """Outcome of downloading media for an aweme list."""

    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    image_posts_downloaded: int = 0
    images_downloaded: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.downloaded + self.skipped + self.failed


@dataclass(frozen=True)
class _MediaCandidate:
    kind: str
    name: str
    downloads: tuple[tuple[str, Path], ...]


async def download_file(
    session: aiohttp.ClientSession,
    url: str,
    dest_path: Path,
) -> int:
    """Download a single media file to *dest_path*.

    Returns the number of bytes written.
    Raises ``aiohttp.ClientError`` (or subclasses) on failure.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"Referer": DOUYIN_REFERER}
    total = 0
    async with session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                f.write(chunk)
                total += len(chunk)
    return total


async def download_video(
    session: aiohttp.ClientSession,
    video_url: str,
    dest_path: Path,
) -> int:
    """Download a single video to *dest_path*."""
    return await download_file(session, video_url, dest_path)


async def download_aweme_videos(
    aweme_list: list[dict[str, Any]],
    base_dir: Path,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    progress_callback: ProgressCallback | None = None,
) -> DownloadResult:
    """Download every supported media item in *aweme_list* under *base_dir*.

    * Videos are stored as <video_name>/video.mp4.
    * Image posts are stored as <video_name>/images/image_0001.<ext>.
    * Skips media items whose target files already exist (non-empty).
    * Download failures are logged and counted; they do not abort the batch.
    """
    base_dir.mkdir(parents=True, exist_ok=True)

    # Build the download work-list, resolving name collisions with a counter.
    used_names: set[str] = set()
    candidates: list[_MediaCandidate] = []
    result = DownloadResult()

    for item in aweme_list:
        name = video_folder_name(item)
        original = name
        counter = 2
        while name in used_names:
            name = f"{original}_{counter}"
            counter += 1
        used_names.add(name)

        if is_image_aweme(item):
            image_urls = extract_image_urls(item)
            if not image_urls:
                result.skipped += 1
                result.errors.append(f"{name}: image post has no downloadable image URLs")
                continue
            downloads = tuple(
                (
                    url,
                    base_dir
                    / name
                    / "images"
                    / f"image_{index:04d}{_image_suffix(url)}",
                )
                for index, url in enumerate(image_urls, start=1)
            )
            candidates.append(_MediaCandidate("images", name, downloads))
            continue

        url = extract_video_url(item)
        if not url:
            result.skipped += 1
            result.errors.append(f"{name}: no downloadable video URL")
            continue

        candidates.append(_MediaCandidate("video", name, ((url, base_dir / name / "video.mp4"),)))

    total = len(candidates)
    completed = 0
    work: list[_MediaCandidate] = []
    for candidate in candidates:
        pending_downloads = tuple(
            (url, dest)
            for url, dest in candidate.downloads
            if not (dest.exists() and dest.stat().st_size > 0)
        )
        if not pending_downloads:
            result.skipped += 1
            completed += 1
            if progress_callback:
                progress_callback(completed, total, result)
            continue
        work.append(_MediaCandidate(candidate.kind, candidate.name, pending_downloads))

    if not work:
        return result

    semaphore = asyncio.Semaphore(concurrency)

    async def _download_one(candidate: _MediaCandidate) -> tuple[_MediaCandidate, bool]:
        async with semaphore:
            try:
                for url, dest in candidate.downloads:
                    await download_file(session, url, dest)
                return candidate, True
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{candidate.name}: {exc}")
                return candidate, False

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300)
    headers = {
        "Referer": DOUYIN_REFERER,
        "User-Agent": "Mozilla/5.0",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers, trust_env=True) as session:
        tasks = [asyncio.create_task(_download_one(candidate)) for candidate in work]
        for task in asyncio.as_completed(tasks):
            candidate, ok = await task
            if ok:
                result.downloaded += 1
                if candidate.kind == "images":
                    result.image_posts_downloaded += 1
                    result.images_downloaded += len(candidate.downloads)
            else:
                result.failed += 1
            completed += 1
            if progress_callback:
                progress_callback(completed, total, result)

    return result
