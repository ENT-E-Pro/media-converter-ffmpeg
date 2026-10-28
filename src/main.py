"""Apify Actor entrypoint: Media Converter (FFmpeg) for AI agents, pay-per-event."""
import asyncio, os, tempfile, uuid
import httpx
from apify import Actor
from .core import convert, ConversionError, billable_minutes, PRESETS

MAX_DOWNLOAD = 2 * 1024**3


async def download(url: str, dest: str) -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as c:
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            n = 0
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    n += len(chunk)
                    if n > MAX_DOWNLOAD:
                        raise ConversionError("File larger than 2 GB limit")
                    f.write(chunk)


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        files = inp.get("files") or []
        if inp.get("url"):
            files.append({"url": inp["url"]})
        default_target = inp.get("target", "mp3")
        max_dur = float(inp.get("maxDurationSeconds", 10800))
        if not files:
            raise ValueError("Provide 'url' or 'files' [{url, target}]")
        kvs = await Actor.open_key_value_store()
        for item in files:
            item = item if isinstance(item, dict) else {"url": item}
            url = item["url"]
            target = item.get("target") or default_target
            start = item.get("startSeconds", inp.get("startSeconds"))
            dur = item.get("durationSeconds", inp.get("durationSeconds"))
            row = {"sourceUrl": url, "target": target}
            with tempfile.TemporaryDirectory() as td:
                try:
                    src = os.path.join(td, "input")
                    await download(url, src)
                    res = await asyncio.to_thread(convert, src, target, td, start, dur, max_dur)
                    key = f"{uuid.uuid4().hex[:12]}.{res.ext}"
                    with open(res.output_path, "rb") as f:
                        await kvs.set_value(key, f.read(), content_type=res.mime)
                    minutes = billable_minutes(res.output_duration or res.input_probe.get("duration"))
                    row.update(status="ok", outputKey=key, outputUrl=await kvs.get_public_url(key),
                               mime=res.mime, outputBytes=res.output_bytes, outputDurationSeconds=res.output_duration,
                               input=res.input_probe, billedMinutes=minutes)
                    # charge only on success: one per file + per media minute (thumbnails: file only)
                    r = await Actor.charge(event_name="file-converted", count=1)
                    if target != "thumbnail":
                        await Actor.charge(event_name="media-minute", count=minutes)
                    await Actor.push_data(row)
                    if getattr(r, "event_charge_limit_reached", False):
                        Actor.log.info("Spending limit reached; stopping.")
                        break
                except (ConversionError, httpx.HTTPError, Exception) as e:  # never-fail: error rows are free
                    row.update(status="error", error=str(e)[:500])
                    await Actor.push_data(row)
