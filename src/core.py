"""Pure conversion logic (no Apify dependency) so it is unit-testable."""
from __future__ import annotations
import json, os, shutil, subprocess, tempfile
from dataclasses import dataclass

# target format -> (extension, ffmpeg args, mime)
PRESETS = {
    "mp3":  ("mp3",  ["-vn", "-c:a", "libmp3lame", "-q:a", "2"], "audio/mpeg"),
    "wav":  ("wav",  ["-vn", "-c:a", "pcm_s16le"], "audio/wav"),
    "wav16k": ("wav", ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"], "audio/wav"),  # speech-to-text ready
    "flac": ("flac", ["-vn", "-c:a", "flac"], "audio/flac"),
    "ogg":  ("ogg",  ["-vn", "-c:a", "libvorbis", "-q:a", "5"], "audio/ogg"),
    "m4a":  ("m4a",  ["-vn", "-c:a", "aac", "-b:a", "192k"], "audio/mp4"),
    "mp4":  ("mp4",  ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"], "video/mp4"),
    "webm": ("webm", ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "33", "-deadline", "realtime", "-c:a", "libopus"], "video/webm"),
    "gif":  ("gif",  ["-vf", "fps=8,scale=360:-1:flags=lanczos", "-an"], "image/gif"),
    "thumbnail": ("jpg", ["-frames:v", "1", "-q:v", "3"], "image/jpeg"),
}
MAX_DURATION_DEFAULT = 3 * 3600


class ConversionError(Exception):
    pass


@dataclass
class Result:
    output_path: str
    ext: str
    mime: str
    input_probe: dict
    output_bytes: int
    output_duration: float | None


def probe(path: str) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise ConversionError(f"Not a readable media file: {p.stderr.strip()[:300]}")
    d = json.loads(p.stdout)
    fmt = d.get("format", {})
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = fmt.get("duration")
    return {
        "format": fmt.get("format_name"),
        "duration": float(dur) if dur not in (None, "N/A") else None,
        "sizeBytes": int(fmt["size"]) if fmt.get("size") else None,
        "video": {"codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height")} if v else None,
        "audio": {"codec": a.get("codec_name"), "sampleRate": int(a["sample_rate"]) if a.get("sample_rate") else None,
                  "channels": a.get("channels")} if a else None,
    }


def convert(src: str, target: str, out_dir: str, start: float | None = None, duration: float | None = None,
            max_duration: float = MAX_DURATION_DEFAULT, timeout: int = 1800) -> Result:
    if target not in PRESETS:
        raise ConversionError(f"Unsupported target '{target}'. Use one of: {', '.join(PRESETS)}")
    info = probe(src)
    ext, args, mime = PRESETS[target]
    if target in ("mp3", "wav", "wav16k", "flac", "ogg", "m4a") and not info["audio"]:
        raise ConversionError("Input has no audio stream")
    if target in ("mp4", "webm", "gif", "thumbnail") and not info["video"]:
        raise ConversionError("Input has no video stream")
    if info["duration"] and info["duration"] > max_duration and duration is None:
        raise ConversionError(f"Input is {info['duration']:.0f}s, above maxDurationSeconds={max_duration:.0f}")
    out = os.path.join(out_dir, f"output.{ext}")
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", src]
    if duration:
        cmd += ["-t", str(duration)]
    if target == "mp4" and not info["audio"]:
        args = [a for a in args if a not in ("aac",)]
        args = [x for i, x in enumerate(args) if not (x == "-c:a" )]
    cmd += args + [out]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        raise ConversionError(f"ffmpeg failed: {p.stderr.strip()[:500]}")
    out_info = probe(out) if target != "thumbnail" else {"duration": None}
    return Result(out, ext, mime, info, os.path.getsize(out), out_info.get("duration"))


BILLING_TOLERANCE_SECONDS = 0.5  # encoder padding (e.g. 60:00.02) must not add a billed minute


def billable_minutes(seconds: float | None) -> int:
    """Per started minute of output, minimum 1, ignoring up to 0.5 s of encoder padding."""
    if not seconds:
        return 1
    import math
    return max(1, math.ceil(max(0.0, seconds - BILLING_TOLERANCE_SECONDS) / 60))
