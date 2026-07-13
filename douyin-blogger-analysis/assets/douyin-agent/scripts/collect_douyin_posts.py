from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from douyin_agent.tools.collect_aweme_posts import collect_douyin_aweme_posts_async


def print_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _extract_channel_name(result: dict) -> str:
    """Extract a filesystem-safe channel name from collected data or URL."""
    # Try author nickname from the first aweme item.
    for item in result.get("aweme_list", []):
        author = item.get("author") or {}
        nickname = author.get("nickname")
        if nickname:
            return _sanitize_dir_name(nickname)
    # Fall back to user ID from profile URL.
    profile_url = result.get("profile_url", "")
    match = re.search(r"/user/([^/?#]+)", profile_url)
    if match:
        return _sanitize_dir_name(match.group(1))
    return "unknown"


def _sanitize_dir_name(name: str) -> str:
    """Remove characters that are unsafe in file/directory names."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "unknown"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect raw Douyin aweme posts from a creator profile.")
    parser.add_argument("profile_url", help="Douyin creator homepage URL, for example https://www.douyin.com/user/...")
    parser.add_argument(
        "--max-idle-rounds",
        type=int,
        default=3,
        help=(
            "Deprecated compatibility option; direct axios pagination does not use "
            "idle scroll rounds."
        ),
    )
    parser.add_argument(
        "--login-wait-rounds",
        type=int,
        default=300,
        help="How many polling rounds to wait for manual Douyin login before giving up.",
    )
    parser.add_argument(
        "--max-response-parse-retries",
        type=int,
        default=5,
        help=(
            "Deprecated compatibility option; direct axios responses are decoded "
            "immediately."
        ),
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output JSON file path. If omitted, saves to <channel_name>/douyin_posts.json.",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    result = await collect_douyin_aweme_posts_async(
        args.profile_url,
        max_idle_rounds=args.max_idle_rounds,
        login_wait_rounds=args.login_wait_rounds,
        max_response_parse_retries=args.max_response_parse_retries,
        progress=print_progress,
    )

    # Determine output path: data/<channel_name>/douyin_posts.json by default.
    if args.output:
        output_file = Path(args.output)
        channel_dir = output_file.parent
    else:
        channel_name = _extract_channel_name(result)
        channel_dir = Path(f"data/{channel_name}")
        channel_dir.mkdir(parents=True, exist_ok=True)
        output_file = channel_dir / "douyin_posts.json"
    output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[posts] saved output={output_file}", file=sys.stderr, flush=True)

    summary = result.get("summary", {})
    print(
        f"Results written to {output_file}\n"
        f"  items_collected: {summary.get('items_collected', 0)}\n"
        f"  pages_seen: {summary.get('pages_seen', 0)}\n"
        f"  scroll_count: {summary.get('scroll_count', 0)}\n"
        f"  termination_reason: {summary.get('termination_reason', 'unknown')}",
        file=sys.stderr,
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
