from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from douyin_agent.tools.collect_aweme_comments import collect_douyin_aweme_comments_async


def print_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect incremental Douyin comments for one selected video.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--video-url")
    source.add_argument("--profile-url")
    parser.add_argument("--aweme-id")
    parser.add_argument("--input-json", type=Path)
    parser.add_argument("--comment-limit", type=int, default=100)
    parser.add_argument(
        "--reply-limit",
        type=int,
        default=0,
        help="Maximum replies to collect per scanned top-level comment. Defaults to 0, which skips replies.",
    )
    args = parser.parse_args()
    if args.video_url and args.aweme_id:
        parser.error("--aweme-id cannot be used with --video-url")
    if args.video_url and args.input_json:
        parser.error("--input-json cannot be used with --video-url")
    if not args.video_url and not (args.profile_url or args.input_json):
        parser.error("provide --video-url, --profile-url, or --input-json")
    if not args.video_url and not args.aweme_id:
        parser.error("--profile-url and --input-json require --aweme-id")
    return args


async def main_async() -> None:
    args = parse_args()
    output_path = await collect_douyin_aweme_comments_async(
        video_url=args.video_url,
        profile_url=args.profile_url,
        aweme_id=args.aweme_id,
        input_json=args.input_json,
        comment_limit=args.comment_limit,
        reply_limit=args.reply_limit,
        progress=print_progress,
    )
    print(output_path)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
