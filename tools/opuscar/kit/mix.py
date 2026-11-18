"""The Programmatic Film's sound mix (VideoStudio's own code): narration at its beats, procedural sound effects from
Lemo-Opuscar's sfx.py (MIT) at the film's cues, and music ducked under the voice -> one 48 kHz stereo WAV.

    python mix.py spec.json out.wav
spec = {"dur": s, "narration": [{"path", "t"}], "events": [{"t", "type", "gain"?}], "music": path | null}
Prints one JSON line {"ok": true, "path", "events": n_placed}.
"""

import json
import os
import sys

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core", "audio"))
import sfx  # noqa: E402  (Lemo-Opuscar, MIT)

SR = sfx.SR
EFFECTS = {  # cue type -> (sound, gain)
    "click": (lambda: sfx.click(), 0.5), "clack": (lambda: sfx.clack(), 0.5), "pop": (lambda: sfx.pop(), 0.45),
    "whoosh": (lambda: sfx.whoosh(), 0.35), "thump": (lambda: sfx.thump(), 0.6), "step": (lambda: sfx.step(), 0.4),
    "ding": (lambda: sfx.ding(), 0.35), "creak": (lambda: sfx.creak(), 0.3), "rumble": (lambda: sfx.rumble(), 0.35),
    "crash": (lambda: sfx.crash(), 0.4), "heartbeat": (lambda: sfx.heartbeat(), 0.5), "beep": (lambda: sfx.quindar(), 0.25),
}


def load(path: str) -> np.ndarray:
    """Any WAV/MP3 soundfile reads, as mono float at SR."""
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        g = np.gcd(int(sr), SR)
        x = resample_poly(x, SR // g, int(sr) // g).astype("float32")
    return x


def main():
    spec = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    n = int(round(float(spec["dur"]) * SR))
    voice = np.zeros((n, 2), "float32")
    fx = np.zeros((n, 2), "float32")
    speech = np.zeros(n, "float32")
    for v in spec.get("narration") or []:
        x = load(v["path"])
        sfx.add(voice, x, float(v["t"]), 1.0)
        s = int(float(v["t"]) * SR)
        speech[max(0, s):min(n, s + len(x))] = 1.0
    placed = 0
    for e in spec.get("events") or []:
        kind = EFFECTS.get(str(e.get("type") or "").lower())
        if not kind:
            continue
        make, gain = kind
        sfx.add(fx, make(), float(e["t"]), gain * float(e.get("gain") or 1.0), float(e.get("pan") or 0.0))
        placed += 1
    mix = voice + fx
    if spec.get("music") and os.path.exists(spec["music"]):
        m = load(spec["music"])[:n]
        m = np.pad(m, (0, n - len(m)))
        k = int(0.25 * SR)  # duck under the voice, smoothed so it breathes instead of pumping
        duck = np.convolve(speech, np.ones(k) / k, mode="same")
        gain = 0.32 - 0.2 * np.clip(duck * 1.5, 0, 1)
        fade = np.ones(n, "float32")
        f = min(n, int(1.5 * SR))
        fade[-f:] = np.linspace(1, 0, f)
        mix += (m * gain * fade)[:, None] * 0.9
    mix = np.stack([sfx.limit(mix[:, c], 0.95) for c in range(2)], axis=1)  # sfx.limit is 1-D: per channel
    sf.write(out, mix.astype("float32"), SR)
    print(json.dumps({"ok": True, "path": os.path.abspath(out), "events": placed}))


if __name__ == "__main__":
    main()
