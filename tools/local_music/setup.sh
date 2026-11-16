#!/usr/bin/env bash
# Local music for VideoStudio: MusicGen (Meta) through Hugging Face transformers, in the same local venv that runs
# Ditto and Kokoro (tools/vendor-src/ditto-talkinghead/.venv, made by tools/local_lipsync/setup.sh; it has torch).
# Free and offline after the first run. The first music render downloads facebook/musicgen-small (~2.4 GB).
# Licence: MusicGen weights are CC-BY-NC 4.0 (non-commercial use).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=vendor-src/ditto-talkinghead/.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "Run tools/local_lipsync/setup.sh first (it creates the local venv with torch)." >&2
  exit 1
fi
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PY" "transformers>=4.45,<5" soundfile
else
  "$PY" -m pip install "transformers>=4.45,<5" soundfile
fi
echo "Testing a 4 s clip (downloads the model the first time)..."
"$PY" local_music/musicgen_run.py --prompt "warm minimal lo-fi beat, soft piano" --seconds 4 \
  --out "${TMPDIR:-/tmp}/videostudio_music_test.wav"
echo READY
