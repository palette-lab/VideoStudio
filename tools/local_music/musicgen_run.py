#!/usr/bin/env python3
"""Make a music bed locally with MusicGen (Meta, via Hugging Face transformers). Free, runs on this Mac.

usage: python musicgen_run.py --prompt "calm lo-fi, soft piano, 90 bpm" --seconds 15 --out music.wav [--device auto]

Model: facebook/musicgen-small by default (MUSICGEN_MODEL to change). Weights are CC-BY-NC 4.0 (non-commercial).
MusicGen makes at most 30 s at a time; longer beds are made in 30 s pieces that continue from the previous piece's
last seconds, then joined. Prints one JSON line: {"ok": true, "path": ..., "seconds": ...} or {"ok": false, ...}.
"""
import argparse
import json
import os
import sys

import numpy as np


def pick_device(want: str) -> str:
    import torch
    if want != "auto":
        return want
    return "mps" if torch.backends.mps.is_available() else "cpu"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default=os.environ.get("MUSICGEN_DEVICE", "auto"))
    a = ap.parse_args()
    try:
        import torch
        import soundfile as sf
        from transformers import AutoProcessor, MusicgenForConditionalGeneration
    except ImportError as e:
        print(json.dumps({"ok": False, "error": f"missing package: {e.name} (run tools/local_music/setup.sh)"}))
        sys.exit(1)

    name = os.environ.get("MUSICGEN_MODEL", "facebook/musicgen-small")
    device = pick_device(a.device)
    proc = AutoProcessor.from_pretrained(name)
    model = MusicgenForConditionalGeneration.from_pretrained(name)
    try:
        model = model.to(device)
    except Exception:
        device = "cpu"
    sr = model.config.audio_encoder.sampling_rate
    fps = model.config.audio_encoder.frame_rate  # tokens per second of audio
    want = max(1.0, min(a.seconds, 180.0))

    pieces, made = [], 0.0
    overlap = 3.0
    while made < want - 0.05:
        chunk = min(30.0, want - made + (overlap if pieces else 0.0))
        kw = {"text": [a.prompt], "padding": True, "return_tensors": "pt"}
        if pieces:  # continue the music from the end of the previous piece, so it stays one track
            kw["audio"] = [pieces[-1][-int(overlap * sr):]]
            kw["sampling_rate"] = sr
        inputs = {k: v.to(device) for k, v in proc(**kw).items()}
        with torch.no_grad():
            audio = model.generate(**inputs, do_sample=True, guidance_scale=3.0,
                                   max_new_tokens=int(chunk * fps) + 2)
        wav = audio[0, 0].float().cpu().numpy()
        if pieces:
            wav = wav[int(overlap * sr):]  # the prompt audio comes back at the start: keep only the new part
        pieces.append(wav)
        made += len(wav) / sr
        if len(wav) < sr * 0.5:
            break
    wav = np.concatenate(pieces)[: int(want * sr)]
    peak = float(np.max(np.abs(wav))) or 1.0
    sf.write(a.out, (wav / peak * 0.89).astype(np.float32), sr)
    print(json.dumps({"ok": True, "path": a.out, "seconds": round(len(wav) / sr, 2), "device": device, "model": name}))


if __name__ == "__main__":
    main()
