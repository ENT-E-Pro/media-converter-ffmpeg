"""Actor-level billing: failed items are never charged, exactly one minute event per file."""
import asyncio, shutil, subprocess, types
import pytest
import src.main as m


class FakeKVS:
    async def set_value(self, *a, **k): pass
    async def get_public_url(self, key): return f"https://example/{key}"


class FakeActor:
    def __init__(self, inp, prices):
        self.inp, self.prices, self.rows, self.charges = inp, prices, [], []
        self.log = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get_input(self): return self.inp
    async def open_key_value_store(self): return FakeKVS()
    async def push_data(self, row): self.rows.append(dict(row))
    async def charge(self, event_name, count=1):
        self.charges.append((event_name, count)); return types.SimpleNamespace(event_charge_limit_reached=False)
    def get_charging_manager(self):
        return types.SimpleNamespace(get_pricing_info=lambda: types.SimpleNamespace(per_event_prices=self.prices))


@pytest.fixture
def files(tmp_path):
    big = tmp_path / "big.mkv"
    subprocess.run(["ffmpeg","-y","-v","error","-f","lavfi","-i","testsrc=size=1920x1080:rate=5","-t","0.4","-c:v","mpeg4",str(big)],check=True)
    return {"https://ok/big.mkv": str(big)}


def _run(monkeypatch, files, inp, prices):
    fa = FakeActor(inp, prices)
    monkeypatch.setattr(m, "Actor", fa)
    async def fake_download(url, dest):
        if url not in files: raise m.ConversionError("download failed")
        shutil.copy(files[url], dest)
    monkeypatch.setattr(m, "download", fake_download)
    asyncio.run(m.main())
    return fa


PRICES = {"file-converted": 0.005, "media-minute": 0.002, "video-reencode-minute": 0.04}


def test_failed_items_free_and_hd_reencode_charged_once(monkeypatch, files):
    fa = _run(monkeypatch, files, {"files": [{"url": "https://bad/x.mp4", "target": "mp4"},
                                             {"url": "https://ok/big.mkv", "target": "mp4"},
                                             {"url": "https://ok/big.mkv", "target": "nope"}]}, PRICES)
    assert [r["status"] for r in fa.rows] == ["error", "ok", "error"]
    assert fa.charges == [("file-converted", 1), ("video-reencode-minute", 1)]
    assert fa.rows[1]["minuteEvent"] == "video-reencode-minute" and fa.rows[1]["videoReencoded"] is True


def test_falls_back_to_media_minute_before_new_price_is_live(monkeypatch, files):
    prices = {k: v for k, v in PRICES.items() if k != "video-reencode-minute"}
    fa = _run(monkeypatch, files, {"url": "https://ok/big.mkv", "target": "webm"}, prices)
    assert fa.charges == [("file-converted", 1), ("media-minute", 1)]
