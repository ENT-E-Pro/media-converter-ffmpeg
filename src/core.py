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
    # x264 superfast/CRF 23, 2 threads (keeps memory low at 1 GB); output capped at 1080p (see VIDEO_SCALE)
    "mp4":  ("mp4",  ["-sn", "-dn", "-c:v", "libx264", "-preset", "superfast", "-crf", "23", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"], "video/mp4"),
    # VP9 realtime, cpu-used 8, row-mt, constrained quality (CRF 33, max 3 Mbit/s); output capped at 1080p
    "webm": ("webm", ["-sn", "-dn", "-c:v", "libvpx-vp9", "-crf", "33", "-b:v", "3M", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-c:a", "libopus"], "video/webm"),
    "gif":  ("gif",  ["-vf", "fps=8,scale=360:-1:flags=lanczos", "-an"], "image/gif"),
    "thumbnail": ("jpg", ["-frames:v", "1", "-q:v", "3"], "image/jpeg"),
}
MAX_DURATION_DEFAULT = 3 * 3600
# Fit inside 1920x1080 (landscape) or 1080x1920 (portrait); never upscales, keeps aspect ratio, even dimensions.
VIDEO_SCALE = ("scale=w='if(gte(iw,ih),min(iw,1920),min(iw,1080))':h='if(gte(iw,ih),min(ih,1080),min(ih,1920))'"
               ":force_original_aspect_ratio=decrease:force_divisible_by=2")
# Inputs that are already in the target codec can be remuxed (stream copy) instead of re-encoded.
COPY_OK = {"mp4": ({"h264"}, {"aac"}), "webm": ({"vp9", "vp8", "av1"}, {"opus", "vorbis"})}


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
    reencoded: bool = False          # video stream was encoded (not stream-copied); mp4/webm only
    output_width: int | None = None
    output_height: int | None = None


def probe(path: str) -> dict:
    return _probe_full(path)[0]


def _probe_full(path: str) -> tuple[dict, dict | None, dict | None]:
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
    return ({
        "format": fmt.get("format_name"),
        "duration": float(dur) if dur not in (None, "N/A") else None,
        "sizeBytes": int(fmt["size"]) if fmt.get("size") else None,
        "video": {"codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height")} if v else None,
        "audio": {"codec": a.get("codec_name"), "sampleRate": int(a["sample_rate"]) if a.get("sample_rate") else None,
                  "channels": a.get("channels")} if a else None,
    }, v, a)


def convert(src: str, target: str, out_dir: str, start: float | None = None, duration: float | None = None,
            max_duration: float = MAX_DURATION_DEFAULT, timeout: int = 1800) -> Result:
    if target not in PRESETS:
        raise ConversionError(f"Unsupported target '{target}'. Use one of: {', '.join(PRESETS)}")
    info, vstream, astream = _probe_full(src)
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
    reencoded = False
    if target in ("mp4", "webm"):
        args = _video_args(target, args, vstream, astream, trimmed=bool(start or duration))
        reencoded = "copy" not in args
    cmd += args + [out]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        raise ConversionError(f"ffmpeg failed: {p.stderr.strip()[:500]}")
    out_info = probe(out) if target != "thumbnail" else {"duration": None}
    ov = out_info.get("video") or {}
    return Result(out, ext, mime, info, os.path.getsize(out), out_info.get("duration"),
                  reencoded, ov.get("width"), ov.get("height"))


def _fits_1080p(v: dict) -> bool:
    w, h = v.get("width") or 0, v.get("height") or 0
    return 0 < max(w, h) <= 1920 and 0 < min(w, h) <= 1080


def _video_args(target: str, args: list, v: dict | None, a: dict | None, trimmed: bool) -> list:
    """Remux when the input is already web-ready (same codec, 8-bit 4:2:0, <=1080p, no trim);
    otherwise re-encode with the preset, scaled down to fit 1080p."""
    vcodecs, acodecs = COPY_OK[target]
    v_ok = (v is not None and not trimmed and v.get("codec_name") in vcodecs
            and v.get("pix_fmt") in ("yuv420p", "yuvj420p") and _fits_1080p(v))
    if v_ok:
        out = ["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-c:v", "copy"]
        if a is None:
            out += ["-an"]
        elif a.get("codec_name") in acodecs:
            out += ["-c:a", "copy"]
        else:
            out += ["-c:a", "aac" if target == "mp4" else "libopus"]
        return out + (["-movflags", "+faststart"] if target == "mp4" else [])
    out = list(args)
    i = out.index("-c:v")
    out[i:i] = ["-vf", VIDEO_SCALE]
    if a is None:  # no audio stream: drop the audio codec options
        j = out.index("-c:a"); del out[j:j + 2]
    return out


BILLING_TOLERANCE_SECONDS = 0.5  # encoder padding (e.g. 60:00.02) must not add a billed minute


def billable_minutes(seconds: float | None) -> int:
    """Per started minute of output, minimum 1, ignoring up to 0.5 s of encoder padding."""
    if not seconds:
        return 1
    import math
    return max(1, math.ceil(max(0.0, seconds - BILLING_TOLERANCE_SECONDS) / 60))


# --- pay-per-event plan (pure, unit-tested) ---
REENCODE_EVENT = "video-reencode-minute"
HD_REENCODE_MIN_SIDE = 720  # "over 720p": the shorter output side must exceed 720 px (1080p-class)


def is_hd_reencode(target: str, res: Result) -> bool:
    """True when mp4/webm video was actually re-encoded and the output is above 720p (after the 1080p cap)."""
    if target not in ("mp4", "webm") or not res.reencoded:
        return False
    w, h = res.output_width or 0, res.output_height or 0
    return min(w, h) > HD_REENCODE_MIN_SIDE


def charge_plan(target: str, res: Result, minutes: int, reencode_event_live: bool = True) -> list[tuple[str, int]]:
    """Events to charge for ONE successful output. Exactly one per-minute event (never both):
    video-reencode-minute for 1080p-class re-encodes, else media-minute; thumbnails pay the file event only.
    If the new event is not in the run's pricing yet (notice period), fall back to media-minute."""
    plan = [("file-converted", 1)]
    if target == "thumbnail":
        return plan
    event = REENCODE_EVENT if (reencode_event_live and is_hd_reencode(target, res)) else "media-minute"
    return plan + [(event, minutes)]
