# poxiaoxing-skills

这个仓库用于存放个人 Codex skills。目前包含一个完整技能：

- `douyin-blogger-analysis`：抖音博主分析工具。它会采集博主作品数据、下载视频、抽取视频截图，并可选用 AuralWise 转写字幕。


## 目录结构

```text
.
├── README.md
├── LICENSE
└── douyin-blogger-analysis/
    ├── SKILL.md                         # Codex skill 入口说明
    ├── agents/openai.yaml               # 技能在 Codex 中的展示信息
    ├── scripts/douyin_blogger_analysis.py
    ├── references/workflow.md           # 更详细的运行流程和排障说明
    └── assets/douyin-agent/             # 随 skill 打包的实际采集/处理代码
```

## 技能能力

`douyin-blogger-analysis` 封装了四个主要步骤：

1. 采集指定抖音创作者主页的作品列表，保存为 `douyin_posts.json`。
2. 从采集结果中下载视频，默认下载最新 10 条。
3. 使用 `ffmpeg` 从视频中抽取截图。
4. 可选使用 AuralWise 生成 `subtitles.srt` 和 `transcript.json`。

输出默认保存在运行目录下的 `data/<频道名>/` 中。每个下载的视频作品会有独立目录，方便后续整理素材、做内容分析或交给其他 agent 继续处理。

## 环境要求

- Python 3.12+
- `uv`
- `ffmpeg`
- 可以登录抖音网页版的 Chrome 环境
- AuralWise API 配置，仅在需要字幕转写时必需
- Chrome 144+
- 配置 chrome://inspect/#remote-debugging 打开远程调试

字幕转写需要以下环境变量，或者在命令中传入同名参数：

```bash
export AURALWISE_API_KEY="asr_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

如果还没有 AuralWise 账号或 API Key，可以通过返利链接注册/开通并获得试用金：[https://auralwise.cn/refid=njybamgr](https://auralwise.cn/refid=njybamgr)。

## 安装到 Codex

把技能目录复制或链接到 Codex 的 skills 目录即可，例如：

```bash
mkdir -p ~/.codex/skills
ln -s "$(pwd)/douyin-blogger-analysis" ~/.codex/skills/douyin-blogger-analysis
```

安装后，在 Codex 中可以直接要求：

```text
Use $douyin-blogger-analysis to collect a Douyin creator profile, download videos, extract screenshots, and transcribe subtitles.
```

## 命令行使用

也可以不通过 Codex，直接运行技能自带 wrapper：

```bash
python douyin-blogger-analysis/scripts/douyin_blogger_analysis.py setup
```

首次运行 `setup` 会检查 `uv`、同步 Python 依赖，并检查 `ffmpeg`。如果缺少抖音登录状态，采集时会打开一个持久化 Chrome profile：

```text
~/.cache/douyin-agent/chrome-profile
```

### 采集作品列表

```bash
python douyin-blogger-analysis/scripts/douyin_blogger_analysis.py collect \
  --profile-url "https://www.douyin.com/user/PROFILE_ID"
```

默认输出：

```text
data/<频道名>/douyin_posts.json
```

首次采集可能需要在打开的浏览器窗口中手动登录抖音。登录后保持窗口打开，直到脚本捕获到作品接口响应。

### 获取视频评论

已有作品列表时，只需指定 JSON 和视频 ID：

```bash
python douyin-blogger-analysis/scripts/douyin_blogger_analysis.py comments \
  --input-json data/<频道名>/douyin_posts.json \
  --aweme-id "AWEME_ID"
```

程序会从匹配视频 item 的 `author.sec_uid` 获取 `PROFILE_ID`，无需再传 `--profile-url`。也可以继续使用带 `modal_id` 的 `--video-url`，或显式传入 `--profile-url` 和 `--aweme-id`。

### 下载视频

```bash
python douyin-blogger-analysis/scripts/douyin_blogger_analysis.py download \
  --input-json data/<频道名>/douyin_posts.json
```

默认下载最新 10 条。下载全部作品：

```bash
python douyin-blogger-analysis/scripts/douyin_blogger_analysis.py download \
  --input-json data/<频道名>/douyin_posts.json \
  --limit 0
```

### 抽取截图

```bash
python douyin-blogger-analysis/scripts/douyin_blogger_analysis.py screenshots \
  --channel-dir data/<频道名>
```

常用参数：

- `--interval 1`：每隔 1 秒抽一帧。
- `--duration 5`：默认只抽取视频前 5 秒。
- `--overwrite`：覆盖已有截图。
- `--ffmpeg-bin /path/to/ffmpeg`：指定 `ffmpeg` 路径。

### 转写字幕

```bash
python douyin-blogger-analysis/scripts/douyin_blogger_analysis.py subtitles \
  --channel-dir data/<频道名>
```

字幕转写会把视频音频抽取为 16 kHz mono MP3，提交到 AuralWise 异步任务接口，并轮询结果。脚本会为每个视频生成：

- `subtitles.srt`
- `transcript.json`
- `tasks.json` 进度记录

默认开启 AuralWise `optimize=true` 优化档，适合中文、英文、西班牙语、法语、葡萄牙语等场景，价格为 0.27/小时。需要更高识别质量、词/字级时间戳，或中英混读、专有名词、品牌名更准时，可使用 `--no-optimize` 切到标准档，价格为 0.6/小时。

| 档位 | 参数 | 费用 | 适合场景 | 时间戳 |
| --- | --- | --- | --- | --- |
| 优化档 | `optimize=true` | 0.27/小时 | 正文级精度、检索、摘要、听写 | 段级约 100ms，仅 `segments[].start/end` |
| 标准档 | `optimize=false` | 0.6/小时 | 更高识别质量、中英混读、专有名词、品牌名 | 词/字级约 40ms，包含 `segments[].words[]` |

同一个目录可以重复运行，脚本会跳过已有字幕结果并继续未完成的视频。

### 一键流水线

采集、下载、抽帧一次跑完：

```bash
python douyin-blogger-analysis/scripts/douyin_blogger_analysis.py pipeline \
  --profile-url "https://www.douyin.com/user/PROFILE_ID"
```

包含字幕转写：

```bash
python douyin-blogger-analysis/scripts/douyin_blogger_analysis.py pipeline \
  --profile-url "https://www.douyin.com/user/PROFILE_ID" \
  --with-subtitles
```

预览将执行的命令，不发起网络请求或媒体处理：

```bash
python douyin-blogger-analysis/scripts/douyin_blogger_analysis.py pipeline \
  --profile-url "https://www.douyin.com/user/PROFILE_ID" \
  --with-subtitles \
  --dry-run
```

## 常用参数

- `--workdir <目录>`：指定相对输入输出路径的根目录，避免把数据写进仓库。
- `--project-root <目录>`：使用外部 `douyin-agent` 项目，默认使用 skill 内置副本。
- `--skip-download`：流水线跳过视频下载。
- `--skip-screenshots`：流水线跳过截图抽取。
- `--with-subtitles`：流水线追加字幕转写。
- `--no-optimize`：字幕转写使用 AuralWise 标准档；默认开启优化档。
- `--video-concurrency 3`：调整视频下载并发。
- `--login-wait-rounds 600`：首次登录时增加等待轮数。

## 输出示例

```text
data/<频道名>/
├── douyin_posts.json
├── <作品目录>/
│   ├── video.mp4
│   ├── screenshots/
│   ├── subtitles.srt
│   └── transcript.json
└── tasks.json
```

## 注意事项

- 采集和下载需要访问抖音网络服务，可能受登录状态、风控、网络环境影响。
- 首次采集是交互式流程，需要在打开的 Chrome 窗口里完成登录。
- 不需要字幕时，不必配置 AuralWise。
- 下载默认只处理最新 10 条作品；需要全量下载时使用 `--limit 0`。
- 正常采集建议不要手动传 `--output`，让脚本使用默认的 `data/<频道名>/douyin_posts.json`，后续步骤会更好衔接。
- 更细的参数说明和排障流程见 `douyin-blogger-analysis/references/workflow.md`。

## 许可证

本仓库使用 Apache License 2.0，详见 `LICENSE`。
