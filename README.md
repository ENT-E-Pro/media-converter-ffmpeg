# Media Converter for AI Agents (FFmpeg)

[![Run on Apify](https://apify.com/ext/run-on-apify.png)](https://apify.com/ent-e/media-converter-ffmpeg)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A pay-per-file **FFmpeg API** for AI agents and automations, running as an [Apify Actor](https://apify.com/ent-e/media-converter-ffmpeg). Send a public audio or video URL and get back an MP3, a Whisper-ready 16 kHz mono WAV, a web-ready MP4, a GIF or a thumbnail, with no ffmpeg install, no Docker and no server to run.

This repository contains the Actor's source code, tests and example inputs. The hosted version is on the Apify Store:

**▶ [Run it on Apify](https://apify.com/ent-e/media-converter-ffmpeg)**

## What it does

You give it a media URL and a target format. It downloads the file, runs FFmpeg, stores the result in the run's key-value store, and returns one dataset row per file with a download link (`outputUrl`) and technical metadata (from ffprobe).

| Target (`target`) | What you get |
|---|---|
| `wav16k` | Mono 16 kHz WAV, ready for Whisper and other speech-to-text models |
| `mp3`, `wav`, `flac`, `ogg`, `m4a` | Audio extracted from video, or audio transcoded between formats |
| `mp4` | H.264 MP4 with faststart, for web playback (max 1080p) |
| `webm` | VP9 WebM (max 1080p) |
| `gif` | Animated preview, 8 fps, 360 px wide, no audio |
| `thumbnail` | A JPEG frame: the first frame, or the frame at `startSeconds` |

- **Trim** with `startSeconds` / `durationSeconds` (top level or per file).
- **Video capped at 1080p:** larger inputs are scaled down to fit 1920×1080 (or 1080×1920 portrait). Inputs already in the target codec (H.264 for `mp4`; VP9/VP8/AV1 for `webm`), 8-bit 4:2:0 and ≤1080p are remuxed without re-encoding when no trim is set. Re-encoding above 720p is billed at the HD re-encode rate (see Pricing).
- **Batch** many URLs in one run with a `files` array, each with its own target and trim.
- **Failed files are free:** an unreadable or unreachable file comes back as a `status: "error"` row and the run continues.
- **Agent-friendly:** use it from MCP clients (Claude, Cursor) through Apify's MCP server, n8n, Make, Zapier or plain HTTP.

## Quick start

You need an Apify account and an API token (Apify Console → Settings → API & Integrations). Keep the token in an environment variable, never in code:

```bash
export APIFY_TOKEN=<your-apify-token>
```

### curl (run synchronously and get dataset items)

```bash
curl -X POST "https://api.apify.com/v2/acts/ent-e~media-converter-ffmpeg/run-sync-get-dataset-items" \
  -H "Authorization: Bearer $APIFY_TOKEN" \
  -H "Content-Type: application/json" \
  -d @examples/single-wav16k-for-whisper.json
```

Synchronous calls wait up to 5 minutes. For long files or big batches, start an asynchronous run (`POST /v2/acts/ent-e~media-converter-ffmpeg/runs`) and read the dataset when it finishes.

### Python (`pip install apify-client`)

```python
import os
from apify_client import ApifyClient

client = ApifyClient(os.environ["APIFY_TOKEN"])
run = client.actor("ent-e/media-converter-ffmpeg").call(
    run_input={"url": "https://download.samplelib.com/mp4/sample-5s.mp4", "target": "wav16k"}
)
for item in client.dataset(run["defaultDatasetId"]).iterate_items():
    print(item["status"], item.get("outputUrl"), item.get("error"))
```

### JavaScript / Node.js (`npm install apify-client`)

```js
import { ApifyClient } from 'apify-client';

const client = new ApifyClient({ token: process.env.APIFY_TOKEN });
const run = await client.actor('ent-e/media-converter-ffmpeg').call({
    url: 'https://download.samplelib.com/mp4/sample-5s.mp4',
    target: 'mp3',
});
const { items } = await client.dataset(run.defaultDatasetId).listItems();
for (const item of items) console.log(item.status, item.outputUrl ?? item.error);
```

### MCP (Claude, Cursor and other MCP clients)

```json
{
  "mcpServers": {
    "apify": { "url": "https://mcp.apify.com?tools=ent-e/media-converter-ffmpeg" }
  }
}
```

### n8n / Make / Zapier

Use the official Apify integration ("Run an Actor and get dataset" in n8n, "Run an Actor" + "Get Dataset Items" in Make, "Run Actor and Fetch Results" in Zapier), select `ent-e/media-converter-ffmpeg`, paste an input from [`examples/`](examples/) and map each item's `outputUrl` into the next step.

**Ready-made templates:** importable n8n workflows and Make blueprints for this Actor are in [ENT-E-Pro/automation-templates](https://github.com/ENT-E-Pro/automation-templates).

## Example inputs

See [`examples/`](examples/):

| File | What it does |
|---|---|
| [`single-wav16k-for-whisper.json`](examples/single-wav16k-for-whisper.json) | One video → mono 16 kHz WAV for speech-to-text |
| [`video-to-mp3.json`](examples/video-to-mp3.json) | Extract audio from a video as MP3 |
| [`gif-preview-trimmed.json`](examples/gif-preview-trimmed.json) | Short animated GIF from a trimmed segment |
| [`thumbnail.json`](examples/thumbnail.json) | JPEG frame at a given second |
| [`batch-mixed-targets.json`](examples/batch-mixed-targets.json) | Batch with a default target and per-file overrides |

## Input

| Field | Type | Default | Description |
|---|---|---|---|
| `url` | string | – | Public URL of one audio/video file (max 2 GB). |
| `target` | string | `mp3` | `mp3`, `wav`, `wav16k`, `flac`, `ogg`, `m4a`, `mp4`, `webm`, `gif`, `thumbnail`. Default for all files in a batch. |
| `startSeconds` | integer | – | Trim start (for `thumbnail`: the frame time). Default for every `files` item. |
| `durationSeconds` | integer | – | Seconds to keep from `startSeconds`. Default for every `files` item. |
| `files` | array | – | Batch: items with `url` and optional `target`, `startSeconds`, `durationSeconds`. Plain URL strings also work. |
| `maxDurationSeconds` | integer | `10800` | Safety cap on input duration; longer inputs are rejected (free) unless trimmed. |

The full schema is in [`.actor/input_schema.json`](.actor/input_schema.json).

## Output

One dataset row per input file:

```json
{
  "sourceUrl": "https://example.com/recordings/team-call.mp4",
  "target": "wav16k",
  "status": "ok",
  "outputKey": "57f39f65123e.wav",
  "outputUrl": "https://api.apify.com/v2/key-value-stores/<storeId>/records/57f39f65123e.wav",
  "mime": "audio/wav",
  "outputBytes": 1920078,
  "outputDurationSeconds": 60.0,
  "input": {
    "format": "mov,mp4,m4a,3gp,3g2,mj2",
    "duration": 60.0,
    "sizeBytes": 4821337,
    "video": { "codec": "h264", "width": 1280, "height": 720 },
    "audio": { "codec": "aac", "sampleRate": 44100, "channels": 2 }
  },
  "billedMinutes": 1,
  "minuteEvent": "media-minute",
  "videoReencoded": null
}
```

A failed file returns `sourceUrl`, `target`, `status: "error"` and `error`, and is not charged. `minuteEvent` shows which per-minute event was charged (`media-minute`, `video-reencode-minute`, or `null` for thumbnails), and `videoReencoded` shows, for `mp4`/`webm`, whether the video was re-encoded (`true`) or copied (`false`).

## Pricing (pay per event on Apify)

| Event | Price |
|---|---|
| File converted (per successful output file) | **$0.005** ($5 per 1,000 files) |
| Media minute (per started minute of output audio, GIF, stream-copied video, or video re-encoded at 720p or lower; thumbnails excluded) | **$0.002** |
| HD video re-encode minute (per started minute when `mp4`/`webm` video is actually re-encoded above 720p, i.e. 1080p-class after the cap; charged *instead of* the media minute, never both) | **$0.04** |
| Actor start (per run, per GB of memory) | $0.00005 |

Failed files are free. Examples: 1,000 thumbnails ≈ $5.00; a 10-minute podcast to `wav16k` ≈ $0.025; a 6-second GIF ≈ $0.007; a 30-second 1080p H.264 MP4 copied to `mp4` ≈ $0.007; the same clip re-encoded at 1080p (e.g. trimmed, HEVC/MOV source, or WebM output) ≈ $0.045; a 30-second 720p re-encode ≈ $0.007. HD re-encoding costs more because 1080p encoding takes about 8× real time on the default 1 GB run. See the [Store page](https://apify.com/ent-e/media-converter-ffmpeg) for current pricing.

## Development

Requires Python 3.13 and `ffmpeg` on your PATH for local runs.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q tests
```

Local run with the Apify SDK: put your input in `storage/key_value_stores/default/INPUT.json`, then:

```bash
CRAWLEE_STORAGE_DIR=./storage .venv/bin/python -m src
```

Project layout:

```
.actor/        Actor definition, input/output/dataset schemas, pay-per-event config
src/           Actor entrypoint (main.py) and FFmpeg conversion logic (core.py)
tests/         Unit tests for the conversion presets and billing helpers
examples/      Sample input JSON
Dockerfile     Apify Python base image + ffmpeg
```

## ENT-E media toolkit

Every tool takes a public file URL, needs nothing installed, charges per file, doesn't charge for failures, and works from MCP, n8n, Make, Zapier and the API.

- **[Media Converter for AI Agents (FFmpeg)](https://apify.com/ent-e/media-converter-ffmpeg)**: audio/video to MP3, Whisper-ready 16 kHz WAV, MP4, WebM, GIF or thumbnails, with trim and batch.
- **[Image Converter & Compressor](https://apify.com/ent-e/image-converter-compressor)**: convert to WebP or AVIF, compress to a target file size, resize, strip EXIF/GPS.
- **[Podcast Loudness Normalizer](https://apify.com/ent-e/podcast-loudness-normalizer)**: normalize to -16, -14, -23 or -24 LUFS with a true-peak ceiling, plus a before/after report.
- **[Video to Captions](https://apify.com/ent-e/video-to-captions-whisper)**: SRT, VTT, TXT and word-level JSON from any video or audio URL, with Whisper.
- **[Video to Shorts](https://apify.com/ent-e/video-to-shorts-captions)**: vertical 9:16 clips with burned-in animated captions for TikTok, Reels and Shorts.

## Also from ENT-E

[Image Converter & Compressor](https://apify.com/ent-e/image-converter-compressor) ([source](https://github.com/ENT-E-Pro/image-converter-compressor)): WebP, AVIF, JPEG and more, target file size and EXIF/GPS stripping, at $1 per 1,000 images.

## License

[MIT](LICENSE) © ENT-E. Only process media you have the right to use.
