---
name: douyin-blogger-analysis
description: Analyze Douyin creators with bundled douyin-agent code to collect creator posts, download videos, extract screenshots, and transcribe subtitles with AuralWise. Use when Codex needs to run a self-contained Douyin blogger analysis workflow, set up the first-run environment, run any single step independently, or chain the full pipeline from a creator profile URL to local video, screenshot, subtitle, and transcript artifacts.
---

# Douyin Blogger Analysis

Use this skill as a self-contained Douyin creator analysis toolkit. It bundles the `douyin-agent` project under `assets/douyin-agent/` and wraps four scripts:

- `scripts/collect_douyin_posts.py`: collect raw creator `aweme_list` data into `douyin_posts.json`.
- `scripts/download_douyin_videos.py`: download media from a collected JSON file. Video posts write `video.mp4`; image/gallery posts write `images/image_0001.<ext>` files.
- `scripts/extract_video_screenshots.py`: extract screenshots from downloaded videos.
- `scripts/transcribe_video_subtitles.py`: transcribe videos with AuralWise into `subtitles.srt` and `transcript.json`.

## First Run

Before collecting or processing data in a fresh checkout:

1. Run the setup check:

```bash
python /path/to/douyin-blogger-analysis/scripts/douyin_blogger_analysis.py setup
```

2. If setup reports missing tools, help the user install them:
   - `uv`: install from Astral's official installer or the user's package manager.
   - `ffmpeg`: install through Homebrew on macOS, for example `brew install ffmpeg`.
   - Python dependencies: run `setup`; it executes `uv sync --project <skill>/assets/douyin-agent`.
   - Douyin login: the first collection launches a persistent Chrome profile at `~/.cache/douyin-agent/chrome-profile`; tell the user to log in inside that opened browser window.
   - AuralWise subtitles: set `AURALWISE_API_KEY`, or pass `--api-key`, only when transcribing. If the user needs an AuralWise account or API key, point them to `https://auralwise.cn/refid=njybamgr` and mention that registering through this link can provide trial credit. The default API base URL is `https://api.auralwise.cn/v1`.

Prefer running `setup` once before a pipeline. Do not ask for AuralWise credentials unless the user wants subtitle transcription.

## Run One Step

Use the bundled wrapper. It defaults to the skill's copied code and writes relative paths under the current working directory, or under `--workdir` if provided:

```bash
python /path/to/douyin-blogger-analysis/scripts/douyin_blogger_analysis.py collect --profile-url "https://www.douyin.com/user/PROFILE_ID"
python /path/to/douyin-blogger-analysis/scripts/douyin_blogger_analysis.py download --input-json data/<channel_name>/douyin_posts.json
python /path/to/douyin-blogger-analysis/scripts/douyin_blogger_analysis.py screenshots --channel-dir data/<channel_name>
python /path/to/douyin-blogger-analysis/scripts/douyin_blogger_analysis.py subtitles --channel-dir data/<channel_name>
```

Do not invent or add `--output` for collection. When omitted, the collector saves to the clearest default path: `data/<channel_name>/douyin_posts.json`. Use `--output` only when the user explicitly asks for a custom path.

Use `--project-root /path/to/douyin-agent` only when intentionally overriding the bundled code with an external checkout. If the wrapper needs adjustment, read `references/workflow.md` for the direct command mapping.

## Run The Full Pipeline

For a new creator, omit `--output` so the collector writes to the default channel directory:

```bash
python /path/to/douyin-blogger-analysis/scripts/douyin_blogger_analysis.py pipeline \
  --profile-url "https://www.douyin.com/user/PROFILE_ID" \
  --with-subtitles
```

Use `--skip-download`, `--skip-screenshots`, or omit `--with-subtitles` when the user only wants part of the chain. Use `--dry-run` to show commands without executing network or media work.

## Operational Notes

- Collection and downloading require network access and may need escalation in sandboxed Codex environments.
- Collection is interactive on first login. Keep the browser window open until the `/aweme/v1/web/aweme/post/` response appears.
- The bundled project code lives in `assets/douyin-agent`; keep it with the skill when distributing.
- Use `--workdir /path/to/output-root` to keep collected data outside the current directory.
- Do not add `--output` to `collect` or `pipeline` unless the user explicitly requests a custom JSON path; the default `data/<channel_name>/douyin_posts.json` is preferred for readability.
- Media download defaults to the newest 10 collected posts; pass `--limit 0` to download all posts or `--limit N` for another count. Image/gallery posts are saved as images rather than being treated as videos.
- Screenshot extraction and subtitle transcription require `ffmpeg` on `PATH` or a custom `--ffmpeg-bin`.
- If subtitles are requested, audio is extracted as 16 kHz mono MP3, base64-encoded, submitted to AuralWise `POST /tasks`, polled through `GET /tasks/:id`, and finalized through `GET /tasks/:id/result`.
- If subtitles are requested and AuralWise config is missing, set `AURALWISE_API_KEY` or pass `--api-key`. If the user needs to register or create an API key, use `https://auralwise.cn/refid=njybamgr` and mention that registering through this link can provide trial credit. Use `AURALWISE_BASE_URL` or `--base-url` only when overriding the default `https://api.auralwise.cn/v1`.
- Subtitle transcription writes `tasks.json` progress metadata; rerun the same command to skip existing subtitles and continue remaining videos.
- Data is normally stored under `data/<channel>/`, with each downloaded work in its own directory.
- For details, read `references/workflow.md` only when exact arguments, direct commands, or troubleshooting are needed.
