#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


REQUIRED_SCRIPTS = {
    "collect": Path("scripts/collect_douyin_posts.py"),
    "download": Path("scripts/download_douyin_videos.py"),
    "screenshots": Path("scripts/extract_video_screenshots.py"),
    "subtitles": Path("scripts/transcribe_video_subtitles.py"),
}
BUNDLED_PROJECT_ROOT = Path(__file__).resolve().parents[1] / "assets" / "douyin-agent"
AURALWISE_NO_CONFIG_GUIDANCE = (
    "Subtitle transcription now uses AuralWise. Set AURALWISE_API_KEY, or pass --api-key. "
    "You can also set AURALWISE_BASE_URL or pass --base-url to override the API base URL."
)


def project_root(value: str | None) -> Path:
    root = Path(value).expanduser().resolve() if value else BUNDLED_PROJECT_ROOT.resolve()
    if not (root / "pyproject.toml").exists():
        raise SystemExit(f"Project root does not contain pyproject.toml: {root}")
    for script in REQUIRED_SCRIPTS.values():
        if not (root / script).exists():
            raise SystemExit(f"Missing expected script: {root / script}")
    return root


def workdir(value: str | None) -> Path:
    directory = Path(value or os.getcwd()).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def run_command(command: list[str], *, cwd: Path, dry_run: bool = False) -> subprocess.CompletedProcess[str] | None:
    print("+ " + " ".join(command), file=sys.stderr)
    if dry_run:
        return None
    return subprocess.run(command, cwd=cwd, text=True, check=True)


def run_capture(command: list[str], *, cwd: Path, dry_run: bool = False) -> subprocess.CompletedProcess[str] | None:
    print("+ " + " ".join(command), file=sys.stderr)
    if dry_run:
        return None
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True)


def has_auralwise_config(args: argparse.Namespace | None = None) -> bool:
    api_key = (getattr(args, "api_key", None) if args else None) or os.environ.get("AURALWISE_API_KEY")
    return bool(api_key)


def print_auralwise_no_config_guidance() -> None:
    print(AURALWISE_NO_CONFIG_GUIDANCE, file=sys.stderr)


def uv_python(script: Path, *args: str) -> list[str]:
    root = project_root(None)
    return ["uv", "run", "--project", str(root), "python", str(root / script), *args]


def uv_python_for(root: Path, script: Path, *args: str) -> list[str]:
    return ["uv", "run", "--project", str(root), "python", str(root / script), *args]


def ensure_setup(args: argparse.Namespace) -> None:
    root = project_root(args.project_root)
    failures: list[str] = []

    if shutil.which("uv") is None:
        failures.append("uv is not installed or not on PATH")
    else:
        run_command(["uv", "sync", "--project", str(root)], cwd=workdir(args.workdir), dry_run=args.dry_run)

    ffmpeg_bin = args.ffmpeg_bin or "ffmpeg"
    if shutil.which(ffmpeg_bin) is None:
        failures.append(f"ffmpeg executable not found: {ffmpeg_bin}")

    chrome_profile = os.environ.get("DOUYIN_AGENT_CHROME_PROFILE_DIR", "~/.cache/douyin-agent/chrome-profile")
    print(f"Douyin agent project: {root}", file=sys.stderr)
    print(f"Output working directory: {workdir(args.workdir)}", file=sys.stderr)
    print(f"Douyin Chrome profile: {chrome_profile}", file=sys.stderr)

    if not has_auralwise_config():
        print_auralwise_no_config_guidance()

    if failures:
        for failure in failures:
            print(f"[setup-missing] {failure}", file=sys.stderr)
        raise SystemExit(1)

    print("Setup check complete.", file=sys.stderr)


def collect(args: argparse.Namespace) -> Path | None:
    root = project_root(args.project_root)
    output_dir = workdir(args.workdir)
    command = uv_python_for(root, REQUIRED_SCRIPTS["collect"], args.profile_url)
    if args.output:
        command.extend(["--output", str(args.output)])
    if args.login_wait_rounds is not None:
        command.extend(["--login-wait-rounds", str(args.login_wait_rounds)])
    if args.max_idle_rounds is not None:
        command.extend(["--max-idle-rounds", str(args.max_idle_rounds)])
    if args.max_response_parse_retries is not None:
        command.extend(["--max-response-parse-retries", str(args.max_response_parse_retries)])

    if args.output or args.dry_run:
        run_command(command, cwd=output_dir, dry_run=args.dry_run)
        return Path(args.output).expanduser() if args.output else None

    completed = run_capture(command, cwd=output_dir, dry_run=False)
    stderr = completed.stderr if completed else ""
    if completed and completed.stdout:
        print(completed.stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    match = re.search(r"Results written to (.+)", stderr)
    return Path(match.group(1).strip()) if match else None


def download(args: argparse.Namespace) -> None:
    root = project_root(args.project_root)
    command = uv_python_for(root, REQUIRED_SCRIPTS["download"], str(args.input_json))
    if args.output_dir:
        command.extend(["--output-dir", str(args.output_dir)])
    if args.video_concurrency is not None:
        command.extend(["--video-concurrency", str(args.video_concurrency)])
    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])
    run_command(command, cwd=workdir(args.workdir), dry_run=args.dry_run)


def screenshots(args: argparse.Namespace) -> None:
    root = project_root(args.project_root)
    command = uv_python_for(root, REQUIRED_SCRIPTS["screenshots"], str(args.channel_dir))
    command.extend(["--interval", str(args.interval), "--duration", str(args.duration)])
    if args.ffmpeg_bin:
        command.extend(["--ffmpeg-bin", args.ffmpeg_bin])
    if args.overwrite:
        command.append("--overwrite")
    run_command(command, cwd=workdir(args.workdir), dry_run=args.dry_run)


def subtitles(args: argparse.Namespace) -> None:
    root = project_root(args.project_root)
    if not has_auralwise_config(args):
        print_auralwise_no_config_guidance()
        if not args.dry_run:
            raise SystemExit(1)
    command = uv_python_for(root, REQUIRED_SCRIPTS["subtitles"], str(args.channel_dir))
    if args.api_key:
        command.extend(["--api-key", args.api_key])
    if getattr(args, "base_url", None):
        command.extend(["--base-url", args.base_url])
    if args.ffmpeg_bin:
        command.extend(["--ffmpeg-bin", args.ffmpeg_bin])
    command.extend(["--request-interval", str(args.request_interval), "--poll-interval", str(args.poll_interval)])
    if not getattr(args, "optimize", True):
        command.append("--no-optimize")
    if args.overwrite:
        command.append("--overwrite")
    run_command(command, cwd=workdir(args.workdir), dry_run=args.dry_run)


def pipeline(args: argparse.Namespace) -> None:
    root = project_root(args.project_root)
    input_json = Path(args.output).expanduser() if args.output else None

    if not args.skip_collect:
        input_json = collect(args)
    if input_json is None:
        if args.dry_run and not args.skip_collect:
            channel_dir = Path(args.channel_dir).expanduser() if args.channel_dir else Path("data/<channel_name>")
            input_json = channel_dir / "douyin_posts.json"
            print(
                "[dry-run] Collection output is inferred only after running; "
                f"previewing downstream steps with {input_json}.",
                file=sys.stderr,
            )
        else:
            raise SystemExit(
                "Pipeline could not infer the collection output. During normal collection, omit --output so "
                "the collector writes data/<channel_name>/douyin_posts.json. If skipping collection or using "
                "a custom JSON path, pass that path explicitly."
            )

    channel_dir = Path(args.channel_dir).expanduser() if args.channel_dir else input_json.parent

    if not args.skip_download:
        download_args = argparse.Namespace(
            project_root=str(root),
            workdir=args.workdir,
            input_json=input_json,
            output_dir=args.download_output_dir or channel_dir,
            video_concurrency=args.video_concurrency,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        download(download_args)

    if not args.skip_screenshots:
        screenshot_args = argparse.Namespace(
            project_root=str(root),
            workdir=args.workdir,
            channel_dir=channel_dir,
            interval=args.interval,
            duration=args.duration,
            ffmpeg_bin=args.ffmpeg_bin,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
        screenshots(screenshot_args)

    if args.with_subtitles:
        subtitle_args = argparse.Namespace(
            project_root=str(root),
            workdir=args.workdir,
            channel_dir=channel_dir,
            api_key=args.api_key,
            base_url=args.base_url,
            ffmpeg_bin=args.ffmpeg_bin,
            request_interval=args.request_interval,
            poll_interval=args.poll_interval,
            optimize=args.optimize,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
        subtitles(subtitle_args)


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-root",
        default=None,
        help="Path to an external douyin-agent project root. Defaults to the skill's bundled copy.",
    )
    parser.add_argument("--workdir", default=None, help="Directory for relative input/output paths. Defaults to cwd.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run douyin-agent blogger analysis scripts individually or as a pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Install/check the first-run environment.")
    add_common(setup_parser)
    setup_parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    setup_parser.set_defaults(func=ensure_setup)

    collect_parser = subparsers.add_parser("collect", help="Collect a creator's raw Douyin aweme posts.")
    add_common(collect_parser)
    collect_parser.add_argument("--profile-url", required=True)
    collect_parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Custom output JSON path. Avoid this unless the user explicitly asks; "
            "omitting it saves to data/<channel_name>/douyin_posts.json."
        ),
    )
    collect_parser.add_argument("--login-wait-rounds", type=int)
    collect_parser.add_argument("--max-idle-rounds", type=int)
    collect_parser.add_argument("--max-response-parse-retries", type=int)
    collect_parser.set_defaults(func=collect)

    download_parser = subparsers.add_parser("download", help="Download videos from douyin_posts.json.")
    add_common(download_parser)
    download_parser.add_argument("--input-json", type=Path, required=True)
    download_parser.add_argument("--output-dir", type=Path)
    download_parser.add_argument("--video-concurrency", type=int)
    download_parser.add_argument("--limit", type=int, help="Maximum newest videos to download. Use 0 for all.")
    download_parser.set_defaults(func=download)

    screenshot_parser = subparsers.add_parser("screenshots", help="Extract screenshots from downloaded videos.")
    add_common(screenshot_parser)
    screenshot_parser.add_argument("--channel-dir", type=Path, required=True)
    screenshot_parser.add_argument("--interval", type=float, default=1.0)
    screenshot_parser.add_argument("--duration", type=float, default=5.0)
    screenshot_parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    screenshot_parser.add_argument("--overwrite", action="store_true")
    screenshot_parser.set_defaults(func=screenshots)

    subtitle_parser = subparsers.add_parser("subtitles", help="Transcribe downloaded videos with AuralWise.")
    add_common(subtitle_parser)
    subtitle_parser.add_argument("--channel-dir", type=Path, required=True)
    subtitle_parser.add_argument("--api-key", help="AuralWise API key. Defaults to AURALWISE_API_KEY in the bundled script.")
    subtitle_parser.add_argument("--base-url", help="AuralWise API base URL. Defaults to https://api.auralwise.cn/v1.")
    subtitle_parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    subtitle_parser.add_argument("--request-interval", type=float, default=5.0)
    subtitle_parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between AuralWise task status polls.")
    subtitle_parser.add_argument("--optimize", dest="optimize", action="store_true", default=True)
    subtitle_parser.add_argument("--no-optimize", dest="optimize", action="store_false")
    subtitle_parser.add_argument("--overwrite", action="store_true")
    subtitle_parser.set_defaults(func=subtitles)

    pipeline_parser = subparsers.add_parser("pipeline", help="Run collect, download, screenshots, and optional subtitles.")
    add_common(pipeline_parser)
    pipeline_parser.add_argument("--profile-url", required=True)
    pipeline_parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Custom collected JSON path. Avoid this unless the user explicitly asks; "
            "omitting it saves to data/<channel_name>/douyin_posts.json."
        ),
    )
    pipeline_parser.add_argument("--channel-dir", type=Path)
    pipeline_parser.add_argument("--download-output-dir", type=Path)
    pipeline_parser.add_argument("--login-wait-rounds", type=int)
    pipeline_parser.add_argument("--max-idle-rounds", type=int)
    pipeline_parser.add_argument("--max-response-parse-retries", type=int)
    pipeline_parser.add_argument("--video-concurrency", type=int)
    pipeline_parser.add_argument("--limit", type=int, help="Maximum newest videos to download. Defaults to 10.")
    pipeline_parser.add_argument("--interval", type=float, default=1.0)
    pipeline_parser.add_argument("--duration", type=float, default=5.0)
    pipeline_parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    pipeline_parser.add_argument("--api-key", help="AuralWise API key. Defaults to AURALWISE_API_KEY in the bundled script.")
    pipeline_parser.add_argument("--base-url", help="AuralWise API base URL. Defaults to https://api.auralwise.cn/v1.")
    pipeline_parser.add_argument("--request-interval", type=float, default=5.0)
    pipeline_parser.add_argument("--poll-interval", type=float, default=5.0)
    pipeline_parser.add_argument("--optimize", dest="optimize", action="store_true", default=True)
    pipeline_parser.add_argument("--no-optimize", dest="optimize", action="store_false")
    pipeline_parser.add_argument("--overwrite", action="store_true")
    pipeline_parser.add_argument("--with-subtitles", action="store_true")
    pipeline_parser.add_argument("--skip-collect", action="store_true")
    pipeline_parser.add_argument("--skip-download", action="store_true")
    pipeline_parser.add_argument("--skip-screenshots", action="store_true")
    pipeline_parser.set_defaults(func=pipeline)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = args.func(args)
    if isinstance(result, Path):
        print(result)


if __name__ == "__main__":
    main()
