from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from douyin_agent.downloader.video_downloader import DownloadResult, download_aweme_videos

PROGRESS_BAR_WIDTH = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download videos from collected Douyin aweme posts.")
    parser.add_argument("input_json", type=Path, help="Path to a collected douyin_posts.json file.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to save videos. Defaults to the input JSON file's parent directory.",
    )
    parser.add_argument(
        "--video-concurrency",
        type=int,
        default=3,
        help="Number of videos to download concurrently.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of newest videos to download. Use 0 to download all videos.",
    )
    return parser.parse_args()


def _load_aweme_list(input_json: Path) -> list[dict[str, Any]]:
    payload = json.loads(input_json.read_text(encoding="utf-8"))
    aweme_list = payload.get("aweme_list")
    if not isinstance(aweme_list, list):
        raise ValueError("Expected aweme_list to be a list")
    return aweme_list


def _apply_download_limit(aweme_list: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit < 0:
        raise ValueError("Expected limit to be greater than or equal to 0")
    if limit == 0:
        return aweme_list
    return aweme_list[:limit]


def _print_download_summary(result: DownloadResult) -> None:
    print(
        f"Videos: downloaded={result.downloaded}, "
        f"skipped={result.skipped}, failed={result.failed}",
        file=sys.stderr,
    )
    for err in result.errors:
        print(f"  [video-download-error] {err}", file=sys.stderr)


def _format_progress_line(completed: int, total: int, result: DownloadResult) -> str:
    if total <= 0:
        filled = 0
    else:
        filled = int(PROGRESS_BAR_WIDTH * completed / total)
    filled = max(0, min(PROGRESS_BAR_WIDTH, filled))
    bar = "#" * filled + "-" * (PROGRESS_BAR_WIDTH - filled)
    return (
        f"Downloading [{bar}] {completed}/{total} "
        f"downloaded={result.downloaded} skipped={result.skipped} failed={result.failed}"
    )


def _print_progress(completed: int, total: int, result: DownloadResult) -> None:
    print(f"\r{_format_progress_line(completed, total, result)}", end="", file=sys.stderr, flush=True)


async def main_async() -> None:
    args = parse_args()
    try:
        aweme_list = _load_aweme_list(args.input_json)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    try:
        aweme_list = _apply_download_limit(aweme_list, args.limit)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

    output_dir = args.output_dir or args.input_json.parent
    print(f"Downloading {len(aweme_list)} videos to {output_dir}/...", file=sys.stderr)
    result = await download_aweme_videos(
        aweme_list,
        output_dir,
        concurrency=args.video_concurrency,
        progress_callback=_print_progress,
    )
    if result.total:
        print(file=sys.stderr)
    _print_download_summary(result)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
