import os, subprocess, pytest
from src.core import convert, probe, ConversionError, billable_minutes, PRESETS

@pytest.fixture(scope="session")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("m")
    av = str(d / "in.mov"); a = str(d / "in.ogg"); bad = str(d / "bad.bin")
    subprocess.run(["ffmpeg","-y","-v","error","-f","lavfi","-i","testsrc=size=320x240:rate=15","-f","lavfi","-i","sine=frequency=440:sample_rate=44100","-t","3","-c:v","mpeg4","-c:a","aac","-shortest",av],check=True)
    subprocess.run(["ffmpeg","-y","-v","error","-f","lavfi","-i","sine=frequency=440","-t","2","-c:a","libvorbis",a],check=True)
    open(bad,"wb").write(os.urandom(2048))
    return {"av": av, "a": a, "bad": bad}

@pytest.mark.parametrize("target", list(PRESETS))
def test_all_targets_from_video(media, tmp_path, target):
    r = convert(media["av"], target, str(tmp_path))
    assert os.path.getsize(r.output_path) > 0 and r.output_path.endswith(r.ext)

def test_wav16k_specs(media, tmp_path):
    r = convert(media["a"], "wav16k", str(tmp_path))
    p = probe(r.output_path)
    assert p["audio"]["sampleRate"] == 16000 and p["audio"]["channels"] == 1

def test_trim(media, tmp_path):
    r = convert(media["av"], "mp3", str(tmp_path), start=1, duration=1)
    assert 0.8 < r.output_duration < 1.3

def test_audio_only_to_video_fails(media, tmp_path):
    with pytest.raises(ConversionError): convert(media["a"], "mp4", str(tmp_path))

def test_garbage_input(media, tmp_path):
    with pytest.raises(ConversionError): convert(media["bad"], "mp3", str(tmp_path))

def test_bad_target(media, tmp_path):
    with pytest.raises(ConversionError): convert(media["av"], "exe", str(tmp_path))

def test_duration_cap(media, tmp_path):
    with pytest.raises(ConversionError): convert(media["av"], "mp3", str(tmp_path), max_duration=1)

def test_billable_minutes():
    assert billable_minutes(None) == 1 and billable_minutes(60) == 1 and billable_minutes(61) == 2

def test_billing_tolerance():
    assert billable_minutes(3600.02) == 60 and billable_minutes(3630) == 61 and billable_minutes(60.4) == 1 and billable_minutes(3.0186) == 1


def _mk(path, *vargs, audio=True):
    cmd = ["ffmpeg","-y","-v","error","-f","lavfi","-i","testsrc=size=320x240:rate=15"]
    if audio: cmd += ["-f","lavfi","-i","sine=frequency=440:sample_rate=44100"]
    subprocess.run(cmd + ["-t","1"] + list(vargs) + (["-shortest"] if audio else []) + [path], check=True)
    return path

def test_h264_input_is_remuxed(tmp_path):
    src = _mk(str(tmp_path / "web.mp4"), "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac")
    r = convert(src, "mp4", str(tmp_path))
    p = probe(r.output_path)
    assert p["video"]["codec"] == "h264" and p["audio"]["codec"] == "aac"
    with open(src, "rb") as a, open(r.output_path, "rb") as b:  # stream copy: same size class, no re-encode
        assert abs(len(a.read()) - len(b.read())) < 20000

def test_trimmed_h264_is_reencoded_accurately(tmp_path):
    src = _mk(str(tmp_path / "web.mp4"), "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac")
    r = convert(src, "mp4", str(tmp_path), start=0.2, duration=0.5)
    assert 0.3 < r.output_duration < 0.8

def test_mp4_without_audio(tmp_path):
    src = _mk(str(tmp_path / "silent.mov"), "-c:v","mpeg4", audio=False)
    r = convert(src, "mp4", str(tmp_path))
    p = probe(r.output_path)
    assert p["video"]["codec"] == "h264" and p["audio"] is None

@pytest.mark.parametrize("target", ["mp4", "webm"])
@pytest.mark.parametrize("size,expect", [("2560x1440", (1920, 1080)), ("1440x2560", (1080, 1920)), ("1600x1600", (1080, 1080))])
def test_video_capped_at_1080p(tmp_path, target, size, expect):
    src = str(tmp_path / f"big{size}.mkv")
    subprocess.run(["ffmpeg","-y","-v","error","-f","lavfi","-i",f"testsrc=size={size}:rate=5","-t","0.4","-c:v","libx264","-pix_fmt","yuv420p",src],check=True)
    r = convert(src, target, str(tmp_path))
    p = probe(r.output_path)
    assert (p["video"]["width"], p["video"]["height"]) == expect

def test_small_video_not_upscaled(media, tmp_path):
    r = convert(media["av"], "webm", str(tmp_path))
    p = probe(r.output_path)
    assert (p["video"]["width"], p["video"]["height"]) == (320, 240) and p["video"]["codec"] == "vp9"
