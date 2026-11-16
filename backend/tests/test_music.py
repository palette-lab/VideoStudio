"""Music bed: the prompt, the off switch, and the real ffmpeg mix (ducking under a voice)."""

import asyncio
import subprocess

from engines import music


def _media(path, video=True, tone=None, seconds=4):
    args = ["ffmpeg", "-v", "error", "-y"]
    if video:
        args += ["-f", "lavfi", "-i", f"testsrc2=size=320x568:rate=24:duration={seconds}"]
    if tone:
        args += ["-f", "lavfi", "-i", f"sine=frequency={tone}:duration={seconds}"]
    args += (["-c:v", "libx264", "-pix_fmt", "yuv420p"] if video else []) + (["-c:a", "aac"] if tone and video else [])
    subprocess.run(args + ["-shortest", str(path)] if video and tone else args + [str(path)], check=True)


def test_prompt_is_instrumental_and_follows_the_planner_or_genre():
    assert music.prompt_for({"genre": "product_film"}).startswith("minimal premium instrumental")
    p = music.prompt_for({"music": "dreamy lo-fi with soft vocals, 90 bpm", "genre": "ugc_ad"})
    assert p == "dreamy lo-fi, 90 bpm, instrumental only, no vocals, no singing"
    assert music.wanted("a serum reel") and not music.wanted("a serum reel, no music please")


def test_music_is_mixed_under_the_film(tmp_path):
    film, bed, silent = tmp_path / "film.mp4", tmp_path / "bed.wav", tmp_path / "silent.mp4"
    _media(film, tone=440)
    _media(bed, video=False, tone=220, seconds=6)
    _media(silent, tone=None)
    for src, voiced in ((film, True), (film, False), (silent, False)):
        out = asyncio.run(music.mix(str(src), str(bed), voiced, tmp_path / f"out_{voiced}_{src.stem}.mp4"))
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
                                "-of", "csv=p=0", out], capture_output=True, text=True).stdout
        assert "audio" in probe and "video" in probe
        assert abs(float(probe.strip().splitlines()[-1]) - 4.0) < 0.2  # the film's length, not the music's


def test_with_a_voice_the_music_is_full_in_pauses_and_ducked_under_speech():
    f = music.mix_filter(True, True, 20)
    assert "volume=0.7" in f and "sidechaincompress" in f and "ratio=20" in f
    assert "sidechaincompress" not in music.mix_filter(True, False, 20)


def test_a_finished_video_gets_music_without_the_user_asking(tmp_path, monkeypatch):
    film = tmp_path / "anim.mp4"
    _media(film, tone=None)  # a programmatic animation: no sound at all
    bed = tmp_path / "bed.wav"
    _media(bed, video=False, tone=220, seconds=6)

    async def fake_compose(prompt, seconds, out):
        assert "instrumental only" in prompt and seconds >= 4
        return str(bed)
    monkeypatch.setattr(music, "available", lambda: None)
    monkeypatch.setattr(music, "compose", fake_compose)
    msgs = []

    async def progress(m):
        msgs.append(m)
    out, prompt = asyncio.run(music.add_to(str(film), "a bar chart of Q3 sales", "explainer", False, tmp_path / "m",
                                           progress, "Motion Studio"))
    assert out.endswith("with_music.mp4") and prompt.startswith("light curious instrumental")
    assert "audio" in subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                                      out], capture_output=True, text=True).stdout
    same, none = asyncio.run(music.add_to(str(film), "a chart, no music", "explainer", False, tmp_path / "n",
                                          progress, "Motion Studio"))
    assert same == str(film) and none is None
