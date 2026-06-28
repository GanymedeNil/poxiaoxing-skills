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

DASHSCOPE_API_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
DASHSCOPE_DEFAULT_REGION = "cn-beijing"
DASHSCOPE_DEFAULT_MODEL = "fun-asr-flash-2026-06-15"
DASHSCOPE_AUDIO_FORMAT = "mp3"
DASHSCOPE_AUDIO_SAMPLE_RATE = "16000"
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
        return {"version": 1, "provider": "dashscope-fun-asr", "tasks": []}
    return json.loads(tasks_file.read_text(encoding="utf-8"))


def save_task_state(tasks_file: Path, state: dict[str, Any]) -> None:
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    state["provider"] = "dashscope-fun-asr"
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
        DASHSCOPE_AUDIO_SAMPLE_RATE,
        "-f",
        DASHSCOPE_AUDIO_FORMAT,
        str(audio_path),
    ]


def build_dashscope_url(workspace_id: str, *, region: str = DASHSCOPE_DEFAULT_REGION) -> str:
    return f"https://{workspace_id}.{region}.maas.aliyuncs.com{DASHSCOPE_API_PATH}"


def build_generation_payload(audio_path: Path, *, model: str = DASHSCOPE_DEFAULT_MODEL) -> dict[str, Any]:
    base64_str = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    data_uri = f"data:audio/{DASHSCOPE_AUDIO_FORMAT};base64,{base64_str}"
    return {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": data_uri,
                            },
                        }
                    ],
                }
            ]
        },
        "parameters": {
            "format": DASHSCOPE_AUDIO_FORMAT,
            "sample_rate": DASHSCOPE_AUDIO_SAMPLE_RATE,
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


def _dashscope_sentences(result: dict[str, Any]) -> list[dict[str, Any]]:
    sentence = result.get("output", {}).get("sentence")
    if isinstance(sentence, dict):
        return [sentence]
    if isinstance(sentence, list):
        return [item for item in sentence if isinstance(item, dict)]
    return []


def segments_to_srt(result: dict[str, Any]) -> str:
    blocks: list[str] = []
    for index, sentence in enumerate(_dashscope_sentences(result), start=1):
        text = str(sentence.get("text") or "").strip()
        if not text:
            continue
        start = format_srt_timestamp_ms(int(sentence.get("begin_time") or 0))
        end = format_srt_timestamp_ms(int(sentence.get("end_time") or sentence.get("begin_time") or 0))
        blocks.append(f"{index}\n{start} --> {end}\n{text}")

    if not blocks:
        text = str(result.get("output", {}).get("text") or "").strip()
        if text:
            duration_seconds = float(result.get("usage", {}).get("duration") or 0)
            blocks.append(f"1\n00:00:00,000 --> {format_srt_timestamp(duration_seconds)}\n{text}")

    return "\n\n".join(blocks) + ("\n" if blocks else "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe video audio into subtitles using DashScope FunASR.")
    parser.add_argument("input_path", type=Path, help="A video file or directory containing downloaded video.mp4 files.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DASHSCOPE_API_KEY"),
        help="DashScope API key. Defaults to DASHSCOPE_API_KEY.",
    )
    parser.add_argument(
        "--workspace-id",
        default=os.environ.get("DASHSCOPE_WORKSPACE_ID"),
        help="DashScope workspace ID used in https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com. Defaults to DASHSCOPE_WORKSPACE_ID.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DASHSCOPE_BASE_URL"),
        help="Full DashScope multimodal generation endpoint. Overrides --workspace-id.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("DASHSCOPE_ASR_MODEL", DASHSCOPE_DEFAULT_MODEL),
        help=f"DashScope ASR model. Defaults to {DASHSCOPE_DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--request-interval",
        type=positive_float,
        default=DEFAULT_REQUEST_INTERVAL_SECONDS,
        help="Seconds between DashScope requests. Defaults to 5.",
    )
    parser.add_argument(
        "--poll-interval",
        type=positive_float,
        default=5.0,
        help="Accepted for wrapper compatibility; DashScope FunASR returns synchronously.",
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
    return parser.parse_args()


class DashScopeClient:
    def __init__(
        self,
        api_key: str,
        *,
        workspace_id: str | None = None,
        base_url: str | None = None,
        model: str = DASHSCOPE_DEFAULT_MODEL,
    ) -> None:
        if not base_url and not workspace_id:
            raise ValueError("DashScope workspace ID is required unless --base-url is provided.")
        self.api_key = api_key
        self.base_url = (base_url or build_dashscope_url(str(workspace_id))).rstrip("/")
        self.model = model

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "disable",
        }

    async def transcribe(self, session: aiohttp.ClientSession, audio_path: Path) -> dict[str, Any]:
        payload = build_generation_payload(audio_path, model=self.model)
        async with session.post(self.base_url, headers=self._headers(), json=payload) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"DashScope API {response.status}: {body}")
            data = json.loads(body)
            if "output" not in data:
                raise RuntimeError(f"DashScope response missing output: {body}")
            return data


def extract_audio(video_path: Path, audio_path: Path, *, ffmpeg_bin: str) -> None:
    command = build_audio_extract_command(video_path, audio_path, ffmpeg_bin=ffmpeg_bin)
    subprocess.run(command, check=True)


def _safe_audio_name(video_path: Path, index: int) -> str:
    parent = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in video_path.parent.name)
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in video_path.stem)
    return f"{index:04d}_{parent}_{stem}.mp3"


async def transcribe_video(
    video_path: Path,
    client: DashScopeClient,
    *,
    ffmpeg_bin: str,
    overwrite: bool,
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
            result = await client.transcribe(session, audio_path)

    transcript_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    subtitle_path.write_text(segments_to_srt(result), encoding="utf-8")
    print(f"Wrote subtitles: {subtitle_path}", file=sys.stderr)
    return True


async def transcribe_videos_batch(
    video_files: list[Path],
    client: DashScopeClient,
    *,
    request_interval: float,
    ffmpeg_bin: str,
    overwrite: bool,
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
                        "provider": "dashscope-fun-asr",
                        "model": client.model,
                        "status": "preparing_audio",
                        "submitted_at": None,
                        "updated_at": now,
                        "result_written": False,
                        "subtitle_path": str(subtitle_path),
                        "transcript_path": str(transcript_path),
                        "request_id": None,
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

                    result = await client.transcribe(session, audio_path)
                    summary["submitted"] += 1
                    transcript_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    subtitle_path.write_text(segments_to_srt(result), encoding="utf-8")
                    task["status"] = "done"
                    task["request_id"] = result.get("request_id")
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
        raise SystemExit("Missing DashScope API key. Set DASHSCOPE_API_KEY or pass --api-key.")
    if not args.workspace_id and not args.base_url:
        raise SystemExit("Missing DashScope workspace ID. Set DASHSCOPE_WORKSPACE_ID, pass --workspace-id, or pass --base-url.")
    if not args.input_path.exists():
        raise SystemExit(f"Input path does not exist: {args.input_path}")
    if shutil.which(args.ffmpeg_bin) is None:
        raise SystemExit(f"ffmpeg executable not found: {args.ffmpeg_bin}")

    video_files = find_video_files(args.input_path)
    if not video_files:
        raise SystemExit(f"No video files found under: {args.input_path}")

    client = DashScopeClient(args.api_key, workspace_id=args.workspace_id, base_url=args.base_url, model=args.model)
    summary = await transcribe_videos_batch(
        video_files,
        client,
        request_interval=args.request_interval,
        ffmpeg_bin=args.ffmpeg_bin,
        overwrite=args.overwrite,
        tasks_file=default_tasks_file(args.input_path),
    )

    if summary["failed"]:
        raise SystemExit(1)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
