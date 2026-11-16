"""Speak text with Kokoro (hexgrad/Kokoro-82M, Apache-2.0) on this machine's CPU, through kokoro-onnx.

Usage (with the local venv that has kokoro-onnx):
  python kokoro_tts.py --models <dir with kokoro-v1.0.int8.onnx + voices-v1.0.bin> --voice af_heart --lang en-us \
      --text "Hello" --out speech.wav
Prints one JSON line: {"ok": true, "path": ..., "seconds": <audio length>}.
"""

import argparse
import json
import os


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--voice", required=True)
    ap.add_argument("--lang", default="en-us")
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()

    import soundfile as sf
    from kokoro_onnx import Kokoro

    k = Kokoro(os.path.join(a.models, "kokoro-v1.0.int8.onnx"), os.path.join(a.models, "voices-v1.0.bin"))
    audio, sr = k.create(a.text, voice=a.voice, lang=a.lang, speed=a.speed)
    sf.write(a.out, audio, sr)
    print(json.dumps({"ok": True, "path": os.path.abspath(a.out), "seconds": round(len(audio) / sr, 2)}))


if __name__ == "__main__":
    main()
