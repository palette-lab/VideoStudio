# Free local models for Presenter Studio

Presenter Studio speaks and lip-syncs on this machine for free. Only the presenter photo uses a paid model
(Nano Banana Pro on fal, a few cents per image).

| Step | Model | Licence | Runs on |
|---|---|---|---|
| Voice | [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) via [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) | Apache-2.0 | CPU |
| Lip-sync | [Ditto](https://github.com/antgroup/ditto-talkinghead) (Ant Group), PyTorch release | Apache-2.0 | Apple GPU (`mps`) or CPU |

Install (about 3.7 GB): `tools/local_lipsync/setup.sh`

- `ditto_run.py`: runs Ditto with every device in its config switched from `cuda` to `mps`/`cpu` (Ditto ships for
  NVIDIA only).
- `kokoro_tts.py`: one line of speech to a WAV file.

Kokoro voices: English (US/UK), Hindi, Spanish, French, Italian, Japanese, Portuguese, Chinese. Other languages
(e.g. Telugu) need the user's own recording, or `PRESENTER_PAID=on` (ElevenLabs).

Settings: `PRESENTER_LOCAL_DEVICE` (auto | mps | cpu), `PRESENTER_LOCAL_TIMEOUT` (seconds per part, default 3600),
`PRESENTER_PAID=on` (adds ElevenLabs and fal lip-sync — Kling AI Avatar v2, LongCat, InfiniteTalk — as fallbacks),
`PRESENTER_LOCAL_DIR` (default `tools/`).

Licence note: Ditto's face detector `det_10g.onnx` comes from InsightFace, whose pretrained models are for
non-commercial research. Check this before using the output in paid ads.

Hosted Palette has no Apple GPU and no local models, so there Presenter Studio needs `PRESENTER_PAID=on`.
