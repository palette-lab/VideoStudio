#!/usr/bin/env bash
# Installs the free local models Presenter Studio uses (about 3.7 GB): Ditto lip-sync and Kokoro voice.
# Safe to re-run: existing files are kept.
set -euo pipefail
cd "$(dirname "$0")/.."                       # tools/
mkdir -p vendor-src && cd vendor-src

if [ ! -d ditto-talkinghead ]; then
  GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/antgroup/ditto-talkinghead.git
fi
cd ditto-talkinghead
[ -x .venv/bin/python ] || uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python torch onnxruntime librosa opencv-python-headless scikit-image cython \
  imageio imageio-ffmpeg filetype tqdm einops mediapipe scipy "numpy<2.3" kokoro-onnx soundfile

B=https://huggingface.co/digital-avatar/ditto-talkinghead/resolve/main
for f in ditto_cfg/v0.4_hubert_cfg_pytorch.pkl \
         ditto_pytorch/aux_models/2d106det.onnx ditto_pytorch/aux_models/det_10g.onnx \
         ditto_pytorch/aux_models/face_landmarker.task ditto_pytorch/aux_models/landmark203.onnx \
         ditto_pytorch/aux_models/hubert_streaming_fix_kv.onnx \
         ditto_pytorch/models/appearance_extractor.pth ditto_pytorch/models/decoder.pth \
         ditto_pytorch/models/lmdm_v0.4_hubert.pth ditto_pytorch/models/motion_extractor.pth \
         ditto_pytorch/models/stitch_network.pth ditto_pytorch/models/warp_network.pth; do
  [ -s "checkpoints/$f" ] || { mkdir -p "checkpoints/$(dirname "$f")"; curl -sSfL --retry 3 -o "checkpoints/$f" "$B/$f"; }
done
cd ..

mkdir -p kokoro-models
K=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
for f in kokoro-v1.0.int8.onnx voices-v1.0.bin; do
  [ -s "kokoro-models/$f" ] || curl -sSfL --retry 3 -o "kokoro-models/$f" "$K/$f"
done
echo "Local models ready."
