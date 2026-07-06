from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

AURALWISE_DEFAULT_BASE_URL = "https://api.auralwise.cn/v1"
AURALWISE_AUDIO_FORMAT = "mp3"
AURALWISE_AUDIO_SAMPLE_RATE = "16000"
AURALWISE_PROVIDER = "auralwise"
DEFAULT_REQUEST_INTERVAL_SECONDS = 5.0
AudioExtractor = Callable[[Path, Path], None]


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Expected a positive number")
    return parsed


def find_video_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.rglob("video.mp4"))


def has_existing_subtitles(subtitle_path: Path) -> bool:
    return subtitle_path.exists() and subtitle_path.stat().st_size > 0


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def default_tasks_file(input_path: Path) -> Path:
    if input_path.is_file():
        return input_path.parent / "tasks.json"
    return input_path / "tasks.json"


def load_task_state(tasks_file: Path) -> dict[str, Any]:
    if not tasks_file.exists():
        return {"version": 1, "provider": AURALWISE_PROVIDER, "tasks": []}
    return json.loads(tasks_file.read_text(encoding="utf-8"))


def save_task_state(tasks_file: Path, state: dict[str, Any]) -> None:
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    state["provider"] = AURALWISE_PROVIDER
    tasks_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def find_task_entry(state: dict[str, Any], video_path: Path) -> dict[str, Any] | None:
    video_key = str(video_path)
    for task in state.get("tasks", []):
        if task.get("video_path") == video_key:
            return task
    return None


def build_audio_extract_command(video_path: Path, audio_path: Path, *, ffmpeg_bin: str) -> list[str]:
    return [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        AURALWISE_AUDIO_SAMPLE_RATE,
        "-f",
        AURALWISE_AUDIO_FORMAT,
        str(audio_path),
    ]


def build_task_payload(
    audio_path: Path,
    *,
    audio_filename: str | None = None,
    optimize: bool = True,
) -> dict[str, Any]:
    base64_str = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    return {
        "audio_base64": base64_str,
        "audio_filename": audio_filename or audio_path.name,
        "options": {
            "enable_asr": True,
            "enable_diarize": False,
            "enable_audio_events": False,
            "optimize": optimize,
        },
    }


def format_srt_timestamp(seconds: float) -> str:
    milliseconds_total = int(seconds * 1000)
    return format_srt_timestamp_ms(milliseconds_total)


def format_srt_timestamp_ms(milliseconds_total: int) -> str:
    milliseconds = milliseconds_total % 1000
    total_seconds = milliseconds_total // 1000
    secs = total_seconds % 60
    minutes_total = total_seconds // 60
    minutes = minutes_total % 60
    hours = minutes_total // 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _auralwise_segments(result: dict[str, Any]) -> list[dict[str, Any]]:
    segments = result.get("segments")
    if isinstance(segments, list):
        return [item for item in segments if isinstance(item, dict)]
    return []


def segments_to_srt(result: dict[str, Any]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(_auralwise_segments(result), start=1):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start_seconds = float(segment.get("start") or 0)
        end_seconds = float(segment.get("end") or segment.get("start") or 0)
        start = format_srt_timestamp_ms(int(start_seconds * 1000))
        end = format_srt_timestamp_ms(int(end_seconds * 1000))
        blocks.append(f"{index}\n{start} --> {end}\n{text}")

    if not blocks:
        text = str(result.get("text") or "").strip()
        if text:
            duration_seconds = float(result.get("audio_duration") or 0)
            blocks.append(f"1\n00:00:00,000 --> {format_srt_timestamp(duration_seconds)}\n{text}")

    return "\n\n".join(blocks) + ("\n" if blocks else "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe video audio into subtitles using AuralWise.")
    parser.add_argument("input_path", type=Path, help="A video file or directory containing downloaded video.mp4 files.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("AURALWISE_API_KEY"),
        help="AuralWise API key. Defaults to AURALWISE_API_KEY.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("AURALWISE_BASE_URL", AURALWISE_DEFAULT_BASE_URL),
        help=f"AuralWise API base URL. Defaults to {AURALWISE_DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--request-interval",
        type=positive_float,
        default=DEFAULT_REQUEST_INTERVAL_SECONDS,
        help="Seconds between AuralWise task submissions. Defaults to 5.",
    )
    parser.add_argument(
        "--poll-interval",
        type=positive_float,
        default=5.0,
        help="Seconds between AuralWise task status polls. Defaults to 5.",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        default="ffmpeg",
        help="ffmpeg executable to use. Defaults to ffmpeg on PATH.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate subtitles even when subtitles.srt already exists.",
    )
    parser.add_argument(
        "--optimize",
        dest="optimize",
        action="store_true",
        default=True,
        help="Use AuralWise optimized mode when supported. Enabled by default.",
    )
    parser.add_argument(
        "--no-optimize",
        dest="optimize",
        action="store_false",
        help="Disable AuralWise optimized mode.",
    )
    return parser.parse_args()


class AuralWiseClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = AURALWISE_DEFAULT_BASE_URL,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    async def create_task(
        self,
        session: aiohttp.ClientSession,
        audio_path: Path,
        *,
        audio_filename: str | None = None,
        optimize: bool = True,
    ) -> dict[str, Any]:
        payload = build_task_payload(audio_path, audio_filename=audio_filename, optimize=optimize)
        async with session.post(f"{self.base_url}/tasks", headers=self._headers(), json=payload) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"AuralWise create task API {response.status}: {body}")
            data = json.loads(body)
            if "id" not in data:
                raise RuntimeError(f"AuralWise create task response missing id: {body}")
            return data

    async def get_task(self, session: aiohttp.ClientSession, task_id: str) -> dict[str, Any]:
        async with session.get(f"{self.base_url}/tasks/{task_id}", headers=self._headers()) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"AuralWise task status API {response.status}: {body}")
            return json.loads(body)

    async def get_result(self, session: aiohttp.ClientSession, task_id: str) -> dict[str, Any]:
        async with session.get(f"{self.base_url}/tasks/{task_id}/result", headers=self._headers()) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"AuralWise task result API {response.status}: {body}")
            data = json.loads(body)
            if "segments" not in data:
                raise RuntimeError(f"AuralWise result response missing segments: {body}")
            return data

    async def transcribe(
        self,
        session: aiohttp.ClientSession,
        audio_path: Path,
        *,
        audio_filename: str | None = None,
        optimize: bool = True,
        poll_interval: float = 5.0,
    ) -> dict[str, Any]:
        task = await self.create_task(
            session,
            audio_path,
            audio_filename=audio_filename,
            optimize=optimize,
        )
        task_id = str(task["id"])
        while True:
            current = await self.get_task(session, task_id)
            status = current.get("status")
            if status == "done":
                result = await self.get_result(session, task_id)
                result.setdefault("task_id", task_id)
                return result
            if status in {"failed", "abandoned"}:
                error = current.get("error_message") or "unknown error"
                raise RuntimeError(f"AuralWise task {task_id} {status}: {error}")
            await asyncio.sleep(poll_interval)


def extract_audio(video_path: Path, audio_path: Path, *, ffmpeg_bin: str) -> None:
    command = build_audio_extract_command(video_path, audio_path, ffmpeg_bin=ffmpeg_bin)
    subprocess.run(command, check=True)


def _safe_audio_name(video_path: Path, index: int) -> str:
    parent = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in video_path.parent.name)
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in video_path.stem)
    return f"{index:04d}_{parent}_{stem}.mp3"


async def transcribe_video(
    video_path: Path,
    client: AuralWiseClient,
    *,
    ffmpeg_bin: str,
    overwrite: bool,
    poll_interval: float = 5.0,
    optimize: bool = True,
) -> bool:
    subtitle_path = video_path.parent / "subtitles.srt"
    transcript_path = video_path.parent / "transcript.json"
    if has_existing_subtitles(subtitle_path) and not overwrite:
        print(f"Skipping existing subtitles: {subtitle_path}", file=sys.stderr)
        return True

    with tempfile.TemporaryDirectory(prefix="douyin-agent-audio-") as tmp_dir:
        audio_path = Path(tmp_dir) / f"{video_path.stem}.mp3"
        extract_audio(video_path, audio_path, ffmpeg_bin=ffmpeg_bin)
        async with aiohttp.ClientSession() as session:
            result = await client.transcribe(
                session,
                audio_path,
                audio_filename=audio_path.name,
                poll_interval=poll_interval,
                optimize=optimize,
            )

    transcript_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    subtitle_path.write_text(segments_to_srt(result), encoding="utf-8")
    print(f"Wrote subtitles: {subtitle_path}", file=sys.stderr)
    return True


async def transcribe_videos_batch(
    video_files: list[Path],
    client: AuralWiseClient,
    *,
    request_interval: float,
    poll_interval: float,
    ffmpeg_bin: str,
    overwrite: bool,
    optimize: bool,
    tasks_file: Path,
    audio_extractor: Callable[..., None] = extract_audio,
    sleep_func: Callable[[float], Any] = asyncio.sleep,
) -> dict[str, int]:
    summary = {"submitted": 0, "completed": 0, "skipped": 0, "failed": 0}
    state = load_task_state(tasks_file)

    with tempfile.TemporaryDirectory(prefix="douyin-agent-audio-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        async with aiohttp.ClientSession() as session:
            for index, video_path in enumerate(video_files, start=1):
                subtitle_path = video_path.parent / "subtitles.srt"
                transcript_path = video_path.parent / "transcript.json"
                task = find_task_entry(state, video_path)

                if has_existing_subtitles(subtitle_path) and not overwrite:
                    summary["skipped"] += 1
                    if task:
                        task["status"] = task.get("status") or "done"
                        task["result_written"] = True
                        task["updated_at"] = utc_now_iso()
                        save_task_state(tasks_file, state)
                    print(f"Skipping existing subtitles: {subtitle_path}", file=sys.stderr)
                    continue

                audio_path = tmp_path / _safe_audio_name(video_path, index)
                now = utc_now_iso()
                if not task or overwrite:
                    if overwrite and task:
                        state["tasks"] = [
                            existing
                            for existing in state.get("tasks", [])
                            if existing.get("video_path") != str(video_path)
                        ]
                    task = {
                        "video_path": str(video_path),
                        "audio_filename": audio_path.name,
                        "provider": AURALWISE_PROVIDER,
                        "status": "preparing_audio",
                        "submitted_at": None,
                        "updated_at": now,
                        "result_written": False,
                        "subtitle_path": str(subtitle_path),
                        "transcript_path": str(transcript_path),
                        "task_id": None,
                        "error_message": None,
                    }
                    state.setdefault("tasks", []).append(task)

                try:
                    audio_extractor(video_path, audio_path, ffmpeg_bin=ffmpeg_bin)
                    if summary["submitted"] > 0:
                        await sleep_func(request_interval)
                    task["status"] = "submitted"
                    task["submitted_at"] = task.get("submitted_at") or utc_now_iso()
                    task["updated_at"] = utc_now_iso()
                    save_task_state(tasks_file, state)

                    result = await client.transcribe(
                        session,
                        audio_path,
                        audio_filename=audio_path.name,
                        poll_interval=poll_interval,
                        optimize=optimize,
                    )
                    summary["submitted"] += 1
                    transcript_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    subtitle_path.write_text(segments_to_srt(result), encoding="utf-8")
                    task["status"] = "done"
                    task["task_id"] = result.get("task_id")
                    task["result_written"] = True
                    task["error_message"] = None
                    task["updated_at"] = utc_now_iso()
                    save_task_state(tasks_file, state)
                    summary["completed"] += 1
                    print(f"Wrote subtitles: {subtitle_path}", file=sys.stderr)
                except Exception as exc:  # noqa: BLE001
                    summary["failed"] += 1
                    task["status"] = "failed"
                    task["error_message"] = str(exc)
                    task["updated_at"] = utc_now_iso()
                    save_task_state(tasks_file, state)
                    print(f"[subtitle-error] {video_path}: {exc}", file=sys.stderr)

    return summary


async def main_async() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("Missing AuralWise API key. Set AURALWISE_API_KEY or pass --api-key.")
    if not args.input_path.exists():
        raise SystemExit(f"Input path does not exist: {args.input_path}")
    if shutil.which(args.ffmpeg_bin) is None:
        raise SystemExit(f"ffmpeg executable not found: {args.ffmpeg_bin}")

    video_files = find_video_files(args.input_path)
    if not video_files:
        raise SystemExit(f"No video files found under: {args.input_path}")

    client = AuralWiseClient(args.api_key, base_url=args.base_url)
    summary = await transcribe_videos_batch(
        video_files,
        client,
        request_interval=args.request_interval,
        poll_interval=args.poll_interval,
        ffmpeg_bin=args.ffmpeg_bin,
        overwrite=args.overwrite,
        optimize=args.optimize,
        tasks_file=default_tasks_file(args.input_path),
    )

    if summary["failed"]:
        raise SystemExit(1)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
