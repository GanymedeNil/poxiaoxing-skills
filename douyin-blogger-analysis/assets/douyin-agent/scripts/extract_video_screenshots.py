from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_DURATION_SECONDS = 5.0


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise ValueError("Expected a positive number")
    return parsed


def _format_number(value: float) -> str:
    return f"{value:g}"


def find_video_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.rglob("video.mp4"))


def has_existing_screenshots(output_dir: Path) -> bool:
    return output_dir.exists() and any(output_dir.glob("*.jpg"))


def build_ffmpeg_command(
    video_path: Path,
    output_dir: Path,
    *,
    interval: float,
    duration: float,
    overwrite: bool,
    ffmpeg_bin: str,
) -> list[str]:
    overwrite_flag = "-y" if overwrite else "-n"
    return [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        overwrite_flag,
        "-i",
        str(video_path),
        "-t",
        _format_number(duration),
        "-vf",
        f"fps=1/{_format_number(interval)}",
        str(output_dir / "frame_%04d.jpg"),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract screenshots from video files.")
    parser.add_argument("input_path", type=Path, help="A video file or directory containing downloaded video.mp4 files.")
    parser.add_argument(
        "--interval",
        type=positive_float,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Seconds between screenshots. Defaults to 1.",
    )
    parser.add_argument(
        "--duration",
        type=positive_float,
        default=DEFAULT_DURATION_SECONDS,
        help="Only extract screenshots from the first N seconds. Defaults to 5.",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        default="ffmpeg",
        help="ffmpeg executable to use. Defaults to ffmpeg on PATH.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate screenshots even when screenshots already exist.",
    )
    return parser.parse_args()


def extract_screenshots(
    video_path: Path,
    *,
    interval: float,
    duration: float,
    overwrite: bool,
    ffmpeg_bin: str,
) -> bool:
    output_dir = video_path.parent / "screenshots"
    if has_existing_screenshots(output_dir) and not overwrite:
        print(f"Skipping existing screenshots: {output_dir}", file=sys.stderr)
        return True

    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(
        video_path,
        output_dir,
        interval=interval,
        duration=duration,
        overwrite=overwrite,
        ffmpeg_bin=ffmpeg_bin,
    )
    subprocess.run(command, check=True)
    print(f"Extracted screenshots: {output_dir}", file=sys.stderr)
    return True


def main() -> None:
    args = parse_args()
    if not args.input_path.exists():
        raise SystemExit(f"Input path does not exist: {args.input_path}")

    if shutil.which(args.ffmpeg_bin) is None:
        raise SystemExit(f"ffmpeg executable not found: {args.ffmpeg_bin}")

    video_files = find_video_files(args.input_path)
    if not video_files:
        raise SystemExit(f"No video files found under: {args.input_path}")

    failed = 0
    for video_path in video_files:
        try:
            extract_screenshots(
                video_path,
                interval=args.interval,
                duration=args.duration,
                overwrite=args.overwrite,
                ffmpeg_bin=args.ffmpeg_bin,
            )
        except subprocess.CalledProcessError as exc:
            failed += 1
            print(f"[screenshot-error] {video_path}: {exc}", file=sys.stderr)

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
